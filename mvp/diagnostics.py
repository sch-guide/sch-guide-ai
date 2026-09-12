"""관리자 전용 실데이터 진단. UI 상태 대신 저장 원문/벡터와 실제 검색·생성 경로를 검사합니다."""

import hashlib
import json
import re
from collections import Counter
from io import BytesIO

import numpy as np

from mvp.ai import answer_text, generate
from mvp.documents import PdfInputError, read_document, read_pdf
from mvp.evidence import assess_evidence
from mvp.library import bounded_embedding_question, protect_private, term_matches
from mvp.query import plan_query
from mvp.search_trace import chunk_row, hit_row
from mvp.settings import DIMENSIONS, MODEL, GuideError

KEYWORDS = ('진정간호', '목적', '진정')


def keyword_counts(text):
    return {word: dict(exact=text.count(word), whitespace_normalized=re.sub(r'\s+', '', text).count(word))
            for word in KEYWORDS}


def extraction_summary(document):
    return dict(total_characters=document.character_count,
                page_count=len(document.pages) if document.source_type == 'pdf' else None,
                unit_count=len(document.pages), source_type=document.source_type,
                keywords=keyword_counts('\n'.join(p.text for p in document.pages)),
                pages=[dict(page_number=p.number, location=p.location, section_title=p.section,
                            characters=len(p.text), keywords=keyword_counts(p.text), sample=p.text[:200],
                            extraction_error=bool(p.error)) for p in document.pages],
                warnings=list(document.warnings))


def scan_summary(content, document):
    """적은 문자 수만으로 스캔이라고 단정하지 않습니다. 이미지 유무도 별도로 기록합니다."""
    import pdfplumber

    rows = []
    with pdfplumber.open(BytesIO(content)) as pdf:
        for original, page in zip(document.pages, pdf.pages, strict=True):
            low_text = len(original.text.strip()) < 80
            images = len(page.images)
            rows.append(dict(page_number=original.number, characters=len(original.text), image_count=images,
                             ocr_candidate=low_text and images > 0,
                             needs_visual_review=low_text or bool(images)))
            page.close()
    return dict(low_text_threshold=80, pages=rows,
                ocr_candidate_pages=[r['page_number'] for r in rows if r['ocr_candidate']],
                note='이미지+80자 미만은 OCR 후보이며 확정 판정이 아닙니다. 일반 텍스트 추출과 OCR 결과를 분리합니다.')


def vectors_summary(rows, chunk_ids):
    """메타데이터의 chunk_count가 아니라 실제 embedding 열/blob를 검사합니다."""
    valid, dimensions, missing, invalid, zero = {}, Counter(), [], [], []
    norms = []
    for identifier, value in rows:
        if value is None:
            missing.append(identifier)
            continue
        try:
            vector = np.asarray(json.loads(value) if isinstance(value, str) else value, dtype=np.float32)
            if vector.ndim != 1:
                raise ValueError('shape')
            dimensions[str(len(vector))] += 1
            if len(vector) != DIMENSIONS or not np.isfinite(vector).all():
                raise ValueError('invalid')
            norm = float(np.linalg.norm(vector))
            if norm == 0:
                zero.append(identifier)
                continue
            norms.append(norm)
            valid[identifier] = vector
        except (ValueError, TypeError):
            invalid.append(identifier)
    ids = [i for i, _ in rows]
    return dict(stored_row_count=len(rows), nonnull_embedding_count=len(rows) - len(missing),
                valid_vector_count=len(valid), dimensions=dict(dimensions), expected_dimensions=DIMENSIONS,
                missing_embedding_ids=missing, invalid_embedding_ids=invalid, zero_vector_ids=zero,
                duplicate_ids=[i for i, n in Counter(ids).items() if n > 1],
                chunks_without_vector_rows=sorted(set(chunk_ids) - set(ids)),
                vector_rows_without_chunks=sorted(set(ids) - set(chunk_ids)),
                norm_range=[min(norms), max(norms)] if norms else None), valid


def safe_failure(exc):
    # 파서/API 예외의 원문·요청 헤더·URL을 보고서에 넣지 않습니다.
    if isinstance(exc, (GuideError, PdfInputError)):
        codes = re.findall(r'\(([A-Z][A-Z0-9_/-]+)\)', str(exc))
        return codes[-1] if codes else 'GUIDE_ERROR'
    return 'DIAGNOSTIC_STAGE_FAILED'


