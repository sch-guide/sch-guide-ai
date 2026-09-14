# Broad procedure AnswerCoverage(B/C) 구현 결과

- 작성일: 2026-09-14
- 상태: **production 구현 및 오프라인·Mock·전체 회귀 통과 / 실제 Groq Live 미실행**
- 실제 Groq 호출: **0회**

## 1. 변경 이유

기존 AnswerCoverage는 broad procedure 질문에서도 required group과 branch의 대표 unit만 선택하면 통과할 수 있었습니다. 최근 Q002 Live에서 g1~g5를 각 1개씩 선택한 5-unit 응답이 이 구조를 통과했지만, 임상 단계와 행동 범위가 충분하지 않았고 post-reconstruction에서 `duplicate_evidence`로 차단됐습니다. 이번 변경은 group 대표성 외에 phase, phase별 action family, action diversity와 source order를 서버가 검증하도록 강화했습니다.

## 2. ProcedureAnswerRequirement 구조

`ProcedureAnswerRequirement`는 요청마다 QueryPlan과 실제 prompt catalog에서 계산합니다. production에서는 Q002 gold, stage label, chunk ID, per-group expected count를 읽지 않습니다.

검증 필드는 다음과 같습니다.

- broad procedure 여부
- required group과 branch
- `before`, `during`, `after` phase slot
- branch·phase별 action-family slot
- action-family diversity
- source order
- 최대 16개 안에서 충족 가능한지 나타내는 capacity

Q002 오프라인 catalog는 40 units 중 21개가 selectable이며, requirement의 capacity witness는 11개로 selection 한도 16 안에 있습니다. AI wire contract는 `AI_VERSION=18`, `PROMPT_EVIDENCE_SCHEMA_VERSION=5`, `RESPONSE_SELECTION_SCHEMA_VERSION=4`입니다. 출력 한도 2048, 요청 token budget 5120, selection·statement 최대 16은 유지했습니다.

## 3. Under-selection 차단 결과

required group 5개에서 각각 selectable unit 1개를 고르는 가능한 조합 72개를 전수 검사했습니다. 72개 모두 `selection_missing_phase`로 차단됐습니다. group/branch 대표만 갖춘 5-unit 선택은 더 이상 broad procedure 답변으로 통과하지 않습니다.

21 selectable units 전체 선택은 기존 validator의 `selection_limit`으로 차단됐습니다. 자동 보충, 자동 재정렬, 자동 dedup은 추가하지 않았습니다.

## 4. Duplicate 의미와 branch metadata 결과

기존 duplicate signature를 `(document_id, server branch, clean(cited text).casefold())`로 유지했습니다.

- 동일 source unit ID 반복: `selection_duplicate_id`
- 같은 문서·같은 branch·정규화된 exact text 동일: `duplicate_evidence`
- 문서가 다름: duplicate 아님
- 실제 branch가 다름: duplicate 아님
- 의미만 같고 text가 다름: duplicate 아님

citation 축약으로 branch heading이 사라질 때 quote만 보고 branch를 다시 추론하면 false duplicate가 발생했습니다. post-reconstruction reassessment에 서버가 이미 가진 evidence-group branch metadata를 전달해 이를 막았습니다. duplicate 정의와 validator 강도는 변경하지 않았습니다.

## 5. Mock 결과

Q002 Mock은 다음 조건을 모두 통과했습니다.

- selected evidence: 12 chunks
- pre/post required gold recall: 10/10
- source-unit gold recall: 10/10
- selected source units: 14
- group count: g1=1, g2=1, g3=1, g4=6, g5=5
- adult/pediatric 및 required phase/action coverage 충족
- reconstruction: 14 statements
- exact text/quote 및 citation coverage: 100%
- citation assessment: `supported`
- duplicate evidence: 없음
- source order reversal: 0
- unsupported number/unit/condition/negation/action: 0
- server-derived answerable: true

