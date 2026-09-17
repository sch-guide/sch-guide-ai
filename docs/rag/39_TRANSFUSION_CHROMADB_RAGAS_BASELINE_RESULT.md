# 수혈 ChromaDB + RAGAS Retrieval Baseline 결과

- 평가일: 2026-09-16
- 대상 저장소: `C:\Users\박보하\Documents\Codex\sch-guide-ai`
- 대상 문서: `실무지침서_수혈간호.pdf`
- 평가 범위: production catalog의 수혈 105 chunks를 재사용한 evaluation-only dense retrieval
- 실제 Groq/Gemini generation 호출: 0회
- Production RAG 수정: 0건
- 상태: **ChromaDB baseline, IR metric 및 RAGAS ID-based metric 평가 완료**

## 1. 결론

Production SQLite catalog에 등록된 수혈 문서 105 chunks와 기존 384차원 embedding을 읽기 전용으로 검증한 뒤, 같은 `chunk_id`와 vector를 ChromaDB evaluation-only collection에 등록했다. Chroma에는 문서 원문을 넣지 않았고 105개 record의 `documents` 값이 모두 `None`임을 다시 확인했다.

사람이 페이지별로 검토해 retrieval 결과와 독립적으로 만든 평가셋은 45문항이다. 이 가운데 본문 gold가 명확한 31문항만 aggregate metric에 포함했다. 표 구조 또는 이미지 배치 확인이 필요한 14문항은 `needs_human_review=true`로 유지해 per-case 결과에는 남기되 aggregate에서는 제외했다.

승인 gold 31문항 기준 성능은 Hit@1 `0.0645`, Hit@5 `0.1613`, Hit@10 `0.3871`, MRR `0.1301`이다. RAGAS ID-based Context Precision은 `0.0419`, Context Recall은 `0.3710`이다. 현재 multilingual MiniLM vector만 사용하는 dense baseline이므로 이 낮은 값 자체가 향후 BM25/RRF 비교를 위한 기준선이다. 성능을 높이기 위한 production retrieval 변경은 수행하지 않았다.

## 2. Catalog 및 dataset 무결성

