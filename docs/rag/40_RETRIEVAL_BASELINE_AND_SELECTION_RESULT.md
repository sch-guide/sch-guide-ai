# 수혈 Retrieval Baseline 비교 및 검색전략 선정 결과

- 평가일: 2026-09-16
- 대상 브랜치: `boha-rag`
- 대상 문서: `실무지침서_수혈간호.pdf`
- 데이터셋: `transfusion-retrieval-v2`
- 평가 범위: ChromaDB dense, 기존 BM25, evaluation-only RRF Hybrid
- 실제 Groq/Gemini generation 호출: 0회
- Production RAG 수정: 0건
- 최종 선정: **BM25 baseline**

## 1. 결론

동일한 수혈 문서 105 chunks, 동일 chunk ID, 동일 50문항, 동일 gold, 동일 Top-K와 동일 metric 계약으로 ChromaDB와 BM25를 비교했다. 승인된 본문 gold 31문항의 aggregate에서 BM25는 Hit@5 `0.7742`, Hit@10 `0.8710`, MRR `0.5721`, Recall@10 `0.8295`를 기록했다. ChromaDB는 각각 `0.1613`, `0.3871`, `0.1301`, `0.3710`이었다.

Chroma와 BM25가 서로 다른 문항에서 이긴 사실을 확인해 같은 결과 목록을 RRF(k=60)로 합친 Hybrid도 evaluation-only로 평가했다. Hybrid는 paraphrase Hit@5를 높였지만 전체 Hit@5 `0.7097`, Hit@10 `0.8065`, MRR `0.4974`, Recall@10 `0.7512`로 BM25보다 낮았다. 평균 검색 지연도 BM25 `0.83 ms`, Chroma `15.09 ms`, Hybrid `15.93 ms`였다.

따라서 현재 수혈 gold와 chunking에서는 BM25가 가장 적합한 단일 baseline이다. “벡터 검색이 최신 기술”이라는 이유가 아니라 실제 IR/RAGAS ID 성능, 의료 용어 검색, 지연과 구현 복잡도를 함께 적용한 결과다. 이번 범위에서는 production BM25/RRF 변경이 금지되어 있으며, 사람 검토 대기 gold 14건도 있으므로 production retrieval은 수정하지 않았다.

## 2. 공정 비교 계약

다음 조건을 세 엔진에 동일하게 적용했다.

| 항목 | 고정값 |
|---|---|
| 문서 | `실무지침서_수혈간호.pdf` |
| Document ID | `bc3d0de4-6560-4560-b78f-645e82cddd94` |
| Document version | SHA-256 `5fc6a9a0...cb659b` |
| Chunk count | 105 |
| CHUNK_VERSION | 4 |
| 질문 | 같은 원문 질문 50개 |
| Top-K | 1, 3, 5, 10 |
| Gold | 동일 `reference_context_ids` |
| Aggregate | `expected_answerable=true`, `needs_human_review=false`만 포함 |
| Negative | IR/RAGAS 평균에서 제외, score diagnostic으로만 기록 |

공통 ranked row에는 `dataset_version`, `document_version`, `chunk_version`, retriever, question ID/type, expected answerability, review flag, k/rank, retrieved chunk ID, score, gold 여부와 latency가 들어간다. BM25 담당자가 동일 계약을 사용할 수 있도록 `bm25_comparison_template.json`에 metric 공식과 row schema를 고정했다.

Retriever별로 다른 것은 scoring뿐이다.

- ChromaDB: 기존 catalog의 MiniLM 384d vector에 대한 cosine similarity
- BM25: 저장소의 기존 `BM25Index` lexical score
- Hybrid: 두 Top-10 순위의 RRF, `1 / (60 + rank)` 합

RRF, reranker, query expansion, Facet-slot과 generation은 BM25/Chroma 단독 baseline에 사용하지 않았다.

## 3. 평가셋과 gold 검증

평가셋은 총 50문항이다.

| 구분 | 문항 수 | Aggregate 포함 |
|---|---:|---:|
| Positive | 45 | 조건부 |
| 승인된 substantive-body gold | 31 | 예 |
| Human review 필요 | 14 | 아니오 |
| Negative/out-of-scope | 5 | 아니오 |

