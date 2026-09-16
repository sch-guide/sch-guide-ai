# Groq Live Response 무호출 디버깅 및 최소 수정 계획

- 작성일: 2026-09-13
- 대상 저장소: D:\보하 바탕화면\SCHAT\sch-guide-ai
- 기준 결과: docs/rag/09_STRUCTURED_OUTPUT_LIVE_RESULT.md
- 분석 방식: 정적 경로 추적, 공식 envelope 합성 fixture, httpx MockTransport
- 실제 Groq 호출: 0회
- 상태: 원인 재현 완료, 최소 수정 승인 대기

## 1. 결론

이번 AI_RESPONSE의 직접적인 코드 결함은 평가용 SingleCallTransport가 실제 응답을 소비한 뒤 잘못 재포장하는 데 있다.

SingleCallTransport는 내부 HTTPTransport가 반환한 response에 response.read()를 호출한다. HTTPX는 Content-Encoding이 있는 응답을 읽을 때 body를 디코딩해 response.content에 보관하지만 원래 Content-Encoding 헤더는 그대로 둔다. 평가 transport는 이미 디코딩된 bytes와 원래 헤더를 새 httpx.Response에 함께 넣는다. 바깥 httpx.Client는 그 body를 원래 헤더에 따라 다시 디코딩하려고 시도하고 httpx.DecodingError를 발생시킨다.

이 오류는 mvp/ai.py의 다음 대입문이 완료되기 전에 발생한다.

    response = client.post(endpoint, json=payload, headers=headers)

따라서 바로 다음 줄의 response_http_status trace가 실행되지 않았고, response.json(), choices와 finish_reason 검사에도 도달하지 못했다. 외부 예외는 generate()의 httpx.HTTPError 처리에서 AI_RESPONSE로 변환됐다.

합성 gzip 응답을 사용한 MockTransport에서 live 결과와 같은 상태를 재현했다.

- 평가 transport 자체에는 HTTP 200, 반환 model과 usage가 기록됨
- generate trace는 stage=llm_request에 머묾
- response_http_status와 finish_reason이 없음
- 최종 오류는 AI_RESPONSE

공식 형태의 response envelope를 평가 transport 없이 generate()에 직접 전달하면 현재 parser와 strict Answer validation은 정상 통과한다. 따라서 현재 증거상 핵심 장애는 Groq strict JSON Schema나 validate_answer()가 아니라 live 평가 transport의 response 재포장이다.

## 2. 확정 원인

### 2.1 body 소비와 stream 상태

HTTPX 0.28.1에서 압축된 합성 response를 읽은 뒤 다음 상태를 테스트로 확인했다.

| 항목 | 확인 결과 |
|---|---|
| response.read() | 압축이 해제된 JSON bytes 반환 |
| is_stream_consumed | true |
| response.content | 디코딩된 JSON bytes로 다시 읽기 가능 |
| Content-Encoding header | gzip이 그대로 남음 |

body가 사라져 다시 읽을 수 없는 문제는 아니다. body는 메모리에 남아 다시 읽을 수 있지만, 표현은 디코딩됐고 헤더는 원래 전송 인코딩을 가리키는 불일치 상태다.

### 2.2 잘못된 재포장

현재 평가 transport는 다음 순서로 동작한다.

1. inner.handle_request(request)로 실제 httpx.Response를 받는다.
2. response.read()로 압축을 해제하고 stream을 소비한다.
3. 디코딩된 bytes를 JSON으로 파싱해 model과 usage를 기록한다.
4. 같은 디코딩 bytes를 원래 response.headers와 함께 새 Response의 content로 넣는다.
5. 바깥 Client가 Content-Encoding에 따라 이미 디코딩된 bytes를 다시 디코딩한다.
6. DecodingError가 client.post() 안에서 발생한다.

MockTransport에 gzip 공식 envelope fixture를 넣으면 5단계에서 DecodingError가 확정적으로 재현된다. 같은 fixture를 직접 MockTransport로 generate()에 넘기면 정상 통과한다.

### 2.3 AI_RESPONSE 발생 위치

generate()의 현재 단계는 다음과 같다.

