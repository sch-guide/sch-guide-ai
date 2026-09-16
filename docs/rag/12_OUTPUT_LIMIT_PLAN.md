# Groq Completion Output Limit 조정 계획

- 작성일: 2026-09-13
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 결과: `docs/rag/11_LIVE_REEVALUATION_RESULT.md`
- 확정 실패: `finish_reason=length`, `AI_INCOMPLETE`, completion 768/768
- 단계: 분석·설계 완료, 구현 승인 대기
- 실제 Groq 호출: 0회
- 최종 권고: **`OUTPUT_LIMIT=2048` 및 결합된 내부 admission cap `GROQ_REQUEST_TOKEN_BUDGET=5120`**

## 1. 목적과 승인 경계

Q002 실제 단일 호출은 HTTP 200을 받았지만 completion이 현재 상한 768 tokens를 정확히 사용하고 `finish_reason=length`로 종료됐다. response parser는 이를 `AI_INCOMPLETE`로 올바르게 차단했다. 이번 계획은 retrieval·prompt·schema를 바꾸지 않고 완결된 strict JSON Answer를 받을 수 있도록 completion 상한만 재설계한다.

이 문서는 계획 산출물이다. 사용자 승인 전에는 다음을 수행하지 않는다.

- `mvp/ai.py` 또는 설정값 수정
- 실제 Groq 호출
- system prompt, compact evidence serialization 또는 Answer schema 수정
- BM25, embedding, RRF, reranker와 context/evidence bundle 수정
- 기존 artifacts 덮어쓰기

## 2. 현재 OUTPUT_LIMIT 정의와 전달 경로

`OUTPUT_LIMIT`은 `mvp/ai.py` 모듈 상수로 정의돼 있다.

```python
OUTPUT_LIMIT = 768
```

전달 경로는 다음과 같다.

1. `prompt_messages()`가 evidence group을 하나씩 추가할 때 `estimated_tokens()`로 요청 예약량을 계산한다.
2. `estimated_tokens(messages, "groq_free")`는 보수적으로 계산한 message tokens에 `OUTPUT_LIMIT`을 더한다.
3. 그 합계가 `GROQ_REQUEST_TOKEN_BUDGET`을 넘는 group은 prompt에서 제외된다.
4. `generate()`가 budget 적용 후 evidence 충분성과 required coverage를 다시 검사한다.
5. `generate()`가 같은 `estimated_tokens()` 값을 `Quota.reserve()`에 전달한다.
6. Groq payload에는 `max_completion_tokens=OUTPUT_LIMIT`이 설정된다.
7. HTTP 200 JSON의 `usage.total_tokens`가 있으면 `Quota.settle()`이 예약량을 실제 사용량으로 교체한다.
8. `finish_reason`이 `stop`이 아니면 Answer content 파싱 전에 `AI_INCOMPLETE`로 차단한다.

즉 하나의 상수가 provider의 생성 상한뿐 아니라 prompt group admission, byte-budget 보수 계산과 quota 선예약량에도 영향을 준다.

## 3. GROQ_REQUEST_TOKEN_BUDGET=3500의 정확한 의미

`GROQ_REQUEST_TOKEN_BUDGET`은 다음 중 어느 하나도 아니다.

- prompt/input만의 상한이 아니다.
- Groq 모델의 context window가 아니다.
- 실제 사용량을 그대로 나타내는 provider quota가 아니다.

현재 SCHAT에서의 의미는 **로컬에서 계산한 보수적 요청 예약량의 per-request admission cap**이다. Groq 경로의 계산식은 개념적으로 다음과 같다.

```text
estimated request tokens
  = ceil((message framing + system/user content tokens) × 1.10)
  + OUTPUT_LIMIT
```

따라서 3500은 추정 prompt와 예약 completion을 합친 총량 상한이다. group-level budget 선택도 이 값을 사용하므로 이를 넘으면 선택 evidence 일부가 원자 group 단위로 제외될 수 있다.

Quota와의 관계는 다음과 같다.