Human review 14건은 `table_layout` 12건, `image_layout` 2건이다. 검색 결과를 gold로 자동 확정하지 않았고, chunk ID·page·parent ID·substantive-body 판정과 exact text SHA-256을 현재 catalog와 대조했다. 승인 gold는 모두 실제 등록 chunk이며 heading-only gold가 아니다. 표나 이미지 배치 해석이 필요한 문항은 per-case 결과에는 남기되 aggregate에서 제외했다.

질문 유형 구성:

| 유형 | 전체 | 승인 aggregate | Human review | Negative |
|---|---:|---:|---:|---:|
| adverse_reaction | 5 | 0 | 5 | 0 |
| fact_specific | 6 | 6 | 0 | 0 |
| monitoring | 1 | 1 | 0 | 0 |
| paraphrase | 5 | 4 | 1 | 0 |
| preparation | 5 | 5 | 0 | 0 |
| procedure | 5 | 4 | 1 | 0 |
| product_specific | 9 | 7 | 2 | 0 |
| temporal | 9 | 4 | 5 | 0 |
| negative_out_of_scope | 5 | 0 | 0 | 5 |

## 4. ChromaDB 설정

| 항목 | 값 |
|---|---|
| ChromaDB | 1.5.9, evaluation-only |
| Collection | `transfusion_baseline` |
| Distance | cosine |
| Embedding model | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` |
| Dimension | 384 |
| Stored IDs | production chunk ID 105개 |
| Stored source documents | 없음, 105/105 `None` 확인 |
| 기존 positive 결과 재사용 | 45문항 |
| 이번 신규 local query | negative 5문항 |
| Index build time | `1048.63 ms` |
| Index storage | `639,140 bytes` |

기존 완료 Chroma positive run은 질문·review flag·gold ID·Top-10 ID/거리의 identity가 정확히 일치할 때만 재사용했다. 외부 embedding API를 쓰지 않았으며 생성 API 호출도 없었다.

## 5. 전체 IR metric

아래 값은 승인 gold 31문항의 macro average다.

| Metric | ChromaDB | BM25 | Hybrid RRF |
|---|---:|---:|---:|
| Hit@1 | 0.0645 | **0.4194** | 0.3548 |
| Hit@3 | 0.0968 | **0.6774** | 0.5484 |
| Hit@5 | 0.1613 | **0.7742** | 0.7097 |
| Hit@10 | 0.3871 | **0.8710** | 0.8065 |
| MRR | 0.1301 | **0.5721** | 0.4974 |
| Recall@1 | 0.0645 | **0.3710** | 0.3226 |
| Recall@3 | 0.0806 | **0.6060** | 0.5323 |
| Recall@5 | 0.1452 | **0.7189** | 0.6336 |
| Recall@10 | 0.3710 | **0.8295** | 0.7512 |
| Precision@1 | 0.0645 | **0.4194** | 0.3548 |
| Precision@3 | 0.0323 | **0.2581** | 0.2043 |
| Precision@5 | 0.0387 | **0.1871** | 0.1548 |
| Precision@10 | 0.0419 | **0.1161** | 0.0968 |

Multi-gold 문항은 Top-K에 gold 하나 이상이 있으면 Hit=1이다. Recall은 Top-K에서 회수한 unique gold 수를 전체 unique gold 수로 나누고, Precision은 Top-K에서 회수한 gold 수를 실제 반환 수로 나눈다. MRR은 최초 gold rank의 역수다.

## 6. RAGAS ID-based metric

RAGAS 0.4.3의 비-LLM `IDBasedContextPrecision`과 `IDBasedContextRecall`을 승인 문항×엔진 93쌍에 실행하고 동일한 set-overlap 계산과 일치함을 확인했다.

| Retriever | ID Context Precision@10 | ID Context Recall@10 |
|---|---:|---:|
| ChromaDB | 0.0419 | 0.3710 |
| BM25 | **0.1161** | **0.8295** |
| Hybrid RRF | 0.0968 | 0.7512 |

LLM judge가 필요한 Context Precision/Recall, Faithfulness와 Response Relevancy는 보류했다. Groq/Gemini 호출은 0회다.

## 7. 질문 유형별 성능

Hit@5 / Hit@10 / MRR / Recall@10 순서다. 승인 gold가 0개인 adverse reaction은 표·이미지 사람 검토 전까지 정량 비교하지 않는다.

| 유형(N) | ChromaDB | BM25 | Hybrid RRF | 판정 |
|---|---|---|---|---|
| fact_specific (6) | .167 / .333 / .065 / .333 | **.833 / .833 / .750 / .833** | .833 / .833 / .625 / .833 | BM25 |
| monitoring (1) | .000 / 1.000 / .167 / 1.000 | **1.000 / 1.000 / 1.000 / 1.000** | 1.000 / 1.000 / 1.000 / 1.000 | BM25 동률·저지연 |
| paraphrase (4) | .000 / .500 / .056 / .500 | .500 / .750 / .325 / .750 | **.750 / .750 / .425 / .750** | Hybrid Top-5 강점 |
| preparation (5) | .200 / .200 / .200 / .200 | **.600 / 1.000 / .520 / .900** | .400 / .600 / .333 / .600 | BM25 |
| procedure (4) | .000 / .000 / .000 / .000 | **.750 / .750 / .271 / .679** | .250 / .750 / .117 / .446 | BM25 |
| product_specific (7) | .286 / .714 / .250 / .643 | 1.000 / 1.000 / .607 / .929 | **1.000 / 1.000 / .619 / .929** | 거의 동률, BM25 저지연 |
| temporal (4) | .250 / .250 / .125 / .250 | **.750 / .750 / .750 / .750** | .750 / .750 / .625 / .750 | BM25 |

BM25는 exact medical term과 사실·제품·절차 질의에서 특히 강했다. Hybrid는 paraphrase Top-5와 일부 product rank를 개선했지만 preparation/procedure 두 문항에서 BM25가 Top-10으로 찾은 gold를 잃었고, 전체 metric도 낮았다. Chroma가 BM25보다 Hit@10이 높은 승인 문항은 1개, BM25가 높은 문항은 16개였다.

## 8. Negative diagnostic

Negative 5문항은 어떤 retriever도 항상 Top-10을 반환하므로 Hit/Recall 평균에 넣지 않았다. 이 결과는 abstention threshold를 정하는 근거가 아니라 score 분포 관찰값이다.

| Retriever | 승인 positive Top-1 평균 | Negative Top-1 평균 | 평균 차이 | Negative 반환 수 |
|---|---:|---:|---:|---:|
| ChromaDB cosine similarity | 0.7554 | 0.4462 | 0.3093 | 10 |
| BM25 score | 9.3294 | 2.5283 | 6.8011 | 10 |
| Hybrid RRF score | 0.0261 | 0.0290 | -0.0029 | 10 |

서로 다른 score scale을 retriever 간 직접 비교하지 않았다. Hybrid RRF score는 negative와 분리 신호가 없으므로 점수 하나로 abstain하는 용도로 사용하면 안 된다. 기존 SCHAT의 domain/evidence gate와 validator는 그대로 유지해야 한다.

## 9. Latency와 자원

| 항목 | ChromaDB | BM25 | Hybrid RRF |
|---|---:|---:|---:|
| Mean latency | 15.09 ms | **0.83 ms** | 15.93 ms |
| p50 | 11.57 ms | **0.78 ms** | 12.54 ms |
| p95 | 37.90 ms | **1.32 ms** | 38.77 ms |
| Maximum | 81.42 ms | **2.54 ms** | 82.53 ms |
| Index build | 1048.63 ms | **134.76 ms** | 1183.39 ms |
| Additional persistent storage | 639,140 bytes | **0 bytes** | 639,140 bytes |

Chroma latency는 local query embedding과 vector query를 포함한다. BM25 latency는 lexical scoring과 rank 계산을 포함한다. Hybrid는 두 retrieval latency와 RRF fusion을 합산했다.

## 10. 최종 전략 선택과 production 반영

선정 기준은 Hit@5, Hit@10, MRR와 Recall@10의 평균이며 동률이면 구현 복잡도가 낮은 방식을 우선한다.

| Retriever | 핵심 4지표 평균 |
|---|---:|
| ChromaDB | 0.2624 |
| BM25 | **0.7617** |
| Hybrid RRF | 0.6912 |

**최종 retrieval baseline 선택: BM25.**

Production 반영은 0건이다. 현재 작업은 production BM25/RRF 변경을 금지했고, 수혈 평가셋의 14문항은 아직 사람 검토가 필요하다. 또한 retrieval-only 결과만으로 기존 진정간호 end-to-end 검색 구성을 pure BM25로 교체하는 것은 범위를 벗어난다. 이번 결과는 현재 production의 BM25 lexical 축을 유지할 강한 근거이며, Chroma-only 전환이나 단순 RRF 추가의 근거는 아니다.

## 11. 안전성과 비변경 확인

- Production RAG, BM25, RRF, reranker, Facet-slot, AnswerCoverage 및 validator 변경 없음
- CHUNK_VERSION, embedding model과 catalog 변경 없음
- Groq/Gemini generation API 0회
- LLM judge 0회
- API key와 Authorization 미기록
- 병원 원문 전체 및 exact SourceUnit text artifact 저장 없음
- Chroma collection의 document text 105/105 미저장 확인
- Human-review gold를 승인 aggregate에 섞지 않음

## 12. 테스트와 정적 검증

- Evaluation 환경 집중 테스트: **23 passed**
- Production 환경 집중 테스트: **21 passed, 2 skipped**
- 전체 pytest: **463 passed, 3 skipped, 6 warnings**
- Ruff (`mvp`, `tests`, `tools`): 통과
- `git diff --check`: 통과
- Production module 및 requirements diff: 0건

Production 환경의 집중 테스트 skip 2건은 ChromaDB와 RAGAS를 production dependency로 설치하지 않았기 때문이다. 동일 테스트는 격리 evaluation 환경에서 통과했다. Warning 6건은 기존 FastEmbed multilingual MiniLM pooling 기본값 안내이며 embedding model이나 catalog vector는 변경하지 않았다.

## 13. 산출물

코드와 fixture:

- `tests/fixtures/transfusion_retrieval_baseline.json`
- `tests/test_transfusion_chroma_baseline.py`
- `tests/test_retrieval_strategy_evaluation.py`
- `tools/chroma_baseline_evaluate.py`
- `tools/retrieval_baseline_metrics.py`
- `tools/retrieval_strategy_evaluate.py`

최종 artifact:

`artifacts/2026-09-16_transfusion-retrieval-baseline/`

- `dataset_manifest.json`
- `chroma_results.csv`, `chroma_results.json`, `chroma_summary.json`
- `bm25_results.csv`, `bm25_results.json`
- `hybrid_rrf_results.csv`, `hybrid_rrf_results.json`
- `metrics_by_question_type.csv`
- `ragas_results.json`
- `bm25_comparison_template.json`
- `comparison_summary.json`
- `environment.json`
- `test_results.json`
- `review.html`

## 14. 남은 제한점

1. 표·이미지 의존 gold 14문항은 사람이 원문 레이아웃을 확인한 뒤 별도 확장 aggregate에 포함해야 한다.
2. 승인 paraphrase는 4문항으로 작다. Hybrid의 paraphrase Top-5 강점은 더 큰 표현 변형 세트에서 재확인해야 한다.
3. Dense baseline은 현재 catalog의 기존 vector를 그대로 사용했으며 pooling 경고를 해결하기 위해 model/vector를 재생성하지 않았다. 이는 공정 비교와 기존 catalog 불변성을 위한 선택이다.
4. Retrieval-only 승자는 BM25이지만, 실제 SCHAT의 최종 변경 판단에는 진정·수혈 문서를 함께 사용한 end-to-end evidence/abstention/citation 회귀가 추가로 필요하다.
