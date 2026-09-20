# SCHAT 검색 구조

## Production 결정

Production retrieval은 계속 **BM25 + MiniLM semantic/vector + RRF + 기존 reranker**다. 이번 최종 검증에서 production 검색 코드를 변경하지 않았다.

## 평가로 확인된 사실

Approved Positive Gold 21건을 같은 corpus fingerprint와 Top-k 1·3·5·10으로 평가한 최신 비교는 다음과 같다.

| 전략 | Hit@10 | MRR | Recall@10 | Precision@10 | 평균 latency | 상태 |
|---|---:|---:|---:|---:|---:|---|
| Pure BM25 | 0.9048 | 0.5822 | 0.4523 | 0.2571 | 0.433ms | Production 구성요소 |
| ChromaDB + MiniLM 384d | 0.3333 | 0.1093 | 0.1043 | 0.0476 | 별도 평가 기록 | Evaluation-only 참고 |
| ChromaDB + Gemini Embedding 2 3072d | 0.9524 | 0.4337 | 0.5593 | 0.3095 | 574.784ms | Evaluation-only |
| Current Hybrid | **0.9524** | **0.7370** | **0.8210** | **0.5365** | 124.642ms | Production 현재 경로 |

Gemini Embedding 2를 사용하자 ChromaDB Hit@10은 0.3333에서 0.9524로 개선됐다. 따라서 최초 저점은 ChromaDB 자체보다 MiniLM 임베딩과 현재 데이터의 조합 영향이 컸다. 다만 Gemini ChromaDB는 Current Hybrid보다 MRR·Recall·Precision이 낮고 외부 API 지연·비용이 있으므로 Production을 자동 교체하지 않았다.

E5는 기존 별도 평가에서 정확도가 높았지만 CPU query latency와 메모리 비용이 크고, 기존 reranker가 E5 후보 일부를 떨어뜨렸다. E5 역시 evaluation-only다.

## Document-context admission

- 수정 전 UAT는 28/36이었고 domain mismatch 5건, no-topic 3건이었다.
- 명시적 단일 문서의 canonical topic을 query expansion 보조 신호로 추가했다.
- Unknown domain admission은 검색된 substantive evidence가 그 한 문서에만 속할 때만 허용한다.
- 원 topic 평가를 먼저 유지하며, `no_topic_evidence`인 구조화 임상 intent만 context topic으로 재평가한다.
- Out-of-scope와 clarification은 context admission을 우회한다.
- 수정 후 UAT 36/36, out-of-scope 4/4 zero-call이다.

BM25/MiniLM/RRF/reranker, threshold와 evidence validator는 변경하지 않았다. 동일 수혈 90문항의
IR/RAGAS ID 점수는 수정 전후 완전히 같다.

## 현재 Retrieval/RAGAS 기준선

| 지표 | 값 |
|---|---:|
| Hit@1 / 3 / 5 / 10 | 0.3974 / 0.6538 / 0.7949 / 0.8846 |
| MRR | 0.5602 |
| Recall@10 | 0.8379 |
| Precision@10 | 0.1192 |
| ID Context Precision / Recall | 0.1192 / 0.8379 |

## Vector storage

Production은 기존 catalog vector를 사용한다. E5 snapshot과 Chroma/Qdrant는 production dependency가 아니다.

Gemini ChromaDB 평가는 다음 경계를 유지했다.

- collection: `schat_chromadb_gemini_embedding_3072_eval_v1`
- cosine distance
- Chroma 기본 embedding function 미사용
- Gemini가 만든 L2-normalized 3072차원 vector를 직접 저장
- BM25, RRF, reranker, context expansion, Evidence gate, SourceUnit, LLM, validator 호출 0
- 실제 embedding API 요청 168회, retry 0
- Production·Gold 변경 0
