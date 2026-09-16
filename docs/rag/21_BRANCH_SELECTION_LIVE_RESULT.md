# Branch-aware Source Unit Selection 단일 Live 평가 결과

- 평가일: 2026-09-14
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 구현: `docs/rag/20_BRANCH_SELECTION_RESULT.md`
- 모델: `openai/gpt-oss-20b`
- 평가 범위: Q006 zero-call 확인 후 Q002 실제 Groq 호출 1회
- 실제 Groq 호출: Q002 1회, Q006 0회
- 상태: **HTTP 400 schema rejection 단계에서 실패, 추가 호출 중지**

## 1. 결론

Q006은 실제 Groq transport 호출 0회를 유지했다. 그 조건을 통과한 뒤 Q002에 대해 승인된 selected evidence 12 chunks만 사용해 실제 Groq API를 정확히 한 번 호출했다.

Q002 요청은 HTTP 400으로 거부됐다. `response_parse_stage=http`, `failure_code=AI_SERVER`이며 response JSON envelope, model selection, server reconstruction과 citation validation에는 도달하지 않았다. 이번 결과는 지정된 실패 분류 중 **schema rejection**으로 분류한다.

확정할 수 있는 범위는 request/strict structured-output schema 경계에서 provider가 요청을 수락하지 않았다는 점까지다. 보안 조건에 따라 provider 오류 body를 읽거나 저장하지 않았으므로, root `anyOf`, 동적 group property, `minItems` 또는 per-slot enum 중 어느 keyword가 구체 원인인지는 단정하지 않는다.

승인 조건에 따라 자동 retry, fallback, prompt/schema 변경, validator 완화 또는 두 번째 실제 호출은 수행하지 않았다. 따라서 이번 Live에서는 “Q002 기준 RAG 엔진 완주”로 판정하지 않는다.

## 2. 실행 순서와 호출 제한

1. Q006을 기존 근거 부족 경로로 실행했다.
2. Q006 transport 0회와 `pre_llm:domain_or_clarification` 차단을 확인했다.
3. Q002 selected evidence가 승인된 12 chunks인지 단일-call transport에서 검사했다.
4. Q002 실제 Groq 요청을 한 번 전송했다.
5. HTTP 400을 받은 즉시 `AI_SERVER`로 차단했다.
6. 추가 호출 없이 안전한 결과 metadata와 문서만 작성했다.

| 호출 항목 | 결과 |
|---|---:|
| Q006 actual transport calls | 0 |
| Q002 actual Groq calls | 1 |
| 자동 retry | 0 |
| Fallback model | 0 |
| Web search/tool call | 0 |
| 실패 후 추가 Groq 호출 | 0 |

## 3. Live 안전 메타데이터

| 항목 | 결과 |
|---|---|
| HTTP status | 400 |
| 요청 모델 | `openai/gpt-oss-20b` |
| 반환 모델 | 수집되지 않음 |
| finish_reason | 수집되지 않음 |
| prompt tokens | 수집되지 않음 |
| completion tokens | 수집되지 않음 |
| total tokens | 수집되지 않음 |
| latency | 496.27 ms |
| response_parse_stage | `http` |
| failure_code | `AI_SERVER` |
| failure_detail | 없음 |
| validation_reason | 없음 |
| answerable | false |
| group slot 수 | 5 |
| required group | 5 |
| required group 충족 수 | 검증 전 거부로 판정 불가 |
| Adult branch 충족 | 판정 불가 |
| Pediatric branch 충족 | 판정 불가 |
| selected source units | 판정 불가 |
| verified statements | 0 |

HTTP 400이므로 `choices`, `message.content`, strict selection response와 usage가 생성된 정상 Chat Completions envelope를 받지 못했다. 반환 모델·finish reason·token usage를 추정하거나 이전 호출 값으로 채우지 않았다.

## 4. 실패 단계 분류

전체 경로에서 실제 도달한 위치는 다음과 같다.

```text
Q002 질문
  → 승인 retrieval/context 결과 로드
  → Evidence/PromptCoverage 통과
  → SourceUnit catalog v3 구성
  → request-scoped group-slot schema 구성
  → selected 12 chunks 전송 범위 확인
  → Groq HTTP 요청 1회
  → HTTP 400 / AI_SERVER
  ✕ response JSON parsing
  ✕ group selection validation
  ✕ server reconstruction
  ✕ validate_answer()
  ✕ citation validation
```

실패 분류:

- 상위 분류: `schema rejection`
- 확정 단계: Groq HTTP request/structured-output schema boundary
- 저장 코드: `AI_SERVER`
- 구체 schema keyword: 미확정

Response parser 이후의 `selection group`, `selection branch`, `selection wrong group`, `selection duplicate`, `selection source order`, AnswerCoverage, reconstruction, citation, number, unit, condition/negation 및 unsupported action 실패는 이번 호출에서 발생했다고 볼 수 없다. 그 단계에 도달하지 않았기 때문이다.

## 5. Retrieval 및 evidence 불변성