| 경계 | live trace | 재현 결과 |
|---|---|---|
| llm_called=true, stage=llm_request | 존재 | 동일 |
| client.post(...) 반환 | 완료되지 않음 | gzip 재포장 시 DecodingError |
| response_http_status 기록 | 미도달 | 동일 |
| response.json() | 미도달 | 동일 |
| choices[0] | 미도달 | 동일 |
| finish_reason | 미도달 | 동일 |
| validate_answer() | 미도달 | 동일 |
| 외부 예외 처리 | AI_RESPONSE | 동일 |

평가 transport가 model과 usage를 기록할 수 있었던 것은 3단계에서 디코딩된 top-level JSON 파싱에는 성공했다는 뜻이다. generate()가 malformed Groq JSON을 받은 뒤 실패한 것이 아니라, 파싱 가능한 응답을 다시 바깥 Client에 전달하는 과정에서 실패했다.

## 3. 추정 원인과 미확정 사항

### 3.1 live 응답의 Content-Encoding

live artifacts에는 응답 헤더를 저장하지 않았으므로 실제 인코딩이 gzip, brotli 또는 다른 지원 인코딩이었는지는 확정할 수 없다. 다만 다음 증거 때문에 압축 응답 이중 디코딩이 live 실패의 가장 강한 원인이다.

- 내부 transport는 HTTP 200 body를 JSON으로 읽어 model과 usage를 확보했다.
- generate()는 client.post() 대입을 끝내지 못했다.
- 압축 합성 fixture가 같은 trace와 AI_RESPONSE를 재현한다.
- 비압축 공식 envelope fixture는 현재 parser를 통과한다.

최소 수정 후에도 실제 호출 없이 gzip과 지원 가능한 다른 Content-Encoding fixture로 회귀 테스트해야 한다.

### 3.2 truncation 가능성

completion_tokens=768은 OUTPUT_LIMIT=768과 같다. finish_reason=length일 가능성은 높지만 다음 이유로 확정하지 않는다.

- live finish_reason을 수집하지 못했다.
- raw response를 저장하지 않았고 사후 복원하지 않는다.
- usage가 한도에 도달했다는 사실만으로 API의 종료 사유를 단정할 수 없다.

합성 fixture에서는 completion_tokens=768이고 finish_reason=length일 때 기존 계약대로 AI_INCOMPLETE가 발생한다. transport 수정 후 실제 finish_reason이 수집되기 전에는 output limit을 늘리지 않는다.

### 3.3 refusal

Groq 문서는 Structured Outputs에서 프로그램 방식의 refusal 감지를 지원한다고 설명한다. 현재 parser는 message.refusal을 검사하거나 trace하지 않는다. 이번 live 실패의 원인으로 볼 증거는 없지만, raw content 없이 정상 답변과 refusal을 구분하기 위한 관측 공백이다.

## 4. 공식 response envelope fixture

tests/fixtures/groq_strict_chat_completion.json을 추가했다. fixture는 Groq Chat Completions API 문서의 다음 envelope를 따른다.

- top-level id, object=chat.completion, created, model
- choices 배열
- choices[0].index
- choices[0].message.role과 content
- choices[0].finish_reason
- usage.prompt_tokens, completion_tokens, total_tokens
- system_fingerprint와 x_groq
- refusal 존재 여부 검사용 message.refusal=null

Groq 공식 API reference는 Chat Completions 응답의 model, choices[0].message.content, finish_reason과 usage 구조를 제시한다. Structured Outputs 문서는 openai/gpt-oss-20b의 strict=true 지원과 message.content의 JSON 파싱 방식을 명시한다.

- https://console.groq.com/docs/api-reference
- https://console.groq.com/docs/structured-outputs

fixture content는 실제 병원 문서나 live 응답이 아닌 합성된 한 문장 Answer다.

## 5. 재현 테스트

새 테스트 파일은 tests/test_live_response_transport.py다.

