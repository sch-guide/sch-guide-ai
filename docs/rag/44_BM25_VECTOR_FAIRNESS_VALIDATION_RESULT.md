# BM25–Vector 공정성 검증 결과

- 평가일: 2026-09-17
- 대상 저장소/브랜치: `sch-guide-ai` / `boha-rag`
- 대상 문서: `실무지침서_수혈간호.pdf`, 105 chunks
- 평가셋: `transfusion-retrieval-v3`, 90문항
- 평가 방식: evaluation-only BM25 ablation, MiniLM vector sanity/representation 실험
- 실제 Groq/Gemini generation 호출: 0회
- 외부 embedding API 호출: 0회
- Production retrieval 변경: 0건
- 최종 판정: **A — minimal BM25도 최선의 현재 로컬 vector 설정보다 명확히 우세**

## 1. 결론

현재 BM25 우세는 SCHAT 전용 tuning만으로 만들어진 결과가 아니다. 형태소 사전, 한국어 n-gram, alias expansion, heading filter와 section context를 모두 제거한 `bm25_minimal_raw`도 Hit@10 0.7051, MRR 0.4309, Recall@10 0.6227로 최선의 vector 구성인 `vector_title_section_body`의 0.3590, 0.1976, 0.3288을 크게 앞섰다.

SCHAT tokenizer와 corpus 정책은 BM25를 추가로 개선한다. Core score는 minimal 0.5871에서 current 0.7694로 0.1823 증가했다. 그러나 best vector의 core score는 0.3015이므로, tuning 효과를 제거해도 BM25가 0.2857 앞선다. 따라서 기존 격차를 “BM25만 유리하게 튜닝된 불공정 비교”로 설명할 수 없다.

Vector 쪽 설정 오류도 발견되지 않았다. Catalog에는 105개의 고유한 384차원 finite vector가 있고 L2 norm은 1에 가깝다. 현재 모델로 body vector를 재생성했을 때 catalog와 최대 절대 오차가 0이었고, query/document는 같은 모델을 사용하며 cosine 방향도 정상이다. `title + section + body` 표현은 current vector보다 개선됐지만 현재 BM25를 넘지 못했다.

따라서 이 corpus에서는 짧고 반복적인 병원 지침 문구, 제품명·수치·시점 표현과 gold chunk의 lexical overlap에 BM25가 더 잘 맞는다. 현재 production의 `BM25 + semantic/vector + RRF + reranker` 구조는 변경하지 않는다. 이번 결과만으로 vector를 제거하거나 별도 fallback을 추가할 근거도 없다.

## 2. 공정 비교 계약

모든 설정은 다음을 공유한다.

- 동일 수혈 문서와 동일 105 chunk IDs
- `CHUNK_VERSION=4`
- 동일 90문항, 동일 approved gold
- Positive 79문항 중 human review 1문항을 제외한 78문항만 IR/RAGAS 평균에 포함
- Negative 11문항은 positive 평균에서 제외하고 Top-1 score distribution만 별도 계산
- Top-K 1/3/5/10, MRR, Recall, Precision, ID Context Precision/Recall
- query당 retrieval latency 측정, mean과 p95 기록
- generation, Facet-slot, evidence gate, RRF와 reranker 미사용

공통 canonical query는 NFKC/공백 정리, 붙임형 topic-aspect 경계 분리와 질문형 noise 제거까지만 수행한다. BM25 n-gram/anchor와 embedding tokenizer는 retriever-specific 처리로 분리했다.

## 3. 기존 비교의 preprocessing 감사

| 항목 | 기존 BM25 baseline | 기존 vector baseline | 분류 |
|---|---|---|---|
| Query 입력 | Raw question | `bounded_embedding_question()` | Vector only |
| Tokenization | SCHAT lexical + 한국어 n-gram | MiniLM tokenizer | Retriever-specific |
| Lexical/alias 처리 | Anchor token | terms + 등록 alias | 양쪽이 서로 다름 |
| Topic/aspect 정규화 | 기존 baseline에서 미사용 | 제한적 compound split | Vector only, 부분 적용 |
| Temporal tier | 기존 baseline에서 미사용 | 없음 | 동일: 미사용 |
| Heading 처리 | heading/running-header 제외 | 모든 catalog body vector | BM25 only |
| Metadata/문서 표현 | section/inherited context 활용 | body only | BM25 only |
| Similarity | BM25 | normalized cosine | Retriever-specific |

기존 baseline은 production `QueryPlan.expanded`나 temporal tier를 BM25에 적용하지 않았다. Query 전처리는 오히려 vector 쪽이 더 많았고, BM25의 장점은 tokenizer와 corpus representation에 있었다.

## 4. 평가 설정

### BM25 ablation

