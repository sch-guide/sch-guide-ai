# Current Hybrid Retrieval 실패 분석

- 질문 원문과 병원 근거 원문은 저장하지 않는다.
- 분류는 승인 Gold ID와 Production Hybrid Top-10 ID만 비교한 결과다.

| Case ID | 질문 유형 | 근거 유형 | 실패 분류 | Top-10 Gold hit |
|---|---|---|---|---:|
| UAT-S05 | fact_specific | text | gold_below_rank_1 | 1 |
| UAT-S06 | fact_specific | text | gold_below_rank_1 | 1 |
| UAT-S09 | comparison | text | gold_below_rank_1 | 1 |
| UAT-S14 | fact_specific | text | gold_below_rank_1 | 1 |
| UAT-T01 | preparation | text | gold_below_rank_1 | 1 |
| UAT-T02 | procedure | text | gold_below_rank_1 | 1 |
| UAT-T11 | table_lookup | table | gold_not_in_hybrid_corpus | 0 |
| UAT-T12 | temporal | text | gold_below_rank_1 | 1 |
| UAT-T16 | summary | mixed | gold_partially_outside_hybrid_corpus | 1 |
