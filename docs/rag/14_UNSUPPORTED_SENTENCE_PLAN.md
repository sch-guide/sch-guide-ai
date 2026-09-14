# Groq `unsupported sentence` 무호출 원인 분석 및 최소 수정 계획

- 작성일: 2026-09-13
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 결과: `docs/rag/14_OUTPUT_LIMIT_LIVE_RESULT.md`
- 분석 방식: 현재 코드 정적 추적 및 합성 `httpx.MockTransport`
- 실제 Groq 호출: 0회
- 상태: 원인 경계 분석 완료, 구현 승인 대기
- 최종 권고: **B. strict Answer schema의 필드 설명 강화**

## 1. 결론

현재 system prompt의 extractive-only 요구는 이미 강하고 구체적이다. `statement.text`에 evidence 원문의 완전한 문장 또는 표 행 하나를 그대로 복사하고, paraphrase·문장 일부·근거 없는 내용을 금지하며, 여러 문장은 각각 별도 statement로 나누라고 명시한다. user message는 질문과 compact evidence schema v2만 전달하고 이 규칙을 반복하지 않으므로, 의미 계약은 system prompt에 집중돼 있다.

반면 Groq에 전달되는 strict JSON schema에는 `statement.text`와 `evidence.quote`의 의미 설명이 전혀 없다. 현재 Pydantic 필드는 자료형과 길이만 정의하며, `Answer.model_json_schema()`로 만든 schema에도 해당 `description`이 없다. 즉 모델이 구조를 생성하는 가장 가까운 제약 표면에는 “원문 그대로인 완전한 한 문장”과 “그 문장을 포함하는 정확한 인용”의 역할 차이가 표현되지 않는다.

validator는 의미적 유사성을 허용하지 않는다. NFC Unicode 정규화와 연속 공백 축소 후, `statement.text`를 문장 단위로 나누어 각 문장이 실제 Chunk에서 추출된 완전한 문장/표 행 중 하나와 정확히 같고 해당 evidence quote에도 포함되는지 검사한다. 어미, punctuation, bullet prefix 또는 단어가 달라지면 실패한다.

이번 live 응답 원문은 저장하지 않았으므로 실제로 어떤 문장이나 차이가 실패를 만들었는지는 확정하지 않는다. `validation_reason=unsupported sentence`가 확정하는 범위는 다음 둘 중 하나뿐이다.

1. 최소 한 개의 생성 문장이 위 exact sentence 계약을 만족하지 않았다.
2. statement에 non-empty `label`이 있었지만 그 label이 어느 정규화된 evidence quote에도 그대로 없었다.

최소 수정은 system prompt나 validator를 바꾸지 않고 `Statement.text`, `Statement.evidence`, `Evidence.chunk_id`, `Evidence.quote`에 명시적 Pydantic `Field(description=...)`을 추가하는 것이다. strict schema의 구조와 runtime validator는 유지하면서 모델이 각 JSON 필드를 채우는 바로 그 위치에 extractive 계약을 전달한다.

## 2. 분석 범위와 고정 불변식

이번 작업과 후속 최소 구현에서 다음은 변경하지 않는다.

- BM25, embedding, RRF, reranker
- 승인된 제목-only 정책과 temporal tier
- 선택 evidence 12 chunks 및 compact evidence schema v2
- `OUTPUT_LIMIT=2048`
- `GROQ_REQUEST_TOKEN_BUDGET=5120`
- `response_format=json_schema`, `strict=true`
- Answer JSON의 필드, 자료형, cardinality와 max length
- exact citation, 숫자·단위·행동 및 source-order validator
- system prompt의 현재 내용
- 자동 retry, fallback 및 web search 금지

이 분석에서는 API key, Authorization header, 전체 prompt, live raw response/content를 읽거나 저장하지 않았고 실제 Groq 호출을 수행하지 않았다.

## 3. 현재 prompt의 요구 명확성

### System prompt

현재 system prompt는 다음 계약을 이미 명시한다.

