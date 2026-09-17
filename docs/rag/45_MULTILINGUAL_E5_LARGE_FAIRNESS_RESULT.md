# Multilingual E5 Large Retrieval 공정성 평가 결과

- 평가일: 2026-09-17
- 대상 저장소/브랜치: `sch-guide-ai` / `boha-rag`
- 대상 문서: `실무지침서_수혈간호.pdf`, 105 chunks
- 평가셋: `transfusion-retrieval-v3`, 90문항
- Alternate model: `intfloat/multilingual-e5-large`
- 실행 방식: FastEmbed 0.8.0 + ONNX Runtime 1.30.0, CPU local inference
- 실제 Groq/Gemini generation 호출: 0회
- 외부 embedding API 호출: 0회
- 병원 데이터 외부 전송: 0회
- Production retrieval/embedding 변경: 0건
- 최종 판정: **C — multilingual-e5-large가 current BM25를 능가하여 vector retrieval 전략 재평가 필요**

## 1. 결론

동일한 수혈 105 chunks, 동일 90문항과 approved gold에서 `intfloat/multilingual-e5-large`는 Hit@10 0.9359, MRR 0.7151, Recall@10 0.9084를 기록했다. Current BM25의 0.8846, 0.5602, 0.8379보다 모두 높다. Hit@5도 E5 0.8718, BM25 0.7949로 E5가 앞선다.

Frozen core score는 E5 0.8578, current BM25 0.7694로 차이가 +0.0884다. E5가 Hit@5, Hit@10, MRR, Recall@10 네 core metric을 모두 이겼으므로 판정 규칙 C를 충족한다.

따라서 이전 MiniLM baseline의 낮은 성능을 vector retrieval 자체의 한계로 일반화할 수 없다. BM25는 current MiniLM보다 명확히 우세했지만, retrieval용 multilingual embedding으로 교체한 evaluation에서는 vector가 BM25를 넘어섰다. **기존 BM25 우세에는 embedding model 선택이 큰 영향을 주었다.**

다만 E5는 모델 cache 약 2.25GB, passage index build 약 99초, CPU query 평균 188.3ms/p95 289.8ms가 필요하다. Current BM25 평균 0.384ms보다 훨씬 느리다. 이번 결과는 전략 재평가 근거이며 production 즉시 교체 승인이 아니다. Production은 기존 `BM25 + semantic/vector + RRF + reranker`를 그대로 유지한다.

## 2. 모델 선정과 공식 입력 계약

FastEmbed 0.8.0의 공식 supported-model registry에서 다음 조건을 모두 만족하는 단일 후보로 `intfloat/multilingual-e5-large`를 선정했다.

- Multilingual 약 100개 언어
- Korean 입력 지원
- Retrieval embedding 용도
- FastEmbed/ONNX local inference
- 1024 dimensions
- 최대 512 input tokens
- Query/document prefix 필수
- MIT license

공식 권장 계약에 따라 query에는 `query: `, chunk에는 `passage: `를 정확히 한 번 붙였다. FastEmbed의 `query_embed()`/`passage_embed()`가 이 버전에서는 prefix를 자동 삽입하지 않으므로 evaluation adapter가 명시적으로 적용했다.

