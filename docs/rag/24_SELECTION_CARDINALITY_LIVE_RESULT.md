# Source Unit 최소 선택 Prompt 및 Policy 단일 Live 재평가 결과

- 평가일: 2026-09-14
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 구현: `docs/rag/23_SELECTION_CARDINALITY_RESULT.md`
- 모델: `openai/gpt-oss-20b`
- 평가 범위: Q006 zero-call 확인 후 Q002 실제 Groq 호출 1회
- 실제 Groq 호출: Q002 1회, Q006 0회
- 상태: **Provider·parser·server validation 경로는 정상 완료됐으나 모델의 명시적 abstention으로 최종 합격 실패**

## 1. 결론

Q006은 실제 Groq transport 호출 0회를 유지했다. 그 조건을 확인한 뒤 Q002에 대해 승인된 selected evidence 12 chunks만 사용해 Groq API를 정확히 한 번 호출했다.

Q002는 HTTP 200, 반환 모델 `openai/gpt-oss-20b`, `finish_reason=stop`이었고 `response_parse_stage=complete`까지 도달했다. `failure_code`와 `validation_reason`은 없었다. 최소화된 provider schema, strict selection parsing과 server validator 경로는 오류 없이 완료됐다.

그러나 모델 응답은 `answerable=false`였고 g1~g5의 selected count가 모두 0이었다. 이는 schema와 서버 계약상 허용된 명시적 abstention이다. Required group 충족은 0/5, adult/pediatric branch는 모두 미충족이며 verified statement는 0개다.

따라서 이전의 21-unit 과선택과 `selection_limit`은 이번 호출에서 재현되지 않았지만, Q002 기준 RAG 엔진 Live 완주 조건은 충족하지 못했다. 모델이 왜 evidence를 불충분하다고 판단했는지는 raw content를 저장하지 않았으므로 추정하지 않는다.

승인 조건에 따라 retry, fallback, 두 번째 호출, 자동 ID 보충·제거·dedup·재정렬, limit 증가와 코드 수정은 수행하지 않았다.

## 2. 실행 순서와 호출 제한

1. Q006을 기존 근거 부족 경로로 실행했다.
2. Q006 transport 0회와 `pre_llm:domain_or_clarification` 차단을 확인했다.
3. Q002 prompt의 evidence chunk 집합이 승인된 12개와 정확히 같은지 단일-call transport에서 확인했다.
4. Q002 실제 Groq 요청을 한 번 전송했다.
5. HTTP 200 응답을 strict schema로 parsing하고 server selection validation을 완료했다.
6. `answerable=false` 및 모든 빈 group selection을 안전 abstention으로 처리했다.
7. 추가 호출이나 코드 변경 없이 결과만 저장했다.

| 실행 항목 | 결과 |
|---|---:|
| Q006 실제 Groq 호출 | 0회 |
| Q002 실제 Groq 호출 | 정확히 1회 |
| 자동 retry | 0회 |
| Fallback model | 0회 |
| Web search/tool call | 0회 |
| 실패 후 추가 Groq 호출 | 0회 |

## 3. Live 응답 메타데이터

| 항목 | 결과 |
|---|---|
| HTTP status | 200 |
| 요청 모델 | `openai/gpt-oss-20b` |
| 반환 모델 | `openai/gpt-oss-20b` |
| finish_reason | `stop` |
| prompt tokens | 2654 |
| completion tokens | 220 |
| total tokens | 2874 |
| latency | 1236.07 ms |
| response_parse_stage | `complete` |
| failure_code | 없음 |
| validation_reason | 없음 |
| answerable | `false` |
| Prompt evidence schema | v4 |
| Response selection schema | v3 |
| AI version | 16 |

Completion은 220/2048로 truncation이 아니다. HTTP, response envelope, strict selection JSON과 server validator에서 기술 오류가 발생한 결과도 아니다.

## 4. Group selection 결과

평가 도구는 validator 호출 직전에 raw response를 저장하지 않고 각 array의 길이만 메모리에서 관측했다. Source-unit ID 목록은 artifacts에 기록하지 않았다.

| Group slot | Selected count |
|---|---:|
| g1 | 0 |
| g2 | 0 |
| g3 | 0 |
| g4 | 0 |
| g5 | 0 |
| 합계 | 0 |

| Coverage 항목 | 결과 |
|---|---:|
| Group slot 수 | 5 |
| Required group 수 | 5 |
| Required group 충족 | 0/5 |
| Adult branch 충족 | false |
| Pediatric branch 충족 | false |
| Selected source units | 0 |

`answerable=false`와 모든 빈 group arrays의 조합은 기존 `validate_source_unit_selection()` 계약과 일치하므로 `selection_inconsistent`, `selection_missing_group` 또는 `selection_branch` 오류를 만들지 않는다. 이는 validator 완화나 자동 보정이 아니라 모델이 선택한 abstention 상태를 그대로 보존한 것이다.

## 5. Evidence 불변성

| 항목 | 결과 |
|---|---:|
| Selected evidence | 12 chunks |
| Pre-budget required gold recall | 10/10, 100% |
| Post-budget required gold recall | 10/10, 100% |
| Selection 최대 | 16, 유지 |
| Answer statements 최대 | 16, 유지 |
| Output limit | 2048, 유지 |
| Request admission budget | 5120, 유지 |

