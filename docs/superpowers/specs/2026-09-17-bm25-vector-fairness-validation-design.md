# BM25–Vector Fairness Validation Design

## 목적

동일한 수혈 105 chunks와 승인된 retrieval gold 90문항을 사용해 기존 BM25 우세가 알고리즘·데이터 적합성 때문인지, BM25와 vector 사이의 tuning 불균형 때문인지 evaluation-only로 판정한다.

## 고정 계약

- Production retrieval `BM25 + semantic/vector + RRF + reranker`는 변경하지 않는다.
- 수혈 문서, 105 chunk IDs, `CHUNK_VERSION=4`, dataset `transfusion-retrieval-v3`를 그대로 사용한다.
- Positive 79문항 중 `needs_human_review=false`인 78문항만 IR/RAGAS aggregate에 포함한다.
- Negative 11문항은 IR/RAGAS 평균에서 제외하고 Top-1 score distribution만 별도 기록한다.
- Top-K와 metric은 1/3/5/10, MRR, Recall, Precision, ID Context Precision/Recall로 고정한다.
- Groq/Gemini, 외부 embedding API, 병원 원문 외부 전송은 0회다.
- Source text와 질문 원문은 산출물에 저장하지 않는다. case ID, config, chunk ID, metric, hash만 저장한다.

## 공정성 감사

기존 baseline의 실제 동작을 source와 artifact로 감사한다.

- BM25 baseline current: raw question → 기존 `BM25Index`; current tokenizer, heading filter, section/inherited-title representation을 사용한다. Production의 `QueryPlan.expanded`와 temporal tier는 쓰지 않는다.
- Vector baseline current: `bounded_embedding_question()` → MiniLM query embedding; catalog의 body-only normalized document vector; cosine space Chroma 결과다.
- 따라서 기존 baseline은 query normalization 측면에서 vector에 더 많은 처리가 적용됐으며, production BM25의 query expansion·temporal handling이 BM25 결과에 포함되지 않았다.

감사 결과는 `동일`, `BM25 only`, `Vector only`로 기록한다.

## 평가 설정

### BM25

1. `bm25_minimal_raw`
   - raw NFKC/whitespace query
   - 단순 word tokenizer
   - body-only
   - 모든 chunk 포함
   - lexical expansion, heading filter, temporal tier 없음
2. `bm25_current_baseline`
   - 기존 `BM25Index`와 raw question
   - 기존 baseline artifact와 rank/metric을 재현해야 한다.
3. `bm25_current_canonical`
   - 기존 `BM25Index`
   - 공통 canonical query 사용
   - temporal tier 없음
4. `bm25_production_query`
   - 기존 `BM25Index`
   - registered document metadata를 사용한 `QueryPlan.expanded`
   - 기존 `rank_bm25_candidates()` temporal tier 적용
   - production 검색 전체가 아니라 BM25 candidate 단계만 평가한다.

BM25 ablation은 tokenizer, corpus heading filter/section context, canonical query, production query expansion/temporal 효과를 단계별로 구분한다.

### Vector

1. `vector_current_minilm`
   - 기존 `bounded_embedding_question()`
   - catalog body vector
   - normalized cosine exact ranking
   - 기존 Chroma Top-10과 일치율을 sanity audit으로 기록한다.
2. `vector_common_query_body`
   - BM25와 공유하는 canonical query
   - body-only representation
3. `vector_title_section_body`
   - common canonical query
   - `title + section + body` representation
4. `vector_document_title_section_body`
   - common canonical query
   - `document title + title + section + body` representation

Representation은 현재 로컬 MiniLM으로 in-memory 재계산하며 chunk ID/gold를 바꾸지 않는다. 입력이 128 tokens를 넘으면 metadata prefix를 유지하고 body tail만 token-safe하게 축약한다.

## 공통 query preprocessing

공통 canonical query는 NFKC, whitespace normalization, 붙임형 topic/aspect 분리, 질문형 noise 제거까지만 수행한다. BM25 n-gram/anchor와 vector embedding 내부 처리는 retriever-specific으로 구분한다. Production 코드는 호출만 하고 변경하지 않는다.

## Chroma/vector sanity

- catalog vector count=105, dimension=384, finite/non-zero, ID unique를 확인한다.
- query/document는 동일 MiniLM을 사용한다.
- vectors는 L2-normalized이며 cosine similarity는 dot product와 같다.
- stored Chroma distance는 `similarity=1-distance`로 해석한다.
- exact cosine ranking을 두 번 실행해 deterministic ID를 확인한다.
- 기존 Chroma Top-10과 exact ranking의 overlap 및 exact-order match를 기록한다.

Chroma package는 production dependency에 추가하지 않는다. 기존 Chroma artifact와 catalog vector를 exact NumPy cosine으로 교차검증한다.

## Alternate embedding 경계

공식 FastEmbed 지원 목록에서 multilingual retrieval 후보를 감사하되, 현재 cache에는 MiniLM만 존재한다. 새 모델 다운로드·dependency 설치 없이 실행 가능한 alternate가 없으면 `not_run_download_required`로 기록한다. 선택 판정에는 포함하지 않는다.

## 유형 분석

기존 `question_type`과 fixture metadata의 `variation_axes`를 사용한다. 추가 diagnostic group은 문자열별 예외 없이 다음처럼 파생한다.

- exact medical/product term: `abbreviation`, `english_term`, `product`
- numeric/time: `time`, `unit`, `frequency`
- temporal, procedure, paraphrase: 기존 question_type
- colloquial: `colloquial`
- long natural language: `natural_language`

## 판정

Core score는 Hit@5, Hit@10, MRR, Recall@10 평균이다.

- A: minimal BM25가 best vector보다 core score 0.05 이상 높고 4개 core metric 중 3개 이상 우세
- B: current BM25는 우세하지만 minimal BM25와 best vector의 core score 차이가 0.03 이내이거나 minimal이 낮고, current-minus-minimal gain이 0.05 이상
- C: best vector가 current BM25 core score 0.05 이내이거나 의미 있는 question group에서 우세하지만 overall을 0.05 이상 능가하지 않음
- D: best vector가 current BM25보다 core score 0.05 이상 높고 core metric 3개 이상 우세

경계가 정확히 일치하지 않으면 가장 가까운 판정을 선택하고 metric 근거와 예외를 명시한다.

## 산출물

- `tools/bm25_vector_fairness_evaluate.py`
- `tests/test_bm25_vector_fairness.py`
- `docs/rag/44_BM25_VECTOR_FAIRNESS_VALIDATION_RESULT.md`
- `artifacts/2026-09-17_bm25-vector-fairness-validation/`
  - `preprocessing_audit.json`
  - `bm25_ablation.json`
  - `vector_config_audit.json`
  - `embedding_comparison.json`
  - `representation_comparison.json`
  - `metrics.csv`
  - `metrics_by_type.csv`
  - `latency.json`
  - `test_results.json`
  - `review.html`

## 검증

- TDD로 config/query/ranking/metric/artifact 안전 계약을 고정한다.
- 기존 baseline current rank/metric 재현을 확인한다.
- evaluation 집중 테스트와 기존 retrieval regression을 실행한다.
- 전체 pytest, Ruff, `git diff --check`, code review, artifact exact-source/secret 감사를 수행한다.