1. `bm25_minimal_raw`: 단순 word tokenizer, body-only, 전체 chunk, raw query
2. `bm25_schat_tokens_body_all`: SCHAT lexical tokenizer만 추가
3. `bm25_current_baseline`: 기존 artifact와 동일한 `BM25Index + raw query`
4. `bm25_current_canonical`: current index + 공통 canonical query
5. `bm25_expanded_no_temporal`: current index + `QueryPlan.expanded`
6. `bm25_production_query`: expanded query + production BM25 candidate temporal tier

마지막 설정은 production 전체 retrieval이 아니라 BM25 candidate 단계만 평가한다. `rank_bm25_candidates()`의 실제 기본 후보 폭 40을 사용했다.

### Vector 설정

1. `vector_current_minilm`: 기존 bounded query + catalog body vector
2. `vector_common_query_body`: 공통 canonical query + 재생성 body vector
3. `vector_title_section_body`: 공통 query + title/section/body
4. `vector_document_title_section_body`: 공통 query + document title/title/section/body

모든 vector 설정은 현재 로컬 cache의 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, 384 dimensions를 사용했다. Representation 원문 token 최대치는 body 98, title/section/body 116, document/title/section/body 124로 128-token 계약을 넘는 chunk가 없었다.

## 5. 전체 metric

| Config | Hit@5 | Hit@10 | MRR | Recall@10 | Precision@10 | ID Precision | ID Recall | Mean ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BM25 minimal | 0.5897 | 0.7051 | 0.4309 | 0.6227 | 0.0859 | 0.0859 | 0.6227 | 0.062 |
| BM25 current | **0.7949** | **0.8846** | **0.5602** | **0.8379** | **0.1192** | **0.1192** | **0.8379** | **0.384** |
| Vector current MiniLM | 0.1410 | 0.2821 | 0.0913 | 0.2564 | 0.0321 | 0.0321 | 0.2564 | 18.651 |
| Vector title/section/body | 0.3205 | 0.3590 | 0.1976 | 0.3288 | 0.0436 | 0.0436 | 0.3288 | 17.792 |
| Alternate embedding | 미실행 | 미실행 | 미실행 | 미실행 | 미실행 | 미실행 | 미실행 | 미실행 |

전체 Hit@1/3/5/10, Recall@1/3/5/10, Precision@1/3/5/10과 p95/index build 수치는 `metrics.csv` 및 `latency.json`에 있다.

## 6. BM25 tuning 기여도

Core score는 Hit@5, Hit@10, MRR, Recall@10의 평균이다.

| 단계 | Core score 변화 | 해석 |
|---|---:|---|
| Minimal → SCHAT tokenizer | +0.1380 | 한국어 lexical/token 경계가 가장 큰 개선 요인 |
| SCHAT tokenizer → current corpus policy | +0.0443 | heading 제외와 section/context 표현의 추가 효과 |
| Current raw → canonical query | +0.0006 | 공통 query normalization 영향은 거의 없음 |
| Canonical → expanded query | -0.0286 | 이 evaluation gold에서는 expansion이 평균을 낮춤 |
| Expanded → temporal tier | -0.0308 | 후보 40 기반 temporal tier가 전체 aggregate를 추가로 낮춤 |

즉 current BM25 tuning은 유효하지만, tuning 전 minimal BM25도 best vector보다 크게 앞선다. Production query 단계가 standalone raw baseline보다 낮다는 사실도 숨기지 않았으며 production pipeline의 RRF/reranker 성능으로 오해하지 않는다.

## 7. Vector sanity와 Chroma 일치성

| 검사 | 결과 |
|---|---:|
| Catalog vector count / dimension | 105 / 384 |
| Chunk ID unique | true |
| Finite vector | true |
| L2 norm 범위 | 0.99999988–1.0 |
| Body rebuild 최대 절대 오차 | 0.0 |
| Deterministic exact ranking | true |
| 기존 BM25 Top-10 exact match | 1.0 |
| 기존 Chroma Top-10 exact-order match | 0.4 |
| 기존 Chroma Top-10 mean overlap | 0.8711 |

현재 검증은 catalog vector에 대한 deterministic exact cosine ranking이다. 이전 Chroma 결과는 HNSW 기반이므로 Top-10의 내부 순서가 완전히 같지는 않지만, current vector의 aggregate IR metric은 기존 Chroma baseline과 동일했다. Score를 distance로 뒤집은 오류나 query/document model mismatch는 확인되지 않았다.

현재 환경에는 Chroma package가 남아 있지 않아 production dependency를 다시 추가하거나 fresh collection을 재구축하지 않았다. 이는 기존 Chroma artifact와 catalog vector를 exact cosine으로 교차 검증한 경계다.

## 8. 질문 유형별 결과

