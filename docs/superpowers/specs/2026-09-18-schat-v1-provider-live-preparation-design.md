# SCHAT v1.0 Provider Live 준비 설계

## 목표

검증된 동일 evidence로 Groq와 Gemini controlled-generation 응답을 비교할 수 있는 evaluation-only 경계를 만든다. 이번 단계는 요청 payload를 메모리에서 만들고 Mock 응답을 공통 validator로 검증하는 데까지만 포함하며, 실제 HTTP 전송·API key 처리·production 연결은 포함하지 않는다.

## 선택한 접근

Provider별 SDK나 dormant live sender를 추가하지 않고 표준 라이브러리와 기존 Pydantic 계약만 사용하는 offline harness를 둔다. Harness는 provider-neutral case를 Groq/Gemini payload 청사진으로 변환하고, 저장된 Mock envelope를 같은 `validate_controlled_paraphrase()` 및 `decide_controlled_generation()` 경계로 정규화한다.

이 방식은 다음을 보장한다.

- 네트워크 전송 가능한 함수가 없어 승인 전 호출이 구조적으로 불가능하다.
- 두 provider는 같은 intent, schema와 SourceUnit 순서를 사용한다.
- provider 차이는 wire-format adapter에만 한정된다.
- Groq strict 지원이 공식적으로 확인되지 않은 `minItems`/`maxItems`는 공통 wire schema에서 제거하고, 같은 개수 제한은 기존 Pydantic 서버 validator가 fail closed로 유지한다.
- raw prompt, 응답과 SourceUnit text는 artifact에 저장하지 않는다.
- production retrieval, embedding, selection과 validator에는 연결하지 않는다.

## 공통 입력 계약

- `case_id`: 평가용 비임상 식별자
- `intent`: 기존 QueryPlan kind와 호환되는 출력 의도
- `source_units`: 최대 16개의 검증된 SourceUnit
- `schema`: `build_controlled_generation_schema()` 결과
- `wire schema`: 위 schema에서 `minItems`/`maxItems`만 제거한 closed object/array/enum 계약
- `prompt`: `build_controlled_generation_prompt()`가 메모리에서 생성한 evidence-only instruction

원 질문은 provider에 보내지 않는다. 문서명, 페이지, section, production chunk ID, 사용자 ID와 전체 chunk도 보내지 않는다. Provider가 받는 evidence는 request-local SourceUnit ID와 선택된 exact text뿐이다.

## Provider wire-format

### Groq

- Chat Completions용 `messages`
- `response_format.type=json_schema`
- strict closed JSON Schema
- temperature 0, tools/stream/store 없음

### Gemini

- Generate Content용 `contents`
- `generationConfig.responseFormat.text.mimeType=application/json`
- Groq와 의미가 같은 JSON Schema
- tools, search, file 입력 없음

Wire field는 [Groq Structured Outputs](https://console.groq.com/docs/structured-outputs)와 [Gemini Generate Content Structured Outputs](https://ai.google.dev/gemini-api/docs/generate-content/structured-output)의 공식 REST 계약을 기준으로 한다.

모델명은 caller가 평가 manifest에서 제공한다. Harness는 모델 allowlist나 운영 provider 설정을 추가하지 않는다.

## Mock 결과 정규화

Provider adapter는 각 envelope에서 생성 text만 추출한다. 추출 결과는 기존 controlled-generation parser와 invariant validator를 통과해야 한다. 정상 Mock도 semantic support가 승인되지 않았으므로 publication은 계속 `semantic_support_pending`으로 차단되고 기존 extractive Answer로 fallback한다.

비교 결과에는 다음 안전 metadata만 남긴다.

- provider와 model label
- schema/prompt/evidence SHA-256
- SourceUnit 수와 문자 수
- parse/validation reason
- statement와 covered-ID 개수
- retry count와 publish/fallback 상태

Raw prompt, SourceUnit text, raw response와 인증정보는 남기지 않는다.

## 외부 전송 승인 경계

향후 Live 단계에는 별도 승인이 필요하다.

- provider별 계정·서비스와 정확한 model ID
- 병원 evidence exact text 외부 전송 허용 여부
- 승인 case와 provider별 최대 호출 수
- 전송 SourceUnit ID/hash/count/byte/token 상한
- 보존·학습·지역·계약 정책 확인
- raw response 저장 금지와 로그 정책

이번 구현에는 API key 입력, HTTP client와 live CLI flag를 추가하지 않는다.

## TF027 human review

TF027은 다음을 사람이 원본 페이지에서 확정하기 전까지 `needs_human_review=true`다.

- figure 위치와 경계
- 시작/종료 node
- node별 exact label
- 화살표 방향과 연결
- decision branch 조건과 Yes/No 경로
- 단계 순서
- 숫자·단위·시간
- caption/nearby text와 그림 관계
- 흐리거나 가려진 요소
- production gold 사용 승인

Checklist artifact에는 figure ID, page, bbox/fingerprint와 빈 review field만 저장하며 이미지나 병원 원문을 복제하지 않는다.

## 운영 UAT와 v1.0 완료 기준

운영 UAT fixture는 진정·수혈의 fact, procedure, preparation, caution, temporal, product, table, comparison, summary, follow-up, negative/out-of-scope와 image-pending 시나리오를 포함한다. 각 case는 expected route, answerability, provider 필요 여부와 안전 경계를 기록한다.

v1.0은 별도 승인을 받은 provider Live 비교, semantic support 판정, TF027 사람 검수, 실제 Streamlit 운영 UAT 및 전체 regression/security audit가 모두 완료될 때만 안정 후보가 된다. 승인 전에는 v0.9가 공식 안정 기준점이다.
