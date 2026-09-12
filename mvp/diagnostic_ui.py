"""관리자 전용 검색 진단과 BM25 원시 결과 표시."""

import csv
import json
from io import StringIO

import streamlit as st

from mvp.bm25_evaluation import CSV_COLUMNS as EVALUATION_CSV_COLUMNS
from mvp.bm25_evaluation import (
    DEFAULT_QUESTIONS,
    evaluate_bm25,
    report_csv,
    report_json,
    save_bm25_artifacts,
)
from mvp.diagnostics import run_diagnostics
from mvp.settings import GuideError

BM25_CSV_COLUMNS = (
    'query', 'rank', 'bm25_score', 'document_name', 'page_number',
    'section_title', 'chunk_id', 'chunk_text',
)


def bm25_debug_rows(query, trace):
    """검색 trace를 화면과 CSV에서 함께 쓰는 고정 형식으로 바꿉니다."""
    actual_query = trace.get('actual_query') or query
    rows = []
    for rank, item in enumerate(trace.get('bm25_top10', ())[:10], 1):
        rows.append({
            'query': actual_query,
            'rank': rank,
            'bm25_score': float(item.get('score', item.get('bm25_score', 0))),
            'document_name': item.get('document_name') or '',
            'page_number': item.get('page_number'),
            'section_title': item.get('section_title') or '',
            'chunk_id': item.get('chunk_id') or '',
            'chunk_text': item.get('chunk_text') or item.get('sample') or '',
        })
    return rows


def bm25_csv(query, trace):
    """한글이 Excel에서도 깨지지 않는 UTF-8 BOM CSV를 만듭니다."""
    output = StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=BM25_CSV_COLUMNS, lineterminator='\n')
    writer.writeheader()
    writer.writerows(bm25_debug_rows(query, trace))
    return ('\ufeff' + output.getvalue()).encode('utf-8')


def render_bm25_debug(query, trace, index):
    """관리자 채팅에서만 호출하는 질문별 BM25 원시 결과 화면입니다."""
    actual_query = trace.get('actual_query') or query
    rows = bm25_debug_rows(actual_query, trace)
    with st.expander('BM25 원시 검색 결과 · 관리자 전용'):
        st.caption('최종 AI 답변과 무관하게 BM25가 실제로 계산한 상위 10개 chunk입니다.')
        st.markdown('**실제 검색 query**')
        st.code(actual_query, language=None)
        if not rows:
            st.info('BM25 검색 결과가 없습니다.')
        for row in rows:
            st.markdown(f"**{row['rank']}위** · score: {row['bm25_score']:.4f}")
            page = f"p.{row['page_number']}" if row['page_number'] not in (None, '') else '페이지 정보 없음'
            st.caption(f"문서: {row['document_name']} · {page}")
            st.caption(f"section: {row['section_title'] or '미지정'}")
            preview = row['chunk_text'][:300]
            st.text(preview + ('…' if len(row['chunk_text']) > 300 else ''))
        st.download_button(
            'BM25 결과 CSV 저장',
            data=bm25_csv(actual_query, trace),
            file_name=f'bm25-search-{index + 1}.csv',
            mime='text/csv',
            key=f'bm25_csv_{index}',
            on_click='ignore',
        )