| 테스트 | 검증 내용 | 결과 |
|---|---|---|
| official fixture parser | 공식 형태 envelope를 직접 MockTransport로 전달 | 정상 Answer 및 citation 통과 |
| completion length | completion_tokens=768, finish_reason=length | AI_INCOMPLETE |
| compressed body state | response.read() 뒤 body와 header 상태 | decoded content + stale encoding header 확인 |
| evaluation re-wrap | decoded body를 원래 gzip header로 재포장 | httpx.DecodingError |
| generate boundary | 같은 transport를 generate()에 연결 | client.post 경계에서 AI_RESPONSE |

Structured Outputs 기존 테스트와 함께 실행한 결과는 12 passed이며 Ruff도 통과했다. 모든 테스트는 MockTransport 또는 합성 Response만 사용했고 실제 Groq 호출은 0회다.

## 6. raw content 없는 response shape 진단

최소 수정 시 generate()가 각 경계를 통과하기 직전에 고정된 boolean, count와 type 이름만 trace하도록 한다. 값이나 원문은 기록하지 않는다.

| trace 필드 | 허용 값 |
|---|---|
| response_parse_stage | transport, json, top_level, choices, choice, message, content, finish, validation |
| response_json_succeeded | boolean |
| response_top_level_type | object, array, string, number, boolean, null |
| response_choices_present | boolean |
| response_choices_count | 0 이상의 정수 또는 null |
| response_choice0_type | object 등 제한된 type 이름 |
| response_finish_reason_present | boolean |
| finish_reason | 알려진 짧은 문자열 또는 null |
| response_message_present | boolean |
| response_message_type | object 등 제한된 type 이름 |
| response_content_present | boolean |
| response_content_type | string 등 제한된 type 이름 |
| response_content_char_count | 문자열일 때 문자 수, 아니면 null |
| response_refusal_present | key 존재 여부 |
| response_refusal_non_null | boolean |
| llm_error_code | AI_INCOMPLETE, AI_RESPONSE, AI_EVIDENCE 등 기존 고정 코드 |

안전 규칙은 다음과 같다.

1. response body, content 일부, refusal 문자열과 예외 본문은 기록하지 않는다.
2. dict key 전체 목록도 provider가 예상하지 않은 민감 필드를 포함할 수 있으므로 기록하지 않는다.
3. type은 type(value).__name__을 그대로 기록하지 않고 허용 목록으로 정규화한다.
4. content 길이는 Python 문자 수만 기록하고 hash, prefix, suffix와 샘플은 남기지 않는다.
5. usage는 기존 세 정수 token 필드만 유지한다.
6. finish_reason은 제한된 길이와 문자 집합을 통과한 값만 기록하고 나머지는 unknown으로 둔다.

## 7. 최소 수정안

### 7.1 평가 transport가 response를 소비하지 않도록 변경

권고안은 SingleCallTransport가 inner response를 읽거나 새 Response로 재구성하지 않고 그대로 반환하는 것이다.

    response = self.inner.handle_request(request)
    self.status_code = response.status_code
    return response

model과 usage는 generate()가 response.json()에 성공한 뒤 기존 안전 trace에 기록한 값을 평가 보고서가 사용한다. transport가 같은 body를 먼저 파싱할 필요가 없다.

latency는 evaluate()에서 generate() 호출 직전과 직후의 monotonic clock으로 측정한다. 이는 retrieval이 끝난 이후의 generation 호출 범위를 재며 response body를 소비하지 않는다.

이 방식은 Content-Encoding별 header 수정 규칙을 평가 코드에 복제하지 않고 HTTPX의 원래 stream 소유권을 유지한다. 이미 디코딩한 body에서 Content-Encoding과 Content-Length만 제거해 재포장하는 대안도 가능하지만, provider response semantics를 수동 복제하므로 권고하지 않는다.

### 7.2 generate() parse stage 추가

response가 반환되면 다음 순서로 stage를 먼저 기록하고 검사한다.

1. transport: client.post가 정상 반환됨
2. json: response.json 성공 여부
3. top_level: dict 여부
4. choices: key 존재와 list 길이
5. choice: 첫 항목 dict 여부
6. finish: finish_reason key와 안전 값
7. message: message key와 dict 여부
8. content: content key, 타입과 문자 수
9. validation: validate_answer 진입

