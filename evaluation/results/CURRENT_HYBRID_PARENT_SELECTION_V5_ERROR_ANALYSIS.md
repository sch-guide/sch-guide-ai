# Current Hybrid Parent Selection v5 오류 분석

저장된 v5·v4 JSON, 현재 코드와 실행 커밋 간 diff만 읽었다. 검색·API·Baseline을 재실행하거나 코드·JSON을 수정하지 않았다. v5의 서비스 소스 해시는 현재 파일과 모두 일치한다.

## 1. 문항별 결과

아래 M은 JSON에 저장된 동일한 메시지의 약칭이다.

> 앱에 설정된 최근 24시간 AI 질문 횟수에 도달했습니다. 원문 검색은 가능합니다. (AI_LIMIT_CALLS)

| ID | 상태 | 오류 발생 단계 | error type | error message | Groq 호출 여부 |
| --- | --- | --- | --- | --- | --- |
| TRF-001 | error | after_budget 이후 로컬 quota 예약 | GuideError | M | false |
| TRF-002 | error | after_budget 이후 로컬 quota 예약 | GuideError | M | false |
| TRF-003 | error | after_budget 이후 로컬 quota 예약 | GuideError | M | false |
| TRF-004 | error | after_budget 이후 로컬 quota 예약 | GuideError | M | false |
| TRF-005 | error | after_budget 이후 로컬 quota 예약 | GuideError | M | false |
| TRF-006 | error | after_budget 이후 로컬 quota 예약 | GuideError | M | false |
| TRF-007 | no_hits | context selection | 없음 | 없음 | 호출 경로 미도달 |
| TRF-008 | error | after_budget 이후 로컬 quota 예약 | GuideError | M | false |
| TRF-009 | error | after_budget 이후 로컬 quota 예약 | GuideError | M | false |
| TRF-010 | error | after_budget 이후 로컬 quota 예약 | GuideError | M | false |

9건 모두 exception class, message가 동일하며 retry_after_seconds=null이다. TRF-007은 generation_trace가 빈 사전이라 llm_called 필드가 없지만, no_hits 분기에서 생성 호출을 하지 않는 코드 경로와 일치한다. 실제 Groq 호출은 전체 0건이다.

## 2. 공통 예외의 정확한 위치와 조건

- 예외 클래스: `mvp.settings.GuideError`.
- 발생 위치: `mvp/ai.py:243`, `Quota.reserve()`.
- 조건: `calls >= settings.daily_limit or own >= settings.user_daily_limit`.
- calls: 최근 24시간 reservation 전체 개수.
- own: 같은 user_hash의 최근 24시간 reservation 개수.
- 호출 경로: `evaluation/run_bm25_baseline.py:192`의 `run_case()` → `mvp.ai.generate()` → `quota.reserve(settings, user_id, reserved)`.

9개 질문 모두 context_ready, pre_llm_assessment=supported, budget_assessment=supported까지 진행했다. 그 뒤 요청 사용량을 예약하려다가 공통 제한 조건에서 예외가 발생했다. 저장 stage가 after_budget인 이유는 quota 전용 stage를 따로 기록하지 않고, API 요청 직전에야 llm_request로 변경하기 때문이다.

이 조건은 숫자·토큰 입력 구조 오류나 질문 문구의 오류가 아니다. Groq 서버에 전송하기 전의 로컬 guard다. Groq의 HTTP 429, 모델 미지원, 인증 오류로 해석하면 안 된다.

### 어떤 설정·사용 기록이 공유되는가?

결과 metadata의 quota_scope는 `evaluation/.runtime/usage.sqlite3`다. 공통 실행기 `run_baseline()`은 같은 경로를 사용하며 `run_case()`는 버전과 관계없이 사용자 ID `bm25-baseline-v1`을 전달한다. 따라서 새 결과 파일 v5를 만들었다고 사용량 집계가 초기화되거나 실행별로 분리되지 않는다. 서비스 사용량 DB와는 분리되지만 평가 버전끼리는 같은 예약 기록을 공유한다.