def render_bm25_evaluation(library, auth, documents):
    """등록된 저장 chunk만 사용하는 관리자용 BM25 일괄 기준선 화면입니다."""
    auth.require_admin()
    ready = {doc["id"]: doc for doc in documents if doc.get("status") == "ready"}
    with st.expander("BM25 기준선 평가 · 관리자 전용"):
        st.caption("벡터 검색과 AI를 호출하지 않고 현재 저장 chunk의 BM25 원시 Top-10만 평가합니다.")
        if not ready:
            st.info("검색 가능한 지침서를 먼저 등록하세요.")
            return
        with st.form("bm25_batch_evaluation"):
            selected = st.multiselect(
                "평가할 지침서",
                list(ready),
                default=list(ready),
                format_func=lambda identifier: ready[identifier]["document_name"],
            )
            question_text = st.text_area(
                "BM25 테스트 질문",
                value="\n".join(DEFAULT_QUESTIONS),
                height=160,
                max_chars=5000,
                help="한 줄에 질문 하나를 입력합니다. 최대 50개까지 평가합니다.",
            )
            submitted = st.form_submit_button("BM25 기준선 평가 실행", type="primary")
        if not submitted:
            return
        questions = [line.strip() for line in question_text.splitlines() if line.strip()]
        try:
            with st.spinner("저장된 chunk에서 BM25 Top-10을 계산하고 있습니다…"):
                report = evaluate_bm25(library, questions, selected)
                saved = save_bm25_artifacts(report)
            auth.require_admin()
        except GuideError as exc:
            st.error(str(exc))
            return

        left, middle, right = st.columns(3)
        left.metric("문서", f"{report['document_count']}개")
        middle.metric("저장 chunk", f"{report['chunk_count']}개")
        right.metric("질문", f"{report['question_count']}개")
        st.success("BM25 기준선 결과 파일을 저장했습니다.")
        st.caption("저장 파일: artifacts/bm25_results.csv · artifacts/bm25_results.json")
        st.warning("결과에는 지침 원문이 포함됩니다. 승인된 관리자만 검토하고 외부에 공유하지 마세요.")

        for query in report["queries"]:
            st.markdown(f"#### {query['original_query']}")
            st.caption(
                f"실제 query: {query['actual_query']} · BM25 입력: {query['expanded_query']} · "
                f"양수 점수 {query['positive_result_count']}개 · {query['elapsed_ms']:.3f}ms"
            )
            top_five = [
                {
                    "rank": row["rank"],
                    "bm25_score": round(row["bm25_score"], 6),
                    "document_name": row["document_name"],
                    "page_number": row["page_number"],
                    "section_title": row["section_title"],
                    "chunk_id": row["chunk_id"],
                    "chunk_preview_300": row["chunk_text"][:300],
                }
                for row in query["top10"][:5]
            ]
            st.dataframe(top_five, hide_index=True, width="stretch")
            if not query["positive_result_count"]:
                st.warning("BM25 양수 점수 결과가 없습니다. 표시된 0점 chunk는 검색 성공이 아닙니다.")

        st.download_button(
            "전체 BM25 결과 CSV 저장",
            data=report_csv(report),
            file_name="bm25_results.csv",
            mime="text/csv",
            key="bm25_batch_csv",
            on_click="ignore",
        )
        st.download_button(
            "전체 BM25 결과 JSON 저장",
            data=report_json(report),
            file_name="bm25_results.json",
            mime="application/json",
            key="bm25_batch_json",
            on_click="ignore",
        )
        st.caption(f"서버 저장 확인: {len(saved)}개 파일 · CSV 컬럼 {', '.join(EVALUATION_CSV_COLUMNS)}")


