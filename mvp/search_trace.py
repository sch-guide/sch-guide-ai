"""검색 실행 경로의 선택적 진단. 저장/전송하지 않으며 호출자가 관리자 권한을 검사합니다."""

from dataclasses import asdict

import numpy as np


def chunk_row(chunk):
    return dict(chunk_id=chunk.id, document_id=chunk.document_id, document_name=chunk.document_name,
                page_number=chunk.page, section_title=chunk.section, location=chunk.location,
                index=chunk.index, parent_id=chunk.parent_id, characters=len(chunk.text), sample=chunk.text[:200])


def hit_row(hit):
    return dict(chunk_row(hit.chunk), similarity=hit.similarity, bm25_score=hit.bm25_score,
                fusion_score=hit.fusion_score, rerank_score=hit.rerank_score,
                context_only=hit.context_only, context_complete=hit.context_complete)


def begin_trace(trace, plan, doc_ids, minimum):
    if trace is not None:
        trace.update(actual_query=plan.query, plan=asdict(plan), requested_document_ids=sorted(set(doc_ids)),
                     allowed_document_ids=[], indexed_chunk_ids=[], bm25_index_size=0,
                     bm25_top10=[], vector_top10=[], fused_top10=[], reranked_top5=[], final_hits=[],
                     requested_temporal_phase=None,
                     rerank_decisions=[], reason='not_searched',
                     pre_llm_required_group_keys=[], pre_llm_optional_group_keys=[],
                     pre_llm_procedure_coverage=None, prompt_selected_group_keys=[],
                     prompt_excluded_groups=[], prompt_schema_version=None, prompt_group_tokens=[],
                     estimated_request_tokens=None, request_token_budget=None,
                     request_token_headroom=None, post_budget_required_group_keys=[],
                     post_budget_optional_group_keys=[], post_budget_procedure_coverage=None,
                     thresholds=dict(minimum_score=minimum, unsupported_dense_minimum=max(minimum, .55),
                                     lexical_supported_bypasses_similarity=True, rrf_k=60,
                                     note='BM25/RRF/재정렬 점수는 서로 다른 척도이며 정답 확률이 아닙니다.'))


def candidate_trace(trace, chunks, bm25_scores, dense_hits, candidates, bm25_ranking=None):
    if trace is None:
        return
    positions = (bm25_ranking.positions if bm25_ranking is not None
                 else tuple(int(i) for i in np.argsort(-bm25_scores, kind='stable')))
    tiers = (bm25_ranking.tiers if bm25_ranking is not None
             else tuple('inactive' for _ in positions))
    trace.update(indexed_chunk_ids=[c.id for c in chunks], bm25_index_size=len(chunks),
                 requested_temporal_phase=(bm25_ranking.requested_phase
                                           if bm25_ranking is not None else None),
                 bm25_top10=[dict(chunk_row(chunks[position]), score=float(bm25_scores[position]),
                                    temporal_tier=tier, chunk_text=chunks[position].text)
                              for position, tier in zip(positions[:10], tiers[:10])],
                 vector_top10=[dict(hit_row(h), score=h.similarity) for h in dense_hits[:10]],
                 fused_top10=[hit_row(h) for h in sorted(candidates, key=lambda h: -h.fusion_score)[:10]])


def finish_trace(trace, seeds, hits):
    if trace is not None:
        trace.update(reranked_top5=[hit_row(h) for h in seeds[:5]], final_hits=[hit_row(h) for h in hits],
                     reason='retrieved' if hits else 'no_candidates_after_rerank')