- 공급된 evidence만 사용한다.
- 각 statement는 quote로 완전히 지지돼야 한다.
- evidence 안의 완전한 원문 문장 또는 표 행 하나를 `statement.text`로 그대로 복사한다.
- paraphrase와 외부 의료 지식 추가를 금지한다.
- 조건과 부정을 포함한 문장 전체를 유지한다.
- PDF 줄바꿈은 layout whitespace일 수 있으므로 시각적 한 줄만 떼지 않는다.
- 여러 문장은 별도 statement와 exact citation으로 나눈다.
- 최대 10 statements 안에 필요한 절차를 온전히 담을 수 없으면 `answerable:false`를 반환한다.

따라서 자연어 지시 자체의 명확성은 **높음**으로 판단한다. 같은 지시를 system prompt에 반복 추가하는 A안은 토큰과 중복만 늘릴 가능성이 있으며, 현재 실패 원인을 특정하지 못한 상태에서 첫 변경으로 삼을 근거가 약하다.

### User message

user message의 고정 골격은 질문과 compact evidence group JSON을 제공한다. extractive 규칙은 반복하지 않고 system prompt에 위임한다. 이는 규칙과 데이터를 분리하는 현재 구조와 맞으며, selected evidence 원문 외 문서를 추가로 보내지 않는다.

## 4. 현재 Answer schema의 역할 표현

현재 runtime 모델의 핵심 필드는 다음 계약만 갖는다.

| 필드 | 현재 schema 제약 | 의미 설명 존재 |
|---|---|---|
| `Statement.text` | string, 1~700자 | 없음 |
| `Statement.evidence` | Evidence 1~4개 | 없음 |
| `Evidence.chunk_id` | string | 없음 |
| `Evidence.quote` | string, 4~1600자 | 없음 |

따라서 strict schema만 보면 `statement.text`가 자연스러운 요약문인지 원문 extract인지, `evidence.quote`가 text와 같아야 하는지 더 넓은 정확 인용이어도 되는지 알 수 없다. schema가 structural validity는 강제하지만 grounding 의미는 전달하지 못한다.

현재 서버 계약에서 두 필드의 실제 역할은 다음과 같다.

- `statement.text`: 사용자에게 표시될 내용의 입력값이다. 검증 중 하나 이상의 원문 문장으로 분해되며, 통과 후에는 서버가 검증한 원문 문장들로 canonicalize한다.
- `evidence.quote`: 지정 `chunk_id`의 실제 text 안에 존재해야 하는 정확 인용 범위다. statement의 각 완전한 원문 문장을 포함할 수 있으며, statement와 반드시 전체 문자열이 같을 필요는 없다.

## 5. `unsupported sentence` 판정 경로

HTTP envelope와 strict Pydantic parsing이 끝난 뒤 `validate_answer()`는 각 statement에 다음 순서로 적용한다.

1. `chunk_id`가 selected hits에 존재하는지 확인한다.
2. `clean(evidence.quote)`가 `clean(chunk.text)`의 부분 문자열인지 확인한다. 실패하면 `citation`이다.
3. 기존 action, number, unit 검사를 수행한다. 각 실패는 별도 reason을 가진다.
4. `source_sentences(statement.text)`로 생성 text를 문장/행 단위로 나눈다.
5. 각 생성 문장에 대해 다음을 동시에 만족하는 evidence를 찾는다.
   - 그 문장이 `source_sentences(selected_chunk.text)`의 **완전한 원소와 정확히 동일**하다.
   - 그 문장이 `clean(evidence.quote)` 안에 **그대로 포함**된다.
6. 한 문장이라도 matching evidence가 없으면 전체 statement가 `unsupported sentence`로 실패한다.
7. non-empty `statement.label`이 어느 quote에도 그대로 없을 때도 같은 `unsupported sentence` reason으로 실패한다.
8. 통과한 문장은 서버가 문장마다 별도 verified Statement로 다시 구성하고 evidence quote도 그 완전한 문장으로 canonicalize한다.

따라서 `unsupported sentence`는 response parsing 실패가 아니라 grounding validation 실패다. 다만 현재 reason 하나만으로 문장 불일치와 label 불일치를 구분할 수는 없다.

## 6. 정규화 및 경계별 현재 계약

