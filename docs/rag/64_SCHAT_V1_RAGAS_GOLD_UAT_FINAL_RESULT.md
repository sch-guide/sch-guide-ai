# SCHAT v1.0 RAGAS·Gold·UAT 통합 최종 결과

- 검증일: 2026-09-18
- 작업 전 공식 안정 버전: `v0.9`
- 최종 제안 버전: **`v1.0-rc1`**
- 실제 Groq/Gemini/vision/LLM judge 호출: **0회**
- 병원 데이터 외부 전송: **0회**
- Git commit/tag/push: **0회**

## 1. 결론

운영 UAT의 8개 실패는 검색 점수 문제가 아니라, 사용자가 이미 선택한 지침 문맥이
QueryPlan의 domain/topic admission에 전달되지 않던 문제였다. 단일 문서를 사용자가 명시적으로
선택한 경우에만 그 문서의 canonical topic을 검색 보조 신호로 쓰고, 실제 검색 결과가 모두 그
문서의 substantive evidence인 경우에만 `hospital` admission을 허용하도록 수정했다.

선택 문서만으로 hospital 질문으로 만들지 않으며, 명백한 out-of-scope는 이 경로에 들어오지 않는다.
그 결과 동일 36문항 UAT는 **28/36 → 36/36**으로 개선됐고 out-of-scope 4건과 Q006은 provider
0회를 유지했다. 같은 수혈 90문항의 retrieval/RAGAS ID 점수는 모든 지표가 수정 전과 동일하다.

Provider Live, LLM judge, TF027 이미지 사람 검수, 운영 positive Gold의 사람 승인이 남아 있으므로
정식 `v1.0`이 아니라 **승인 대기형 `v1.0-rc1`**로 판정한다. 공식 안정 tag는 계속 `v0.9`다.

## 2. Baseline

| 항목 | 수정 전 |
|---|---:|
| 운영 UAT | 28 PASS / 8 FAIL |
| `hospital_domain_mismatch` | 5 |
| `pre_budget_not_supported` / `no_topic_evidence` | 3 |
| Out-of-scope provider calls | 0 |
| Table approved Hit@10 | 1.0 |

8건은 unsafe answer가 아니라 pre-LLM fail-closed 차단이었다.

## 3. 무엇을 수정했는가

### Document-context admission

- Streamlit sidebar에서 `전체 등록 지침` 또는 단일 지침을 명시적으로 선택한다.
- 기본값은 전체 문서이며 기존 질의 동작을 바꾸지 않는다.
- 단일 선택 문서의 ID와 title-derived canonical topic을 QueryPlan에 전달한다.
- out-of-scope/clarification은 문서 선택과 무관하게 기존 차단을 유지한다.
- unknown domain은 검색된 substantive evidence가 단일 선택 문서에만 속할 때만 admission 후보가 된다.
- 먼저 원 질문 topic으로 기존 evidence gate를 그대로 실행한다.
- `no_topic_evidence`이면서 구조화된 임상 intent일 때만 선택 문서 topic으로 한 번 재평가한다.
- threshold, BM25, MiniLM, RRF, reranker, parent atomicity와 validator는 변경하지 않았다.

### Table route

운영 UAT의 table/mixed 질문은 기존 evaluation-only structured table index도 함께 확인한다. 표 row가
검색됐다는 사실은 `candidate_found_not_human_gold`로만 기록하며 Gold 정답으로 자동 승격하지 않는다.
UAT-T06과 UAT-T11은 이 경로로 안전하게 처리됐다.

## 4. After UAT

| 범위 | 전체 | PASS | FAIL |
|---|---:|---:|---:|
| 진정 | 14 | 14 | 0 |
| 수혈 | 18 | 18 | 0 |
| Out-of-scope | 4 | 4 | 0 |
| 합계 | **36** | **36** | **0** |

| 기존 FAIL | Before | After |
|---|---|---|
| UAT-S06 | domain mismatch | text supported |
| UAT-S10 | no topic evidence | text supported |
| UAT-T06 | no topic evidence | table candidate supported |
| UAT-T07 | domain mismatch | text supported |
| UAT-T09 | domain mismatch | table candidate supported |
| UAT-T10 | domain mismatch | text supported |
| UAT-T11 | no topic evidence | table candidate supported |
| UAT-T15 | domain mismatch | text supported |

## 5. Retrieval Metrics

동일 `transfusion-retrieval-v3`, 수혈 105 chunks, 90문항, approved positive 78개, 동일 Gold와
Top-K로 수정 후 재실행했다.

| Metric | Before | After | Delta |
|---|---:|---:|---:|
| Hit@1 | 0.3974 | 0.3974 | 0 |
| Hit@3 | 0.6538 | 0.6538 | 0 |
| Hit@5 | 0.7949 | 0.7949 | 0 |
| Hit@10 | 0.8846 | 0.8846 | 0 |
| MRR | 0.5602 | 0.5602 | 0 |
| Recall@1 | 0.3141 | 0.3141 | 0 |
| Recall@3 | 0.5742 | 0.5742 | 0 |
| Recall@5 | 0.7170 | 0.7170 | 0 |
| Recall@10 | 0.8379 | 0.8379 | 0 |
| Precision@1 | 0.3974 | 0.3974 | 0 |
| Precision@3 | 0.2607 | 0.2607 | 0 |
| Precision@5 | 0.1974 | 0.1974 | 0 |
| Precision@10 | 0.1192 | 0.1192 | 0 |