- [FastEmbed supported models](https://qdrant.github.io/fastembed/examples/Supported_Models/)
- [multilingual-e5-large model card](https://huggingface.co/intfloat/multilingual-e5-large)

## 3. 공정 비교 조건

| 항목 | 계약 |
|---|---|
| Corpus | 동일 수혈 105 chunks |
| Chunk identity | 동일 production catalog chunk IDs |
| Dataset | 동일 90문항, `transfusion-retrieval-v3` |
| Positive aggregate | 승인 positive 78문항 |
| Human review | 1문항 aggregate 제외 |
| Negative | 11문항, IR 평균 제외·score diagnostic만 계산 |
| Top-K | 1, 3, 5, 10 |
| Representation | Body-only chunk text |
| Query content | Current MiniLM과 동일한 `embedding_question()` 결과 |
| E5 model-specific 처리 | `query: ` / `passage: ` prefix만 추가 |
| Ranking | Deterministic exact cosine |

90개 query 모두 current MiniLM의 bounded query content와 E5 prefix 전 query content가 일치했다. Current MiniLM 최대 query 길이는 21 tokens로 truncation 차이도 없었다.

## 4. 전체 metric

| Config | Hit@1 | Hit@3 | Hit@5 | Hit@10 | MRR | Recall@10 | Precision@10 | ID Precision | ID Recall | Mean ms | P95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BM25 minimal | 0.3205 | 0.4872 | 0.5897 | 0.7051 | 0.4309 | 0.6227 | 0.0859 | 0.0859 | 0.6227 | 0.062 | 0.127 |
| BM25 current | 0.3974 | 0.6538 | 0.7949 | 0.8846 | 0.5602 | 0.8379 | 0.1192 | 0.1192 | 0.8379 | **0.384** | **0.579** |
| Current MiniLM vector | 0.0385 | 0.0897 | 0.1410 | 0.2821 | 0.0913 | 0.2564 | 0.0321 | 0.0321 | 0.2564 | 18.651 | 24.872 |
| Multilingual E5 large | **0.5897** | **0.8205** | **0.8718** | **0.9359** | **0.7151** | **0.9084** | **0.1308** | **0.1308** | **0.9084** | 188.307 | 289.771 |

E5는 정확도 metric 전체에서 current BM25를 앞섰고 latency에서는 크게 뒤졌다. ID Context Precision/Recall은 동일 chunk ID 정의로 계산했으므로 Precision@10/Recall@10과 같은 값이다.

## 5. 질문 유형별 결과

| Type | N | BM25 Hit@10 | E5 Hit@10 | BM25 MRR | E5 MRR | BM25 Recall@10 | E5 Recall@10 |
|---|---:|---:|---:|---:|---:|---:|---:|
| adverse_reaction | 8 | 1.0000 | 1.0000 | 0.8750 | **0.9375** | 1.0000 | 1.0000 |
| fact_specific | 10 | 0.9000 | 0.9000 | **0.6750** | 0.5954 | 0.9000 | 0.9000 |
| monitoring | 2 | 1.0000 | 1.0000 | 0.6000 | **1.0000** | 1.0000 | 1.0000 |
| paraphrase | 6 | 0.8333 | 0.8333 | 0.3278 | **0.6250** | 0.8333 | 0.8333 |
| preparation | 10 | 1.0000 | 1.0000 | 0.4926 | **0.7492** | 0.9500 | **1.0000** |
| procedure | 6 | 0.8333 | **1.0000** | 0.2639 | **0.7222** | 0.6429 | **0.8095** |
| product_specific | 18 | 0.8889 | **0.9444** | 0.4500 | **0.6991** | 0.8056 | **0.8889** |
| temporal | 18 | 0.7778 | **0.8889** | 0.6759 | 0.6759 | 0.7500 | **0.8889** |

E5 개선은 procedure, paraphrase, preparation과 product-specific에서 두드러졌다. Fact-specific에서는 Hit@10/Recall@10이 같고 MRR은 BM25가 더 높다. 따라서 C 판정은 전체 aggregate 우세이며 모든 질문 유형의 일률적 우세를 뜻하지 않는다.

## 6. Index와 vector sanity

| 항목 | 결과 |
|---|---:|
| Model cache size | 2,252,997,322 bytes, 약 2.25GB |
| Passage vector count | 105 |
| Dimensions | 1024 |
| Chunk IDs unique | true |
| Finite vectors | true |
| Raw vector norm 범위 | 27.4344–30.3456 |
| Similarity | Exact cosine, ranking 시 norm 반영 |
| Passage index build | 98,965.40 ms |
| Local inference | true |

Raw vector는 unit norm이 아니지만 ranking 함수가 query/document norm으로 나누는 exact cosine을 사용하므로 score 방향과 정규화는 올바르다. 저장된 90개 결과는 각 Top-10 ID가 유일하고 score가 내림차순임을 독립적으로 다시 확인했다.

## 7. Negative diagnostic

Negative 11문항은 Top-10을 반환하더라도 positive IR/RAGAS aggregate에 포함하지 않았다.

| E5 Top-1 | Count | Mean | P50 | P95 | Max |
|---|---:|---:|---:|---:|---:|
| Positive approved | 78 | 0.8749 | 0.8748 | 0.9049 | 0.9202 |
| Negative | 11 | 0.8108 | 0.8061 | 0.8442 | 0.8477 |

Mean separation은 0.0641이다. 이는 운영 abstention threshold가 아니며 기존 domain/evidence gate와 Q006 zero-call을 대체하지 않는다.

## 8. 판정

Core score는 Hit@5, Hit@10, MRR, Recall@10 평균이다.

| Config | Core score |
|---|---:|
| BM25 minimal | 0.5871 |
| BM25 current | 0.7694 |
| Multilingual E5 large | **0.8578** |

E5 − current BM25 차이는 +0.0884이고 E5가 네 core metric 모두 우세하다. 따라서 최종 판정은 다음과 같다.

**C. multilingual-e5-large가 BM25를 능가 → vector retrieval 전략 재평가**

이 판정은 evaluation 결론이다. 모델 크기, latency, production memory와 현재 RRF/reranker 상호작용을 검증하지 않았으므로 production embedding이나 retrieval 구성을 자동 변경하지 않았다. 다음 의사결정 단계는 동일 90문항에서 `BM25 + E5` evaluation-only Hybrid/RRF와 end-to-end candidate latency를 비교하는 것이다.

## 9. 검증

- Prefix/판정/artifact TDD: `5 passed`
- 집중 및 retrieval 회귀: `42 passed, 3 skipped, 1 warning`
- 전체 pytest: `519 passed, 4 skipped, 7 warnings`
- Ruff (`mvp`, `tests`, `tools`): 통과
- `git diff --check`: 통과
- Artifact security audit: 7 files, exact source match 0, forbidden field/secret 0
- 독립 metric 재계산: Hit@10/MRR/Recall@10 저장값과 일치
- Code review: 추가 조치가 필요한 correctness/safety/regression 결함 없음

Warning은 기존 FastEmbed 0.8.0 mean-pooling 안내다. E5 공식 평균 pooling 계약과 일치하며 package pin이나 production dependency 변경은 수행하지 않았다.

## 10. 변경 및 산출물

Evaluation-only 추가:

- `tools/multilingual_e5_fairness_evaluate.py`
- `tests/test_multilingual_e5_fairness.py`
- `docs/superpowers/plans/2026-09-17-multilingual-e5-large-fairness-evaluation.md`
- `artifacts/2026-09-17_multilingual-e5-large-fairness-validation/`
  - `model_audit.json`
  - `comparison_metrics.csv`
  - `metrics_by_question_type.csv`
  - `e5_results.json`
  - `e5_summary.json`
  - `test_results.json`
  - `review.html`

Local ignored cache:

- `data/models/models--qdrant--multilingual-e5-large-onnx/`

비변경:

- Production BM25, semantic/vector, RRF와 reranker
- Production embedding model과 catalog vectors
- `requirements.txt`, `requirements-dev.txt`, `pyproject.toml`
- SourceUnit, Facet-slot, AnswerCoverage와 validators
- Selection limit, generation provider와 prompts