| 항목 | 결과 |
|---|---|
| Document ID | `bc3d0de4-6560-4560-b78f-645e82cddd94` |
| 페이지 | 17 |
| Registered chunks | 105 |
| Unique/stable chunk IDs | 105/105 |
| Chunk version | 4, 유지 |
| Embedding model | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` |
| Vector shape | 105 × 384 |
| Non-finite/zero vector | 0 |
| Parent metadata 누락 | 0 |
| 평가 문항 | 45 |
| Aggregate 포함 approved gold | 31 |
| Human review 필요 | 14 |

Fixture는 `chunk_id`, page, `parent_id`, substantive-body 판정과 exact text의 SHA-256만 저장한다. 병원 원문이나 exact SourceUnit text는 저장하지 않는다. 실행 시 이 값들을 현재 catalog와 대조해 ID, fingerprint 또는 구조가 달라지면 평가 전에 fail closed한다.

Human review 14건의 구성은 다음과 같다.

- 혈액제제 표 질문 5건
- 검사 유효기간/주기 표 질문 2건
- 요청·수령 및 혈소판 채집백 이미지 배치 2건
- 수혈 이상반응 표 질문 5건

## 3. ChromaDB 설정

| 항목 | 설정 |
|---|---|
| ChromaDB | 1.5.9 |
| Collection | `transfusion_baseline` |
| Distance | cosine |
| Embedding function | 없음 — catalog vector 직접 등록 |
| Stored IDs | production `chunk_id` 그대로 사용 |
| Stored embeddings | production 384d vector 그대로 사용 |
| Stored metadata | document ID, position, page, parent ID |
| Stored documents/source text | 없음 |
| Query embedding | 기존 FastEmbed 0.8.0 + 동일 model |
| Top-K | 1, 3, 5, 10 |

Collection 구축에는 `1048.63 ms`가 걸렸고 index storage는 `639,140 bytes`였다. 45문항의 embedding+query latency는 평균 `14.71 ms`, p50 `11.37 ms`, p95 `35.73 ms`, 최소 `7.20 ms`, 최대 `81.42 ms`였다.

## 4. 전체 IR 및 RAGAS ID metric

아래 값은 `needs_human_review=false`인 31문항만 집계한 macro average다.

| Metric | 결과 |
|---|---:|
| Hit@1 | 0.0645 |
| Hit@3 | 0.0968 |
| Hit@5 | 0.1613 |
| Hit@10 | 0.3871 |
| MRR | 0.1301 |
| Recall@1 | 0.0645 |
| Recall@3 | 0.0806 |
| Recall@5 | 0.1452 |
| Recall@10 | 0.3710 |
| Precision@1 | 0.0645 |
| Precision@3 | 0.0323 |
| Precision@5 | 0.0387 |
| Precision@10 | 0.0419 |
| RAGAS ID Context Precision@10 | 0.0419 |
| RAGAS ID Context Recall@10 | 0.3710 |

RAGAS 0.4.3의 `IDBasedContextPrecision`과 `IDBasedContextRecall`만 실행했다. LLM judge가 필요한 metric은 보류했으며 API 호출은 없다. 각 RAGAS 값은 별도의 deterministic set-overlap 공식과 일치하는지 실행 중 다시 검사한다. RAGAS의 ID metric 정의는 [Context Precision](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/)과 [Context Recall](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_recall/)을 따른다.

## 5. Question type별 결과

표의 aggregate 값은 approved gold만 계산한다. `product_table`과 `adverse_reaction`은 전 문항이 표 확인 대상으로 보류되어 aggregate 값이 없다.

| Question type | 전체 | 평가 포함 | 검토 필요 | Hit@1 | Hit@5 | Hit@10 | MRR | ID Recall@10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| administration | 5 | 4 | 1 | 0.2500 | 0.5000 | 0.5000 | 0.3125 | 0.5000 |
| adverse_reaction | 5 | 0 | 5 | — | — | — | — | — |
| consent | 3 | 3 | 0 | 0.3333 | 0.3333 | 0.3333 | 0.3333 | 0.3333 |
| definition | 2 | 2 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| irradiation | 5 | 5 | 0 | 0.0000 | 0.2000 | 0.8000 | 0.1867 | 0.8000 |
| leukocyte_reduction | 4 | 4 | 0 | 0.0000 | 0.2500 | 0.5000 | 0.1042 | 0.3750 |
| monitoring | 1 | 1 | 0 | 0.0000 | 0.0000 | 1.0000 | 0.1667 | 1.0000 |
| pretransfusion_testing | 6 | 4 | 2 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| procedure | 1 | 1 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| product_table | 5 | 0 | 5 | — | — | — | — | — |
| request_receipt | 2 | 1 | 1 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| return_disposal | 4 | 4 | 0 | 0.0000 | 0.0000 | 0.5000 | 0.0670 | 0.5000 |
| verification | 2 | 2 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |

이 baseline은 dense embedding 단독 성능을 재는 것이므로 BM25, RRF, reranker 또는 production query normalization을 적용하지 않는다. 유형별 0점도 수정하거나 gold를 retrieval 결과에 맞춰 바꾸지 않았다.

## 6. BM25 비교 계약

`comparison_schema.json`은 후속 BM25 evaluator가 동일 dataset을 사용할 수 있게 다음을 고정한다.

- Join keys: `dataset_id`, `case_id`, `chunk_id`
- Case fields: question type/question, review flag, reference IDs, ranked retrieved IDs, latency
- 동일 cutoffs: 1, 3, 5, 10
- 동일 metric fields: Hit, MRR, Recall, Precision, RAGAS ID precision/recall
- Ranked result fields: case/chunk/rank/score

BM25 팀은 fixture의 같은 45개 질문과 reference chunk IDs를 읽고 engine 결과만 채우면 된다. 표·이미지 보류 14건을 aggregate에서 제외하는 정책도 동일하게 적용해야 한다.

## 7. Dependency 및 안전 경계

ChromaDB와 RAGAS는 `.venv-eval-transfusion` 격리 환경에만 설치했다. Production `requirements.txt`, `requirements-dev.txt`와 `.venv`는 변경하지 않았다. RAGAS 0.4.3이 최신 `langchain-community` 0.4.2에서 제거된 compatibility import를 요구해, 평가 전용 환경의 `langchain-community`만 0.3.31로 맞췄다. Production package downgrade는 없다.

- Groq/Gemini generation API: 0회
- LLM judge metric: 미실행
- Chroma anonymized telemetry: 비활성화
- RAGAS telemetry: 비활성화
- Production RAG/BM25/RRF/reranker/Facet-slot/validator: 미변경
- 병원 원문을 JSON/CSV/HTML artifact에 복제: 0건
- Chroma `documents` non-null: 0/105

## 8. 테스트와 코드리뷰

- 평가 전용 집중 테스트: **13 passed**
- 전체 production pytest: **453 passed, 3 skipped, 6 warnings**
- Ruff check: 통과
- `git diff --check`: 통과
- Production module/requirements diff: 없음
- Code review: 평가셋 크기 우회와 fixture document drift 검사를 보완한 뒤 추가 correctness/safety 결함 없음

전체 테스트의 skip 3건 중 2건은 production 환경에 evaluation-only ChromaDB/RAGAS를 설치하지 않았기 때문이며, 해당 통합 테스트는 격리 환경에서 통과했다. Warning 6건은 기존 FastEmbed multilingual MiniLM pooling 안내다.

## 9. 산출물

코드 및 fixture:

- `tests/fixtures/transfusion_retrieval_baseline.json`
- `tests/test_transfusion_chroma_baseline.py`
- `tools/chroma_baseline_evaluate.py`
- `tools/retrieval_baseline_metrics.py`

최종 artifact:

`artifacts/2026-09-16_transfusion-chromadb-ragas-baseline/`

- `dataset_manifest.json`
- `chroma_results.csv`
- `chroma_results.json`
- `chroma_summary.json`
- `metrics_by_question_type.csv`
- `ragas_results.json`
- `environment.json`
- `test_results.json`
- `comparison_schema.json`
- `review.html`
- `chroma_index/`

## 10. 최종 판정과 다음 단계

수혈 catalog 무결성, stable chunk ID, 동일 embedding model, evaluation-only Chroma collection, 45문항 fixture, IR/RAGAS ID metric, 원문 비저장, 전체 회귀 기준을 모두 충족했다. 중단 조건은 발생하지 않았다.

이 결과는 Chroma dense 단독 baseline이다. 다음 비교 단계에서는 production retrieval을 바꾸기 전에 동일 fixture와 comparison schema로 BM25 단독 결과를 산출하고, 승인 gold 31건에서 dense/BM25의 유형별 강점과 실패를 비교하는 것이 적절하다. Human review 14건은 표·이미지 gold를 사람이 확정한 뒤 별도 확장 aggregate에 포함한다.