def render_diagnostics(library, auth, settings, model_factory, documents):
    auth.require_admin()
    with st.expander('검색 실패 진단 · 관리자 전용'):
        st.caption('원본 재추출·저장 chunk·저장 벡터와 실제 검색 경로를 검사합니다. 문서를 변경하거나 재색인하지 않습니다.')
        by_id = {d['id']: d for d in documents}
        with st.form('retrieval_diagnostics'):
            selected = st.multiselect('진단할 지침서', list(by_id), default=list(by_id)[:1], max_selections=10,
                                      format_func=lambda i: by_id[i]['document_name'] + ' · ' + by_id[i]['status'])
            query = st.text_input('진단 질문', value='진정간호 목적에 대해 알려줘', max_chars=500)
            probe = st.text_input('인덱스 비교 검색어', value='진정간호 목적', max_chars=500)
            call_llm = st.checkbox('설정된 AI로 최종 답변까지 재시험 (검색된 근거 전송·사용량 차감)')
            submitted = st.form_submit_button('실제 검색 파이프라인 진단', type='primary')
        if not submitted:
            return
        try:
            with st.spinner('저장 원문·인덱스와 검색 단계를 검사하고 있습니다…'):
                report = run_diagnostics(library, settings, model_factory, query, selected,
                                         probe_query=probe, call_llm=call_llm)
            auth.require_admin()
        except GuideError as exc:
            st.error(str(exc))
            return
        st.warning('이 결과에는 병원 지침 원문이 포함됩니다. 승인된 관리자만 보관·공유하세요.')
        for error in report['errors']:
            st.error(error['stage'] + ' · ' + error['code'])
        for doc in report['documents']:
            st.write(doc['document_name'])
            st.json({k: v for k, v in doc.items() if k not in {
                'chunks', 'text_extraction_without_ocr', 'raw_pdf_extraction', 'extraction_with_configured_ocr'}}, expanded=False)
            for key, label in [('raw_pdf_extraction', 'PDF 기본 추출'),
                               ('text_extraction_without_ocr', '일반 텍스트 추출 (OCR 없음)'),
                               ('extraction_with_configured_ocr', 'OCR 설정 적용 추출')]:
                if key in doc:
                    extraction = doc[key]
                    st.write(f"{label} · {extraction['total_characters']:,}자 · {extraction['unit_count']}페이지/영역")
                    st.json(extraction['keywords'])
                    st.dataframe(extraction['pages'], hide_index=True, width='stretch')
            st.write(f"실제 저장 chunk {doc.get('actual_chunk_count', '미확인')}개")
            if doc.get('chunks'):
                st.dataframe(doc['chunks'], hide_index=True, width='stretch')
        for label, trace in report['searches'].items():
            st.write('질문 검색' if label == 'question' else '인덱스 비교 검색')
            actual_query = trace.get('actual_query') or query
            st.markdown('**실제 검색 query**')
            st.code(actual_query, language=None)
            st.json({k: trace.get(k) for k in ('plan', 'query_embedding', 'allowed_document_ids', 'bm25_index_size',
                                             'thresholds', 'reason', 'evidence_assessment')})
            for key, title in [('bm25_top10', 'BM25 top 10'), ('vector_top10', '실제 vector 검색 top 10'),
                               ('fused_top10', 'RRF 합산 top 10'), ('reranked_top5', '재정렬 top 5'),
                               ('rerank_decisions', '후보별 선택·제외 사유'),
                               ('stored_vector_cosine_top10', '실제 저장 벡터 전수 비교 top 10')]:
                st.write(title)
                if trace.get(key):
                    if key == 'bm25_top10':
                        rows = bm25_debug_rows(actual_query, trace)
                        previews = [{**{k: v for k, v in row.items() if k != 'chunk_text'},
                                     'chunk_preview_300': row['chunk_text'][:300]} for row in rows]
                        st.dataframe(previews, hide_index=True, width='stretch')
                        st.download_button(
                            'BM25 결과 CSV 저장',
                            data=bm25_csv(actual_query, trace),
                            file_name=f'bm25-{label}.csv',
                            mime='text/csv',
                            key=f'diagnostic_bm25_csv_{label}',
                            on_click='ignore',
                        )
                    else:
                        st.dataframe(trace[key], hide_index=True, width='stretch')
                else:
                    st.caption('결과 없음 또는 해당 단계 미실행 · 오류/제외 사유를 확인하세요.')
            if trace.get('vector_score_note'):
                st.caption(trace['vector_score_note'])
        st.write('답변 생성·차단 사유')
        st.json(report.get('generation', {}))
        if report['final_answer'] is not None:
            st.text(report['final_answer'])
            st.json(report.get('cited_sources', []), expanded=False)
        else:
            st.caption('최종 답변 미검증: AI 재시험 미선택 또는 해당 단계 오류입니다.')
        st.download_button('전체 진단 JSON 다운로드', json.dumps(report, ensure_ascii=False, indent=2),
                           file_name='retrieval-diagnostics.json', mime='application/json', on_click='ignore')
