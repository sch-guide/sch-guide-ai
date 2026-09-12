"""관리자 전용, 클릭할 때만 실행하는 실제 검색 진단 화면."""

import json

import streamlit as st

from mvp.diagnostics import run_diagnostics
from mvp.settings import GuideError


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
            st.json({k: trace.get(k) for k in ('plan', 'query_embedding', 'allowed_document_ids', 'bm25_index_size',
                                             'thresholds', 'reason', 'evidence_assessment')})
            for key, title in [('bm25_top10', 'BM25 top 10'), ('vector_top10', '실제 vector 검색 top 10'),
                               ('fused_top10', 'RRF 합산 top 10'), ('reranked_top5', '재정렬 top 5'),
                               ('rerank_decisions', '후보별 선택·제외 사유'),
                               ('stored_vector_cosine_top10', '실제 저장 벡터 전수 비교 top 10')]:
                st.write(title)
                if trace.get(key):
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