| 차이 | 현재 처리 | 결과 |
|---|---|---|
| 앞뒤 공백 | 제거 | 허용 |
| 연속 space/tab | 한 칸으로 축소 | 허용 |
| Unicode canonical form | NFC로 정규화 | 조합형/분해형의 canonical equivalence는 허용 |
| Unicode compatibility form | NFKC 미적용 | 전각/호환 문자 등은 원문과 다르면 실패 가능 |
| PDF soft line wrap | 제한된 한국어 연결어 패턴이면 한 문장으로 다시 연결 | 연결 후 완전 문장과 같으면 허용 |
| 일반 줄바꿈 | 문장·목록·표 행 경계로 유지될 수 있음 | 임의 줄 병합/분할은 실패 가능 |
| bullet/list prefix | 자동 제거하지 않음 | 원문 문장의 일부이면 그대로 필요 |
| punctuation | canonicalization 없음 | 마침표·물음표·콜론 등의 추가/삭제/변경은 실패 |
| 존댓말/어미 | 의미 비교 없음 | 실패 |
| 임의 문장 일부 | 원문 sentence 원소와 같지 않음 | 실패 |
| 완전한 한 원문 문장만 선택 | 원문 sentence 원소와 같음 | 허용 |
| 두 완전한 원문 문장 병합 | 생성 text도 두 문장으로 분리 가능 | 둘 다 exact이면 허용 후 서버가 두 statements로 분리 |
| 대소문자 | case folding 없음 | 영문 case가 다르면 실패 |

“완전 일치”는 Chunk 전체 문자열과 statement 전체 문자열의 equality가 아니다. 정규화·문장 분해 후 생성된 **각 문장**이 원문에서 분해된 문장/표 행의 완전한 원소여야 한다는 뜻이다.

## 7. 합성 MockTransport 재현 결과

모든 사례는 로컬 합성 Chat Completions envelope와 `httpx.MockTransport`만 사용했다. 실제 네트워크 호출은 0회이며 합성 content 원문은 artifacts에 저장하지 않았다.

| 합성 응답 유형 | transport 호출 | 현재 결과 | reason/동작 |
|---|---:|---|---|
| 원문 완전 일치 | 1 | 통과 | `response_parse_stage=complete`, 1 statement |
| 존댓말/어미 변경 | 1 | 차단 | `AI_EVIDENCE / unsupported sentence` |
| 임의 문장 일부만 사용 | 1 | 차단 | `AI_EVIDENCE / unsupported sentence` |
| 두 완전한 원문 문장을 한 text로 병합 | 1 | 통과 후 정규화 | 2개의 verified statements로 분리 |
| punctuation만 변경 | 1 | 차단 | `AI_EVIDENCE / unsupported sentence` |
| quote는 정확, text는 paraphrase | 1 | 차단 | `AI_EVIDENCE / unsupported sentence` |
| text는 정확, quote는 문장 일부 | 1 | 차단 | quote 자체는 Chunk 부분 문자열이지만 완전한 text를 포함하지 않아 `unsupported sentence` |

### 병합 사례의 해석

system prompt는 여러 문장을 별도 statement로 보내라고 요구하지만 validator는 두 완전한 원문 문장을 한 statement에 넣은 응답을 안전하게 분해할 수 있으면 차단하지 않는다. 이는 근거 없는 단계 병합을 허용하는 동작과 다르다. 두 문장은 각각 원문에서 완전히 검증되고 결과 Answer에서는 분리된다.

이번 live 실패가 이 동작과 관련 있다고 추정하지 않는다. live raw content가 없으므로 재현 표는 가능한 validator 경계를 보여줄 뿐이다.

## 8. 대안 비교

