# Groq Output Limit 단일 Live 재평가 결과

- 평가일: 2026-09-13
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 구현: `docs/rag/13_OUTPUT_LIMIT_RESULT.md`
- 모델: `openai/gpt-oss-20b`
- 평가 범위: Q006 무호출 확인 후 Q002 실제 호출 1회
- 상태: 실제 단일 호출 완료, 추가 호출 중지

## 1. 결론

Q006은 실제 Groq 호출 0회를 유지했다. Q002는 실제 Groq를 정확히 1회 호출했고 HTTP 200, 반환 모델 `openai/gpt-oss-20b`, `finish_reason=stop`을 확인했다. completion은 835 tokens로 새 `OUTPUT_LIMIT=2048`보다 작아 이전의 truncation 문제는 해소됐다.

그러나 응답은 `response_parse_stage=validation`에서 `AI_EVIDENCE`로 안전 차단됐다. 고정 validation reason은 `unsupported sentence`다. 즉 response envelope와 output 길이는 정상이나, 생성된 문장 중 최소 하나가 선택 Chunk의 완전한 원문 문장으로 검증되지 않았다.

검증된 Answer가 만들어지지 않았으므로 최종 `answerable=false`, statement 0개다. exact citation, citation coverage, source order와 근거 없는 숫자·단위·조건·행동을 최종 Answer 기준으로 합격 판정할 수 없다. 승인 조건에 따라 자동 retry, fallback 또는 두 번째 실제 호출은 수행하지 않았다.

## 2. 실제 호출 기록

| 항목 | 결과 |
|---|---|
| Q002 실제 Groq 호출 | 정확히 1회 |
| Q006 실제 Groq 호출 | 0회 |
| HTTP status | 200 |
| 요청 모델 | `openai/gpt-oss-20b` |
| 반환 모델 | `openai/gpt-oss-20b` |
| finish_reason | `stop` |
| prompt tokens | 2445 |
| completion tokens | 835 |
| total tokens | 3280 |
| latency | 1482.87 ms |
| response_parse_stage | `validation` |
| failure_code | `AI_EVIDENCE` |
| failure_detail | 없음 |
| validation_reason | `unsupported sentence` |
| prompt schema | compact evidence schema v2 (`2`) |

`failure_detail`이 비어 있는 것은 response envelope 구조 오류가 아니기 때문이다. 이번 차단은 정상적으로 파싱된 content가 기존 evidence validator를 통과하지 못한 `AI_EVIDENCE` 경계다.

## 3. Output limit 평가

| 합격 기준 | 실제 결과 | 판정 |
|---|---|---|
| `OUTPUT_LIMIT=2048` | 유지 | 통과 |
| `GROQ_REQUEST_TOKEN_BUDGET=5120` | 유지 | 통과 |
| `AI_VERSION=11` | 유지 | 통과 |
| finish_reason=stop | `stop` | 통과 |
| completion < 2048 | 835 | 통과 |
| `AI_INCOMPLETE` 없음 | 없음 | 통과 |

이전 호출은 768/768과 `finish_reason=length`로 종료됐다. 이번 호출은 835 tokens에서 `stop`으로 완료됐으므로 2048 상향은 Q002의 structured output truncation을 해결했다.

## 4. Q006 무호출

| 항목 | 결과 |
|---|---|
| transport 호출 | 0회 |
| `llm_called` | `false` |
| 차단 사유 | `pre_llm:domain_or_clarification` |
| 판단 | 통과 |

Q006은 Groq transport가 호출되기 전에 근거 부족 경로로 종료됐다.

## 5. Q002 evidence 불변 확인

| 항목 | 결과 |
|---|---|
| 선택 evidence | 12 chunks |
| pre-budget 필수 gold recall | 10/10, 100% |
| post-budget 필수 gold recall | 10/10, 100% |
| compact evidence schema | v2 유지 |
| 전송 범위 | 선택된 12개 evidence만 전송 |

선택 evidence chunk IDs:

