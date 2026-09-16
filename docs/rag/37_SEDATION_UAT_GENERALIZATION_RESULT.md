# 진정간호 UAT 일반화 결과

- 작업일: 2026-09-16
- 기준 브랜치: `boha-rag`
- 기준 커밋: `f4d994e6ea7cd356cf570657e5423ea3f01f6f94`
- 실제 Groq 호출: **0회**
- 목적: 45개 진정간호 실사용 UAT의 기대 동작을 바로잡고, 기존 28개 실패를 질문별 예외가 아닌 공통 규칙으로 줄인다.

## 1. 결론

45개 UAT가 모두 offline 검색·근거 gate를 통과했다. 독립 질문 43개와 문맥 의존 후속 질문 2개를 구분했으며, Q006과 외부 주제는 provider 호출 없이 차단된다.

- 개선 전: 17 PASS / 28 FAIL
- 개선 후: **45 PASS / 0 FAIL**
- Q006 transport: **0회**
- 전체 pytest: **420 passed, 1 skipped**
- Ruff: 통과
- `git diff --check`: 통과

이 결과는 검색·근거 검증 결과다. 실제 Groq 응답 품질을 재평가한 결과가 아니며 이번 작업에서는 Live API를 호출하지 않았다.

## 2. 기대 동작 재분류

| 기대 모드 | 건수 | 판정 기준 |
|---|---:|---|
| 독립 질문으로 답변 | 39 | 질문 자체에 등록 topic과 필요한 qualifier가 있다. |
| 독립 질문으로 기권 | 4 | 명백한 외부 주제로 검색과 provider 호출을 모두 차단한다. |
| 이전 문맥이 필요한 follow-up | 2 | FACT02, COMP02. 독립 입력에서는 topic을 추측하지 않고, 이전 진정간호 문맥이 있을 때만 topic을 복원한다. |
| clarification | 0 | 현재 45개 fixture에는 별도 명확화 문항이 없다. |

FACT02와 COMP02는 독립 질문에서 evidence를 선택하지 않는 것을 PASS로 평가했다. 별도 회귀 테스트에서 이전 대화에 `진정간호` topic이 있으면 `hospital` domain과 canonical topic이 복원되는 것도 확인했다.

## 3. 기존 28개 실패 재분류

각 실패의 질문, 대표 질문, 실제 QueryPlan, pre/post-budget reason, retrieval evidence 존재 여부는 `failure_categories.json`에 저장했다.

| 공통 실패 유형 | 개선 전 | 개선 후 |
|---|---:|---:|
| intent classification | 2 | 0 |
| topic/canonical topic | 2 | 0 |
| summary | 5 | 0 |
| comparison | 3 | 0 |
| fact-specific | 4 | 0 |
| branch qualifier | 4 | 0 |
| temporal qualifier | 3 | 0 |
| follow-up/context | 2 | 0 |
| semantic block / parent selection | 2 | 0 |
| out-of-scope | 1 | 0 |
| other | 0 | 0 |

## 4. 구현 내용

### Query grammar

- 목적: `왜 해`, `하는 이유`, `목적`을 purpose로 분류한다.
- 요약: `대해 알려줘`, `전체적으로 설명`, `간단히 정리`를 summary로 분류한다.
- 비교: `어떻게 달라`, `차이`, `비교`를 comparison으로 분류한다.
- 준비·주의: 확인사항, 준비할 것, 이상 증상, 모니터링 표현을 인식한다.
- 동사형 topic: `진정하는`을 등록 topic `진정간호`에 연결한다.
- 분기 표기: `성인과 소아`, `성인/소아`, `성인·소아`, `성인‧소아`를 같은 branch qualifier로 처리한다.
- 외부 주제: `축구 경기 결과`를 hospital topic보다 먼저 차단한다.

### Temporal qualifier

`전에`, `하기 전`, `시행 중`, `하는 동안`, `하고 나서`, `시행 후`를 bounded 규칙으로 인식한다. `전 직원`, `중환자`, `후배`, `오전`은 임상 단계로 오인하지 않는다.

### Summary / comparison / fact evidence