저장 JSON에는 당시 calls/own 및 실제 daily_limit/user_daily_limit 값이 없어 둘 중 어느 한도에 먼저 도달했는지와 누적 횟수는 확정할 수 없다. 이번에는 usage DB나 .env를 추가로 읽지 않았다. 기본값을 실제 실행값으로 대입하지 않는다. 반복 진단의 누적 사용량은 구조상 가능한 원인이지만 특정 과거 실행이 얼마를 사용했는지까지 단정하지 않는다.

## 3. v5 변경 파일과의 관계

| 파일 | 역할 | 이번 9개 예외와 관계 |
| --- | --- | --- |
| mvp/context.py | 완전한 parent 조합 선택 | 9건에서 context_ready를 반환; 예외 발생 지점 아님 |
| mvp/retrieval.py | 기존 plan·trace를 expand_context에 전달 | 호출부 변경은 인자 전달뿐; 반환형 불일치 기록 없음 |
| mvp/search_trace.py | context_selection reason을 최종 trace에 반영 | context_ready/no_complete_required_parent 기록 완료; trace 예외 아님 |
| mvp/ai.py | 기존 quota 검사 및 API 호출 | Quota.reserve에서 공통 예외 발생; v4→v5 diff에서 수정되지 않음 |

분류: **F. 기타 — 기존 로컬 24시간 AI 질문 횟수 제한**. A/B/C/D 오류라는 증거는 없고 E(Groq/API 오류)도 아니다.

## 4. v4에서는 보이지 않고 v5에서 발생한 이유

비교 커밋:

- v4: d57af7a41dbe85afc6b6a486be4605be60a32315
- v5: 85e33d8316d78ba8de29718a723555b9a722bc9e

diff는 context.py, retrieval.py, search_trace.py 및 회귀 테스트 변경이다. quota 코드는 바뀌지 않았다. v4는 관련 부모를 전부 합쳐 12개를 넘으면 반환을 포기했다. v5는 완전한 부모 조합 중 기존 질문 요구와 evidence 검사를 만족하는 예산 내 조합을 선택한다.

| ID | v4 상태 | v5 선택 청크 수 | v5 context reason |
| --- | --- | ---: | --- |
| TRF-001 | no_hits | 8 | context_ready |
| TRF-002 | no_hits | 3 | context_ready |
| TRF-003 | no_hits | 10 | context_ready |
| TRF-004 | no_hits | 11 | context_ready |
| TRF-005 | no_hits | 9 | context_ready |
| TRF-006 | no_hits | 2 | context_ready |
| TRF-007 | no_hits | 0 | no_complete_required_parent |
| TRF-008 | abstained | 5 | context_ready |
| TRF-009 | no_hits | 10 | context_ready |
| TRF-010 | no_hits | 2 | context_ready |

v4의 9개 no_hits는 quota 단계에 도달하지 않았다. v5에서는 이 중 8개가 새로 그 단계에 도달했다. TRF-008은 v4 당시 API까지 진행했지만 v5 시점에는 동일 quota 한도에 걸렸다. 당시 사용 기록과 설정이 JSON에 없으므로 두 실행 사이의 정확한 quota 변화량은 확인 불가다.

따라서 “v5 구현이 새로운 공통 exception 버그를 만들었다”는 결론은 기록과 다르다. 더 뒤 단계로 진행하면서 기존 사용량 제한이 드러난 것이다. context_ready가 정답 품질을 보증하거나 TRF-004 표 결손을 해결했다는 의미도 아니다.

## 5. TRF-007 no_hits 별도 분석

- seed_count=6: 검색 seed 존재.
- parent 후보 5개, 청크 수 각각 6, 2, 2, 4, 1; 전체 합계 15.
- context limit=12, selected_chunk_count=0.
- context_selection.reason 및 최종 retrieval reason: `no_complete_required_parent`.
- generation_trace={}; API 호출 및 quota 예약까지 도달하지 않음.