Production retrieval은 **BM25 + MiniLM semantic/vector + RRF + existing reranker** 그대로다.

## 6. RAGAS

| Metric | 결과 | 상태 |
|---|---:|---|
| ID-based Context Precision | **0.1192** | 완료 |
| ID-based Context Recall | **0.8379** | 완료 |
| Faithfulness | pending | generated answer와 LLM judge 승인 필요 |
| Answer Relevancy | pending | generated answer와 LLM judge 승인 필요 |
| LLM Context Precision/Recall | pending | 외부 LLM judge 승인 필요 |

RAGAS ID 점수는 retrieval baseline과 동일하다. 평균 하나로 합격시키지 않고 safety/Gold/UAT를
별도 축으로 유지한다.

## 7. Gold Evaluation

- 기존 수혈 retrieval Gold: approved positive 78, human-review 제외 1, negative 11
- 운영 UAT 36건 모두 label record 생성
- 승인 완료된 운영 abstention Gold: **4/4 통과**
- Positive 운영 UAT mapping: 32건 모두 `provisional_needs_human_review`
- TF027: `image_needs_human_review`
- 새 mapping은 retrieval 결과로 자동 확정하지 않았고 aggregate에서 제외했다.
- Positive critical facts/number/unit/time/condition 검증은 사람 label 및 generated answer 전이라 pending이다.

## 8. Table

| 항목 | 결과 |
|---|---:|
| Structured tables | 5 |
| Search rows | 38 |
| Approved table cases | 5 |
| Hit@1 / Hit@3 / Hit@5 / Hit@10 | 0.4 / 1.0 / 1.0 / **1.0** |
| Recall@10 | **1.0** |

MM003, header/row, number/unit, citation과 local UI 계약은 유지됐다. Exact table text는 artifact에
저장하지 않았다.

## 9. Image와 Provider

- TF027 checklist 10개: 전부 unreviewed
- `needs_human_review=true`
- `production_gold_approved=false`
- Vision call 0회
- Provider Live: 외부 전송 정책/provider/model/region/retention/case/credential 승인 부재
- Groq 0회, Gemini 0회, LLM judge 0회
- Controlled generation은 semantic support 미확정 시 verified extractive answer로 fallback한다.

## 10. Safety와 성능

- Out-of-scope 4/4 PASS, provider 0회
- Q006 zero-call 유지
- Validator 완화 0건
- 병원 데이터 외부 전송 0회
- Local UAT mean/p95: 91.944 / 132.361 ms
- Model/catalog load: 2,515.029 ms
- Retrieval+reranker mean/p95: 41.367 / 76.818 ms
- Process RSS after load: 1,184,624,640 bytes
- Artifact audit: 43 files, exact source 0, forbidden key 0, secret marker 0

## 11. 검증

- Domain/topic + Gold TDD: 통과
- Integrated focused regression: **168 passed, 4 warnings**
- Full pytest: **577 passed, 4 skipped, 9 warnings**
- Ruff: 통과
- `git diff --check`: 통과
- Code review: actionable correctness/safety/regression defect 0
- Artifact security audit: 통과

Warning 9건은 기존 FastEmbed MiniLM mean-pooling 안내다. Production embedding/dependency는 변경하지 않았다.

## 12. 변경 및 비변경

Production 변경:

- `mvp/query.py`: 명시적 단일 document context와 canonical topic metadata
- `mvp/evidence.py`: evidence-backed scoped admission, 기존 gate 재사용
- `mvp/app.py`: 사용자 document scope 선택과 QueryPlan 전달

Evaluation/test 추가:

- `tools/schat_v1_ragas_gold_uat_evaluate.py`
- `tools/schat_v1_final_validate.py`
- `tests/fixtures/schat_v1_operational_gold.json`
- 관련 TDD와 raw-free artifact/dashboard

변경하지 않음:

- BM25, MiniLM, RRF, reranker와 embedding vectors
- Facet-slot, selection limit 16, PromptCoverage, AnswerCoverage
- 모든 임상/citation/source-order validator
- Parent atomicity와 presentation sidecar
- Provider adapter/API key/dependencies
- `v0.9` tag

## 13. 최종 판정

로컬 text/table/search/UAT와 안전 회귀는 통과했다. 다만 Provider Live/LLM judge, TF027 사람 검수,
운영 positive Gold·critical facts 사람 승인이 남아 있으므로 최종 판정은 **`v1.0-rc1`**이다.

새 복구 기준점 저장 준비는 **YES**다. 단 실제 commit/tag/push는 사용자 별도 승인 전까지 수행하지 않는다.

검수 artifact: `artifacts/2026-09-18_schat-v1-ragas-gold-uat-final/`