def run_diagnostics(library, settings, model_factory, question, doc_ids, *, probe_query='', call_llm=False,
                    quota=None, transport=None):
    library.auth.require_admin()
    protect_private(question)
    protect_private(probe_query)
    if not question.strip() or len(question) > 500 or len(probe_query) > 500:
        raise GuideError('진단 질문은 1~500자로 입력하세요.')
    if not 1 <= len(set(doc_ids)) <= 10:
        raise GuideError('진단할 문서는 1~10개를 선택하세요.')
    revision = library.revision()
    all_docs = library.documents(all_status=True)
    selected = [d for d in all_docs if d['id'] in set(doc_ids)]
    if len(selected) != len(set(doc_ids)):
        raise GuideError('진단할 문서를 찾지 못했습니다.')
    searchable = library.documents()
    report = dict(version=1, source='registered_storage_and_live_pipeline', question=question,
                  note='원본은 현재 코드로 다시 추출합니다. 등록 당시 추출 로그가 아니며, 저장 chunk와 별도로 표시합니다.',
                  model=MODEL, configured_minimum_score=settings.min_similarity,
                  documents=[], errors=[], searches={}, final_answer=None)
    chunks_by_id, stored_vectors = {}, {}

    def capture(stage, action):
        try:
            return action()
        except Exception as exc:
            report['errors'].append(dict(stage=stage, code=safe_failure(exc)))
            return None

    for doc in selected:
        identifier = doc['id']
        entry = dict(document_id=identifier, document_name=doc.get('document_name', ''),
                     storage_status=doc.get('storage_status', doc.get('status')),
                     application_status=doc.get('status'), active_in_document_query=identifier in {d['id'] for d in searchable},
                     stored_active=doc.get('stored_active'), stored_indexed=doc.get('stored_indexed'),
                     stored_searchable=doc.get('stored_searchable'),
                     indexed_at=doc.get('stored_indexed_at', doc.get('indexed_at')),
                     metadata_chunk_count=doc.get('stored_chunk_count', doc.get('chunk_count')),
                     metadata_page_count=doc.get('page_count'), registered_model=doc.get('model'),
                     chunk_version=doc.get('chunk_version'), extraction_version=doc.get('extraction_version'),
                     filter_note='null은 미저장/미확인입니다. searchable/indexed 상태를 추측하지 않고 실제 chunk·벡터·검색을 확인합니다.')
        report['documents'].append(entry)
        limited_visibility = settings.mode == 'staff' and not entry['active_in_document_query']
        entry['storage_visibility_note'] = ('비활성 문서는 기존 RLS가 chunk 조회를 제한할 수 있습니다. 0건이어도 DB에서 삭제됐다고 판단하지 않습니다.'
                                            if limited_visibility else '현재 관리자 JWT/권한으로 조회 가능한 저장 행을 검사합니다.')
        chunks = capture(identifier + ':stored_chunks', lambda: library.diagnostic_chunks(identifier))
        if chunks is not None:
            for chunk in chunks:
                protect_private(chunk.text)
                chunks_by_id[chunk.id] = chunk
            entry.update(actual_chunk_count=None if limited_visibility else len(chunks), visible_chunk_count=len(chunks),
                         chunks=[dict(chunk_row(c), keywords=keyword_counts(c.text)) for c in chunks],
                         sedation_chunk_ids=[c.id for c in chunks if term_matches('진정', c.text + ' ' + c.section)],
                         saved_chunk_characters=sum(len(c.text) for c in chunks))
        rows = capture(identifier + ':stored_vectors', lambda: library.diagnostic_vectors(identifier))
        if rows is not None:
            entry['vectors'], valid = vectors_summary(rows, [c.id for c in chunks or []])
            stored_vectors.update(valid)
        source = capture(identifier + ':original_storage', lambda: library.diagnostic_source(identifier))
        if source is not None:
            name, content = source
            entry['original_hash_matches_metadata'] = (
                hashlib.sha256(content).hexdigest() == doc['file_hash'] if doc.get('file_hash') else None)
            plain = capture(identifier + ':text_extraction', lambda: read_document(name, content, ocr=False))
            if plain is not None:
                entry['text_extraction_without_ocr'] = extraction_summary(plain)
                if plain.source_type == 'pdf':
                    raw = capture(identifier + ':pdf_raw_extraction', lambda: read_pdf(name, content))
                    if raw is not None:
                        entry['raw_pdf_extraction'] = extraction_summary(raw)
                        entry['scan_check'] = capture(identifier + ':scan_check', lambda: scan_summary(content, raw))
                if settings.ocr_enabled:
                    enhanced = capture(identifier + ':configured_ocr_extraction', lambda: read_document(name, content, ocr=True))
                    if enhanced is not None:
                        entry['extraction_with_configured_ocr'] = extraction_summary(enhanced)
            del content, source

    model = capture('embedding_model', model_factory)
    if model is not None:
        queries = [('question', question)] + ([('index_probe', probe_query)] if probe_query and probe_query != question else [])
        for label, query in queries:
            plan = plan_query(query, documents=searchable)
            trace = report['searches'][label] = {}
            embedding_query = bounded_embedding_question(plan.expanded, model)
            encoded = capture(label + ':query_embedding', lambda: model.encode([embedding_query]))
            if encoded is None:
                continue
            vector = np.asarray(encoded[0], dtype=np.float32)
            trace['query_embedding'] = dict(text=embedding_query, generated=True, dimensions=int(vector.size),
                                           finite=bool(np.isfinite(vector).all()), norm=float(np.linalg.norm(vector)))
            if vector.shape != (DIMENSIONS,) or not np.isfinite(vector).all() or not np.linalg.norm(vector):
                report['errors'].append(dict(stage=label + ':query_embedding', code='INVALID_QUERY_VECTOR'))
                continue
            library.auth.require_admin()
            hits = capture(label + ':actual_search', lambda: library.search(
                plan.query, vector, doc_ids, settings.min_similarity, plan=plan, trace=trace))
            if hits is None:
                continue
            allowed = set(trace['allowed_document_ids'])
            exact = [dict(chunk_row(chunks_by_id[i]),
                          cosine=float(np.dot(v, vector) / (np.linalg.norm(v) * np.linalg.norm(vector))),
                          inner_product=float(np.dot(v, vector)))
                     for i, v in stored_vectors.items() if i in chunks_by_id and chunks_by_id[i].document_id in allowed]
            trace['stored_vector_cosine_top10'] = sorted(exact, key=lambda r: -r['cosine'])[:10]
            trace['stored_vector_audit_note'] = '저장 벡터를 읽어 별도 전수 비교한 값입니다. 실제 RPC/FAISS 결과는 vector_top10입니다.'
            assessment = assess_evidence(plan, hits)
            trace['evidence_assessment'] = dict(sufficient=assessment.sufficient, reason=assessment.reason,
                                                accepted_chunk_ids=[h.chunk.id for h in assessment.hits])
            if label == 'question':
                report['retrieved_evidence'] = [dict(hit_row(h), text=h.chunk.text) for h in assessment.hits]
                report['generation'] = dict(llm_called=False, block_reason=None,
                                           status='not_requested' if not call_llm else 'pending')
                if call_llm:
                    library.ensure_revision(revision)
                    library.auth.require_admin()
                    generated = capture('answer_generation', lambda: generate(
                        settings, plan.query, hits, library.auth.user_id, quota=quota,
                        transport=transport, plan=plan, trace=report['generation']))
                    if generated is not None:
                        answer, used = generated
                        report['final_answer'] = answer_text(answer)
                        report['answer'] = answer.model_dump()
                        cited = {e.chunk_id for s in answer.statements for e in s.evidence}
                        report['cited_sources'] = [dict(hit_row(h), text=h.chunk.text) for h in used if h.chunk.id in cited]
                        report['generation']['status'] = 'completed'
                    else:
                        report['generation']['status'] = 'error'
                elif not assessment.sufficient:
                    report['generation']['block_reason'] = 'pre_llm:' + assessment.reason
    library.auth.require_admin()
    library.ensure_revision(revision)
    if all_docs != library.documents(all_status=True):
        raise GuideError('진단 중 문서가 변경되었습니다. 다시 실행하세요. (CORPUS_CHANGED)')
    return report