Q006은 catalog 0, transport 0회, 고정 근거 부족 응답을 유지했습니다.

전체 검증은 집중 테스트 36 passed, 전체 테스트 305 passed·1 skipped, Ruff 통과, `git diff --check` 통과입니다. fastembed의 pooling 변경 안내 warning 2건만 있었으며 실패는 아닙니다.

## 6. Q002 Live 결과

Live 전 기술 게이트는 모두 통과했습니다. 실제 실행 명령은 외부 요청 전에 자동 승인 검토에서 거절됐습니다. 따라서 HTTP status, 반환 모델, finish reason, latency, token usage, parse 결과와 server answerable은 측정되지 않았습니다.

- Q002 실제 Groq 호출: 0회
- raw response 저장: 없음
- 전체 prompt 저장: 없음
- API key·Authorization 저장: 없음
- exact SourceUnit 원문 로그 저장: 없음
- 판정: `LIVE_BLOCKED_BEFORE_REQUEST`

## 7. Q001~Q006 결과

Q002 Live가 완주한 경우에만 Q001/Q003/Q004/Q005를 각 최대 1회 실행하도록 제한 도구를 만들었습니다. Q002가 실행되지 않았으므로 pilot도 실행하지 않았습니다.

- Q006: Mock zero-call 통과, Live runtime check 미실행
- Q002: Live 미실행
- Q001/Q003/Q004/Q005: 조건부 pilot 미실행
- 전체 실제 Groq 호출: 0회

## 8. 성능·latency·token

오프라인 prompt reservation은 4709/5120 tokens, headroom은 411 tokens입니다. 필요한 최소 headroom `max(256, reservation × 8%)`는 377 tokens이므로 budget gate를 통과했습니다.

Mock과 회귀 테스트 외에 실제 API 요청이 없으므로 Live latency와 completion token은 없습니다.

## 9. 아직 남은 한계

phase와 action family는 문서에 명시된 일반 표지와 동작어를 기반으로 계산합니다. 표가 비정상적으로 추출되거나 단계 표지가 생략된 문서는 `unspecified`가 늘어나 안전하게 답변을 차단할 수 있습니다. 이는 임상 의미를 추론해 채우지 않는 설계의 보수적 한계입니다.

또한 실제 provider가 facet contract를 보고 14-unit 수준의 충분한 선택을 반환하는지는 Live 1회가 실행되기 전까지 확정할 수 없습니다. 이번 결과는 서버 validator와 Mock 완주를 증명하지만 실제 Groq 선택 품질을 증명하지는 않습니다.

현재 저장소는 기존 rebase가 중단된 detached HEAD 상태이며 `mvp/search_trace.py`, `tests/test_retrieval_diagnostics.py`가 index 기준 충돌 상태입니다. 이번 작업에서는 stage, rebase, commit, reset, clean을 수행하지 않았습니다.

## 10. 다음 단계

병원 지침 근거 12 chunks를 Groq에 1회 전송하는 Q002 Live에 대해 사용자가 명시적으로 다시 승인하면, 이미 생성한 제한 도구로 Q006 zero-call 확인 후 Q002를 정확히 1회 실행할 수 있습니다. Q002가 모든 기준을 통과할 때만 남은 4개 pilot을 각 최대 1회 실행합니다. 실패하거나 공통 provider/auth/schema 오류가 발생하면 즉시 중단하며 재호출하지 않습니다.

## 산출물

- [Offline facet report](../../artifacts/2026-09-14_rag-procedure-answer-coverage/offline_facet_report.json)
- [Duplicate contract report](../../artifacts/2026-09-14_rag-procedure-answer-coverage/duplicate_contract_report.json)
- [Mock report](../../artifacts/2026-09-14_rag-procedure-answer-coverage/mock_report.json)
- [Live report](../../artifacts/2026-09-14_rag-procedure-answer-coverage/live_report.json)
- [Review HTML](../../artifacts/2026-09-14_rag-procedure-answer-coverage/review.html)