- `Quota.reserve()`는 위 추정 총량을 요청 전에 minute/day token ledger에 기록한다.
- 현재 앱 자체 한도는 minute 8000, rolling day 200000 tokens다.
- 정상 HTTP JSON에 usage가 있으면 `Quota.settle()`이 예약량을 `usage.total_tokens`로 교체한다.
- `finish_reason=length`여도 JSON과 usage 파싱이 성공하면 실제 total tokens로 정산된다.
- 429·인증·HTTP 오류처럼 usage를 얻지 못한 일부 경로는 예약 취소 또는 보수적 예약 유지 정책을 따른다.

현재 live 값에서는 로컬 예약량 3130과 실제 최대 사용량 3213 사이에 83 tokens 차이가 있었다. 로컬 계산은 admission을 위한 근사치이므로 provider usage와 정확히 같다고 가정하지 않는다.

## 4. Groq 모델 제약

Groq 공식 모델 문서상 `openai/gpt-oss-20b`의 context window는 131,072 tokens, 최대 output은 65,536 tokens다. 현재 가격은 input $0.075/1M tokens, output $0.30/1M tokens로 안내된다. [Groq GPT-OSS 20B 모델 문서](https://console.groq.com/docs/model/openai/gpt-oss-20b)

Groq Chat Completions API에서 `max_completion_tokens`는 생성 가능한 최대 토큰 수이며 input과 output 합계는 모델 context length 안에 있어야 한다. [Groq API Reference](https://console.groq.com/docs/api-reference)

현재 Q002 prompt 2445와 가장 큰 후보 output 2048의 합은 4493 tokens로 131,072의 약 3.4%다. 모든 후보가 provider context window 및 65,536 max output보다 충분히 작다. 따라서 현재 병목은 provider context가 아니라 SCHAT의 completion 상한과 내부 3500 admission cap이다.

공식 rate-limit 표의 실제 적용값은 계정·조직 plan에 따라 달라질 수 있다. 이번 계획은 외부 한도를 추정해 코드화하지 않고, 현재 SCHAT의 더 작은 자체 minute/day quota를 그대로 유지한다. [Groq Rate Limits](https://console.groq.com/docs/rate-limits)

## 5. Q002 기준 후보 계산

기준값:

- 실제 prompt usage: 2445 tokens
- 실제 completion usage: 768 tokens
- 실제 total usage: 3213 tokens
- 현재 로컬 보수 예약량: 3130 tokens
- 로컬 예약량 중 고정 completion을 제외한 부분: `3130 - 768 = 2362`
- 현재 output 단가: $0.30/1M tokens

아래 비용은 input 2445 tokens가 동일하고 각 후보가 output 상한까지 모두 사용한다고 가정한 최대치다. 실제 응답이 `stop`으로 일찍 끝나면 더 적다.

| OUTPUT_LIMIT | 현재 대비 추가 output | 로컬 예상 예약량 | 실제 input+최대 output | 3500 충돌 | 최대 요청 비용 | 현재 대비 최대 비용 증가 |
|---:|---:|---:|---:|---|---:|---:|
| 1024 | +256 | 3386 | 3469 | 기술적으로 통과, 안전 headroom 부족 | $0.000491 | +$0.000077 |
| 1280 | +512 | 3642 | 3725 | 충돌 | $0.000567 | +$0.000154 |
| 1536 | +768 | 3898 | 3981 | 충돌 | $0.000644 | +$0.000230 |
| 2048 | +1280 | 4410 | 4493 | 충돌 | $0.000798 | +$0.000384 |

현재 768 상한의 같은 방식 최대 비용은 약 $0.000414다.

## 6. 후보별 비교

### 6.1 1024

예상 장점:

- 상수 증가 폭과 비용 증가가 가장 작다.
- 로컬 예상 예약량 3386으로 현재 3500 아래에 있어 12개 evidence가 admission을 통과할 가능성이 높다.
- provider context/window에는 영향이 없다.

위험과 quota 영향:

- completion 여유는 256 tokens뿐이다. 이미 768을 모두 사용한 10-stage procedure strict JSON에 충분하다는 근거가 없다.
- 3500 대비 로컬 headroom은 114 tokens뿐이며 기존 안전 기준 `max(256, ceil(3386×8%))=271`을 충족하지 않는다.
- 실제 prompt 2445를 기준으로 상한까지 생성하면 total 3469로 3500 대비 31 tokens만 남는다.
- 재차 `finish_reason=length`가 날 가능성을 보수적으로 제거하지 못한다.

판단: 한 줄만 바꾸는 최소안이지만 live 재평가를 다시 소비할 가치가 충분하지 않아 기각한다.

### 6.2 1280

예상 장점:

- 현재보다 66.7% 많은 completion 공간을 제공한다.
- 1024보다 strict JSON closing structure와 quote 반복을 완결할 여유가 크다.
- 최대 추가 비용은 요청당 약 $0.000154로 작다.

위험과 quota 영향:

- 로컬 예상 예약량 3642가 3500을 넘어 현재 cap과 충돌한다.
- `OUTPUT_LIMIT`만 변경하면 마지막 required parent group이 budget 단계에서 제외되고 Groq 호출 전 evidence gate가 차단될 수 있다.
- 10 statements와 exact quotes, reasoning token까지 고려하면 여전히 경계값일 수 있다.

판단: `GROQ_REQUEST_TOKEN_BUDGET`을 함께 조정해야 하는 최소 실용 후보지만 보수적 완결성 기준에는 부족하다.

### 6.3 1536

예상 장점:

- 현재 상한의 2배로, 구조화 JSON·최대 10 statements·exact quote를 위한 의미 있는 여유를 준다.
- input을 포함한 최대 3981 tokens는 provider context의 약 3.0%다.
- 최대 추가 비용은 요청당 약 $0.000230다.

위험과 quota 영향:

- 로컬 예상 예약량 3898이 3500을 초과한다.
- completion에 reasoning token이 포함되거나 10개 statement가 긴 원문 문장을 각각 반복하면 다시 길이 경계에 닿을 가능성을 배제할 수 없다.
- 안전 headroom을 유지하려면 내부 cap도 최소 약 4210 이상이어야 한다.

판단: 비용과 여유의 균형안이다. 다만 이번 목표는 다시 truncation을 피하는 보수적 단일 평가이므로 최종 권고보다 한 단계 낮다.

### 6.4 2048

예상 장점:

- 현재보다 1280 tokens, 약 166.7% 더 많은 completion 공간을 제공한다.
- 최대 10 statements 각각에 extractive text와 exact evidence quote가 들어가는 JSON 구조, closing tokens와 low-effort reasoning을 함께 수용할 후보 중 가장 큰 여유가 있다.
- input과 최대 output 합계 4493은 provider context의 약 3.4%이며 최대 output 한도의 약 3.1%에 불과하다.
- 출력 상한까지 모두 사용해도 최대 추가 비용은 현재 대비 약 $0.000384다.

위험과 quota 영향:

- 현재 3500 admission cap과 충돌하므로 단독 변경할 수 없다.
- 로컬 예상 예약량은 4410이며, 한 요청은 현재 자체 minute 8000 quota 안에 들지만 동시 또는 연속 요청의 가용량은 줄어든다.
- 더 긴 출력이 허용되므로 모델이 불필요하게 장황해질 가능성은 있으나 최대 10 statements, extractive Answer schema와 citation validator가 결과 표면을 제한한다.

판단: Q002 strict JSON 완결을 우선하는 가장 보수적인 후보로 선택한다.

## 7. 최종 권고와 결합 budget

### 권고값

```python
OUTPUT_LIMIT = 2048
GROQ_REQUEST_TOKEN_BUDGET = 5120
```

`OUTPUT_LIMIT=2048`만 바꾸고 3500을 유지하면 group admission에서 Q002 required evidence를 잃을 수 있다. 따라서 `GROQ_REQUEST_TOKEN_BUDGET`의 5120 조정은 prompt를 늘리는 변경이 아니라 **동일한 12개 evidence와 더 큰 completion 예약을 admission할 수 있게 하는 결합된 내부 cap 변경**이다.

5120을 선택한 근거:

- 새 로컬 예상 예약량 4410을 수용한다.
- 예상 headroom은 `5120 - 4410 = 710` tokens다.
- 기존 안전 기준 `max(256, ceil(4410×8%)) = 353`을 충족한다.
- 실제 prompt 2445와 최대 completion 2048의 합 4493을 기준으로도 627 tokens가 남는다.
- cap 5120 자체가 quota에 예약되는 것은 아니다. 실제 `Quota.reserve()`에는 계산된 약 4410이 전달되고 usage 수신 후 실제 `total_tokens`로 정산된다.
- 4410은 현재 앱의 단일 요청 minute token 한도 8000 안에 있다.

`GROQ_REQUEST_TOKEN_BUDGET`을 input-only 의미로 재정의하거나 계산식을 분리하는 것은 이번 최소 변경 범위에 포함하지 않는다. 기존 의미를 유지한 채 값만 결합 조정하는 것이 rollback과 회귀 검증이 가장 단순하다.

## 8. Q002 출력 크기에 대한 보수성

현재 Answer 계약은 최대 10 statements이며 각 statement는 다음을 포함할 수 있다.

- extractive `text` 최대 700자
- evidence 1~4개
- evidence별 exact `quote` 최대 1600자
- `chunk_id`, 선택적 label과 JSON 구조 문자

schema의 이론적 최대 크기를 모두 수용하는 것이 목표는 아니다. 시스템은 필요한 문장만 선택하고 10개 이하를 사용하도록 요구한다. 하지만 Q002는 필수 gold stage 10개, common/adult/pediatric branch와 원문 순서를 보존해야 하고, `text`와 `quote`가 일부 중복되므로 768 또는 1024는 지나치게 타이트하다.

이전 조건대로 1 procedural unit을 1 statement로 강제하지 않고, statement 수를 줄이기 위해 근거 없는 단계를 병합하지도 않는다. 2048은 schema 최대치를 허용하려는 값이 아니라 정상적인 extractive multi-chunk 답변과 strict JSON 완결에 필요한 보수적 실행 여유다.

## 9. 변경하지 않는 불변식

output limit 조정 시에도 다음은 변경하지 않는다.

- 승인된 SCHAT BM25와 temporal tier
- embedding model과 semantic search
- RRF 및 reranker
- Q002 선택 evidence 12 chunks
- required/optional group, parent 원자성과 branch/source order
- `response_format=json_schema`, `strict=true`
- `Answer` Pydantic/JSON schema
- exact citation, 숫자·단위·행동 및 source-order validator
- system prompt
- compact evidence schema v2
- 자동 retry 및 fallback 금지
- Q006 LLM 호출 0회

`finish_reason=length` 계약도 유지한다. 2048에서도 length가 발생하면 `AI_INCOMPLETE`로 차단하고, 자동으로 값을 더 올리거나 재요청하지 않는다.

## 10. 최소 코드 변경 범위

사용자 승인 후 예상 변경은 다음으로 제한한다.

| 파일 | 변경 |
|---|---|
| `mvp/ai.py` | `OUTPUT_LIMIT` 768→2048, 결합 admission cap 3500→5120, generation/cache 구분을 위한 `AI_VERSION` 10→11 |
| `tests/test_groq_structured_output.py` | payload의 `max_completion_tokens=2048`, stop/length 계약 갱신 |
| `tests/test_live_response_transport.py` | completion 2048 + `finish_reason=length`의 `AI_INCOMPLETE` 회귀 |
| prompt/evidence 관련 기존 테스트 | 12 chunks, required recall, 새 예약량/headroom 및 quota 계약 갱신 |
| 새 artifacts 디렉터리 | Mock 결과와 별도 승인된 단일 live 결과 저장 |
| 후속 결과 문서 | 변경·테스트·단일 평가 결과 기록 |

`tools/rag_groq_evaluate.py`는 이미 상수를 import하고 안전 trace를 기록하므로 동작 변경이 필요하지 않아야 한다. 구현 중 그 외 production 파일 수정이 필요하다는 증거가 나오면 작업을 멈추고 범위를 다시 승인받는다.

## 11. Mock 테스트 계획

### 상수와 payload

- Groq payload가 `max_completion_tokens=2048`을 정확히 사용한다.
- `response_format=json_schema`와 `strict=true`가 유지된다.
- system prompt와 compact evidence schema version 2 snapshot이 변하지 않는다.
- reasoning effort, model과 selected evidence IDs가 변하지 않는다.

### budget 및 quota

- Q002 12 chunks의 예상 예약량은 현재 tokenizer 기준 약 4410이다.
- request budget 5120에서 headroom은 약 710이며 최소 `max(256, 8%)` 기준을 통과한다.
- pre/post 필수 gold recall은 10/10이다.
- required group 100%, parent partial inclusion 0건, common/adult/pediatric branch와 source order를 유지한다.
- `Quota.reserve()`에는 5120이 아니라 실제 추정 예약량이 전달된다.
- Mock usage가 반환되면 `Quota.settle()`이 `usage.total_tokens`로 교체한다.
- 단일 예약량이 앱 자체 minute 8000 한도를 넘지 않는다.

### 완료와 안전 차단

- strict-valid `finish_reason=stop`, completion <2048 응답은 기존 Answer/citation 검증을 통과한다.
- completion 2048, `finish_reason=length` fixture는 반드시 `AI_INCOMPLETE`다.
- length 응답에 자동 retry 또는 fallback이 발생하지 않는다.
- malformed schema는 `AI_RESPONSE`, 잘못된 citation은 `AI_EVIDENCE`로 기존처럼 차단된다.
- Q006은 transport 0회다.

### 회귀

- 관련 집중 테스트, 전체 테스트와 Ruff를 실행한다.
- BM25 Q001~Q006, semantic/RRF/rerank 및 phase1 context artifacts는 수정하지 않는다.
- 기존 Groq live artifacts를 덮어쓰지 않는다.

## 12. 별도 승인 후 실제 단일 호출 합격 기준

Mock 검증이 모두 통과하고 사용자가 실제 전송을 별도로 승인한 경우에만 Q006 확인 후 Q002를 한 번 호출한다.

합격 기준:

1. Q006 실제 Groq 호출 0회
2. Q002 실제 Groq 호출 정확히 1회
3. 선택 evidence 12 chunks만 전송
4. pre/post 필수 gold recall 10/10
5. HTTP 200 및 반환 모델 `openai/gpt-oss-20b`
6. `finish_reason=stop`
7. completion tokens가 2048 미만
8. `response_parse_stage=complete`
9. `failure_code`와 `failure_detail` 없음
10. `answerable=true`
11. statement 수 1~10
12. 모든 statement가 1개 이상의 실제 selected chunk evidence를 가짐
13. exact quote/chunk citation validation 통과
14. citation coverage 100%
15. source order 역전 0건
16. 근거 없는 숫자·단위·조건·행동 추가 0건
17. 자동 retry, fallback과 추가 호출 0회

`finish_reason=length`이면 합격 실패로 기록하고 즉시 `AI_INCOMPLETE`로 종료한다. 2048보다 높은 상한으로 자동 변경하거나 같은 평가를 다시 호출하지 않는다.

## 13. Rollback

영속 데이터와 retrieval 구조를 바꾸지 않으므로 rollback은 상수와 테스트 단위다.

1. `OUTPUT_LIMIT`을 768로 되돌린다.
2. `GROQ_REQUEST_TOKEN_BUDGET`을 3500으로 되돌린다.
3. `AI_VERSION`을 10으로 되돌린다.
4. 해당 상수에 맞춰 갱신한 테스트를 되돌린다.
5. 새 Mock/live artifacts는 기존 결과와 분리해 보존한다.

BM25, embedding, RRF, reranker, evidence bundle, Answer schema와 기존 평가 artifacts는 rollback 대상이 아니다.

## 14. 승인 대기

최종 권고는 `OUTPUT_LIMIT=2048`이다. 현재 3500은 input-only budget이 아니라 output 예약을 포함한 admission cap이므로, 동일 12개 evidence를 유지하려면 `GROQ_REQUEST_TOKEN_BUDGET=5120`을 함께 조정해야 한다. 이 결합 조정은 prompt나 evidence를 늘리지 않고 더 큰 completion 상한을 안전하게 예약하기 위한 최소 변경이다.

이번 단계에서는 파일 분석과 이 계획 문서 작성만 수행했다. 실제 Groq 호출은 0회이며 코드, budget 값, system prompt와 RAG/BM25 경로는 수정하지 않았다. 사용자 승인 전에는 구현 또는 실제 재평가로 진행하지 않는다.
