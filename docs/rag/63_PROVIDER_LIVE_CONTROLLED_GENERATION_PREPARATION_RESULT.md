# SCHAT v1.0 Provider Live Controlled Generation 준비 결과

- 작업일: 2026-09-18
- 기준 안정 버전: `v0.9`
- 범위: Groq/Gemini 공통 offline harness, TF027 checklist, 운영 UAT와 v1.0 완료 기준
- 실제 Groq/Gemini/vision 호출: **0회**
- 병원 데이터 외부 전송: **0회**
- Production retrieval/embedding/validator 변경: **0건**
- 상태: **Offline 준비 완료, Live는 별도 승인 대기**

## 1. 결론

동일한 verified SourceUnit evidence를 Groq와 Gemini의 각 wire format으로 변환하고, caller가 제공한 Mock envelope를 공통 controlled-generation validator로 검증하는 evaluation-only harness를 구현했다. Harness에는 HTTP client, provider SDK, API key 입력, live flag가 없으므로 이번 단계에서 외부 호출을 실행할 경로가 없다.

두 provider는 동일 evidence, instruction과 JSON schema를 공유한다. Provider strict 호환성이 불명확한 `minItems`와 `maxItems`는 wire schema에서만 제거했고, statement와 evidence ID 개수 제한은 기존 Pydantic 서버 validator가 그대로 fail closed로 적용한다. 정상 Mock도 semantic support가 승인되지 않았으므로 사용자 공개 없이 기존 verified extractive Answer로 fallback했다.

TF027은 workflow를 추정하지 않고 10개 사람 검수 항목을 모두 미검수로 유지했다. 진정 14개, 수혈 18개, out-of-scope 4개로 구성한 36개 운영 UAT fixture도 준비했지만 실제 provider/UAT 실행은 하지 않았다.

## 2. Groq/Gemini 공통 계약

공통 입력은 다음뿐이다.

- 비임상 `case_id`
- QueryPlan과 호환되는 `intent`
- 최대 16개의 검증된 request-local SourceUnit ID와 exact text
- 같은 evidence-only instruction
- 같은 closed JSON schema와 SourceUnit ID enum

Provider별 차이는 envelope 위치뿐이다.

| 항목 | Groq | Gemini |
|---|---|---|
| 입력 envelope | `messages` | `contents` |
| JSON 출력 설정 | `response_format.json_schema`, strict | `generationConfig.responseFormat.text` |
| MIME | JSON schema strict mode | `application/json` |
| Temperature | 0 | 0 |
| Tools/search/files | 없음 | 없음 |
| Live transport/API key | 없음 | 없음 |

두 blueprint의 evidence/schema/instruction SHA-256이 각각 일치하는지 Mock에서 확인한다. `minItems`/`maxItems`는 공통 wire schema에 없고 개수·coverage·ID 검증은 서버에서 유지된다.