| 기준 | A. prompt 강화 | B. schema description 강화 | C. 표시용 자연어/검증용 extract 분리 | D. validator 완화 |
|---|---|---|---|---|
| 실패 예방 가능성 | 중간. 이미 강한 지시의 반복 | 높음. 모델이 값을 생성하는 필드 자체에 계약 제공 | 높음. 역할은 가장 명확 | 겉보기 통과율은 높아질 수 있으나 안전성 저하 |
| 변경 범위 | system prompt 및 token snapshot | Pydantic Field 설명과 schema/test | Answer schema, UI, validator, citation 계약 전반 | 핵심 grounding validator |
| strict schema 활용 | 간접 | 직접 | 직접이나 새 필드 필요 | 무관 |
| prompt token 영향 | 지속 증가 | 작은 schema description 증가 | 가장 큼 | 없음 |
| 기존 API/UI 영향 | 없음 | 없음 | 있음 | 없음 |
| citation 안전성 | 유지 | 유지 | 설계를 잘하면 유지 가능 | 약화 위험 |
| rollback | prompt 상수 복원 | Field description과 version 복원 | schema/UI migration 복원 | matcher 복원 및 재검증 필요 |
| 판단 | 예비 대안 | **권고** | 후속 대안 | 기각 |

### A. extractive-only prompt를 더 강화

현재 prompt에 이미 verbatim, complete sentence/table row, no paraphrase, separate statements, conditions/negations 보존이 있다. 추가 문구는 중복 지시가 될 가능성이 높다. B 적용 후에도 mock 또는 승인된 단일 live 평가에서 같은 실패가 반복될 때만 짧은 예시 또는 금지 예시를 추가하는 별도 변경으로 검토한다.

### B. `statement.text`와 citation schema description 강화

최소 권고안이다. JSON 구조를 바꾸지 않고 다음 의미를 각 필드 description에 담는다.

- `Statement.text`: selected evidence에서 복사한 정확히 하나의 완전한 원문 문장 또는 표 행. paraphrase, fragment, 두 문장 결합, punctuation/어미 변경 금지.
- `Statement.evidence`: 해당 text 전체를 지지하는 1~4개의 정확한 출처 연결.
- `Evidence.chunk_id`: supplied source의 정확한 chunk ID.
- `Evidence.quote`: 지정 chunk의 원문에 그대로 존재하고 `statement.text`의 완전한 원문 문장을 포함하는 exact excerpt. 요약 금지.

`Answer.model_json_schema()`가 description을 자동 포함하므로 별도 hand-written provider schema를 만들지 않는다. `_strict_schema_node()`는 description을 보존하면서 기존 required/additionalProperties 계약을 유지해야 한다.

### C. 사용자 표시용 자연어와 검증용 extractive statement 분리

예를 들어 검증용 exact extracts와 별도의 natural-language display를 둘 수 있다. 역할은 명확하지만 자연어 문장 자체를 무엇으로 검증할지 새 계약이 필요하고, Answer schema·UI·citation coverage·안전 정책 전체가 바뀐다. 현재 SCHAT가 extractive answer를 안전 경계로 채택한 상태에서 단일 실패를 해결하기에는 과도하다. 향후 사용성 요구가 명시될 때 별도 architecture 승인 대상으로 둔다.

### D. validator 완화

semantic similarity, punctuation 무시, 어미 허용 또는 부분 문장을 허용하면 조건·부정·용량·행동이 바뀐 문장을 통과시킬 수 있다. 병원 지침 안전성과 exact citation 계약을 약화하므로 기각한다. 현재 NFC/whitespace 정규화보다 느슨하게 만들지 않는다.

## 9. 최종 권고와 최소 코드 변경 범위

**B안만 먼저 구현한다.** system prompt는 이미 충분히 명확하므로 변경하지 않고, strict schema에 누락된 필드 의미를 채운다.

예상 최소 변경 범위:

| 파일 | 변경 |
|---|---|
| `mvp/ai.py` | `Evidence`와 `Statement`의 기존 Field에 description 추가, 생성 계약 변경 식별을 위한 `AI_VERSION` 증가 검토 |
| `tests/test_groq_structured_output.py` | strict schema에 네 필드 description이 존재하고 strict-compatible임을 검증 |
| grounding 관련 테스트 | 이번 MockTransport 7개 경계와 기존 citation/action/number/unit/source-order 회귀 고정 |
| 새 mock artifacts/결과 문서 | raw content 없이 pass/block, validation reason, token reservation과 Q006 call count 기록 |

