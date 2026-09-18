# Current Hybrid + Groq v5 Final 분석

분석일: 2026-09-18. `bm25_answer_diagnostics_groq_v5_final.json`의 저장 결과만 읽었다. 검색·API·평가를 재실행하지 않았으며 코드·설정·기존 결과를 변경하지 않았다. v4 비교 수치는 사용자가 제시한 9 no_hits / 1 abstained를 기준으로 한다.

## 1. 문항별 결과

| ID | 최종 상태 | 중단 단계 | error/reason | Groq 호출 여부 |
| --- | --- | --- | --- | --- |
| TRF-001 | error | llm_request, 응답 완성 여부 처리 | GuideError / AI_INCOMPLETE | true |
| TRF-002 | abstained | citation_validation → sentence_matching | invalid_citation_or_statement / unsupported sentence | true |
| TRF-003 | abstained | complete | llm_abstained | true |
| TRF-004 | error | after_budget, 호출 전 | RateLimitError / AI_LIMIT_MINUTE, 59초 | false |
| TRF-005 | error | after_budget, 호출 전 | RateLimitError / AI_LIMIT_MINUTE, 59초 | false |
| TRF-006 | error | after_budget, 호출 전 | RateLimitError / AI_LIMIT_MINUTE, 58초 | false |
| TRF-007 | no_hits | parent selection | no_complete_required_parent | 미도달, generation_trace={} |
| TRF-008 | error | after_budget, 호출 전 | RateLimitError / AI_LIMIT_MINUTE, 58초 | false |
| TRF-009 | error | after_budget, 호출 전 | RateLimitError / AI_LIMIT_MINUTE, 58초 | false |
| TRF-010 | error | after_budget, 호출 전 | RateLimitError / AI_LIMIT_MINUTE, 58초 | false |

전체 API 호출 기록은 3건(TRF-001/002/003)이다. 나머지 7건은 호출되지 않았다. 상태 error 7건을 API 오류 7건으로 해석하면 안 된다.

## 2. 7개 error 원인별 분류

### 그룹 A: 로컬 분당 사용량 제한 — 6건

대상: TRF-004, 005, 006, 008, 009, 010.

- exception class: `RateLimitError`.
- 앱 오류 code: `AI_LIMIT_MINUTE`.
- 실제 메시지: “짧은 시간에 질문이 몰려 앱에서 잠시 대기합니다. 약 59초 뒤 다시 시도하세요. 하루 사용량 소진은 아닙니다. (AI_LIMIT_MINUTE)” 또는 같은 문구의 58초 버전.
- retry_after_seconds: 004/005는 59, 나머지 네 문항은 58.
- llm_called=false, stage=after_budget.
- pre_llm_assessment와 budget_assessment는 모두 supported.

이는 **로컬 evaluation quota의 분당 제한**이다. 메시지 자체가 앱의 대기를 알리고, 실제 API 호출도 없었다. Groq 서버가 반환한 rate limit 또는 HTTP 429로 분류할 근거가 없다. fresh DB가 앞선 실행의 누적 호출 횟수는 분리했지만 이번 실행 중 생기는 분당 사용량까지 없애는 것은 아니다.

### 그룹 B: 호출 후 응답 미완성 — 1건

대상: TRF-001.

- exception class: `GuideError`.
- 앱 오류 code: `AI_INCOMPLETE`.
- 실제 메시지: “AI 답변이 완성되기 전에 중단되었습니다. 원문을 확인해 주세요. (AI_INCOMPLETE)”
- retry_after_seconds=null.
- llm_called=true, stage=llm_request.
- validation_reason, final_validation_stage, final_validation_reason은 기록되지 않았다.

응답 미완성으로 처리됐다는 사실은 확인된다. 하지만 원래 finish_reason, HTTP status, provider error code, 출력 토큰 사용량, 원본 응답이 없어 **출력 토큰 한도 도달이나 특정 provider 장애가 원인이라고 단정할 수 없다**. JSON parsing 문제로 기록된 것도 아니다. post-LLM 문장 검증에 도달했다는 증거는 없다.

### 요청한 분류별 결론

