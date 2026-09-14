# Groq Live 단일 호출 재평가 결과

- 평가일: 2026-09-13
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 구현: `docs/rag/11_LIVE_RESPONSE_DEBUG_RESULT.md`
- 모델: `openai/gpt-oss-20b`
- 평가 범위: Q006 무호출 확인 후 Q002 실제 호출 1회
- 상태: 재평가 완료, 추가 실제 호출 중지

## 1. 결론

Q006은 실제 Groq 호출 0회를 유지했다. 이어서 Q002에 대해서만 실제 Groq를 정확히 1회 호출했으며 HTTP 200과 반환 모델 `openai/gpt-oss-20b`를 확인했다.

이번에는 response parser가 `finish_reason=length`를 정상 수집했다. prompt 2445 tokens, completion 768 tokens, total 3213 tokens이며 completion이 설정된 `OUTPUT_LIMIT=768`에 도달했다. `generate()`는 `finish` parse stage에서 이를 `AI_INCOMPLETE / finish_reason`으로 안전 차단했다.

따라서 이전 평가의 불명확한 `AI_RESPONSE`와 달리 이번 실패 원인은 **출력 길이 제한으로 완료되지 않은 응답**으로 확정된다. Answer JSON 파싱, statement 및 citation 검증 단계에는 진입하지 않았다. 승인 조건에 따라 자동 retry, fallback 또는 두 번째 실제 호출은 수행하지 않았다.

## 2. 실제 호출 기록

| 항목 | 결과 |
|---|---|
| HTTP status | 200 |
| 요청 모델 | `openai/gpt-oss-20b` |
| 반환 모델 | `openai/gpt-oss-20b` |
| 실제 Groq 호출 | Q002 1회 |
| Q006 실제 Groq 호출 | 0회 |
| finish_reason | `length` |
| prompt tokens | 2445 |
| completion tokens | 768 |
| total tokens | 3213 |
| latency | 1312.0 ms |
| response parse stage | `finish` |
| failure code | `AI_INCOMPLETE` |
| failure detail | `finish_reason` |
| prompt schema version | compact evidence schema v2 (`2`) |
| response format | `json_schema`, `strict=true` |

latency는 평가 transport가 body를 읽지 않고 `generate()` 호출 전후의 `time.perf_counter()`로 측정한 값이다.

## 3. Q006 무호출 확인

| 항목 | 결과 |
|---|---|
| transport 호출 | 0회 |
| `llm_called` | `false` |
| 차단 사유 | `pre_llm:domain_or_clarification` |
| 판단 | 통과 |

Q006은 외부 transport가 생성되기 전에 고정 근거 부족 경로로 종료됐다.

## 4. Q002 evidence 불변 확인

| 항목 | 결과 |
|---|---|
| 선택 evidence | 12 chunks |
| pre-budget 필수 gold recall | 10/10, 100% |
| post-budget 필수 gold recall | 10/10, 100% |
| pre-budget 보완 gold recall | 2/3, 66.7% |
| post-budget 보완 gold recall | 2/3, 66.7% |
| request token budget | 3500 유지 |
| 실제 전송 범위 | 선택된 12개 evidence만 전송 |

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

## 5. Answer 및 citation 검증 결과

| 검증 항목 | 결과 | 해석 |
|---|---|---|
| statement 수 | 0 | incomplete 차단으로 Answer 파싱 전 종료 |
| answerable | `false` | 검증된 Answer 객체 없음 |
| exact citation validation | 미실행 | citation 단계에 진입하지 않음 |
| citation coverage | 미평가 | 보고서 산출 값은 statement 0건에 따른 `0.0` |
| source order | 미실행 | 검증 가능한 statement 없음 |
| 근거 없는 숫자·단위·조건·행동 | 미평가 | incomplete output을 파싱하거나 저장하지 않음 |
| 최종 validation | 실패 | `AI_INCOMPLETE`가 선행 차단 |

`citation_coverage=0.0`은 잘못된 citation이 발견됐다는 의미가 아니다. statement가 생성·검증되지 않아 계산 가능한 citation이 0건이라는 기계적 산출 값이다. 같은 이유로 exact citation, source order와 근거 없는 내용 여부를 성공 또는 실패로 판정하지 않는다.

## 6. 응답 파싱 진단

이번 응답은 다음 경계까지 안전하게 확인됐다.

- HTTP response 수신: 성공
- `response.json()`: 성공
- top-level object: 확인
- choices: 존재하며 첫 항목 object 확인
- `finish_reason`: 존재, 문자열 `length`
- parse 종료 stage: `finish`

`finish_reason=length`를 확인한 즉시 `AI_INCOMPLETE`로 중단했으므로 message content의 타입·길이, refusal, Answer content는 읽어 trace하거나 artifacts에 저장하지 않았다. 이 동작은 승인된 raw-free parser 계약과 일치한다.

## 7. 보안 및 실행 제약 확인

- API key 미기록
- Authorization header 미기록
- 전체 prompt 미저장
- raw response/content 미저장
- refusal 원문 미저장
- 자동 retry 0회
- fallback 모델 사용 없음
- web search 없음
- Q002 외 실제 Groq 호출 없음
- 실제 호출 후 추가 호출 없음
- `OUTPUT_LIMIT=768` 유지
- `GROQ_REQUEST_TOKEN_BUDGET=3500` 유지
- system prompt와 compact evidence schema v2 유지
- BM25, embedding, RRF와 reranker 미변경

첫 실행 시 시스템 Python에 `httpx`가 없어 import 단계에서 종료된 시도가 1회 있었다. 이 시도는 평가 함수와 transport 생성 전에 종료되어 HTTP 요청은 0회였다. 이후 프로젝트 `.venv`로 실행한 요청만 실제 Groq 호출 1회로 집계했다.

## 8. 산출물 및 다음 단계 제한

새 산출물은 `artifacts/2026-09-13_rag-groq-live-reevaluation/`에 저장했다.

- `groq_evaluation_report.json`
- `q002_statements.csv` — incomplete 차단으로 header만 존재
- `review.html`

이번 재평가로 `finish_reason` 미수집 문제는 해소됐고, 현재 실패가 `OUTPUT_LIMIT=768` 도달에 따른 `AI_INCOMPLETE`임을 확정했다. 출력 제한 변경이나 prompt/system 변경은 이번 승인 범위에 포함되지 않으므로 수행하지 않았다.

실제 Groq 호출 1회 사용 후 작업을 중지한다. 다음 변경 또는 재호출은 사용자 별도 승인 전까지 진행하지 않는다.
