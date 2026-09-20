# SCHAT 최종 Retrieval 비교

기존 frozen 평가 산출물만 비교했으며 검색과 외부 API 호출을 다시 실행하지 않았다.
MiniLM ChromaDB는 주 3방식 비교에서 제외하고 Gemini embedding 개선 참고로만 사용했다.

## 단독 우세

- Pure BM25: 3건
- ChromaDB + Gemini Embedding 2 (3072d): 3건
- Current Hybrid: 14건
- 완전 동률: 1건

## 우선 사람 검수 대상

- gemini_only_success: -
- bm25_over_hybrid: UAT-T01, UAT-T02, UAT-T16
- hybrid_mrr_over_gemini: UAT-S01, UAT-S02, UAT-S03, UAT-S04, UAT-S05, UAT-S08, UAT-S09, UAT-S11, UAT-S14, UAT-T01, UAT-T02, UAT-T03, UAT-T12, UAT-T13, UAT-T16
- all_failed: UAT-T11
- table_related: UAT-T11, UAT-T16
- same_hit_different_recall_or_precision: UAT-S01, UAT-S02, UAT-S04, UAT-S05, UAT-S06, UAT-S08, UAT-S09, UAT-S11, UAT-S12, UAT-S13, UAT-S14, UAT-T01, UAT-T02, UAT-T03, UAT-T12, UAT-T13, UAT-T15, UAT-T16, UAT-T17
- uat_t11: UAT-T11

## 해석 경계

- Hybrid는 전반적인 순위와 다중 Gold 회수에서 강하지만 context expansion을 포함해 운영 복잡도가 높다.
- Gemini Chroma는 MiniLM Chroma보다 크게 개선됐지만 embedding 생성 지연과 외부 provider 비용이 있다.
- 표 기반 UAT-T11 실패는 retriever 교체만으로 해결되지 않으며 corpus의 table 표현을 별도 검토해야 한다.