다음은 변경하지 않는다.

- `source_sentences()`, `sentence_evidence()`와 `validate_answer()`의 판정 로직
- system prompt 문자열
- Answer JSON 구조 및 compact evidence payload
- selected 12 chunks와 retrieval/context/evidence 결과
- output/request budget

`AI_VERSION`은 schema description이 모델 생성 계약을 바꾸는 점을 캐시에서 구분해야 한다면 11에서 12로 올린다. 이는 구현 승인 시 현재 cache key 사용 경로를 다시 확인한 뒤 적용하며, schema 필드나 retrieval version은 바꾸지 않는다.

## 10. 구현 후 Mock 검증 계획

실제 Groq 호출 없이 다음을 검증한다.

1. `response_format=json_schema`, `strict=true`가 유지된다.
2. strict schema의 `Statement.text`, `Statement.evidence`, `Evidence.chunk_id`, `Evidence.quote`에 의미 description이 존재한다.
3. schema의 required 필드, `additionalProperties:false`, 길이와 max statements 계약은 이전과 같다.
4. 원문 완전 일치 응답은 정상 통과한다.
5. 어미 변경, 임의 fragment, punctuation 변경, paraphrase는 `AI_EVIDENCE / unsupported sentence`로 차단된다.
6. 정확 text와 불충분한 partial quote 조합도 차단된다.
7. 두 완전한 원문 문장을 한 text로 보낸 현재 canonical split 동작을 명시적으로 고정하거나, 이를 더 엄격히 차단하려면 별도 validator 강화 승인을 받는다. 이번 B안에서 matcher는 바꾸지 않는다.
8. 잘못된 chunk/quote는 기존 `citation`으로 차단된다.
9. 숫자·단위·행동·source order 검증이 유지된다.
10. Q002 selected evidence 12개, pre/post required gold recall 10/10, parent/branch/source order가 유지된다.
11. Q006은 transport 0회와 고정 근거 부족 문구를 유지한다.
12. schema description 증가 후 실제 계산 reservation이 5120 admission cap 안에 있고 required group이 모두 남는지 확인한다.
13. 관련 테스트, 전체 테스트와 Ruff를 실행한다.

Mock artifacts에는 입력/응답 원문을 저장하지 않고 다음 안전 메타데이터만 기록한다.

- case name
- transport call count
- pass/block
- response parse stage
- failure code와 고정 validation reason
- verified statement count
- schema description 존재 여부
- selected chunk ID와 required coverage 요약
- token reservation과 headroom

## 11. 후속 단일 live 평가 합격 기준

B안의 구현 및 Mock 결과를 사용자가 승인한 뒤에만 Q002 실제 호출 1회를 별도로 요청한다. 자동 retry와 두 번째 호출은 없다.

합격 기준:

1. Q006 실제 Groq 호출 0회
2. Q002 실제 Groq 호출 정확히 1회
3. HTTP 200, 반환 모델 일치, `finish_reason=stop`
4. completion tokens < 2048
5. `response_parse_stage=complete`
6. failure code와 validation reason 없음
7. `answerable=true`, 최종 verified statements 1~10개
8. 모든 statement가 selected Chunk의 완전한 원문 문장/표 행
9. exact quote/chunk citation 및 citation coverage 100%
10. source order 역전 0건
11. 근거 없는 숫자·단위·조건·부정·행동 0건
12. selected evidence 12 chunks 외 원문 전송 없음

실패하더라도 raw response를 저장하거나 재호출하지 않는다. 안전 메타데이터만 기록하고 다음 변경은 별도 분석·승인을 받는다.

## 12. 승인 대기

이번 분석에서 실제 Groq 호출과 코드 수정은 수행하지 않았다. live raw response가 없으므로 이전 응답의 구체적인 실패 문장, punctuation, 어미, label 또는 병합 여부를 추정하지 않는다.

최종 권고는 strict-compatible Pydantic schema에 필드별 extractive 의미를 추가하는 B안이다. system prompt와 validator는 그대로 유지한다. 구현, Mock artifacts 생성 및 테스트는 사용자 승인 후에만 진행한다.