- Summary는 제목만으로 열지 않고 substantive body와 완전한 parent를 요구한다.
- Comparison은 성인·소아 양쪽 branch, 완전한 parent, 같은 요청 aspect의 직접 본문 지지를 요구한다.
- Fact는 간격, 동의서, 산소포화도, 활력징후, 관찰, 투약, 회복, 입원실 이동 qualifier가 실제 본문에 있어야 한다.
- 문서 metadata는 topic 연결에만 사용하며 임상 qualifier를 대신하지 않는다.

### Branch-aware parent atomicity

- 단일 branch 질문은 해당 branch와 common parent만 유지한다.
- comparison과 broad summary는 같은 canonical topic 안에서 성인·소아 parent를 모두 원자적으로 포함한다.
- parent 전체가 context limit 안에 들어오지 않으면 부분 parent를 넣지 않는다.
- 원문 순서와 exact chunk identity를 유지하며 의미 기반 dedup, 자동 재정렬, SourceUnit 자동 보충은 하지 않는다.

### Follow-up / out-of-scope

- topic이 없는 FACT02와 COMP02는 독립 질문에서 `unknown`으로 남고 evidence를 열지 않는다.
- 이전 대화에서 등록 topic이 제공되면 기존 follow-up query 결합으로 canonical topic을 복원한다.
- Q006과 명백한 외부 주제는 selected evidence 0, provider transport 0회를 유지한다.

## 5. 변경하지 않은 계약

다음은 수정하지 않았다.

- BM25 점수와 tokenizer
- embedding model
- RRF
- reranker
- Facet-slot SourceUnit Selection
- selection limit 16
- AnswerCoverage 의미
- `validate_answer()`
- citation, number, unit, condition, negation, unsupported action validator
- presentation sidecar
- retry, fallback, web search

버전 상수도 그대로 유지했다: AI 19, Prompt Evidence Schema 6, Response Selection Schema 5, Search 14, Chunk 4. 응답 계약이 바뀌지 않았기 때문이다.

## 6. 회귀 검증

- 진정간호 UAT/query/temporal/follow-up 집중 테스트: 26 passed
- Q002 required facet 41, Gold reconstruction, budget, Q006 zero-call: 4 passed
- Q001/Q003/Q004 및 Q002/Q006 offline 경계: 5 passed
- Q001~Q005 answerable 표현 변형 pre/post budget: 14 passed
- context/evidence/retrieval/RAG 계약: 96 passed
- 전체: **420 passed, 1 skipped, 5 warnings**
- Ruff: 통과
- `git diff --check`: 통과

Q002의 required facet 41개, selection limit 16, 기존 Mock reconstruction은 유지된다. Q006은 전체 평가에서 실제 transport 0회다.

## 7. UAT 유형별 결과

| 유형 | 결과 |
|---|---:|
| procedure | 5 / 5 PASS |
| purpose | 4 / 4 PASS |
| preparation | 5 / 5 PASS |
| cautions | 5 / 5 PASS |
| summary_broad | 5 / 5 PASS |
| branch_specific | 7 / 7 PASS |
| fact_specific | 6 / 6 PASS |
| comparison | 4 / 4 PASS |
| negative_out_of_scope | 4 / 4 PASS |

## 8. 산출물

`artifacts/2026-09-16_rag-sedation-uat-generalization/`

- `uat_report.json`: 45개 질문의 raw-text-free 진단 결과
- `uat_results.csv`: 사람이 검토할 수 있는 질문별 표
- `failure_categories.json`: 기존 28개 실패와 개선 후의 유형별 비교
- `test_results.json`: 테스트, 버전, 호출 수 기록
- `review.html`: 질문별 PASS/FAIL 검토 화면

산출물에는 병원 원문 전체, SourceUnit exact text, API key, Authorization header, 전체 prompt, raw provider response를 저장하지 않았다.

## 9. 코드 검토 결과와 제한사항

차단할 코드 결함은 발견되지 않았다. branch parent 확장은 등록 canonical topic과 이미 검색된 문서 범위 안에서만 수행하며, qualifier는 본문 직접 지지를 요구한다.

현재 결과는 하나의 진정간호 평가 corpus에 대한 offline UAT다. 다른 구조의 실제 병원 문서를 추가하면 동일한 branch/parent metadata 품질을 별도로 확인해야 한다. 또한 Live Groq 답변의 문장 품질은 이번 결과 범위에 포함되지 않는다.