Wire 위치는 [Groq Structured Outputs](https://console.groq.com/docs/structured-outputs)와 [Gemini Generate Content Structured Outputs](https://ai.google.dev/gemini-api/docs/generate-content/structured-output)의 공식 REST 예시를 기준으로 확인했다. 문서 조회만 수행했으며 생성 API에는 요청하지 않았다.

## 3. 실제 Live에서 승인 후 전송할 최소 범위

전송 허용 후보:

- 선택이 끝난 request-local SourceUnit ID
- 해당 선택 SourceUnit의 exact text
- 답변 형식용 intent
- 출력 JSON schema
- 안전 instruction

전송하지 않는 항목:

- 사용자의 원 질문
- 문서 전체와 선택되지 않은 chunk
- document name/ID, page, section과 production chunk ID
- 사용자·직원 식별자, 대화 이력
- API key와 Authorization
- table/figure 원본, PDF 파일

Artifact에는 payload, prompt, raw response와 SourceUnit exact text를 저장하지 않는다. provider/model, hash, count, byte 수, parse/validation reason, usage와 latency 같은 안전 metadata만 저장할 수 있다.

## 4. Mock 결과

| 항목 | Groq | Gemini |
|---|---:|---:|
| 실제 외부 호출 | 0 | 0 |
| Evidence count | 1 synthetic | 1 synthetic |
| Parse stage | complete | complete |
| Finish reason | stop | stop |
| Validation reason | `semantic_support_pending` | `semantic_support_pending` |
| Publish controlled | false | false |
| Extractive fallback | true | true |
| Retry | 0 | 0 |

Mock artifact는 synthetic evidence만 메모리에서 사용하고 저장본에는 그 exact text도 남기지 않았다. 실제 파일 스캔 결과 raw evidence, full instruction, raw envelope와 secret marker는 모두 0건이다.

## 5. TF027 사람 검수

`tests/fixtures/tf027_image_human_review_checklist.json`과 사람용 문서에 다음 10개 항목을 고정했다.

1. Figure 경계
2. 시작·종료 node
3. Node label
4. 화살표 방향과 연결
5. Decision branch 조건
6. Workflow 순서
7. 숫자·단위·시간
8. Caption/nearby text 관계
9. 흐림·가림·잘림
10. Production gold 승인

모든 `reviewed_value`, reviewer와 reviewed_at은 비어 있다. `needs_human_review=true`, `production_gold_approved=false`, aggregate 제외, vision API 0회를 유지한다.

## 6. 운영 UAT fixture

`schat-v1-operational-uat-v1`은 총 36개다.

| 범위 | 개수 | 상태 |
|---|---:|---|
| 진정 | 14 | 준비, Live 미실행 |
| 수혈 | 18 | 준비, Live 미실행 |
| Out-of-scope | 4 | provider 0회 기대 |

Fact, procedure, preparation, cautions, temporal, product, table, comparison, summary, follow-up, image workflow와 negative를 포함한다. Positive case는 별도 승인 뒤 provider별 최대 1회, out-of-scope와 TF027 image case는 현재 최대 0회로 고정했다.

## 7. 검증

- Provider harness: **7 passed**
- TF027/UAT contract: **4 passed**
- Provider/readiness 통합 집중 회귀: **38 passed**
- Full pytest: **545 passed, 4 skipped, 7 warnings**
- Ruff: 통과
- `git diff --check`: 통과
- Artifact security audit: raw evidence/prompt/envelope/secret 0
- Code review: 수정된 provider schema 호환성 위험을 wire projection으로 해결, 남은 actionable defect 0

Warning 7건은 기존 FastEmbed MiniLM mean-pooling 안내다. Production embedding/dependency는 변경하지 않았다.

## 8. Production 비변경과 남은 단계

변경하지 않은 영역:

- BM25 + MiniLM semantic/vector + RRF + reranker
- Facet-slot, selection limit 16과 AnswerCoverage
- `validate_answer()`와 모든 임상 validator
- Provider production adapter, prompt와 API key 처리
- Table/image production routing
- `v0.9` tag

v1.0까지 남은 단계:

1. 병원 evidence exact text 외부 전송 정책과 provider/model/보존·지역 조건 승인
2. 승인 case와 provider별 1회 상한으로 동일 evidence Live 비교
3. Semantic support/unsupported synthesis 최종 판정 검증
4. TF027 사람 검수와 두 번째 확인자 승인
5. 36개 실제 Streamlit 운영 UAT
6. 최종 회귀·보안 감사 후 별도 commit/tag 승인

현재 공식 복구 기준점은 `v0.9`다. 새 안정 버전 후보는 **`v1.0-rc1`**이며 이번 작업에서는 commit, tag와 push를 수행하지 않았다.

## 9. 산출물

- `tools/provider_controlled_generation_evaluate.py`
- `tests/test_provider_controlled_generation_harness.py`
- `tests/test_schat_v1_readiness_contracts.py`
- `tests/fixtures/tf027_image_human_review_checklist.json`
- `tests/fixtures/schat_v1_operational_uat.json`
- `docs/rag/61_TF027_IMAGE_HUMAN_REVIEW_CHECKLIST.md`
- `docs/rag/62_SCHAT_V1_COMPLETION_CRITERIA.md`
- `artifacts/2026-09-18_schat-v1-provider-preparation/`
