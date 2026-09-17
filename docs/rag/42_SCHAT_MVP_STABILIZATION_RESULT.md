# SCHAT MVP retrieval 확장 평가 및 안정화 결과

- 평가·검증일: 2026-09-17
- 대상 저장소: `sch-guide-ai`, branch `boha-rag`
- 대상 문서: `실무지침서_수혈간호.pdf`, 17 pages, 105 chunks
- 평가 범위: 수혈 retrieval 확장, ChromaDB/BM25/Hybrid 비교, ID 기반 RAGAS, 표·이미지 경계, 기존 UAT 재검증
- 실제 Groq/Gemini generation 호출: 0회
- Production RAG 변경: 0건
- 최종 선택: **standalone baseline은 BM25**, production은 기존 BM25 후보 + RRF/rerank 경로 유지

## 1. 결론

수혈 retrieval 평가셋을 90문항으로 확장하고 동일한 105개 chunk, 동일한 gold ID, 동일한 Top-K와 metric으로 ChromaDB, BM25 및 evaluation-only Hybrid RRF를 비교했다. Positive 79문항 중 사람이 승인한 78문항을 정량 집계에 사용했고, 1문항은 이미지의 순차 의미를 안전하게 확정할 수 없어 human review 상태로 남겼다. Negative 11문항은 positive IR/RAGAS 평균에서 제외하고 별도 score diagnostic으로 측정했다.

BM25가 Hit@10 88.46%, MRR 0.5602, Recall@10 0.8379로 가장 높았으며 평균 검색 지연도 0.37 ms로 가장 낮았다. ChromaDB는 Hit@10 28.21%, MRR 0.0913이었고, Hybrid RRF는 ChromaDB보다 높지만 BM25보다 낮았다. 따라서 최신 기술 여부가 아니라 동일 조건의 실측 결과로 BM25를 standalone winner로 선택했다.

다만 production은 이미 BM25 candidate generation 뒤에 RRF와 reranker를 적용한다. 이번 결과는 standalone retrieval 비교이며 기존 production 경로를 우회할 근거가 아니므로 production 코드는 변경하지 않았다. Validator, Facet-slot, AnswerCoverage, citation 계약과 Q006 zero-call도 그대로 유지된다.

## 2. 평가셋과 gold 검수

| 항목 | 결과 |
|---|---:|
| 전체 문항 | 90 |
| Positive | 79 |
| 승인된 positive 집계 대상 | 78 |
| Human review 유지 | 1 |
| Negative/out-of-scope | 11 |
| 검수 대상 문항 | 14 |
| 자동 승인 | 13 |
| 구조적 표 근거 승인 | 12 |
| 인접 본문 근거 승인 | 1 |

Gold는 retrieval 출력으로 자동 확정하지 않았다. 표 문항은 렌더링한 페이지 구조와 catalog fingerprint를 함께 확인했고, 본문으로 충분한 문항은 인접 substantive text를 확인했다. `TF027`은 workflow screenshot의 단계 순서를 이미지 해석 없이 확정할 수 없어 `needs_human_review=true`를 유지하며 aggregate에서 제외했다.

Fixture에는 질문, chunk ID, parent ID 및 SHA-256 fingerprint만 저장한다. 병원 source chunk 원문, 전체 PDF 본문 또는 모델 raw response는 저장하지 않았다.

## 3. 공정 비교 계약

세 retriever는 다음 조건을 공유한다.

- Document/version: 동일 수혈 PDF와 동일 document hash
- Chunk corpus: production catalog의 동일 105 chunks 및 동일 chunk IDs
- `CHUNK_VERSION=4`
- Dataset: `transfusion-retrieval-v3`
- Top-K: 1, 3, 5, 10
- Gold: 동일 `reference_context_ids`
- Metric: 동일 multi-gold Hit, MRR, Recall, Precision 및 ID 기반 RAGAS
- Negative: positive aggregate에서 제외하고 score distribution만 별도 계산

ChromaDB 설정:

