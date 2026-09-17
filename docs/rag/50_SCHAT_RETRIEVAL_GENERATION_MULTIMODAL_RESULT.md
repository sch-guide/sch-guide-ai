# SCHAT Retrieval·Generation·Multimodal 통합 고도화 결과

- 검증일: 2026-09-17
- 브랜치: `boha-rag`
- Retrieval 평가: 수혈 105 chunks, `transfusion-retrieval-v3` 90문항, approved positive 78
- 실제 Groq/Gemini/vision API 호출: 0회
- 병원 데이터 외부 전송: 0회
- Production retrieval/embedding 변경: 0건
- 최종 production 판정: **E — 기존 BM25 + semantic/vector + RRF + reranker 유지**

## 1. 결론

동일 수혈 corpus에서 raw retrieval 정확도는 `BM25 + multilingual-E5-large RRF`가
가장 높았다. Hit@10 0.9487, Recall@10 0.9359, core 0.8712로 E5-only의
0.9359/0.9084/0.8578을 소폭 상회했다.

하지만 existing SCHAT reranker까지 적용한 안전 경로는 core 0.7825로 떨어졌고,
BM25 current 대비 이득은 +0.0131이었다. Always-on E5는 평균 질의 지연
193.5 ms, model RSS 증분 약 1.54 GB를 요구한다. Selective E5도 40/90문항에서
작동했지만 core 0.8113으로 best와 거리가 커서 production 근거가 되지 못했다.

따라서 raw RRF 수치를 위해 기존 reranker나 validator를 우회하지 않고 production은
그대로 유지했다. Text UX, controlled-generation Mock, table evidence/citation/UI,
evidence-type routing은 이미 완료된 구조를 재구현하지 않고 통합 회귀로 검증했다.
Image/TF027은 임상 workflow 해석이 불확실하므로 human-review pending을 유지했다.

## 2. Retrieval 비교

| Config | Hit@1 | Hit@3 | Hit@5 | Hit@10 | MRR | Recall@10 | Precision/ID Precision@10 | Mean/P95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BM25 current | 0.3974 | 0.6538 | 0.7949 | 0.8846 | 0.5602 | 0.8379 | 0.1192 | 1.010 / 1.628 |
| E5 large | **0.5897** | **0.8205** | 0.8718 | 0.9359 | **0.7151** | 0.9084 | 0.1308 | 193.474 / 282.720 |
| BM25 + E5 RRF | 0.5128 | 0.7949 | **0.9231** | **0.9487** | 0.6769 | **0.9359** | **0.1359** | 194.651 / 283.856 |
| RRF + existing reranker | 0.3205 | 0.6923 | 0.8333 | 0.9103 | 0.5184 | 0.8681 | 0.1244 | 265.158 / 372.321 |
| Selective E5 | 0.4103 | 0.7436 | 0.8590 | 0.9103 | 0.5997 | 0.8764 | 0.1256 | 87.778 / 270.402 |

ID Context Recall은 각 전략의 Recall@10과 같다. Negative 11문항은 positive IR/RAGAS
평균에 섞지 않았다. Human-review 1문항도 aggregate에서 제외했다.

## 3. 질문 유형별 핵심 결과

| Type | N | BM25 Hit@10/MRR | E5 Hit@10/MRR | RRF Hit@10/MRR | RRF Recall@10 |
|---|---:|---:|---:|---:|---:|
| adverse_reaction | 8 | 1.0000 / 0.8750 | 1.0000 / 0.9375 | 1.0000 / 0.9375 | 1.0000 |
| fact_specific | 10 | 0.9000 / **0.6750** | 0.9000 / 0.5954 | 0.9000 / 0.5900 | 0.9000 |
| monitoring | 2 | 1.0000 / 0.6000 | 1.0000 / 1.0000 | 1.0000 / 1.0000 | 1.0000 |
| paraphrase | 6 | 0.8333 / 0.3278 | 0.8333 / **0.6250** | 0.8333 / 0.5750 | 0.8333 |
| preparation | 10 | 1.0000 / 0.4926 | 1.0000 / **0.7492** | 1.0000 / 0.6283 | 1.0000 |
| procedure | 6 | 0.8333 / 0.2639 | 1.0000 / **0.7222** | 1.0000 / 0.6667 | **0.8333** |
| product_specific | 18 | 0.8889 / 0.4500 | 0.9444 / **0.6991** | 0.9444 / 0.5944 | **0.9444** |
| temporal | 18 | 0.7778 / 0.6759 | 0.8889 / 0.6759 | **0.9444 / 0.7204** | **0.9444** |

E5/RRF는 procedure·product·temporal recall을 개선했고, exact fact-specific MRR은 BM25가
더 높았다. 이 보완성은 지속 연구 가치가 있지만, 현재 reranker가 E5의
의미 후보 순위를 낮추는 현상을 먼저 해결해야 production에 반영할 수 있다.

## 4. Runtime·snapshot