| 유형 | 이번 결과에서 확인된 내용 |
| --- | --- |
| 로컬 evaluation quota | AI_LIMIT_MINUTE 6건. AI_LIMIT_CALLS 0건 |
| Groq provider rate/token limit | 서버가 제한했다는 기록 없음; TRF-001 토큰 제한 원인은 미확인 |
| HTTP 4xx | 저장된 status 없음. 해당 오류라고 분류할 근거 없음 |
| HTTP 5xx | 저장된 status 없음. 해당 오류라고 분류할 근거 없음 |
| API 응답 parsing 문제 | 해당 오류 기록 없음 |
| post-LLM validation 문제 | TRF-002의 abstained 1건. error 7건에는 포함되지 않음 |
| 기타 코드 exception | 임의 Python 예외 기록 없음. TRF-001은 명시적 앱 오류 AI_INCOMPLETE |

**7건은 동일 exception/동일 원인이 아니다.** HTTP status 및 provider error code는 모두 미기록이다. 로컬 제한 6건은 API를 호출하지 않았으므로 해당 요청의 provider 응답 코드 자체가 없다. JSON에 traceback이나 발생 파일·함수 필드가 없어 이번 '저장 결과만' 분석에서 정확한 소스 위치를 새로 확정하지 않는다. 단계 수준으로는 로컬 호출 전 제한과 호출 후 미완성 처리가 명확히 구분된다.

## 3. fresh 평가 quota 분리 확인

metadata.quota_scope와 quota_preflight.quota_db는 동일하게 다음 파일을 가리킨다.

```text
C:\Users\Administrator\Documents\sch-guide-ai\evaluation\.runtime\usage_v5_final.sqlite3
```

저장된 preflight:

| 항목 | 값 |
| --- | ---: |
| exists | false |
| calls / user_calls | 0 / 0 |
| tokens / minute_tokens | 0 / 0 |
| daily_call_limit / user_daily_call_limit | 40 / 10 |
| remaining_calls / planned_questions | 10 / 10 |
| ten_question_call_capacity | true |
| completion_guaranteed | false |

**기존 평가 DB와 다른 fresh scope를 사용한 것으로 기록되어 있으며, AI_LIMIT_CALLS는 재발하지 않았다.** TRF-001~003은 실제 호출까지 진행했다. 하지만 이후 여섯 문항은 AI_LIMIT_MINUTE로 차단됐으므로 “로컬 quota를 모두 통과했다”고 말할 수는 없다. 24시간 호출 횟수 문제와 분당 제한을 구분해야 한다.

이번에는 DB 자체를 읽거나 생성·초기화하지 않았다. 위 결론은 결과에 저장된 경로와 preflight, 실제 실행 trace의 일관성에 근거한다.

## 4. TRF-002 / TRF-003 abstained 분석

### TRF-002: 응답 후 문장 매칭 실패

- API 호출: true.
- stage=citation_validation.
- block_reason=invalid_citation_or_statement.
- final_validation_stage=sentence_matching.
- final_validation_reason=unsupported sentence.
- exact_match_passed=false, normalization_attempted=true, normalization_passed=false.
- statement_index=1, sentence_index=1, failure_type=sentence_not_in_source.
- label 검사는 미시도다.

실패 문장:

> 입원한 후 입원 진료과에서 수혈 처방을 추가로 내면 수혈 동의서를 다시 받는다.

label은 `입원 후 추가 처방`이다. chunk_found=true, quote_in_chunk=true지만 sentence_in_chunk=false, sentence_in_quote=false다. 여기서 확정되는 것은 원문/normalization 매칭 실패이며, 이 기록만으로 임상적 허위 여부를 판정하지 않는다.

### TRF-003: 모델의 답변 보류

- API 호출: true.
- stage=complete.
- block_reason=llm_abstained.
- answerable=false.
- error=null. validation_reason 또는 validation_failure 없음.

사전·예산 검사 모두 supported지만 최종 모델 답변은 답변 불가로 처리됐다. 이는 TRF-002의 validator 거절과 다르다. 모델이 왜 보류했는지에 대한 설명은 저장되지 않아 추측하지 않는다.

## 5. TRF-007 no_hits

context_selection에 seed_count=6이 있으므로 검색 seed는 존재했다. 부모 후보 5개, 전체 청크 수 15, limit=12이며 최종 selected_chunk_count=0이다. 정확한 reason은 `no_complete_required_parent`다.

