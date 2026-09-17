# Integrated Enhancement Phase 01 — BM25/E5 retrieval strategy

- 상태: 완료
- 외부 embedding/generation/vision API 호출: 0회
- Production retrieval 변경: 0건
- Git commit/push: 0회

## 완료 항목

- 동일 수혈 105 chunks, 90문항, approved positive 78, Top-K 1/3/5/10으로 5개 전략을 평가했다.
- E5 passage vector는 원문 없는 ignored local snapshot으로 1회 생성하고 재로드를 검증했다.
- Existing SCHAT `rrf()` k=60과 existing `rerank()`를 재사용했다.
- Selective policy는 gold, question ID, fixture type을 입력으로 읽지 않고 QueryPlan/BM25 confidence/lexical overlap/exact signal만 사용했다.
- Snapshot, model load, query embedding, RRF, reranker, RSS memory를 측정했다.

## 전체 metric

| Config | Hit@5 | Hit@10 | MRR | Recall@10 | Core | Mean ms | P95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| BM25 current | 0.7949 | 0.8846 | 0.5602 | 0.8379 | 0.7694 | 1.010 | 1.628 |
| E5 large | 0.8718 | 0.9359 | 0.7151 | 0.9084 | 0.8578 | 193.474 | 282.720 |
| BM25 + E5 RRF | **0.9231** | **0.9487** | 0.6769 | **0.9359** | **0.8712** | 194.651 | 283.856 |
| RRF + existing reranker | 0.8333 | 0.9103 | 0.5184 | 0.8681 | 0.7825 | 265.158 | 372.321 |
| Selective E5 | 0.8590 | 0.9103 | 0.5997 | 0.8764 | 0.8113 | 87.778 | 270.402 |

## Runtime

- Model load: 4,424.18 ms
- Local model cache: 2,252,997,322 bytes
- RSS model delta: 1,541,464,064 bytes
- Passage build reference: 98,965.40 ms
- Persistent snapshot: 389,672 bytes, reload 11.46 ms
- E5 query embedding: mean 192.98 ms, p95 282.16 ms
- E5 runtime passage re-embedding: 불필요
- Selective E5 activation: 40/90, 44.44%

## 판정

- Raw retrieval 정확도 1위는 `BM25 + E5 RRF`이다.
- 하지만 안전성 계약인 existing reranker까지 적용하면 core는 0.7825로, BM25 대비 +0.0131에 그쳤다.
- Selective policy도 best core보다 0.0598 낮아 production 선택 기준을 못 충족했다.
- 따라서 운영 판정은 **E — existing production hybrid 유지**이다.

## 변경 파일

- `tools/bm25_e5_strategy_evaluate.py`
- `tests/test_bm25_e5_strategy_evaluation.py`
- `docs/superpowers/plans/2026-09-17-bm25-e5-integrated-retrieval-and-multimodal.md`
- `artifacts/2026-09-17_bm25-e5-retrieval-strategy-02/`
- local ignored snapshot: `data/evaluation/e5-transfusion-v3.npz`

## 테스트

- TDD RED: evaluator module 부재로 collection failure
- Unit/smoke/policy/RSS: **6 passed**
- Ruff focused: 통과
- 독립 repeatability: initial/final overall metrics 5/5 일치

## 다음 단계

- Production retrieval을 변경하지 않는 Phase 02 판정을 고정한다.
- 이미 완료된 text/table/routing/web UAT는 재구현 없이 회귀만 검증한다.

## Pending / 중단 조건

- Provider controlled generation: 병원 데이터 외부 전송 승인 대기
- Image/TF027: human review 대기
- 새 중단 조건: 없음
