# Groq Live Response 무호출 디버깅 및 최소 수정 결과

- 구현·검증일: 2026-09-13
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 계획: `docs/rag/10_LIVE_RESPONSE_DEBUG_PLAN.md`
- 검증 방식: 합성 공식 envelope fixture 및 MockTransport
- 실제 Groq 호출: 0회
- 상태: 최소 수정과 무호출 검증 완료, 실제 재평가 승인 대기

## 1. 결론

평가용 `SingleCallTransport`가 실제 `httpx.Response`의 body를 먼저 읽고 새 response로 재포장하던 동작을 제거했다. 이제 transport는 요청 제약을 확인하고 내부 transport가 반환한 동일 response 객체를 그대로 `generate()`에 전달한다. latency는 response body를 소비하지 않고 `generate()` 호출 직전과 직후의 monotonic clock으로 측정한다.

`generate()`에는 raw content 없는 단계별 response shape trace와 고정 failure detail을 추가했다. 비압축·gzip·deflate 공식 Chat Completions envelope가 모두 정상 처리됐고, `finish_reason=length`는 `AI_INCOMPLETE`, envelope 구조 오류와 non-null refusal은 세부 원인이 구분된 `AI_RESPONSE`, 잘못된 citation은 기존과 동일한 `AI_EVIDENCE`로 안전 차단됐다.

전체 테스트는 264 passed, 1 skipped이며 Ruff도 통과했다. OUTPUT_LIMIT, request budget, strict schema, system prompt, compact evidence schema v2와 retrieval 계층은 변경하지 않았다.

## 2. 확정 원인과 수정

이전 live 평가에서 `HTTP 200` 뒤 `AI_RESPONSE`가 발생한 직접 원인을 실제 원시 응답 없이 단정할 수는 없다. 다만 평가 transport가 다음과 같은 불필요한 응답 소유권 변경을 수행하던 구조적 결함은 확정됐다.

1. 응답을 `response.read()`로 선소비했다.
2. 읽은 bytes와 headers로 별도 `httpx.Response`를 재구성했다.
3. model·usage·latency를 transport에서 별도로 추출했다.

수정 후에는 다음 계약을 적용한다.

- `SingleCallTransport`는 response body를 읽지 않는다.
- 실제 `httpx.Response`를 재포장하지 않고 그대로 반환한다.
- latency는 평가 함수가 `time.perf_counter()`로 `generate()` 전체 구간을 측정한다.
- 반환 model과 usage는 `generate()`가 파싱한 안전 trace에서만 평가 결과로 가져온다.
- 자동 retry와 fallback은 추가하지 않았다.

이 변경으로 HTTPX의 압축 해제 및 response stream 소유권을 원래 client에 유지하고, production parser와 평가 parser가 서로 다른 body 상태를 관찰할 가능성을 제거했다.

## 3. raw-free response parse trace

`generate()`는 HTTP 200 이후 다음 순서로 response envelope를 검사한다.

| stage | 기록 정보 | 원문 저장 |
|---|---|---|
| `transport` / `http` | 요청 진입, HTTP status | 없음 |
| `json` | `response.json()` 성공 여부 | 없음 |
| `top_level` | object/array/string/null 등 고정 타입 | 없음 |
| `choices` | field 존재, list 여부, 개수 | 없음 |
| `choice` | 첫 항목의 고정 타입 | 없음 |
| `finish` | field 존재, 타입, 제한된 안전 값 | 없음 |
| `message` | field 존재, object 여부 | 없음 |
| `content` | field 존재, 타입, 문자 길이만 | 없음 |
| `validation` / `complete` | 기존 Answer·citation 검증 진입 및 완료 | 없음 |

`refusal`은 field 존재 여부와 non-null 여부만 기록한다. message content, refusal 내용, raw response, 전체 prompt, API key와 Authorization header는 trace나 artifacts에 기록하지 않는다.

## 4. 고정 failure detail