| Type | N | BM25 current Hit@10 / MRR | Best vector Hit@10 / MRR |
|---|---:|---:|---:|
| adverse_reaction | 8 | 1.0000 / 0.8750 | 0.5000 / 0.2470 |
| fact_specific | 10 | 0.9000 / 0.6750 | 0.4000 / 0.2533 |
| monitoring | 2 | 1.0000 / 0.6000 | 0.5000 / 0.1000 |
| paraphrase | 6 | 0.8333 / 0.3278 | 0.3333 / 0.1111 |
| preparation | 10 | 1.0000 / 0.4926 | 0.2000 / 0.0643 |
| procedure | 6 | 0.8333 / 0.2639 | 0.1667 / 0.1667 |
| product_specific | 18 | 0.8889 / 0.4500 | 0.3889 / 0.1662 |
| temporal | 18 | 0.7778 / 0.6759 | 0.3889 / 0.3000 |

Best vector는 minimal BM25와 비교하면 paraphrase Hit@10에서 0.3333 대 0.1667로 앞섰다. 그러나 current BM25가 같은 유형에서 0.8333이고, best vector는 어떤 주요 question type에서도 current BM25를 이기지 못했다. 따라서 selective vector fallback을 production에 추가할 증거는 아직 없다.

## 9. Negative와 latency

Negative 11문항은 Top-10을 반환하더라도 positive IR 평균에 섞지 않았다.

| BM25 current Top-1 | Count | Mean | P50 | P95 | Max |
|---|---:|---:|---:|---:|---:|
| Positive approved | 78 | 8.6319 | 8.1531 | 14.4092 | 20.3106 |
| Negative | 11 | 2.8076 | 3.4171 | 5.8297 | 6.2822 |

평균 score separation은 5.8242다. 이것은 운영 threshold가 아니라 diagnostic이며 Q006/evidence gate를 대체하지 않는다.

BM25 current index build는 84.481 ms, best vector representation build는 11,536.409 ms였다. Mean query latency는 각각 0.384 ms와 17.792 ms다. 절대 latency는 로컬 환경 수치이며 config 간 상대 비교로만 해석한다.

## 10. Alternate embedding 경계

공식 FastEmbed 지원 목록에서 강한 multilingual retrieval 후보인 `intfloat/multilingual-e5-large`를 검토했지만 현재 local cache에 없었다. 새 모델 다운로드는 사용 권한과 저장공간을 요구하는 중단 조건이므로 실행하지 않았다. 따라서 이번 판정은 “현재 MiniLM/vector 구성과 안전하게 수행 가능한 representation 개선” 범위의 결론이며 모든 multilingual embedding 일반에 대한 결론은 아니다.

- [FastEmbed supported models](https://qdrant.github.io/fastembed/examples/Supported_Models/)
- [paraphrase-multilingual-MiniLM-L12-v2 model card](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2)

## 11. 최종 판정과 production 영향

Frozen rule에 따른 판정은 A다.

- Minimal BM25 core: 0.5871
- Current BM25 core: 0.7694
- Best vector core: 0.3015
- Minimal − best vector: +0.2857
- Current − minimal: +0.1823

Minimal BM25가 best vector보다 네 core metric 모두 높고 core score 차이도 0.05를 크게 넘는다. 따라서 **현재 수혈 105-chunk corpus에는 BM25 자체가 더 적합하고, SCHAT tuning은 이미 존재하는 우세를 추가 강화한다.**

Production retrieval은 기존 `BM25 + semantic/vector + RRF + reranker`를 유지한다. BM25, vector, RRF, reranker, Facet-slot, validator, embedding model과 dependencies를 변경하지 않았다.

## 12. 검증 및 산출물

- 집중/retrieval 회귀: `37 passed, 3 skipped, 1 warning`
- 전체 pytest: `514 passed, 4 skipped, 7 warnings`
- Ruff (`mvp`, `tests`, `tools`): 통과
- `git diff --check`: 통과
- Artifact exact source/question/secret 안전 계약: 통과
- 실제 Groq/Gemini 및 외부 embedding API 호출: 0회

Warning은 기존 FastEmbed mean-pooling 안내이며 model/dependency를 변경하지 않았다.

산출물:

- `tools/bm25_vector_fairness_evaluate.py`
- `tests/test_bm25_vector_fairness.py`
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

## 13. 제한점과 다음 단계

- Gold 1문항은 기존대로 human review 상태이며 aggregate에서 제외했다.
- Current MiniLM은 paraphrase similarity 모델이며 alternate retrieval embedding은 다운로드 경계 때문에 평가하지 않았다.
- Exact cosine과 기존 Chroma HNSW Top-10 순서는 100% 같지 않지만 aggregate metric은 재현됐다.
- 이 결과는 수혈 105 chunks와 승인된 78 positive에 대한 결론이다. 다른 지침서로 일반화하려면 동일 계약의 교차 문서 평가가 필요하다.
- 다음 실험이 필요하다면 production 변경 없이 별도 승인된 local cache 환경에서 multilingual retrieval embedding 한 가지를 같은 fixture로 비교하는 것이 우선이다.
