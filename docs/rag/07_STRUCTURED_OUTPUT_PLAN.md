# Groq Structured Outputs 분석 및 최소 수정 계획

- 작성일: 2026-09-13
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 분석 대상: `mvp/ai.py`, `openai/gpt-oss-20b`, 최신 Q002 단일 호출 결과
- 기준 산출물: `artifacts/2026-09-13_rag-groq-evaluation-03/groq_evaluation_report.json`
- 상태: 분석·계획 완료, 구현 및 실제 Groq 재호출 승인 대기
- 권고: 기존 `Answer` 모델을 schema source로 유지하되 Groq strict 요구에 맞게 정규화한 `json_schema`를 요청에 사용

## 1. 결론

현재 SCHAT는 Structured Outputs를 사용하지 않는다. Groq 요청의 `response_format`은 `{"type":"json_object"}`이며, 이는 JSON 문법만 유도하고 SCHAT의 `Answer` 필드·타입·중첩 구조를 강제하지 않는다.

`openai/gpt-oss-20b`에는 `reasoning_effort="low"`를 보내고 있으며 이는 공식 지원 범위다. `reasoning_format`은 보내지 않는다. Groq 공식 문서상 GPT-OSS 20B/120B에는 `reasoning_format`이 지원되지 않으므로 현재처럼 보내지 않는 것이 맞다.

최신 Q002 호출은 HTTP 200, 실제 모델 `openai/gpt-oss-20b`, input 2185, output 768, total 2953 tokens였다. output token이 `OUTPUT_LIMIT=768`과 정확히 같고 trace가 `citation_validation`으로 전환되기 전 `llm_request`에 머물렀다. 현재 코드에서는 `finish_reason`이 `stop` 또는 `None`이 아니면 `validate_answer()` 전에 `AI_INCOMPLETE`를 발생시킨다. 따라서 `finish_reason=length`로 JSON이 완성되기 전에 잘렸을 가능성이 가장 높다. 다만 현재 평가 도구가 안전한 `finish_reason`조차 저장하지 않고 여러 `GuideError`를 `AI_REQUEST_FAILED`로 합쳤으므로 확정 진단은 아니다.

Groq 공식 문서는 `openai/gpt-oss-20b`가 `json_schema`와 `strict:true`를 지원한다고 명시한다. 다만 현재 `Answer.model_json_schema()`를 아무 변환 없이 그대로 strict schema로 보낼 수는 없다. `Answer.format`, `Answer.conflict`, `Statement.label`은 Pydantic default가 있어 생성 schema의 `required`에서 빠지는데, Groq strict mode는 모든 object property가 `required`여야 한다. 모든 object의 `additionalProperties:false` 조건은 현재 schema가 이미 충족한다.

최소 수정은 다음 두 부분이다.

1. `Answer.model_json_schema()`를 단일 source로 사용하되 request 전용 순수 함수가 default keyword를 제거하고 모든 object property를 `required`로 만드는 strict-compatible schema를 생성한다.
2. `response_format`을 `json_schema`/`strict:true`로 바꾸고, raw content를 남기지 않은 채 HTTP status, 반환 model, `finish_reason`, usage와 안전한 실패 코드만 trace한다.

`OUTPUT_LIMIT=768`, system prompt, request token budget 3500, evidence schema v2와 검색 경로는 이번 최소 변경에서 유지한다. strict schema는 형식 준수를 해결하지만 출력 길이 부족을 해결하지는 않는다. 재평가에서 다시 `finish_reason=length`가 확인되면 실제 호출을 반복하지 않고 completion budget을 별도 설계·승인받는다.

## 2. 현재 요청 payload

`mvp/ai.py`의 `generate()`는 다음 payload를 만든다.

```python
payload = {
    "model": settings.llm_model,
    "messages": messages,
    "temperature": 0,
    "response_format": {"type": "json_object"},
    "max_completion_tokens": 768,
    "reasoning_effort": "low",
}
```

Groq 경로에는 `max_tokens`가 아니라 `max_completion_tokens`가 사용된다. `stream`, `tools`, `tool_choice`, fallback model과 retry는 없다.

### 확인 결과

