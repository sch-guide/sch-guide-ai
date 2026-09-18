# SCHAT v1.0 최종 검증 결과

- 검증일: 2026-09-18
- 작업 전 공식 안정 버전: `v0.9`
- 최종 판정: **C — v0.9 유지**
- 실제 Groq/Gemini/Vision 호출: **0회**
- 병원 데이터 외부 전송: **0회**
- Production retrieval/embedding/validator 변경: **0건**
- Git commit/tag/push: **0건**

## 1. 결론

Provider Live 실행 전 readiness를 먼저 확인했으나 병원 evidence exact text 외부 전송 정책, provider/model/region/retention 조건과 case별 호출 승인이 명시적으로 존재하지 않았다. Groq/Gemini Live는 실행하지 않았고 최종 provider 판정은 `E — 외부 전송 승인 부재로 Live 미평가`다.

승인 없이 가능한 로컬 검증은 계속했다. 등록된 진정 42 chunks와 수혈 105 chunks를 현재 production의 BM25 + MiniLM + RRF + reranker, parent atomicity, pre/post evidence gate와 SourceUnit prompt contract에 그대로 통과시켜 운영 UAT 36문항을 실행했다. 결과는 **28 PASS / 8 FAIL**이었다.

8건은 unsafe answer가 아니라 모두 pre-LLM fail-closed 차단이다. 5건은 사용자가 문서 안의 제품·대상을 말했지만 질문에 `진정` 또는 `수혈` 주제를 생략해 `domain=unknown`이 됐고, 3건은 hospital로 분류됐지만 broad/product/table 표현의 substantive body support가 부족해 `no_topic_evidence`가 됐다. 이 문제를 해결하려면 선택 문서 문맥과 domain/topic gate를 일반적으로 연결하는 별도 설계가 필요하다. 이번 범위에서는 evidence gate를 느슨하게 하거나 질문·chunk를 하드코딩하지 않았다.

따라서 core UAT가 모두 통과해야 하는 `v1.0`은 물론, core text/table/search/UAT 완료를 요구하는 `v1.0-rc1`도 부여하지 않았다. 공식 안정 기준은 계속 `v0.9`다.

## 2. Provider Live readiness와 결과

| 항목 | 결과 |
|---|---|
| Credential presence | Groq/Gemini 모두 현재 실행 환경에서 확인되지 않음 |
| 병원 evidence 외부 전송 승인 | 없음 |
| Provider policy/model/region/retention 승인 | 없음 |
| Live allowed | false |
| Groq 실제 호출 | 0회 |
| Gemini 실제 호출 | 0회 |
| Retry/fallback provider call | 0회 |
| Provider 판정 | **E — Live 미평가** |

Offline harness의 공통 evidence/schema/instruction과 fail-closed extractive fallback은 그대로 유지된다. Semantic support는 deterministic invariant 이후 별도 entailment가 승인·검증돼야 하며, caller boolean으로 우회할 수 없다. 이번에는 provider output이 없으므로 semantic support Live 결과도 `not_evaluated`다.

## 3. TF027 사람 검수

TF027 검수 HTML과 로컬 Streamlit 화면을 만들었다.

- Figure 경계
- 시작·종료 node
- Node label
- 화살표 방향과 연결
- Decision branch 조건
- Workflow 순서
- 숫자·단위·시간
- Caption/nearby text 관계
- 흐림·가림·잘림
- Production gold 승인

10개 항목은 모두 `unreviewed`다. 값을 자동 추정하거나 저장하지 않았으며 `needs_human_review=true`, `production_gold_approved=false`, aggregate 제외와 vision API 0회를 유지했다.

실행:

```powershell
streamlit run tools/tf027_human_review_app.py
```

## 4. 운영 UAT 36문항

### 결과

| 범위 | 전체 | PASS | FAIL |
|---|---:|---:|---:|
| 진정 | 14 | 12 | 2 |
| 수혈 | 18 | 12 | 6 |
| Out-of-scope | 4 | 4 | 0 |
| 합계 | **36** | **28** | **8** |

Positive case는 실제 production QueryPlan, document filter, local embedding, BM25/RRF/reranker, context expansion, pre/post evidence assessment와 prompt SourceUnit contract까지만 실행했다. Provider 생성은 승인 부재로 실행하지 않았다. Artifact의 `correct_evidence`는 human gold가 아니라 `server_assessment_supported_not_human_gold`로 명시했다.

### 실패 유형

| 유형 | 개수 | Case |
|---|---:|---|
| `hospital_domain_mismatch` | 5 | UAT-S06, UAT-T07, UAT-T09, UAT-T10, UAT-T15 |
| `pre_budget_not_supported:no_topic_evidence` | 3 | UAT-S10, UAT-T06, UAT-T11 |

Out-of-scope 4건은 domain이 `out_of_scope` 또는 `unknown`이어도 hospital admission이 없고 selected evidence/provider call이 0인 안전 계약으로 판정했다. Q006 전용 회귀도 별도로 통과했다.