| 응답 경계 | 결과 코드 | failure detail |
|---|---|---|
| JSON decode 실패 | `AI_RESPONSE` | `json_decode` |
| top-level object 아님 | `AI_RESPONSE` | `top_level_type` |
| choices 누락 | `AI_RESPONSE` | `choices_missing` |
| choices가 list 아님 | `AI_RESPONSE` | `choices_type` |
| choices가 비어 있음 | `AI_RESPONSE` | `choices_empty` |
| choices[0]가 object 아님 | `AI_RESPONSE` | `choice_type` |
| Groq finish_reason 누락·타입·값 오류 | `AI_RESPONSE` | `finish_missing`, `finish_type`, `finish_reason` |
| finish_reason이 stop 아님 | `AI_INCOMPLETE` | 기존 incomplete 계약 유지 |
| message 누락·타입 오류 | `AI_RESPONSE` | `message_missing`, `message_type` |
| refusal non-null | `AI_RESPONSE` | `refusal` |
| content 누락·타입 오류 | `AI_RESPONSE` | `content_missing`, `content_type` |
| schema-valid이나 citation 오류 | `AI_EVIDENCE` | 기존 validator 계약 유지 |

failure detail은 예외 메시지나 provider 원문이 아니라 위 고정 값만 사용한다.

## 5. provider 호환성 경계

Groq provider의 strict Chat Completions 응답에는 `finish_reason`을 필수로 검사한다. 누락 시 성공으로 추정하지 않고 `AI_RESPONSE / finish_missing`으로 차단한다.

저장소 내부의 기존 합성 provider fixture 일부는 `finish_reason`을 제공하지 않는 레거시 계약을 사용하고 있었다. 전체 회귀 테스트에서 이 차이를 확인했으며, 외부 Groq 응답 경계를 약화하지 않도록 누락 차단은 Groq provider에 적용하고 내부 provider는 기존 동작을 유지했다. 이는 system prompt나 Answer schema 변경이 아니다.

## 6. MockTransport 및 회귀 검증

### 응답 형식

| fixture | 결과 |
|---|---|
| 비압축 공식 envelope | 통과 |
| gzip 공식 envelope | 통과 |
| deflate 공식 envelope | 통과 |
| 동일 response 객체 반환 | 통과 |

### parser 및 안전 계약

- `finish_reason=stop`: 정상 Answer와 citation 검증 통과
- `finish_reason=length`, `completion_tokens=768`: 반드시 `AI_INCOMPLETE`
- `finish_reason` 누락: `AI_RESPONSE / finish_missing`
- choices, choice, message, content의 누락·빈 값·타입 오류: 각각 고정 detail로 `AI_RESPONSE`
- refusal non-null: content를 기록하지 않고 `AI_RESPONSE / refusal`
- schema-valid이지만 잘못된 citation: 기존 `AI_EVIDENCE`
- Q006: transport 호출 0회 유지

실행 결과:

- 집중 테스트: `25 passed`
- 관련 회귀 테스트: `143 passed`
- 전체 테스트: `264 passed, 1 skipped`
- Ruff: 통과
- Mock evaluator: Q002 mock 1회 및 validation 통과, Q006 mock 0회
- 코드 검수: 추가 조치가 필요한 결함 없음

## 7. 변경 및 비변경 범위

변경:

- `tools/rag_groq_evaluate.py`: body 선소비·response 재포장 제거, `generate()` 전후 latency, 안전 trace 결과 사용
- `mvp/ai.py`: raw-free response stage/shape trace와 고정 failure detail
- `tests/test_live_response_transport.py`: 공식 envelope, 압축 전송과 malformed boundary 회귀 테스트
- `tests/fixtures/groq_strict_chat_completion.json`: 합성 공식 envelope fixture

유지:

- `OUTPUT_LIMIT=768`
- `GROQ_REQUEST_TOKEN_BUDGET=3500`
- `response_format=json_schema`, `strict=true`
- 기존 Answer schema와 citation·숫자·단위·행동·source-order 검증
- system prompt와 compact evidence schema v2
- BM25, embedding, RRF, reranker와 evidence selection
- 자동 retry 없음, fallback 없음, web search 없음

## 8. 산출물 및 승인 대기

무호출 검수 산출물은 `artifacts/2026-09-13_rag-live-response-debug-fix/`에 저장했다.

- `mock_response_debug_report.json`
- `review.html`

이번 작업에서는 실제 Groq를 호출하지 않았다. 이 결과 작성으로 작업을 멈추며, 실제 재평가는 사용자의 별도 승인 전까지 수행하지 않는다.
