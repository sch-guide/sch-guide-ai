# SCHAT Final MVP Phase 01 — Retrieval/Reranker

- 완료일: 2026-09-18
- 기준 복구점: `v0.8` (`a1ce3207f4fd786bb6e40a179f9f6930485df6fb`)
- 외부 API 호출: 0회
- Production retrieval 변경: 0건

## 완료 항목

- Frozen 수혈 90문항/105 chunks 결과로 current reranker 병목을 재현했다.
- Gold와 question ID를 ranking 입력으로 받지 않는 평가 계약을 테스트로 고정했다.
- no reranker, normalized input, candidate-aware, rank-preserving, top-N, origin-feature 변형을 동일 Top-10에서 비교했다.
- 모든 안전 변형은 raw RRF Top-10 ID 집합을 보존하고 자동 보충·삭제를 하지 않는다.

## 핵심 결과

| 전략 | Hit@5 | Hit@10 | MRR | Recall@10 | Core | Mean ms |
|---|---:|---:|---:|---:|---:|---:|
| BM25 current | 0.7949 | 0.8846 | 0.5602 | 0.8379 | 0.7694 | 1.010 |
| BM25+E5 raw RRF | **0.9231** | **0.9487** | 0.6769 | **0.9359** | **0.8712** | 194.651 |
| Existing reranker | 0.8333 | 0.9103 | 0.5184 | 0.8681 | 0.7825 | 265.158 |
| Candidate-aware bounded rerank | 0.9103 | 0.9487 | **0.6836** | 0.9359 | 0.8696 | 194.669 |
| Top-N rerank | 0.9231 | 0.9487 | 0.6626 | 0.9359 | 0.8676 | 194.685 |

Existing reranker는 raw RRF Top-10 밖의 후보를 다시 선택하면서 Hit@10/Recall@10까지 낮춘다. 즉 병목은 E5 후보 자체가 아니라, lexical/coverage 중심의 기존 reranker가 candidate origin과 raw fusion 순위를 충분히 보존하지 않는 데 있다. Candidate-aware 변형은 raw recall을 보존했지만 always-on E5의 약 195 ms 지연과 약 1.54 GB RSS 비용은 그대로다.

## 최종 판정

최고 evaluation 정확도는 raw RRF이며, 가장 가까운 안전 변형은 candidate-aware rerank다. 그러나 둘 다 E5 runtime 비용을 요구하고, raw RRF를 production에 넣으려면 기존 안전 reranker를 우회해야 한다. 따라서 이번 단계의 최종 선택은 **기존 production BM25 + MiniLM semantic/vector + RRF + reranker 유지**다.

E5를 production 후보로 채택하지 않았으므로 ChromaDB/Qdrant production dependency를 추가하지 않는다. 기존 raw-free NumPy snapshot 방식이 evaluation 규모에는 충분하다.

## 변경 파일

- `tools/reranker_bottleneck_evaluate.py`
- `tests/test_reranker_bottleneck_evaluation.py`
- `artifacts/2026-09-18_schat-final-mvp-completion/retrieval/`
- 이 progress 문서

## 테스트 결과

- Reranker TDD: `4 passed`
- Frozen evaluation: 90문항, approved positive 78, 외부 호출 0회

## 다음 단계

- MM003 table numeric/time row ranking을 table-only scorer에서 수정하고 5개 table UAT를 재검증한다.

## Pending / 중단 조건

- E5 production 도입: 보류
- Vector DB 비교: E5 미채택으로 불필요
- 중단 조건 발생: 없음
