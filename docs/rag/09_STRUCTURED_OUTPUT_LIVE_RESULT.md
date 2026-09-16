# Groq Structured Outputs 실제 1회 평가 결과

- 평가일: 2026-09-13
- 대상 저장소: D:\보하 바탕화면\SCHAT\sch-guide-ai
- 평가 대상: Q006 zero-call, Q002 실제 Groq 단일 호출
- 모델: openai/gpt-oss-20b
- 실제 Groq 호출: Q006 0회, Q002 1회
- 상태: 외부 요청 HTTP 200, 애플리케이션 응답 처리 AI_RESPONSE

## 1. 결론

Q006은 Groq transport를 호출하지 않고 고정 근거 부족 문구를 반환했다. Q002는 승인된 12개 evidence chunk만 compact evidence schema v2로 전송했고 실제 Groq 요청은 정확히 1회 발생했다. 자동 retry, fallback, web search와 추가 호출은 없었다.

Groq는 HTTP 200, 요청한 모델과 동일한 반환 모델, token usage를 반환했다. 그러나 응답이 애플리케이션의 정상 response 객체 처리 경계까지 전달되지 못해 finish_reason과 message.content를 파싱하지 못했고 실제 실패 코드는 AI_RESPONSE로 기록됐다. 따라서 Answer schema, exact citation, source order 검증에는 도달하지 못했다.

output token은 OUTPUT_LIMIT와 같은 768이지만 finish_reason이 수집되지 않았다. 이를 finish_reason=length 또는 AI_INCOMPLETE로 추정하지 않는다. 이번 평가의 확정 결과는 HTTP 200과 AI_RESPONSE이며, 승인된 한 번의 실제 호출을 이미 사용했으므로 추가 호출 없이 종료한다.

## 2. 호출 제한 준수

| 항목 | 결과 |
|---|---|
| Q006 실제 Groq 호출 | 0회 |
| Q002 실제 Groq 호출 | 정확히 1회 |
| 자동 retry | 0회 |
| fallback 모델 | 없음 |
| web search / tool 호출 | 없음 |
| 두 번째 실제 호출 | 없음 |
| 전송 evidence | 선택된 12개 chunk만 전송 |
| 문서 전체 전송 | 하지 않음 |

처음 사용한 파일 직접 실행 명령은 Python import 단계의 ModuleNotFoundError로 종료됐다. 이 프로세스는 HTTP transport 생성과 Q002 평가 전에 끝났으며 artifacts도 만들지 않아 실제 외부 호출은 0회였다. 이후 검증된 모듈 진입점으로 실행한 요청만 유일한 실제 Groq 호출이다.

## 3. Q006 결과

| 항목 | 결과 |
|---|---|
| llm_called | false |
| transport 호출 | 0회 |
| 반환 | 등록된 지침서에서 확인할 수 없습니다. |
| abstention reason | pre_llm:domain_or_clarification |

Q006 검증은 Q002 외부 요청 전에 완료됐다. Q006 차단 실패 시 Q002를 호출하지 않도록 평가 도구의 실행 순서를 유지했다.

## 4. Q002 HTTP 및 usage

| 항목 | 결과 |
|---|---|
| HTTP status | 200 |
| 요청 모델 | openai/gpt-oss-20b |
| 반환 모델 | openai/gpt-oss-20b |
| finish_reason | 수집되지 않음 |
| input tokens | 2445 |
| output tokens | 768 |
| total tokens | 3213 |
| latency | 1520.57 ms |
| 실제 실패 코드 | AI_RESPONSE |
| request token budget | 3500 |
| OUTPUT_LIMIT | 768 |
| prompt schema version | 2 |

HTTP status, 반환 모델과 usage는 실제 HTTP transport의 안전 메타데이터에서 확보했다. generate() trace는 response_http_status, response_model, response_usage와 finish_reason을 채우기 전에 AI_RESPONSE로 종료됐다. 원시 응답을 저장하지 않는 조건 때문에 누락된 finish_reason이나 content를 사후 복원하지 않았다.

## 5. Evidence 범위

pre-budget과 post-budget의 필수 gold stage recall은 모두 10/10이다. 선택 evidence는 다음 12개 chunk다.

1. eval-741858d2e9162acf8d38-chunk-00013
2. eval-741858d2e9162acf8d38-chunk-00015
3. eval-741858d2e9162acf8d38-chunk-00016
4. eval-741858d2e9162acf8d38-chunk-00019
5. eval-741858d2e9162acf8d38-chunk-00020
6. eval-741858d2e9162acf8d38-chunk-00021
7. eval-741858d2e9162acf8d38-chunk-00022
8. eval-741858d2e9162acf8d38-chunk-00023
9. eval-741858d2e9162acf8d38-chunk-00024
10. eval-741858d2e9162acf8d38-chunk-00025
11. eval-741858d2e9162acf8d38-chunk-00026
12. eval-741858d2e9162acf8d38-chunk-00027

전송 직전에 payload의 evidence ID 집합과 위 selected evidence ID 집합이 정확히 같은지 검사했다. 다른 chunk, 문서 전체, tool 또는 web-search 요청이 있으면 transport가 요청을 거부하도록 유지했다.

## 6. Answer 및 citation 검증

| 검증 항목 | 결과 |
|---|---|
| statement 수 | 0 |
| answerable | false |
| exact quote/chunk citation | 도달하지 못함 |
| citation coverage | statement가 없어 평가 불가 |
| source order | statement가 없어 평가 불가 |
| 근거 없는 단계·조건·숫자·단위·행동 | 구조화 답변이 없어 평가 불가 |
| validation_passed | false |

여기서 answerable=false는 Groq의 구조화 판단 결과가 아니다. 응답 처리 실패로 Answer 객체를 얻지 못했기 때문에 평가 보고서가 사용하는 실패 상태다. citation coverage 0.0도 잘못된 citation 비율을 뜻하지 않으며 검증할 statement가 없음을 뜻한다.

## 7. 보안 및 비변경 확인

- API key와 Authorization header를 기록하지 않았다.
- 전체 prompt를 저장하지 않았다.
- raw response와 message content를 저장하지 않았다.
- 안전 메타데이터와 검증된 statement만 저장하도록 했으며 이번에는 statement가 없다.
- BM25, embedding, RRF, reranker를 변경하지 않았다.
- system prompt와 compact evidence schema v2를 변경하지 않았다.
- request token budget 3500과 OUTPUT_LIMIT 768을 변경하지 않았다.
- 기존 artifacts를 덮어쓰지 않았다.

## 8. 산출물

- artifacts/2026-09-13_rag-structured-output-live/groq_evaluation_report.json
- artifacts/2026-09-13_rag-structured-output-live/q002_statements.csv
- artifacts/2026-09-13_rag-structured-output-live/review.html

JSON에는 안전 평가 메타데이터와 gold recall이 있고 raw response는 없다. CSV에는 header만 있으며 검증된 statement 행은 없다.

## 9. 후속 판단과 작업 중지

실제 Groq 호출은 승인된 1회를 모두 사용했다. 이번 결과만으로 strict Structured Outputs 응답의 schema·citation 성공을 승인할 수 없다.

다음 조사가 필요하다면 실제 Groq를 호출하지 않고 먼저 HTTP transport에서 generate()로 response 객체를 반환하는 경계가 왜 AI_RESPONSE로 분류됐는지 MockTransport와 저장되지 않는 예외 유형을 이용해 분석·설계해야 한다. finish_reason이나 raw content를 확인하기 위한 추가 실제 호출은 별도 승인 없이는 수행하지 않는다.

이 결과 문서 작성으로 작업을 멈춘다.
