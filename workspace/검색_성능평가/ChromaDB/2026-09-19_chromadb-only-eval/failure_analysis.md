# ChromaDB-only Retrieval 실패 분석

- 질문 원문과 병원 근거 원문은 저장하지 않았다.
- 아래 분류는 동결된 Gold ID와 ChromaDB Top-10 ID만 비교한 결과다.

| Case ID | 질문 유형 | 근거 유형 | 실패 분류 | Top-10 Gold hit |
|---|---|---|---|---:|
| UAT-S01 | fact_specific | text | gold_below_rank_1 | 1 |
| UAT-S02 | procedure | text | gold_below_rank_1 | 1 |
| UAT-S03 | preparation | text | no_gold_hit_top_10 | 0 |
| UAT-S04 | cautions | text | gold_below_rank_1 | 1 |
| UAT-S05 | fact_specific | text | no_gold_hit_top_10 | 0 |
| UAT-S06 | fact_specific | text | no_gold_hit_top_10 | 0 |
| UAT-S08 | temporal | text | no_gold_hit_top_10 | 0 |
| UAT-S09 | comparison | text | no_gold_hit_top_10 | 0 |
| UAT-S11 | procedure | text | no_gold_hit_top_10 | 0 |
| UAT-S12 | follow_up | text | gold_below_rank_1 | 1 |
| UAT-S13 | procedure | text | gold_below_rank_1 | 1 |
| UAT-S14 | fact_specific | text | no_gold_hit_top_10 | 0 |
| UAT-T01 | preparation | text | no_gold_hit_top_10 | 0 |
| UAT-T02 | procedure | text | no_gold_hit_top_10 | 0 |
| UAT-T03 | cautions | text | no_gold_hit_top_10 | 0 |
| UAT-T11 | table_lookup | table | gold_not_in_chromadb_corpus | 0 |
| UAT-T12 | temporal | text | no_gold_hit_top_10 | 0 |
| UAT-T13 | cautions | text | no_gold_hit_top_10 | 0 |
| UAT-T16 | summary | mixed | gold_partially_outside_chromadb_corpus | 1 |
| UAT-T17 | follow_up | text | no_gold_hit_top_10 | 0 |