현재 조합 선택 코드는 완전성, 충돌 근거 보존, 질문의 supported-term 보존, assess_evidence를 통과한 조합이 예산만 초과할 때 `context_budget_exceeded`로 구분한다. 이번 reason은 그 경우가 아니다. **전체 후보가 15개라는 사실만으로 예산 초과가 직접 원인이라고 하면 안 된다.** 현 정책상 최상위 부모를 포함하면서 요구사항 검사를 통과하는 완전한 조합을 찾지 못한 경로다.

조합별 탈락 사유는 저장하지 않아 완전성/요구 용어/충돌 보존/기존 evidence 검사 중 정확히 어느 검사가 막았는지 더 좁힐 수 없다. 검색 재실행이나 추정으로 보충하지 않았다. 9개 AI_LIMIT_CALLS와 별개 문제다.

## 6. 수정·rollback 판단

이번 9개 오류를 해결하기 위해 parent 선택 코드를 되돌리거나 반환형을 고칠 근거는 없다. 로컬 quota 차단은 의도된 동작이다. 따라서 “공통 구현 버그 1개를 수정하면 된다”는 전제도 맞지 않는다.

최소 개선 후보는 평가 실행기의 **읽기 전용 quota 사전 점검**이다. `evaluation/run_bm25_baseline.py`의 실행 전 경로에서 남은 앱/사용자 호출 한도를 안내하고, 부족할 때 10문항을 진행하기 전에 명확히 알리는 방향이다. 필요하다면 `mvp.ai.Quota.summary()`의 기존 조회 기능을 검토할 수 있다. 이번에는 구현하지 않았다.

실행 한도 회복은 기존 사용 기록의 24시간 만료 또는 명시적으로 승인된 평가용 한도 정책으로 다뤄야 한다. 기록 삭제, quota 검사 우회, 임의의 한도 증가는 제안된 최소 버그 수정에 포함되지 않는다. TRF-007의 근거 조합 실패는 별도로 진단해야 한다.

## 원본 보존

- v4 SHA-256: e81d1a6b9633c73570abaa43e9669e1ba2b7b8fff76b724560abe1b7247ab325
- v5 SHA-256: caeade86f2f55d8fc942aa620e2b7b15f73a5c294ffbc688f00a8067e34518bb

## 9개 error의 공통 원인

`mvp/ai.py:243 / Quota.reserve()`의 최근 24시간 앱 또는 사용자 질문 횟수 제한. 동일한 GuideError(AI_LIMIT_CALLS)이며 Groq 호출 이전에 발생했다.

## TRF-007 no_hits 원인

seed 6개는 있었으나 현재 요구사항 검사를 만족하는 완전한 parent 조합을 선택하지 못했다. 실제 reason은 no_complete_required_parent이며 단순 context_budget_exceeded가 아니다.

## v5 설계 자체의 문제인가, 구현 버그인가?

9개 오류는 둘 다 아닌 기존 로컬 quota 제한이다. 9건에서 parent 선택은 예산 내 context를 만들어 다음 단계까지 진행했다. 다만 답변 품질 검증은 API 미호출로 수행되지 않았고, TRF-007의 세부 실패 조건도 아직 불명확하다.

## 최소 수정 위치

이번 예외 자체에 대한 필수 코드 수정은 없다. 개선한다면 `evaluation/run_bm25_baseline.py / 실행 전 준비 경로`에서 기존 quota를 읽기 전용으로 사전 점검해 제한을 명확히 보고하는 방향이다. context/retrieval/search_trace의 반환 구조를 수정할 근거는 없다.

## v5 전체 rollback 필요 여부

**아니오.** 이번 오류 기록은 rollback을 정당화하지 않는다. quota 문제와 TRF-007의 조합 실패를 분리해서 다뤄야 한다.