finish_reason=length는 content 파싱이나 citation 검증 전에 AI_INCOMPLETE로 유지한다. finish_reason key가 없는 응답을 기존처럼 stop과 동일하게 취급하지 않고 AI_RESPONSE로 분리하는 방안을 구현 테스트에서 고정한다.

message.refusal이 존재하고 null이 아니면 content를 기록하거나 검증하지 않고 별도 고정 코드로 안전 차단한다. 새 코드명을 AI_REFUSAL로 둘지 기존 AI_RESPONSE 범주로 유지할지는 구현 승인 시 오류 계약과 UI 매핑을 함께 확인한다.

### 7.3 예외 위치는 코드만 기록

현재 넓은 except가 여러 경계를 AI_RESPONSE 하나로 합친다. 사용자 메시지는 유지하되 내부 trace에 response_parse_stage와 고정 failure detail을 남긴다.

예시 detail:

- transport_decode
- json_decode
- top_level_type
- choices_missing
- choices_empty
- choice_type
- finish_missing
- message_missing
- content_missing
- content_type
- refusal

예외 class 이름과 str(exc)는 저장하지 않는다.

## 8. 구현 후 테스트 계획

### transport 회귀

- gzip, deflate와 비압축 official envelope가 body 선소비 없이 generate()에 전달된다.
- transport 호출은 한 번이고 원 response stream은 바깥 Client가 소유한다.
- response header와 body representation을 재포장하지 않는다.
- 두 번째 요청 거부, model 고정, selected evidence ID 검사는 유지한다.

### shape 진단

- JSON decode 실패
- top-level array
- choices 누락, 빈 배열, list가 아닌 값
- choices[0] 비object
- finish_reason 누락, stop, length, 알 수 없는 값
- message 누락 또는 비object
- content 누락, null, 비문자열, 정상 문자열
- refusal key 없음, null, non-null

각 경우 raw content 없이 expected parse stage, shape 필드와 failure code만 비교한다.

### 기존 안전 계약

- 공식 strict envelope는 Answer와 citation 검증을 통과한다.
- completion_tokens=768과 finish_reason=length는 AI_INCOMPLETE다.
- schema-valid이지만 잘못된 quote는 AI_EVIDENCE 안전 차단이다.
- Q006은 transport 0회다.
- API key, Authorization, 전체 prompt와 raw response가 trace 및 artifacts에 없다.

## 9. 비변경 범위

- strict json_schema와 strict=true
- Answer.model_json_schema() 기반 schema
- OUTPUT_LIMIT=768
- GROQ_REQUEST_TOKEN_BUDGET=3500
- system prompt와 compact evidence schema v2
- BM25, embedding, RRF, reranker
- evidence selection과 citation validator
- 자동 retry 및 fallback 금지

output limit 증가는 이번 원인 분석과 최소 수정안에 포함하지 않는다.

## 10. 승인 후 예상 변경 파일

| 파일 | 변경 |
|---|---|
| tools/rag_groq_evaluate.py | response 선소비·재포장 제거, trace 기반 model/usage, 외부 generate 구간 latency |
| mvp/ai.py | raw-free response shape stage와 고정 failure detail |
| tests/test_live_response_transport.py | transport 수정 후 gzip/deflate/비압축 및 shape 회귀 |
| tests/test_groq_structured_output.py | finish/refusal/content 경계와 기존 citation 계약 |
| 새 artifacts | MockTransport 결과만 생성 |
| 후속 결과 문서 | 구현 및 무호출 검증 결과 |

실제 Groq 재호출은 이 수정의 구현·Mock 검증과 별개의 승인 항목이다.

## 11. 사용자 승인 대기

이번 단계에서는 합성 fixture와 MockTransport 테스트만 추가했다. production parser, 평가 transport, output limit과 RAG 코드는 아직 수정하지 않았다. 실제 Groq 호출은 0회이며 raw response나 content를 저장하지 않았다.

최소 수정은 평가 transport의 response 선소비·재포장을 제거하고, generate()에 raw-free parse stage를 추가하는 것이다. 사용자 승인 전에는 구현하거나 실제 Groq를 다시 호출하지 않는다.