로컬 검수 화면:

```powershell
streamlit run tools/schat_v1_uat_app.py
```

질문과 SourceUnit 원문은 UAT artifact에 저장하지 않는다.

## 5. Retrieval, table, image 상태

### Retrieval

- Production: BM25 + MiniLM semantic/vector + RRF + 기존 reranker 유지
- E5/Hybrid: evaluation-only 유지
- Production retrieval/embedding/index/reranker 변경 0건
- 선택 문서 문맥 기반 topic admission은 다음 별도 TDD 대상

### Table

| 항목 | 결과 |
|---|---:|
| Structured table records | 5 |
| Rows | 38 |
| Approved cases | 5 |
| Hit@10 | 1.0 |
| MM003 first gold rank | 2 |

Header/row 관계, number/unit와 document/page/table/row citation 계약을 유지했다. Exact table text는 artifact에 저장하지 않았다. Production persistence schema migration은 수행하지 않았다.

### Image

- Figure inventory metadata 유지
- TF027 human review pending
- Approved image-dependent gold 0
- Production answerability 연결 0
- Vision API 0회

## 6. 성능

| 항목 | Mean / P95 또는 값 |
|---|---:|
| Model/catalog load | 2,609.494 ms |
| Local UAT total | 89.007 / 146.481 ms |
| Query planning | 2.717 / 3.901 ms |
| Query embedding | 9.588 / 16.551 ms |
| Retrieval + reranker | 43.245 / 81.394 ms |
| Pre-budget evidence | 4.304 / 8.349 ms |
| Selection + post validation | 52.231 / 28.360 ms |
| RSS after model load | 964,755,456 bytes |
| Table inventory + retrieval 전체 | 10,305.698 ms |
| Provider latency | 미측정 — Live 호출 없음 |
| Answer assembly | 미측정 — Provider Answer 없음 |

Selection/post 구간은 첫 실행 초기화 outlier 때문에 mean이 P95보다 높다. 저장된 값은 36-case local run의 실측이며 provider latency를 포함하지 않는다.

## 7. Validator와 안전 계약

다음을 변경하거나 완화하지 않았다.

- Facet-slot과 selection limit 16
- Parent atomicity
- PromptCoverage/AnswerCoverage
- Exact citation/source sentence
- Number/unit/time
- Condition/contraindication/negation/action
- Branch/phase/source order/duplicate evidence
- `validate_answer()` 전체
- Q006 provider zero-call
- Controlled-generation fail-closed fallback

## 8. 검증과 보안

| 검증 | 결과 |
|---|---:|
| Final validation 신규 테스트 | 16 passed |
| Provider/controlled generation/final contracts | 38 passed |
| Retrieval·진정·수혈 집중 회귀 | 99 passed, 2 skipped |
| Table/Multimodal UI | 10 passed |
| Citation·presentation·parent atomicity·SourceUnit | 76 passed |
| Full pytest | **561 passed, 4 skipped, 7 warnings** |
| Ruff | PASS |
| `git diff --check` | PASS |
| Code review | actionable defect 0 |

최종 artifact 감사:

- exact hospital source match 0
- forbidden artifact key 0
- secret marker 0
- API key/Authorization/full prompt/raw response 0
- 질문/SourceUnit exact text 저장 0
- 병원 데이터 외부 전송 0

## 9. 변경 범위

### 추가한 evaluation/test 파일

- `tools/schat_v1_final_validate.py`
- `tools/schat_v1_uat_app.py`
- `tools/tf027_human_review_app.py`
- `tests/test_schat_v1_final_validation.py`
- 최종 plan/progress/result/current-source 문서와 raw-free artifact

### 변경하지 않은 production 영역

- `mvp/` production retrieval, query, evidence, generation, validator, UI
- Production embedding과 dependency
- Catalog/chunk/vector
- Provider adapter/API key 처리
- Table/image production schema

## 10. Pending과 버전 판정

Pending:

1. 선택 문서 문맥을 안전하게 domain/topic에 연결해 운영 UAT 8건 해결
2. 외부 전송 정책과 provider 조건 승인 후 제한 Live 비교
3. Semantic entailment 최종 검증
4. TF027 1차·2차 사람 검수
5. 36/36 운영 UAT 재검증

최종 판정은 **v0.9 유지**다. 새 안정 복구 기준점 저장 준비는 **NO**다. 현재 변경은 평가·검수 도구로는 회귀와 보안 검사를 통과했지만, v1.0 core UAT가 8건 실패했으므로 새 v1 계열 안정 tag를 제안하지 않는다.

## 11. 산출물

`artifacts/2026-09-18_schat-v1-final-validation/`

- `provider_readiness.json`
- `uat_summary.json`
- `uat_results.json`
- `performance.json`
- `table_summary.json`
- `tf027_status.json`
- `tf027_review.html`
- `security_audit.json`
- `test_results.json`
- `code_review.json`
- `summary.json`
- `review.html`
