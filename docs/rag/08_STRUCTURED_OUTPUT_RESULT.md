# Groq Structured Outputs 최소 수정 결과

- 구현·검증일: 2026-09-13
- 대상 저장소: D:\보하 바탕화면\SCHAT\sch-guide-ai
- 기준 계획: docs/rag/07_STRUCTURED_OUTPUT_PLAN.md 권고안 C
- 검증 방식: httpx MockTransport 및 로컬 테스트
- 실제 Groq 호출: 0회
- 상태: 구현 및 Mock 검증 완료, 실제 호출 승인 대기

## 1. 결론

Groq 요청을 기존 json_object에서 Answer 모델 기반 strict JSON Schema로 변경했다. Answer.model_json_schema()에서 요청용 schema를 매번 생성하고, 모든 object의 properties를 required로 고정하며 additionalProperties를 false로 설정했다. Pydantic 런타임 기본값은 유지하되 strict request schema에서는 default 키를 제거한다.

MockTransport에서 strict-valid 응답은 기존 검증기를 통과했다. finish_reason=length는 validate_answer() 전에 AI_INCOMPLETE로 분리됐고, 비정상 JSON 또는 choices 부재는 AI_RESPONSE로 분류됐다. schema-valid이지만 quote가 실제 chunk와 일치하지 않는 응답은 기존 citation 검증에 의해 AI_EVIDENCE 안전 차단됐다. Q006은 transport 호출 0회를 유지했다.

실제 Groq 요청은 수행하지 않았다. 따라서 이번 결과는 로컬 payload·parser·검증 계약의 통과를 의미하며, Groq가 실제 strict schema를 수락하는지와 OUTPUT_LIMIT=768에서 응답이 완결되는지는 아직 승인되지 않은 실서비스 검증 항목이다.

## 2. 구현 범위

### strict-compatible schema

mvp/ai.py에 groq_answer_json_schema()를 추가했다.

- 원천은 Answer.model_json_schema()다.
- Answer 최상위 필드는 answerable, statements, format, conflict 모두 required다.
- Statement 필드는 text, evidence, label 모두 required다.
- Evidence 필드는 chunk_id, quote 모두 required다.
- 모든 object에 additionalProperties=false를 적용한다.
- request schema의 default 키는 제거한다.
- 변환은 새 dict/list를 생성하므로 Pydantic 모델 schema와 런타임 기본값을 변형하지 않는다.

Groq provider의 response_format은 type=json_schema, name=schat_answer, strict=true와 변환한 schema를 사용한다. Groq 이외의 기존 provider는 json_object 동작을 유지한다.

### 응답 파싱과 안전 trace

HTTP 응답 이후 전체 JSON object, 반환 model과 정수 usage, choices[0], finish_reason을 순서대로 확인한다. 정상 종료 후에만 message.content를 기존 validate_answer()에 전달한다. citation, 숫자, 단위, 행동, 원문 문장 및 procedure source order 검증은 그대로 유지한다.

trace에는 response_http_status, response_model, 검증된 response_usage, finish_reason과 llm_error_code만 추가했다. API key, Authorization 헤더, 전체 prompt, message.content와 raw response는 기록하지 않는다.

## 3. 고정 불변식

| 항목 | 결과 |
|---|---|
| OUTPUT_LIMIT | 768 유지 |
| GROQ_REQUEST_TOKEN_BUDGET | 3500 유지 |
| system prompt | 수정 없음 |
| compact evidence schema | v2 유지 |
| reasoning_effort | low 유지 |
| reasoning_format | 전송하지 않음 |
| 자동 retry / fallback | 추가하지 않음 |
| BM25 / embedding / RRF / reranker | 수정 없음 |
| 실제 Groq 호출 | 0회 |

## 4. Mock 검증 결과

| 시나리오 | 기대 결과 | 실제 결과 | 판정 |
|---|---|---|---|
| strict-valid 구조화 응답 | 정상 Answer 및 citation 통과 | answerable=true, complete | 통과 |
| finish_reason=length | validate_answer 전 AI_INCOMPLETE | AI_INCOMPLETE 및 안전 코드 기록 | 통과 |
| JSON이 아니거나 choices 없음 | AI_RESPONSE | AI_RESPONSE | 통과 |
| schema-valid, 잘못된 exact quote | 기존 안전 차단 | AI_EVIDENCE, answerable=false | 통과 |
| Q006 근거 없음 | transport 0회 | 0회, 고정 근거 부족 문구 | 통과 |

평가 도구의 dry-run MockTransport에서도 Q002는 정확히 1회 호출되고 validation을 통과했으며, Q006은 0회였다. 이는 실제 네트워크 호출 횟수가 아니라 MockTransport 호출 횟수다.

## 5. 기존 안전 검증 유지

- chunk_id가 selected evidence에 존재해야 한다.
- quote가 서버 측 실제 Chunk 원문의 정확한 문장 또는 표 행이어야 한다.
- 답변 statement는 인용 원문으로 지지되어야 한다.
- 근거 없는 숫자·단위·행동을 차단한다.
- procedure citation의 source order를 검증한다.
- 최대 statement 제한과 conflict 검증을 유지한다.

compact prompt metadata는 최종 citation의 진실 원천으로 사용하지 않는다. 검증은 계속 서버 측 Hit/Chunk 객체를 기준으로 수행한다.

## 6. 테스트와 정적 검사

| 검증 | 결과 |
|---|---|
| Structured Outputs 집중 테스트 | 7 passed |
| 관련 RAG·계약 테스트 | 101 passed |
| 전체 테스트 | 246 passed, 1 skipped |
| Ruff | 통과 |
| Mock evaluator dry-run | exit code 0 |

외장 드라이브의 가상환경을 사용해 테스트와 mock evaluator를 동시에 시작했을 때 dependency import가 일시적으로 실패했다. 같은 환경에서 순차 import를 재확인한 뒤 모든 검증을 순차 실행했고 위 결과로 통과했다. 제품 코드 회귀나 dependency 변경은 발견되지 않았다.

## 7. 코드 검수 결과와 잔여 위험

승인 범위 diff를 검수했으며 추가 retry, fallback, raw content trace 또는 retrieval 변경은 발견되지 않았다.

1. Groq의 실제 strict Structured Outputs endpoint 호환성은 아직 호출하지 않아 확인되지 않았다.
2. strict schema가 정상이어도 모델 출력이 768 token을 넘으면 finish_reason=length와 AI_INCOMPLETE가 발생할 수 있다.
3. 이 경우에도 자동 retry나 OUTPUT_LIMIT 변경은 하지 않으며 별도 사용자 승인 대상으로 남긴다.
4. usage가 없거나 형식이 다르면 trace에는 검증된 정수 필드만 남고 원시 객체는 저장하지 않는다.

## 8. 산출물

- artifacts/2026-09-13_rag-structured-output-mock/structured_output_mock_report.json
- artifacts/2026-09-13_rag-structured-output-mock/review.html
- tests/test_groq_structured_output.py

## 9. 사용자 승인 대기

이번 구현은 MockTransport 검증까지만 완료했다. 실제 Groq 호출, system prompt나 budget 변경, retrieval 조정은 수행하지 않았다. 다음 단계는 사용자가 실제 Groq 1회 재평가를 별도로 승인한 뒤에만 진행한다.