| 확인 항목 | 현재 상태 |
|---|---|
| `response_format` | `json_object` |
| `json_schema` | 사용하지 않음 |
| `strict` | 사용하지 않음 |
| `reasoning_effort` | Groq에만 `low` 전송 |
| `reasoning_format` | 전송하지 않음 |
| `include_reasoning` | 전송하지 않음 |
| `max_completion_tokens` | 768 |

Groq API reference는 `json_object`를 구형 JSON mode로 설명하며, 지원 모델에서는 `json_schema` 사용을 권장한다. GPT-OSS의 `reasoning_effort` 지원값은 `low`, `medium`, `high`다. 별도의 reasoning 문서에는 GPT-OSS가 `reasoning_format`을 지원하지 않고 reasoning을 별도 response field로 제공한다고 명시돼 있다.

- [Groq API reference](https://console.groq.com/docs/api-reference)
- [Groq reasoning 문서](https://console.groq.com/docs/reasoning)

## 3. HTTP 200 응답 처리 경로

현재 비스트리밍 응답은 다음 순서로 처리된다.

1. `httpx.Client(..., follow_redirects=False)`가 `POST /chat/completions`를 한 번 수행한다.
2. 429, 401/403, 기타 4xx/5xx를 각각 `AI_RATE`, `AI_AUTH`, `AI_SERVER`로 분리한다.
3. `response.json()`으로 전체 응답을 Python 값으로 파싱한다.
4. 최상위 값이 dict인지 확인한다.
5. `data.get("usage")`를 `quota.settle()`에 전달한다. 이후 답변이 불완전하거나 citation 검증에 실패해도 실제 usage는 정산된다.
6. `choice = data["choices"][0]`을 읽는다.
7. `choice.get("finish_reason")`이 `stop` 또는 `None`인지 확인한다.
8. 통과하면 trace stage를 `citation_validation`으로 바꾼다.
9. `choice["message"]["content"]` 문자열을 `validate_answer()`에 전달한다.
10. 검증된 citation만 남긴 뒤 evidence 충분성을 다시 검사한다.

`data["model"]`은 현재 production 파서에서 검증하거나 trace하지 않는다. 평가용 `SingleCallTransport`만 안전하게 반환 model, status, latency와 usage를 별도로 읽는다.

## 4. `validate_answer()` 전 실패 조건

`mvp/ai.py`에는 `AI_REQUEST_FAILED`라는 고유 오류 코드가 없다. 이는 `tools/rag_groq_evaluate.py`가 `AI_AUTH`가 아닌 모든 `GuideError`를 하나로 합친 평가 보고용 분류다. 따라서 최신 보고서의 `AI_REQUEST_FAILED`만으로 schema 오류라고 단정할 수 없다.

`validate_answer()`에 들어가기 전에 실패하는 경로는 다음과 같다.

| 조건 | 실제 애플리케이션 코드 | 평가 보고서 현재 표기 |
|---|---|---|
| HTTP timeout | `AI_TIMEOUT` | `AI_REQUEST_FAILED` |
| 응답 JSON decode 실패 | `AI_RESPONSE` | `AI_REQUEST_FAILED` |
| 최상위가 object가 아님 | `AI_RESPONSE` | `AI_REQUEST_FAILED` |
| `choices` 없음/빈 배열 | `AI_RESPONSE` | `AI_REQUEST_FAILED` |
| `finish_reason`이 `stop`/`None` 아님 | `AI_INCOMPLETE` | `AI_REQUEST_FAILED` |
| `message` 또는 `content` 없음 | `AI_RESPONSE` | `AI_REQUEST_FAILED` |

`validate_answer()`에 진입한 뒤 JSON/schema/citation/action/number/unit/source-order 검증이 실패하면 `AI_EVIDENCE`가 내부에서 발생한다. 그러나 `generate()`가 이를 잡아 `blocked("invalid_citation_or_statement")`로 반환하므로 평가 도구에는 일반적으로 exception형 `AI_REQUEST_FAILED`가 아니라 answerable false와 block reason이 보여야 한다.

최신 trace가 `llm_request`에 머물고 output tokens가 768에 정확히 닿은 점은 `finish_reason=length` 가설과 일치한다. 응답 JSON 자체가 깨졌다면 `response.json()` 단계의 `AI_RESPONSE`도 가능하지만, 현 증거상 우선순위는 더 낮다.

## 5. 768-token 절단 가능성

가능성이 높다.

- Q002는 필수 gold stage 10개와 12개 evidence chunk를 포함한다.
- `Answer`는 최대 10 statements를 허용한다.
- 각 statement는 `text`와 하나 이상의 `evidence.quote`를 포함하므로 같은 원문 문장이 JSON 안에서 중복될 수 있다.
- 최신 completion usage가 한도와 동일한 768 tokens다.
- 768은 Groq 모델 자체 한도가 아니다. 공식 모델 페이지의 GPT-OSS 20B 최대 output은 훨씬 크므로 SCHAT가 설정한 애플리케이션 한도다.

다만 raw response와 `finish_reason`을 보존하지 않았기 때문에 “JSON이 잘렸다”를 확정 사실로 쓰지는 않는다. 다음 구현은 content를 저장하지 않고도 `finish_reason`을 확인할 수 있게 해야 한다.

Strict Structured Outputs도 token cap을 넘어선 응답을 완성시켜 주지는 않는다. constrained decoding은 schema 모양을 보장하지만 필요한 JSON 전체가 768 tokens 안에 들어간다는 보장은 아니다. 따라서 strict 전환의 성공 기준에 반드시 `finish_reason == "stop"`을 포함한다.

- [GPT-OSS 20B 모델 한도](https://console.groq.com/docs/model/openai/gpt-oss-20b)

## 6. 현재 Answer Pydantic schema의 strict 적합성

로컬 Pydantic 2.13.5에서 `Answer.model_json_schema()`를 검사한 결과는 다음과 같다.

| object | properties | 현재 required | strict 문제 |
|---|---|---|---|
| Answer | answerable, statements, format, conflict | answerable, statements | format, conflict가 required 아님 |
| Statement | text, evidence, label | text, evidence | label이 required 아님 |
| Evidence | chunk_id, quote | chunk_id, quote | 없음 |

모든 object에는 현재 `additionalProperties:false`가 생성된다. `$defs`와 `$ref`도 사용하며 Groq 공식 문서가 재사용 subschema로 지원하는 구조다. 전체 생성 schema 크기는 compact JSON 기준 약 1,094 bytes다.

Groq strict mode의 필수 조건은 다음과 같다.

- 모든 object property가 `required`여야 한다.
- 모든 object에 `additionalProperties:false`가 있어야 한다.
- 선택 필드는 null union으로 표현하되 필드 자체는 required여야 한다.
- `openai/gpt-oss-20b`는 `strict:true` 지원 모델이다.
- streaming과 tool use는 Structured Outputs와 함께 사용할 수 없다. SCHAT는 둘 다 사용하지 않는다.

따라서 기존 Pydantic 모델을 schema source로 활용할 수는 있지만 생성 결과를 문자 그대로 그대로 보낼 수는 없다. request schema를 strict-compatible하게 정규화해야 한다. runtime의 `Answer.model_validate()`와 더 강한 citation 검증은 그대로 유지한다.

- [Groq Structured Outputs](https://console.groq.com/docs/structured-outputs)

## 7. 대안 비교

| 대안 | 내용 | 장점 | 위험 | 판단 |
|---|---|---|---|---|
| A. 현재 `json_object` 유지 | prompt만으로 Answer 형태 유도 | 변경 없음 | schema 불일치가 계속 가능 | 기각 |
| B. `Answer.model_json_schema()`를 그대로 strict로 전송 | Pydantic 직접 재사용 | 구현이 가장 짧음 | default 필드가 required가 아니어서 strict 조건 위반 | 기각 |
| C. Answer schema를 request용으로 정규화 | 모든 property required, default 제거, closed object 유지 | 단일 schema source 유지, runtime 호환성 보존 | schema 정규화 함수 테스트 필요 | 권고 |
| D. 별도 hand-written Groq schema | 완전 통제 가능 | provider subset에 맞추기 쉬움 | Answer 모델과 drift 가능 | 기각 |
| E. Pydantic 필드 default 자체 제거 | 생성 schema가 자연히 strict해짐 | 변환 함수가 단순해짐 | 기존 Python 호출자와 테스트 계약 변경 | 현재 범위에서 기각 |

## 8. 권고 최소 수정안

### 8.1 Request schema 생성

`mvp/ai.py`에 request 전용 순수 함수를 둔다.

```python
def groq_answer_json_schema() -> dict:
    schema = deepcopy(Answer.model_json_schema())
    # 모든 object node에서:
    # 1. required = list(properties)
    # 2. additionalProperties = False 확인
    # 3. property의 default keyword 제거
    return schema
```

함수는 schema를 손으로 복제하지 않는다. `Answer`, `Statement`, `Evidence`가 바뀌면 같은 source에서 다시 생성한다. runtime Pydantic default는 유지하므로 기존 내부 호출자는 영향을 받지 않는다. strict 응답에서는 `format`, `conflict`, 각 `label`을 항상 명시하게 된다.

정규화 함수는 `$defs` 아래 object까지 재귀 적용하고 입력 schema를 mutate하지 않는다. 모든 object에서 `set(required) == set(properties)`와 `additionalProperties is False`를 자체 검증한 후에만 payload를 만든다.

### 8.2 Groq payload

Groq provider에만 다음 형식을 사용한다.

```python
payload["response_format"] = {
    "type": "json_schema",
    "json_schema": {
        "name": "schat_answer",
        "strict": True,
        "schema": groq_answer_json_schema(),
    },
}
payload["reasoning_effort"] = "low"
payload["max_completion_tokens"] = 768
```

`reasoning_format`은 추가하지 않는다. `include_reasoning`도 이번 최소 변경에서는 추가하지 않는다. internal provider의 기존 JSON object 호환성은 별도 계약이므로 변경하지 않는다.

### 8.3 응답 관측성

raw response와 content를 저장하지 않고 다음 안전한 metadata만 trace한다.

- HTTP status
- 반환 model ID
- `finish_reason`
- usage의 prompt/completion/total token 정수
- response object/choices/message/content 존재 여부를 고정 코드로 표현한 parse stage
- 실패 시 `AI_INCOMPLETE`, `AI_RESPONSE`, `AI_EVIDENCE` 중 실제 코드

평가 도구는 모든 비인증 `GuideError`를 `AI_REQUEST_FAILED`로 합치지 않고 메시지의 고정 괄호 코드를 안전하게 분류한다. 예외 본문, response content, prompt, key와 Authorization은 기록하지 않는다.

## 9. 유지할 검증 경계

Strict schema는 형식 보장일 뿐 의료적 정확성이나 citation 정확성을 보장하지 않는다. 다음 기존 검증은 삭제하거나 완화하지 않는다.

- `Answer.model_validate(json.loads(content))`
- answerable/statements 일관성
- selected chunk ID 존재 여부
- quote가 서버측 실제 `Chunk.text`에 포함되는지 확인
- unsupported action 검사
- 숫자와 단위 보존
- 완전 문장 또는 표 행 검사
- procedure source order 검사
- conflict 및 문서/entity coverage 검사
- citation evidence 재평가

prompt의 compact metadata가 아니라 서버측 실제 Chunk를 검증 기준으로 쓰는 현재 계약도 유지한다.

## 10. Output limit 처리 결정

이번 최소 수정에서는 `OUTPUT_LIMIT=768`과 `GROQ_REQUEST_TOKEN_BUDGET=3500`을 바꾸지 않는다. 이유는 strict schema 변경과 completion 용량 변경을 한 번에 적용하면 실패 원인을 분리할 수 없기 때문이다.

구현 후 mock 검증과 승인된 단일 live 평가에서 다음을 기록한다.

1. `finish_reason`
2. 실제 completion tokens
3. schema parsing 성공 여부
4. statement 수와 citation 검증 결과

`finish_reason=length` 또는 completion tokens 768이 다시 관찰되면 strict schema 실패로 해석하지 않는다. Q002의 완전한 output 크기에 맞춘 completion budget 및 전체 request budget 변경 계획을 별도 작성한다. required evidence 축소, parent 일부 절단, quote 삭제, 근거 없는 statement 병합으로 768에 억지로 맞추지 않는다.

## 11. 변경 예정 범위

사용자 승인 후 예상되는 최소 변경은 다음과 같다.

| 파일 | 변경 |
|---|---|
| `mvp/ai.py` | strict-compatible Answer request schema 생성, Groq `json_schema/strict:true`, 안전한 finish/model/usage trace |
| `tools/rag_groq_evaluate.py` | 실제 GuideError 고정 코드와 finish reason을 원문 없이 기록 |
| `tests/test_mvp_chat.py` | payload schema, finish reason, malformed response, usage 정산 회귀 테스트 |
| `tests/test_rag_contract.py` 또는 집중 테스트 | strict 응답도 기존 exact citation·source order 검증을 통과해야 함을 확인 |

다음은 변경하지 않는다.

- system prompt와 compact evidence schema v2
- `OUTPUT_LIMIT=768`, request token budget 3500
- BM25, embedding, RRF, reranker와 context/evidence selection
- Q002 gold fixture와 selected evidence
- API key 및 설정 파일
- 기존 artifacts

## 12. 테스트 계획

### Schema 단위 테스트

- 모든 object의 property가 전부 required다.
- 모든 object가 `additionalProperties:false`다.
- default keyword가 request schema에 없다.
- `$defs`/`$ref`, enum, array와 Pydantic field constraints가 보존된다.
- 원본 `Answer.model_json_schema()` 객체는 mutate되지 않는다.
- runtime `Answer` default 동작은 기존과 같다.

### Payload 테스트

- Groq payload만 `response_format.type=json_schema`다.
- `json_schema.name=schat_answer`, `strict=true`다.
- `reasoning_effort=low`다.
- `reasoning_format`, tools, tool_choice, stream이 없다.
- `max_completion_tokens=768`, model과 messages가 기존과 같다.
- internal provider 계약은 의도하지 않게 바뀌지 않는다.

### Response 처리 테스트

- HTTP 200 + `finish_reason=stop` + strict-valid content는 `validate_answer()`로 전달된다.
- `finish_reason=length`는 content를 파싱하지 않고 `AI_INCOMPLETE`로 끝나며 safe trace에 `length`만 남긴다.
- JSON이 아니거나 choices/message/content가 없으면 `AI_RESPONSE`다.
- usage는 불완전 응답과 citation 실패에도 실제 값으로 정산된다.
- schema-valid하지만 citation이 틀린 답은 기존과 같이 차단된다.
- Q006은 LLM transport 0회를 유지한다.
- 자동 retry와 fallback은 없다.

### 실행 순서

1. strict schema와 safe trace 테스트를 먼저 작성한다.
2. `mvp/ai.py`와 평가 도구를 최소 범위로 변경한다.
3. 관련 집중 테스트, 전체 테스트와 Ruff를 실행한다.
4. MockTransport에서 Q002 strict-valid, length, invalid-citation 세 경로를 검증한다.
5. mock 결과 문서를 작성하고 멈춘다.
6. 사용자가 별도로 승인한 경우에만 Q002 실제 Groq 호출을 최대 1회 수행한다.

## 13. 합격 기준

1. Groq request가 `json_schema`와 `strict:true`를 사용한다.
2. request schema가 모든 object required/closed 조건을 만족한다.
3. 기존 `Answer`/citation/source-order 검증을 유지한다.
4. Q006 transport 호출은 0회다.
5. Q002 selected evidence와 pre/post required gold 10/10이 유지된다.
6. system prompt, budget, retrieval과 compact evidence schema가 변하지 않는다.
7. raw prompt, raw content, key와 Authorization이 trace/artifacts에 없다.
8. `finish_reason`, 반환 model, usage와 고정 실패 코드는 안전하게 관측된다.
9. 전체 테스트와 정적 검사가 통과한다.
10. 실제 Groq 재호출은 별도 승인 전 0회다.

## 14. 승인 대기

분석상 가장 가능성 높은 직접 원인은 768-token completion 한도 도달에 따른 불완전 종료이며, 현재 `json_object`가 Answer schema를 강제하지 않는 문제도 별도로 존재한다. 다음 변경은 먼저 strict-compatible `Answer` request schema와 안전한 finish reason 관측성을 도입해 두 문제를 분리하는 것이다.

이 문서 작성 과정에서는 실제 Groq를 호출하지 않았고 system prompt, prompt budget, BM25/RAG retrieval, API key, 응답 처리 코드와 기존 artifacts를 수정하지 않았다. 구현 및 mock 검증은 사용자의 명시적 승인 후에만 진행한다.
