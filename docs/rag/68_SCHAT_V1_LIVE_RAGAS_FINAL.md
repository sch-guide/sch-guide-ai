# SCHAT v1.0 Groq Live RAGAS 최종 검증 결과

- 검증일: 2026-09-18
- 기준 안정 tag: `v0.9`
- Provider/model: Groq / `openai/gpt-oss-20b`
- 시작 readiness: `READY_EXCEPT_LIVE_RAGAS`
- 최종 readiness: **READY_EXCEPT_LIVE_RAGAS**
- Session synthetic 실제 호출: **4회**
- Real approved case 실제 호출: **1건**
- Production retrieval/validator 변경: **0건**
- Git commit/tag/push: **0회**

## 1. 결론

비민감 synthetic preflight를 거쳐 approved Positive Gold 대표 subset 5건을 시작했으나,
첫 real case인 `UAT-S01`에서 `critical_fact_preservation`을 확정하지 못했다. 요청된 safety
gate에 따라 즉시 중단했고 나머지 subset 4건과 approved 21건 전체 확대는 실행하지 않았다.

`UAT-S01`은 HTTP 200, strict schema, request-local citation, number/unit/time/condition/
negation/action 검사를 통과했다. 그러나 controlled-generation validator는 semantic support를
아직 승인하지 않으며, local Gold critical fact lexical 검사도 통과하지 못했다. Raw response를
저장하지 않았으므로 이를 확정적인 임상 누락이라고 추측하지 않고 **핵심 사실 보존 미검증**으로
fail closed했다.

## 2. Groq 정책과 실행 조건

공식 Groq 문서 기준으로 일반 synchronous inference의 customer input/output은 기본 보관하지
않는다. Usage metadata는 유지되며, 시스템 신뢰성 또는 abuse 조사 시 입력·출력이 최대 30일
임시 기록될 수 있다. 보관이 발생할 경우 문서상 data location은 미국이다. Batch,
fine-tuning, LoRA와 provider-side stateful 기능은 사용하지 않았다.

- Data Controls/ZDR 계정 설정: `account_console_manual_confirmation_pending`
- API region pin: `UNKNOWN`
- Batch/fine-tuning/LoRA: 사용 안 함
- Browser/tool/code execution: 사용 안 함
- `include_reasoning=false`, temperature 0, retry 0
- Structured Outputs strict mode: 사용

공식 근거:

- https://console.groq.com/docs/your-data
- https://console.groq.com/docs/structured-outputs
- https://console.groq.com/docs/model/openai/gpt-oss-20b

## 3. Synthetic preflight

세션 동안 synthetic 실제 호출은 총 4회였다. 첫 연결 확인 뒤 safety runner를 정리하는 과정에서
영문 synthetic critical fact와 한국어 재서술 계약의 lexical 불일치, 배열 cardinality의
provider/server 계약 불일치를 발견했다. 병원 데이터 전송 전에 이를 fixed-property strict
schema로 해결했다.

최종 synthetic는 다음을 통과했다.

- Model 접근과 HTTP 200
- Strict JSON schema parse
- SourceUnit별 필수 statement property
- Request-local citation mapping
- `include_reasoning=false`
- Raw request/response 및 key 비저장

## 4. Real subset 결과

| 항목 | 결과 |
|---|---:|
| 계획 subset | 5건 |
| 실제 호출 | **1건** |
| 중단 case | `UAT-S01` |
| Context ID Precision | 1.0000 |
| Context ID Recall | 1.0000 |
| Schema pass | 1/1 |
| Citation pass | 1/1 |
| Number/unit/time pass | 1/1 |
| Condition/negation/action pass | 1/1 |
| Gold critical fact pass | **0/1** |
| Critical safety error | **1건** |
| Provider latency | 1,857.942 ms |

Failure type은 `critical_fact_preservation`이며 suspected layer는 controlled-generation semantic
coverage/Gold fact verification이다. Production retrieval, Gold, validator threshold를 수정하거나
완화하지 않았다.

## 5. RAGAS 상태

- ID Context Precision: 1.0000, 실행된 1건 기준
- ID Context Recall: 1.0000, 실행된 1건 기준
- Faithfulness: `not_measured_no_judge_payload_authority`
- Answer Relevancy: `not_measured_original_question_not_transmitted`

승인된 payload allowlist에는 원 질문과 generated answer를 별도 LLM judge로 재전송하는 권한이
없다. 따라서 Faithfulness와 Answer Relevancy를 proxy 점수나 0점으로 대체하지 않았다.

## 6. Abstention과 제외 범위

- Approved abstention 4건: provider zero-call 유지
- Deferred 11건: 호출 0건
- TF027/image: 호출 0건
- Approved 21건 전체 확대: 미실행

## 7. Security

Artifact audit는 PASS다.

- Raw clinical text match: 0
- Raw prompt/response: 0
- API key/Authorization/secret marker: 0
- Forbidden raw field: 0
- 병원 PDF/table/image 원본 저장: 0

실행 artifact에는 case ID, provider/model, hash, count, validator result, latency와 failure code만
저장했다.

## 8. Regression 상태

Live가 safety gate에서 중단됐으므로 성공 후 전체 regression 단계에는 진입하지 않았다.
Production module은 변경하지 않았다. 이번 evaluation adapter의 집중 테스트는 21 passed다.

직전 검증 기준은 다음과 같다.

- Operational UAT: 36/36 PASS
- Full pytest: 648 passed, 4 skipped
- Safety regression: 184 passed
- Artifact security: PASS

## 9. 최종 판정

최종 readiness는 **READY_EXCEPT_LIVE_RAGAS**다. 남은 blocker는 다음이다.

1. `UAT-S01` generated statement의 Gold critical fact 보존을 승인된 semantic judge 또는 사람
   검수로 판정한다.
2. 원 질문과 generated answer를 judge에 전송할지 별도 범위를 승인해야 Faithfulness와 Answer
   Relevancy를 측정할 수 있다.
3. Data Controls/ZDR 계정 상태를 Groq Console에서 사람이 확인한다.

추가 provider 호출, production 변경, commit/tag/push는 수행하지 않았다.