검색 결과 없음이 아니라 parent selection에서 선택 가능한 완전한 요구 충족 조합을 얻지 못했다. `context_budget_exceeded`로 기록된 것은 아니므로 합계 15만으로 단순 예산 초과라고 단정할 수 없다. 이전 일반 v5 분석에서 알려진 TRF-007과 같은 단계·reason이다. 다만 이번에는 과거 파일을 다시 읽지 않았고 사용자가 제공한 이전 결과와 비교했다. 조합별 탈락 사유는 이번 JSON에도 없다.

## 6. TRF-006 / TRF-008은 validator에 도달했는가?

둘 다 **도달하지 않았다**. context_ready, pre_llm_assessment=supported, budget_assessment=supported까지 진행했지만 AI_LIMIT_MINUTE로 API 호출 전에 종료됐다.

- TRF-006: 선택 context 2개, llm_called=false, retry_after_seconds=58.
- TRF-008: 선택 context 5개, llm_called=false, retry_after_seconds=58.

이번 결과는 과거 label/normalization 문제가 해결됐는지 또는 재현되는지 판단할 자료가 아니다. 검증할 새로운 모델 응답 자체가 없다.

## 7. parent-selection 개선 평가

| 지표 | v4 (제공된 결과) | v5 Final |
| --- | ---: | ---: |
| no_hits | 9 | 1 |
| context 확보 후 후속 처리 | 1 | 9 |
| 실제 API 호출 | 이 보고서에서 별도 집계하지 않음 | 3 |
| 최종 answered | 0 | 0 |

v5 Final에서 TRF-007을 제외한 9건은 context_ready이며 사전·예산 evidence 검사도 supported다. 선택 청크 수는 순서대로 8, 3, 10, 11, 9, 2, 0, 5, 10, 2이며 모두 limit=12 안이다.

따라서 **검색/context 파이프라인의 진행 측면에서는 개선 효과가 있다.** error 증가를 곧 context 선택 악화로 해석하면 안 된다. 준비된 context가 더 뒤 단계인 로컬 분당 제한, 응답 완성 검사, 문장 검증, 모델 자체 보류에 도달했다.

그러나 정답 품질 향상이나 모든 필요한 사실의 확보까지 입증한 것은 아니다. TRF-004 표 결손 해결 여부도 이번 실행으로 증명되지 않는다. 최종 성공 답변은 여전히 없다.

## 원본 보존

입력 SHA-256: `35ceb65cc324c4df2309af8a2146f118ecb4f3c8d6b4093d17666c88958037ca`.

이번 산출물은 이 보고서 한 개다.

## 7개 error의 공통 원인

단일 공통 원인은 없다. 6건은 호출 전 로컬 AI_LIMIT_MINUTE이고 1건(TRF-001)은 호출 후 AI_INCOMPLETE다. Groq provider HTTP 오류 7건이 아니다.

## 로컬 quota 문제는 해결됐는가?

기존 AI_LIMIT_CALLS 문제는 이번 fresh scope에서 재발하지 않았다. 다만 분당 제한 AI_LIMIT_MINUTE는 여섯 문항을 차단했다. 호출 횟수 여유가 전체 실행 완료를 보장하지 않는다는 preflight 안내와 일치한다.

## TRF-002/003 abstain 원인

002는 응답 후 unsupported sentence로 문장 검증 실패. 003은 complete 단계의 llm_abstained로 모델 답변 보류. 둘 다 API는 호출됐다.

## TRF-007 no_hits 원인

seed는 있지만 parent selection에서 no_complete_required_parent로 빈 context가 반환됐다. API나 quota 문제가 아니다.

## parent-selection v5 수정은 효과가 있었는가?

예. no_hits가 9건에서 1건으로 줄고 9건이 근거·예산 검사를 통과했다. 최종 답변 품질 개선은 아직 확인되지 않았다.

## 다음에 실제로 수정해야 할 1순위

**평가 runner의 요청 간격 제어를 보강하는 것**을 우선 제안한다. 호출 전 로컬 분당 제한에 걸린 경우 기록된 retry_after_seconds를 존중해 제한을 충족한 뒤 진행하도록 설계해야 한다. API가 이미 호출된 문항은 자동 재호출하지 않고, 대기 상한·중단·체크포인트를 명시해야 한다. 운영/provider 제한 완화나 quota DB 재초기화로 우회해서는 안 된다. AI_INCOMPLETE는 별도로 실제 finish_reason·안전한 토큰 사용량을 확인한 후 대응해야 하며 지금 출력 한도 때문이라고 가정해 변경하지 않는다. 이번에는 수정하지 않았다.