- Evaluation-only ChromaDB 1.5.9
- Cosine space
- `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- 384 dimensions
- Collection count 105
- 외부 embedding/generation API 0회

## 4. 전체 IR 및 RAGAS 결과

| Retriever | Hit@1 | Hit@3 | Hit@5 | Hit@10 | MRR |
|---|---:|---:|---:|---:|---:|
| ChromaDB | 0.0385 | 0.0897 | 0.1410 | 0.2821 | 0.0913 |
| BM25 | **0.3974** | **0.6538** | **0.7949** | **0.8846** | **0.5602** |
| Hybrid RRF | 0.3205 | 0.4872 | 0.6538 | 0.8333 | 0.4599 |

| Retriever | Recall@1 | Recall@3 | Recall@5 | Recall@10 |
|---|---:|---:|---:|---:|
| ChromaDB | 0.0321 | 0.0705 | 0.1282 | 0.2564 |
| BM25 | **0.3141** | **0.5742** | **0.7170** | **0.8379** |
| Hybrid RRF | 0.2500 | 0.4103 | 0.5339 | 0.7555 |

| Retriever | Precision@1 | Precision@3 | Precision@5 | Precision@10 |
|---|---:|---:|---:|---:|
| ChromaDB | 0.0385 | 0.0342 | 0.0359 | 0.0321 |
| BM25 | **0.3974** | **0.2607** | **0.1974** | **0.1192** |
| Hybrid RRF | 0.3205 | 0.1795 | 0.1410 | 0.1038 |

RAGAS retrieval-only ID metric은 같은 ID 집합 정의로 계산했다.

| Retriever | ID Context Precision | ID Context Recall |
|---|---:|---:|
| ChromaDB | 0.0321 | 0.2564 |
| BM25 | **0.1192** | **0.8379** |
| Hybrid RRF | 0.1038 | 0.7555 |

LLM judge가 필요한 Context Precision/Recall과 generation 기반 Faithfulness/Response Relevancy는 외부 API 금지에 따라 실행하지 않았다.

## 5. 질문 유형별 BM25 성능

| Type | N | Hit@5 | Hit@10 | MRR | Recall@10 | Precision@10 | Mean latency ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| adverse_reaction | 8 | 1.0000 | 1.0000 | 0.8750 | 1.0000 | 0.2000 | 0.324 |
| fact_specific | 10 | 0.9000 | 0.9000 | 0.6750 | 0.9000 | 0.1100 | 0.376 |
| monitoring | 2 | 1.0000 | 1.0000 | 0.6000 | 1.0000 | 0.1000 | 0.374 |
| paraphrase | 6 | 0.5000 | 0.8333 | 0.3278 | 0.8333 | 0.0833 | 0.368 |
| preparation | 10 | 0.7000 | 1.0000 | 0.4926 | 0.9500 | 0.1100 | 0.428 |
| procedure | 6 | 0.8333 | 0.8333 | 0.2639 | 0.6429 | 0.1667 | 0.382 |
| product_specific | 18 | 0.7778 | 0.8889 | 0.4500 | 0.8056 | 0.1056 | 0.358 |
| temporal | 18 | 0.7778 | 0.7778 | 0.6759 | 0.7500 | 0.1056 | 0.444 |

BM25 Top-10은 승인 문항 78개 중 69개를 hit했다. Miss 9개는 temporal 4, product-specific 2, fact-specific 1, paraphrase 1, procedure 1이다. 따라서 다음 일반화 우선순위는 temporal 표현, 제품별 용어 변형, 긴 procedure의 multi-gold 회수다.

## 6. 성능과 저장 비용

| Retriever | Mean latency ms | Index build ms | Extra storage bytes |
|---|---:|---:|---:|
| ChromaDB | 12.065 | 1048.626 | 639,140 |
| BM25 | **0.374** | **79.234** | 0 |
| Hybrid RRF | 12.451 | 1127.861 | 639,140 |

Negative Top-1 평균은 BM25 2.8076, positive는 8.6319로 평균 separation 5.8242였다. ChromaDB cosine score separation은 0.2295였다. RRF 점수는 probability가 아니며 negative threshold 결정에 사용하지 않았다.

## 7. 표·이미지 경계 평가

PDF 표를 production chunk와 분리된 evaluation-only sidecar로 분석했다.

| 항목 | 결과 |
|---|---:|
| 감지 표 | 4 |
| Table units | 60 |
| 승인 table 문항 | 22 |
| Table index build | 83.462 ms |
| Figure metadata candidates | 55 |
| Vision descriptions | 0 |

Table subset의 text-only BM25는 Hit@10 0.8636, MRR 0.6742, Recall@10 0.8182였다. Table-aware 방식은 Hit@10 0.8636, MRR 0.6553, Recall@10 0.8409였다. Recall은 소폭 증가했지만 MRR은 감소해 명확한 개선으로 보지 않았고 production에 반영하지 않았다.

Figure candidate는 page/bbox/caption hash와 인접 chunk ID 같은 metadata만 저장했다. 승인된 image-dependent gold가 0건이고 `TF027`이 human review 상태이므로 multimodal retrieval은 `not_evaluated_no_approved_image_dependent_gold`로 종료했다. 이미지에서 임상 의미를 추론하거나 vision API를 호출하지 않았다.

## 8. UAT와 안전 경계

- 기존 진정간호 offline UAT 45/45 통과 결과를 재사용했다.
- Q006 out-of-scope는 transport 0회, `answerable=false`, selected evidence 0을 유지한다.
- 수혈은 generation 없는 retrieval-only UAT로 승인 78개 중 Top-10 hit 69개를 확인했다.
- Production 변경이 없고 병원 데이터의 외부 provider 전송 정책 승인이 없으므로 수혈 live chat UAT는 수행하지 않았다.
- Groq/Gemini generation 호출은 모두 0회다.

## 9. Provider 비교 중단 경계

Live Groq/Gemini 비교는 `blocked_security_policy_confirmation_required`로 중단했다. 현재 저장소에는 Gemini adapter가 없으며 병원 데이터 외부 전송 정책과 사용자 계정/서비스 승인이 필요하다. 사용자 승인 없이 adapter를 추가하거나 병원 근거를 외부 provider에 전송하지 않았다.

다음에 필요한 사용자 조치는 하나다: **병원 데이터의 외부 전송 정책을 확인하고 Gemini 서비스/account 사용을 명시적으로 승인하는 것**이다.

## 10. 회귀·정적 검사·코드 리뷰

| 검증 | 결과 |
|---|---|
| 전체 pytest | **474 passed, 3 skipped, 6 warnings** |
| 최종 수정 관련 집중 테스트 | 10 passed |
| Ruff (`mvp`, `tests`, `tools`) | 통과 |
| `git diff --check` | 통과 |
| Artifact 보안 감사 | 26 files, exact source match 0, forbidden text field 0 |
| Production module/dependency diff | 없음 |

Warning 6건은 기존 FastEmbed MiniLM pooling 기본값 안내다. Embedding model과 dependency를 변경하지 않았다.

Code review 결과 추가 조치가 필요한 correctness, safety 또는 regression 결함은 발견되지 않았다. Retrieval result JSON에서 평가 질문을 제거했고, 최종 artifact에는 source chunk 원문·API key·Authorization·raw provider response가 없다. 남은 위험은 BM25 Top-10 miss 9개, `TF027`의 사람 검수, provider 비교의 정책 승인 대기다.

## 11. 변경 및 비변경 범위

추가·갱신:

- `tests/fixtures/transfusion_retrieval_baseline.json`: v3, 90문항과 review audit
- `tests/fixtures/transfusion_multimodal_retrieval.json`: ID 기반 표/이미지 평가 계약
- `tools/chroma_baseline_evaluate.py`
- `tools/retrieval_baseline_metrics.py`
- `tools/retrieval_strategy_evaluate.py`
- `tools/schat_mvp_stabilize.py`
- `tools/multimodal_retrieval_evaluate.py`
- `tools/schat_mvp_stabilization_report.py`
- 관련 evaluation/test 파일과 raw-free artifacts

변경하지 않음:

- Production retrieval/BM25/RRF/reranker
- Embedding model과 production dependencies
- Facet-slot, SourceUnit, AnswerCoverage와 selection limit
- Citation 및 모든 안전 validator
- Generation provider와 prompt

## 12. 산출물과 최종 판정

확장 retrieval:

`artifacts/2026-09-17_transfusion-expanded-retrieval/`

- BM25/ChromaDB/Hybrid row 결과와 summary
- 질문 유형별 metrics
- ID 기반 RAGAS
- comparison schema, environment, test results, review HTML

MVP 안정화:

`artifacts/2026-09-17_schat-mvp-stabilization/`

- Human review audit
- Table/figure manifests
- Multimodal boundary 결과
- Retrieval selection
- UAT/provider gate
- 최종 summary, test results, review HTML

최종 판정은 **offline retrieval 비교와 안전 안정화 완료, production 변경 불필요, live provider 비교는 외부 전송 정책 승인 대기**다. 다음 개발 우선순위는 BM25 miss의 temporal/product/procedure 일반화와 `TF027` 사람 검수이며, 그 뒤 승인된 범위에서만 provider 비교를 진행한다.