전송된 evidence ID 집합은 이전 승인된 12 chunks와 동일하다. 문서 전체 또는 다른 chunk는 전송하지 않았다.

## 6. Reconstruction 및 grounding 판정

`answerable=false`이고 선택 unit이 없으므로 서버는 빈 abstention Answer를 구성하고 기존 `validate_answer()` 계약을 통과시켰다. 임상 statement의 reconstruction은 발생하지 않았다.

| 검증 항목 | 결과 | 판정 |
|---|---|---|
| Empty abstention reconstruction | 완료 | 정상 안전 경로 |
| Verified statements | 0 | Live 합격 기준 미충족 |
| Reconstructed statement text | 없음 | 판정 대상 없음 |
| Reconstructed quote | 없음 | 판정 대상 없음 |
| Exact quote/chunk citation | 미실행 | 판정 불가 |
| Citation coverage | 0.0 형식값 | 합격 아님 |
| Source order reversal count | 0 형식값 | 빈 Answer이므로 실질 합격 근거 아님 |
| Unsupported number | 판정 불가 | statement 없음 |
| Unsupported unit | 판정 불가 | statement 없음 |
| Unsupported condition | 판정 불가 | statement 없음 |
| Unsupported negation | 판정 불가 | statement 없음 |
| Unsupported action | 판정 불가 | statement 없음 |

빈 Answer에 대한 형식값을 citation 또는 임상 검증 성공으로 재해석하지 않았다.

## 7. 합격 기준 판정

| 기준 | 실제 결과 | 판정 |
|---|---|---|
| Q006 실제 호출 0회 | 0회 | 통과 |
| Q002 실제 호출 정확히 1회 | 1회 | 통과 |
| HTTP 200 | 200 | 통과 |
| 반환 모델 일치 | 일치 | 통과 |
| finish_reason=stop | `stop` | 통과 |
| response_parse_stage=complete | `complete` | 통과 |
| failure_code 없음 | 없음 | 통과 |
| validation_reason 없음 | 없음 | 통과 |
| answerable=true | `false` | 실패 |
| Required group 5/5 | 0/5 | 실패 |
| Adult branch 충족 | false | 실패 |
| Pediatric branch 충족 | false | 실패 |
| Selected units 1~16 | 0 | 실패 |
| Verified statements 1~16 | 0 | 실패 |
| Statement reconstruction | 없음 | 미충족 |
| Exact citation validation | 미실행 | 미충족 |
| Citation coverage 100% | 0.0 형식값 | 미충족 |
| Source order reversal 0 | 실질 Answer 없음 | 미충족 |
| Unsupported 내용 0 | 판정 대상 없음 | 미충족 |

최종 판정: **Q002 기준 RAG 엔진 Live 완주 실패 — model-declared abstention**.

## 8. 실패 원인 분류

이번 결과에서 확정할 수 있는 사실은 다음과 같다.

- Provider schema는 수락됐다.
- Response parsing과 server selection validation은 정상 완료됐다.
- Selection limit 초과는 발생하지 않았다.
- 모델이 `answerable=false`와 모든 빈 group array를 반환했다.

Raw response/content를 저장하지 않았으므로 모델이 다음 중 무엇을 판단했는지는 단정하지 않는다.

- 최소 충분 subset이 16개를 넘는다고 판단했는지
- 질문에 충분한 evidence가 없다고 판단했는지
- 새 instruction을 과도하게 보수적으로 적용했는지

따라서 실패 분류는 추정 원인이 아니라 관측 가능한 상태인 `model_declared_abstention`으로만 기록한다.

## 9. 보안 및 비변경 확인

- API key 미기록
- Authorization header 미기록
- 전체 prompt 미저장
- Raw response/content 미저장
- Selected SourceUnit exact text 및 raw ID 목록 미저장
- Group별 selected count만 저장
- BM25, embedding, RRF와 reranker 미변경
- Selected evidence, segmentation과 eligibility 미변경
- PromptCoverage와 AnswerCoverage 미변경
- Response schema v3 미변경
- `validate_source_unit_selection()`과 `validate_answer()` 미변경
- System prompt 추가 수정 없음
- Output/request budget 및 limit 미변경
- 자동 truncation, ID 제거·보충, dedup와 재정렬 없음
- Retry, fallback과 web search 없음

Group count 기록을 위해 평가 도구에 raw ID를 보존하지 않는 일시적 관측 wrapper만 추가했다. Production validator와 생성 경로는 변경하지 않았다.

## 10. 산출물 및 작업 중지

새 산출물 디렉터리:

`artifacts/2026-09-14_rag-selection-cardinality-live/`

- `live_report.json`
- `failure_summary.json`
- `review.html`

기존 artifacts는 덮어쓰지 않았다. 승인된 실제 Q002 호출 1회를 사용했으며 결과와 관계없이 추가 호출하지 않았다. 이번 결과 뒤 prompt, schema, validator, limit 또는 production 코드를 수정하지 않고 작업을 중지한다.
