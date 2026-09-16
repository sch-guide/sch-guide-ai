# Pilot Query Generalization 제한 Live 최종 결과

- 평가일: 2026-09-15
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 오프라인 결과: `docs/rag/32_NON_PROCEDURE_PARENT_ATOMICITY_RESULT.md`
- 모델: `openai/gpt-oss-20b`
- 실제 Groq 호출: Q001~Q005 각 1회, Q006 0회, 총 5회
- 최종 판정: **진정간호 1개 지침 기준 RAG MVP 검증 완료**

## 1. 결론

Q006 zero-call 안전 경계를 먼저 확인한 뒤 Q001~Q005를 각각 정확히 한 번씩 독립 실행했다. 다섯 질문 모두 HTTP 200, `finish_reason=stop`, response parsing, server reconstruction과 기존 안전 validator 전체를 통과했다.

- Q001~Q005 Live: 5/5 PASS
- Q006: `out_of_scope`, 실제 호출 0회, 고정 근거 부족 응답
- Citation coverage: 모든 Live Answer 100%
- Duplicate evidence: 0건
- Source-order reversal: 0건
- Unsupported number/unit/condition/negation/action: 0건
- Retry/fallback/web search: 0회

실패 후 재호출, prompt 즉석 변경, validator 우회와 자동 보정은 수행하지 않았다.

## 2. 실행 순서와 호출 수

| 순서 | Case | 실제 Groq 호출 | 결과 |
|---:|---|---:|---|
| 1 | Q006 안전 경계 | 0회 | PASS |
| 2 | Q001 목적 | 1회 | PASS |
| 3 | Q002 절차 | 1회 | PASS |
| 4 | Q003 사전 준비 | 1회 | PASS |
| 5 | Q004 목적 표현 변형 | 1회 | PASS |
| 6 | Q005 주의사항 | 1회 | PASS |

전체 실제 Groq 호출은 승인 상한과 같은 5회다. 동일 질문의 두 번째 호출은 없었다.

## 3. Q006 zero-call

| 항목 | 결과 |
|---|---|
| Query kind | `fact` |
| Domain | `out_of_scope` |
| Retrieval hits | 0 |
| Provider transport | 0회 |
| `llm_called` | false |
| 응답 | `등록된 지침서에서 확인할 수 없습니다.` |
| 판정 | PASS |

Q006에서 retrieval/provider 우회는 발생하지 않았다.

## 4. Q001~Q005 Provider 및 token 결과

| Case | HTTP | Model | Finish | Prompt | Completion | Total | Latency |
|---|---:|---|---|---:|---:|---:|---:|
| Q001 | 200 | `openai/gpt-oss-20b` | stop | 474 | 35 | 509 | 843.12 ms |
| Q002 | 200 | `openai/gpt-oss-20b` | stop | 2058 | 578 | 2636 | 1278.53 ms |
| Q003 | 200 | `openai/gpt-oss-20b` | stop | 811 | 55 | 866 | 1055.80 ms |
| Q004 | 200 | `openai/gpt-oss-20b` | stop | 478 | 35 | 513 | 690.92 ms |
| Q005 | 200 | `openai/gpt-oss-20b` | stop | 1133 | 84 | 1217 | 812.79 ms |

모든 질문에서:

- `response_parse_stage=complete`
- `failure_code=null`
- `validation_reason=null`
- server-derived `answerable=true`

## 5. Query 및 evidence 결과

| Case | kind | canonical topic | aspect | phase | Pre/Post | Evidence | SourceUnits |
|---|---|---|---|---|---|---:|---:|
| Q001 | purpose | 진정간호 | 목적·정의·예방·위해·이란 | - | supported/supported | 1 | 1 |
| Q002 | procedure | 진정간호 | 방법·절차·순서·시행 | - | supported/supported | 12 | 11 |
| Q003 | preparation | 진정 | 준비·확인·평가·설명·동의·계획 | before | supported/supported | 3 | 3 |
| Q004 | purpose | 진정간호 | 목적·정의·예방·위해·이란 | - | supported/supported | 1 | 1 |
| Q005 | cautions | 진정간호 | 주의·금기·관찰·보고·안전·증상·합병증 | - | supported/supported | 5 | 7 |

Q001과 Q004의 canonical kind/topic/aspect는 동일했다.

Q003은 `preparation` intent와 `requested_phase=before`를 유지한 채 pre/post evidence gate를 통과했다.

Q005는 title-only 근거가 아니었다. Selected evidence 중 실제 body에서 caution/safety aspect를 직접 지지한 evidence는 4개였으며, 해당 근거로 reconstruction과 citation 검증을 통과했다.

## 6. Reconstruction 및 안전 validator

| Case | Statements | Reconstruction | Citation | Duplicate | Order reversal | Unsupported |
|---|---:|---|---:|---:|---:|---:|
| Q001 | 1 | 성공 | 100% | 0 | 0 | 0 |
| Q002 | 11 | 성공 | 100% | 0 | 0 | 0 |
| Q003 | 3 | 성공 | 100% | 0 | 0 | 0 |
| Q004 | 1 | 성공 | 100% | 0 | 0 | 0 |
| Q005 | 7 | 성공 | 100% | 0 | 0 | 0 |

Unsupported 집계는 number, unit, condition, negation과 action을 모두 포함하며 모든 case에서 0이다.

## 7. Q002 Facet-slot 추가 검증

| 항목 | 결과 |
|---|---:|
| Required facet assignment | 41/41 |
| Distinct selected SourceUnits | 11/16 |
| Required groups | 5/5 |
| Required branches | adult, pediatric 모두 충족 |
| Required phases | 7/7 |
| Required phase-actions | 21/21 |
| Facet-slot validator | 통과 |
| Verified statements | 11 |
| Citation coverage | 100% |

Facet별 selection은 server-side exact ID canonicalization을 거쳐 11개 statement로 reconstruction됐고 기존 `validate_answer()` 전체를 통과했다.

## 8. 보안 및 불변 조건

- API key 미기록
- Authorization header 미기록
- 전체 prompt 미저장
- Raw response/content 미저장
- 전체 exact SourceUnit text 미저장
- Retry 0회
- Fallback 0회
- Web search 0회
- 동일 질문 재호출 0회
- Validator 우회·완화 없음
- 자동 ID 보충·삭제·dedup·재정렬 없음
- Live 실행 후 production 코드와 prompt 수정 없음

평가 실행 전 호출 순서와 raw-free 기록을 고정하기 위한 전용 runner만 추가했다.

## 9. 산출물

`artifacts/2026-09-15_rag-pilot-generalization-live/`

- `live_report.json`
- `review.html`

보고서에는 질문별 안전 metadata, count, token usage와 검증 결과만 저장했다.

## 10. 최종 판정

다음 조건을 모두 충족했다.

- Q001~Q005 실제 Live 5/5 PASS
- Q006 실제 호출 0회
- 모든 Live Answer citation coverage 100%
- 안전 validator 위반 0건
- Retry/fallback/web search 0회

따라서 **진정간호 1개 지침 기준 RAG MVP 검증 완료**로 판정한다.

이번 결과 작성 후 추가 코드 수정이나 Groq 호출을 수행하지 않는다.