| 항목 | 결과 |
|---|---:|
| E5 model load | 4,424.18 ms |
| Local model cache | 2,252,997,322 bytes |
| Process RSS model delta | 1,541,464,064 bytes |
| Passage build reference | 98,965.40 ms |
| Persistent snapshot | 389,672 bytes |
| Snapshot reload | 11.46 ms |
| E5 query embedding mean/p95 | 192.98 / 282.16 ms |
| Exact vector search mean/p95 | 0.50 / 0.61 ms |
| RRF mean/p95 | 0.17 / 0.22 ms |
| Existing reranker mean/p95 | 70.51 / 100.91 ms |

Snapshot은 chunk ID 105개, 105×1024 float vector, dataset/document/model contract만 가지며
원문과 질문을 포함하지 않는다. 웹앱 실행 때 passage 105개를 매번
재임베딩할 필요는 없다. 다만 production은 E5를 사용하지 않으므로 이 snapshot은
evaluation-only local ignored 데이터다.

## 5. Generation UX 및 controlled paraphrasing

기존 presentation sidecar는 verified Answer를 변경하지 않고 intent별 paragraph, checklist,
warning bullet, procedure branch/phase, summary heading을 표시한다. Statement, quote, chunk ID,
순서와 citation은 그대로다.

Controlled paraphrasing은 request-scoped SourceUnit enum, supporting IDs, server citation 재생성,
number/unit/time/condition/negation/branch/phase/coverage validator까지 Mock으로 준비됐다.
그러나 semantic entailment는 `semantic_support_pending=true`이며, 병원 데이터 외부 전송
승인 없이 production provider에 연결하지 않았다.

## 6. Table evidence·citation·UI

- Structured table 5개, row 33개
- `pdf_cells` 3개, `catalog_row_fallback` 2개
- Citation: document/page/table ID/row index
- Approved table cases 5개, Hit@10 0.8
- Local Streamlit UI에서만 exact cell/row text 표시
- Artifact에는 ID, bbox, fingerprint, SHA-256만 저장

MM003 1건 miss로 table retrieval의 production 이득이 명확하지 않아 기존 text retrieval과
분리된 evaluation-only 경로를 유지했다. Header/cell 관계를 추측하거나 표 값을
생성하지 않았다.

## 7. Image/diagram 및 multimodal routing

- Figure metadata candidates: 55
- Vision description/API call: 0건
- TF027 workflow: `needs_human_review=true`, aggregate/production 제외
- QueryPlan evidence routing: text/table/image/mixed candidate metadata
- Routing metadata는 retrieval, evidence gate, answerability에 미연결

화살표 방향, workflow 순서, 숫자·단위를 사람 검수 없이 해석하는 것은
임상 추론이 될 수 있어 image track만 pending으로 유지했다.

## 8. UAT·안전·회귀

- Integrated focused regression: **236 passed, 4 warnings**
- Sedation UAT: 기존 45/45 통과 계약 유지
- Q006 out-of-scope/provider zero-call: 단일 재검증 **1 passed**
- Full pytest: **525 passed, 4 skipped, 7 warnings**
- Ruff: 통과
- `git diff --check`: 통과
- Independent metric recomputation: 5/5 configs 일치
- Artifact audit: 13 files, exact source match 0, forbidden exact key 0
- Code review: actionable correctness/safety/regression 결함 없음

Warning 7건은 기존 FastEmbed mean-pooling 안내다. Package pin, production dependency,
production model을 변경하지 않았다.

## 9. 변경·비변경

이번 통합 작업 추가:

- `tools/bm25_e5_strategy_evaluate.py`
- `tests/test_bm25_e5_strategy_evaluation.py`
- E5 ignored local snapshot
- Superpowers plan/progress, retrieval strategy artifact, 본 결과 문서

변경하지 않음:

- Production BM25, MiniLM semantic/vector, RRF, reranker
- Production embedding/catalog vectors/requirements
- Facet-slot, SourceUnit, selection limit 16
- Parent atomicity, AnswerCoverage, `validate_answer()`
- Citation, number, unit, time, condition, negation, action, source-order validators
- Generation provider/prompt, retry, fallback, web search

## 10. 산출물·pending·다음 단계

Retrieval 전략 최종 검수본:

`artifacts/2026-09-17_bm25-e5-retrieval-strategy-02/`

통합 요약:

`artifacts/2026-09-17_schat-retrieval-generation-multimodal/`

Pending:

1. Existing reranker가 E5 semantic candidates의 MRR을 낮추는 원인을 evaluation-only로 분석
2. 병원 데이터 외부 전송/provider 승인 후 controlled generation Live·semantic entailment 평가
3. TF027 사람 검수와 approved image-dependent gold 확보 후 image/vision 평가
4. MM003 table miss의 gold/row-link 분석

이번 승인 범위는 외부 호출, image 임상 해석, validator 완화 없이
완료했다. Git commit/push는 수행하지 않았다.
