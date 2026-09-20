# Retrieval 3-way case별 우열 분석

종합 우열은 Hit@10 → MRR → Recall@10 → Precision@10 순의 사전식 비교다.
질문 및 병원 원문은 저장하지 않는다.

| Case ID | Hit@10 | MRR | Recall@10 | Precision@10 | 종합 | 완전 동률 |
|---|---|---|---|---|---|---|
| UAT-S01 | bm25_only, chromadb_only, current_hybrid | bm25_only, current_hybrid | bm25_only, chromadb_only, current_hybrid | current_hybrid | current_hybrid | no |
| UAT-S02 | bm25_only, chromadb_only, current_hybrid | bm25_only, current_hybrid | current_hybrid | current_hybrid | current_hybrid | no |
| UAT-S03 | current_hybrid | current_hybrid | current_hybrid | current_hybrid | current_hybrid | no |
| UAT-S04 | bm25_only, chromadb_only, current_hybrid | bm25_only, current_hybrid | current_hybrid | current_hybrid | current_hybrid | no |
| UAT-S05 | bm25_only, current_hybrid | current_hybrid | current_hybrid | current_hybrid | current_hybrid | no |
| UAT-S06 | bm25_only, current_hybrid | bm25_only, current_hybrid | bm25_only, current_hybrid | current_hybrid | current_hybrid | no |
| UAT-S08 | bm25_only, current_hybrid | bm25_only, current_hybrid | current_hybrid | current_hybrid | current_hybrid | no |
| UAT-S09 | bm25_only, current_hybrid | current_hybrid | current_hybrid | current_hybrid | current_hybrid | no |
| UAT-S11 | bm25_only, current_hybrid | bm25_only, current_hybrid | current_hybrid | current_hybrid | current_hybrid | no |
| UAT-S12 | bm25_only, chromadb_only, current_hybrid | current_hybrid | bm25_only | bm25_only | current_hybrid | no |
| UAT-S13 | bm25_only, chromadb_only, current_hybrid | current_hybrid | current_hybrid | current_hybrid | current_hybrid | no |
| UAT-S14 | bm25_only, current_hybrid | current_hybrid | current_hybrid | current_hybrid | current_hybrid | no |
| UAT-T01 | bm25_only, current_hybrid | bm25_only | current_hybrid | current_hybrid | bm25_only | no |
| UAT-T02 | bm25_only, current_hybrid | bm25_only | current_hybrid | current_hybrid | bm25_only | no |
| UAT-T03 | bm25_only, current_hybrid | bm25_only, current_hybrid | current_hybrid | current_hybrid | current_hybrid | no |
| UAT-T11 | bm25_only, chromadb_only, current_hybrid | bm25_only, chromadb_only, current_hybrid | bm25_only, chromadb_only, current_hybrid | bm25_only, chromadb_only, current_hybrid | bm25_only, chromadb_only, current_hybrid | yes |
| UAT-T12 | bm25_only, current_hybrid | current_hybrid | current_hybrid | current_hybrid | current_hybrid | no |
| UAT-T13 | bm25_only, current_hybrid | bm25_only, current_hybrid | bm25_only, current_hybrid | current_hybrid | current_hybrid | no |
| UAT-T15 | bm25_only, chromadb_only, current_hybrid | chromadb_only, current_hybrid | current_hybrid | current_hybrid | current_hybrid | no |
| UAT-T16 | bm25_only, chromadb_only, current_hybrid | bm25_only | current_hybrid | current_hybrid | bm25_only | no |
| UAT-T17 | bm25_only, current_hybrid | current_hybrid | bm25_only, current_hybrid | bm25_only, current_hybrid | current_hybrid | no |