| 항목 | 결과 |
|---|---:|
| Selected evidence | 12 chunks |
| Pre-budget required gold recall | 10/10, 100% |
| Post-budget required gold recall | 10/10, 100% |
| Prompt evidence schema | v3 |
| Response selection schema | v2 |
| AI version | 14 |
| Output limit | 2048 |
| Request admission budget | 5120 |

전송 허용 범위로 확인한 chunk IDs:

1. `eval-741858d2e9162acf8d38-chunk-00013`
2. `eval-741858d2e9162acf8d38-chunk-00015`
3. `eval-741858d2e9162acf8d38-chunk-00016`
4. `eval-741858d2e9162acf8d38-chunk-00019`
5. `eval-741858d2e9162acf8d38-chunk-00020`
6. `eval-741858d2e9162acf8d38-chunk-00021`
7. `eval-741858d2e9162acf8d38-chunk-00022`
8. `eval-741858d2e9162acf8d38-chunk-00023`
9. `eval-741858d2e9162acf8d38-chunk-00024`
10. `eval-741858d2e9162acf8d38-chunk-00025`
11. `eval-741858d2e9162acf8d38-chunk-00026`
12. `eval-741858d2e9162acf8d38-chunk-00027`

Single-call transport는 prompt catalog에 이 12개 이외의 chunk ID가 있으면 외부 요청 전에 거부하도록 유지했다. 문서 전체나 다른 chunk는 전송하지 않았다.

## 6. Reconstruction과 grounding 판정

HTTP 400으로 selection response가 없었기 때문에 다음 값은 실패가 아니라 **미실행/판정 불가**다.

| 검증 | 결과 |
|---|---|
| Required group 5/5 선택 | 판정 불가 |
| Adult/pediatric branch 선택 | 판정 불가 |
| Source-unit ID count | 판정 불가 |
| Statement reconstruction | 미실행 |
| Reconstructed text exact match | false 형식값, 실질 미평가 |
| Reconstructed quote exact match | false 형식값, 실질 미평가 |
| Exact quote/chunk citation | 미평가 |
| Citation coverage | 0.0 형식값, 합격 아님 |
| Source order reversal | 미평가 |
| Unsupported number/unit/condition/negation/action | 미평가 |

Mock에서 확인한 14-ID reconstruction과 citation 100% 결과를 이번 Live 성공값으로 재사용하지 않았다.

## 7. 최종 합격 기준

| 기준 | 실제 결과 | 판정 |
|---|---|---|
| Q006 actual call 0 | 0 | 통과 |
| Q002 actual call 1 | 1 | 통과 |
| HTTP 200 | 400 | 실패 |
| 반환 모델 일치 | 없음 | 미충족 |
| finish_reason=stop | 없음 | 미충족 |
| completion < 2048 | 없음 | 미충족 |
| response_parse_stage=complete | `http` | 실패 |
| failure_code 없음 | `AI_SERVER` | 실패 |
| validation_reason 없음 | 없음 | 해당 단계 미도달 |
| answerable=true | false | 실패 |
| Required group 5/5 | 판정 불가 | 미충족 |
| Adult/pediatric branch | 판정 불가 | 미충족 |
| Selected units 1~16 | 판정 불가 | 미충족 |
| Verified statements 1~16 | 0 | 실패 |
| Reconstruction exact | 미실행 | 미충족 |
| Exact citation 및 coverage 100% | 미실행 | 미충족 |
| Source order reversal 0 | 미실행 | 미충족 |
| Unsupported 내용 0 | 미실행 | 미충족 |
| 승인 12 chunks 외 원문 미전송 | 미전송 | 통과 |

최종 판정: **Q002 기준 RAG 엔진 Live 완주 실패 — schema rejection**.

## 8. 보안 및 비변경 확인

- API key 미기록
- Authorization header 미기록
- 전체 prompt 미저장
- Raw response 및 message content 미저장
- Refusal 원문 미저장
- Selected SourceUnit exact text 미저장
- Provider 오류 body 미저장
- BM25, embedding, RRF와 reranker 미변경
- Retrieval/context 및 selected evidence 미변경
- Segmentation과 SourceUnit eligibility 미변경
- System prompt와 validator 미변경
- Output limit과 request budget 미변경
- 자동 ID 보충, dedup 및 재정렬 없음
- Retry, fallback, web search 없음

Live 실패 뒤 production 코드를 자동 수정하지 않았다.

## 9. 산출물

새 산출물 디렉터리:

`artifacts/2026-09-14_rag-branch-aware-selection-live/`

- `live_report.json`
- `failure_summary.json`
- `review.html`

기존 artifacts는 덮어쓰지 않았다.

## 10. 작업 중지

이번 승인 범위의 Q006 zero-call과 Q002 실제 단일 호출을 완료했다. HTTP 400 이후 두 번째 실제 호출을 하지 않았고 schema, prompt 또는 validator도 변경하지 않았다.

다음 단계가 승인된다면 실제 호출 없이 provider strict subset과 현재 dynamic schema의 keyword 호환성을 먼저 분석해야 한다. 이번 결과만으로 특정 keyword를 임의 제거하거나 A/B 대안으로 자동 전환하지 않는다.