1. `eval-741858d2e9162acf8d38-chunk-00013`
2. `eval-741858d2e9162acf8d38-chunk-00015`
3. `eval-741858d2e9162acf8d38-chunk-00016`
4. `eval-741858d2e9162acf8d38-chunk-00019`
5. `eval-741858d2e9162acf8d38-chunk-00020`
6. `eval-741858d2e9162acf8d38-chunk-00021`
7. `eval-741858d2e9162acf8d38-chunk-00022`
8. `eval-741858d2e9162acf8d38-chunk-00023`
9. `eval-741858d2e9162acf8d38-chunk-00024`
10. `eval-741858d2e9162acf8d38-chunk-00025`
11. `eval-741858d2e9162acf8d38-chunk-00026`
12. `eval-741858d2e9162acf8d38-chunk-00027`

## 6. Answer 및 grounding 검증

| 항목 | 저장 결과 | 해석 |
|---|---|---|
| answerable | `false` | 검증된 Answer 없음 |
| statement 수 | 0 | 안전 차단 후 반환된 statement 기준 |
| exact quote/chunk citation | 정상 완료하지 못함 | validator에서 `unsupported sentence` 감지 |
| citation coverage | 0.0 | statement 0건에 따른 기계적 산출 값, 합격 아님 |
| source order | `true` | 빈 statement 목록의 형식 검사 결과이며 실질 검증 불가 |
| 근거 없는 숫자·단위·조건·행동 | 개별 유형 미평가 | 최소 1개 unsupported sentence는 확정 |
| 최종 validation | 실패 | `AI_EVIDENCE` 안전 차단 |

`unsupported sentence`는 생성 문장이 서버측 selected Chunk의 허용된 완전한 원문 문장과 일치하지 않았음을 뜻한다. raw response/content를 저장하지 않았으므로 어느 문장이나 개별 숫자·단위·조건·행동이 원인이었는지는 이 산출물에서 복원하지 않는다.

따라서 다음을 구분한다.

- output completion: 정상 완료
- strict response envelope: 정상 파싱
- grounded Answer validation: 실패
- 사용자에게 노출 가능한 답변: 없음

## 7. 전체 합격 기준 판정

| 기준 | 판정 |
|---|---|
| Q006 실제 호출 0회 | 통과 |
| Q002 실제 호출 정확히 1회 | 통과 |
| HTTP 200 | 통과 |
| 반환 모델 일치 | 통과 |
| finish_reason=stop | 통과 |
| completion tokens < 2048 | 통과 |
| response_parse_stage=complete | 실패: `validation`에서 중단 |
| failure_code 없음 | 실패: `AI_EVIDENCE` |
| answerable=true | 실패 |
| statement 1~10개 | 실패: 0개 |
| citation coverage 100% | 미충족 |
| exact citation 검증 | 미충족 |
| source order 역전 0건 | 실질 검증 불가 |
| 근거 없는 내용 0건 | 실패: unsupported sentence 감지 |

**Output-limit 변경 자체는 목적을 달성했지만 실제 RAG 답변의 전체 합격 기준은 충족하지 못했다.**

## 8. 보안 및 실행 제약

- API key 미기록
- Authorization header 미기록
- 전체 prompt 미저장
- raw response/content 미저장
- refusal 원문 미저장
- 자동 retry 없음
- fallback 없음
- web search 없음
- Q002 외 실제 Groq 호출 없음
- 실제 호출 이후 추가 호출 없음
- system prompt, Answer schema와 citation validator 미변경
- BM25, embedding, RRF와 reranker 미변경

trace에는 content 원문 대신 안전한 메타데이터만 남았다.

- content 존재: true
- content 타입: string
- content 문자 수: 1718
- refusal non-null: false

## 9. 산출물 및 작업 중지

기존 artifacts는 덮어쓰지 않았다. 새 결과는 다음 경로에 저장했다.

`artifacts/2026-09-13_rag-groq-output-limit-live/`

- `groq_evaluation_report.json`
- `q002_statements.csv` — 안전 차단으로 header만 존재
- `review.html`

이번 실제 호출은 1회로 종료했다. `AI_EVIDENCE / unsupported sentence` 원인에 대한 코드 수정, prompt 변경, 재시도 또는 추가 Groq 호출은 수행하지 않는다. 다음 작업은 사용자 별도 승인 후에만 진행한다.
