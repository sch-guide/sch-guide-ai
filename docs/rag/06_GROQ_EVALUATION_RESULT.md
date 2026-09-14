# SCHAT 제한적 Groq 실제 평가 결과

- 평가일: 2026-09-13
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 평가 질문: Q002 실제 호출 최대 1회, Q006 실제 호출 0회
- 요청 모델: `openai/gpt-oss-20b`
- prompt schema: 2
- prompt budget: 3500 유지
- 최신 결과: Q006 차단 성공, Q002는 HTTP 200 및 지정 모델 응답을 받았으나 구조화 답변 처리 실패로 citation 평가 미완료

## 1. 결론

최신 재평가에서도 Q006은 LLM 호출 없이 `등록된 지침서에서 확인할 수 없습니다.`를 반환했고 transport 호출은 0회였다.

Q002에만 실제 Groq 요청을 정확히 1회 전송했다. 최신 호출은 HTTP 200으로 인증과 모델 접근에 성공했으며 `openai/gpt-oss-20b`와 token usage를 확인했다. 그러나 응답이 애플리케이션의 구조화 답변 처리 경계를 통과하지 못해 `AI_REQUEST_FAILED`로 종료됐고 statement 및 citation 검증에는 도달하지 못했다. 자동 retry, fallback 모델, web search와 두 번째 실제 호출은 없었다.

따라서 Groq 연결 성공과 실제 usage까지는 확인했지만 답변 품질·citation·source order는 아직 승인할 수 없다. 앞선 인증 실패 실행은 이 문서의 이력으로 유지하고 최신 결과는 11절에 분리 기록한다.

API 키 값, Authorization header, 전체 요청 prompt와 원시 오류 응답은 artifacts 또는 로그에 기록하지 않았다.

## 2. 호출 전 불변 확인

| 항목 | 결과 |
|---|---|
| Q002 pre-budget 필수 gold recall | 10/10 |
| Q002 post-budget 필수 gold recall | 10/10 |
| 선택 evidence | 00013, 00015, 00016, 00019~00027 |
| evidence 수 | 12 chunks |
| 예약 token | 3130 |
| prompt budget | 3500, 변경 없음 |
| prompt schema version | 2 |
| BM25/embedding/RRF/reranker | 변경 없음 |
| system prompt | 변경 없음 |

실제 transport는 요청 payload의 evidence chunk ID 집합이 위 selected evidence와 정확히 같은지 전송 직전에 검사했다. 다른 model, `tools`, `tool_choice` 또는 두 번째 요청이 있으면 로컬에서 거부하도록 구성했다.

## 3. Q006 결과

| 항목 | 결과 |
|---|---|
| 질문 | 화성 우주선의 궤도 계산 공식은? |
| `llm_called` | false |
| transport 호출 | 0회 |
| 결과 | 등록된 지침서에서 확인할 수 없습니다. |
| abstention reason | `pre_llm:domain_or_clarification` |

Q006 검증이 실패했으면 Q002 실제 호출 단계로 진입하지 않도록 실행 순서를 고정했다.

## 4. Q002 실제 호출 결과

| 항목 | 결과 |
|---|---|
| 실제 요청 횟수 | 1회 |
| 요청 모델 | `openai/gpt-oss-20b` |
| 반환 모델 | 응답 없음 |
| HTTP 결과 | 인증/권한 오류, 401 또는 403 |
| 애플리케이션 오류 | `AI_AUTH` |
| 자동 retry | 0회 |
| fallback | 없음 |
| web search/tools | 없음 |
| input/output/total token | usage 없음, 측정 불가 |
| latency | 인증 예외로 결과 flush 전 측정값 보존 불가 |
| statement 수 | 0개 |
| validation | 미실행 |
| abstention reason | `request_failed:AI_AUTH` |

`llm_called=true`는 실제 HTTP 요청이 전송됐음을 뜻한다. `answerable=false` 또는 statement 0은 근거 부족 판정이 아니라 인증 실패로 응답 자체를 받지 못한 결과다.

## 5. Citation 및 근거 검증 상태

다음 항목은 모델 응답이 없어 평가하지 못했다.

- statement별 `chunk_id`와 exact quote
- citation coverage 100%
- 근거 없는 단계·조건·숫자·행동 추가 여부
- 답변 source order 역전 여부
- 생성 후 서버측 Chunk 기반 citation assessment

이 항목의 상태는 `not_reached` 또는 `not_evaluated`이며 실패를 통과로 대체하지 않았다. `q002_statements.csv`는 헤더만 있고 검증된 statement 행은 없다.

## 6. 호출 안전장치

평가 전 MockTransport dry-run에서 다음을 확인했다.

- Q006 transport 0회
- Q002 mock transport 1회
- mock answer와 exact citation 검증 통과

실제 transport에는 다음 제한을 적용했다.

- `httpx.HTTPTransport(retries=0)`
- 두 번째 request 즉시 거부
- 요청 model이 설정 model과 다르면 거부
- `tools` 또는 `tool_choice`가 있으면 거부
- 전송 evidence ID가 selected evidence와 다르면 거부
- API key/header/원시 response 비저장

실제 실행에서는 이 제한 아래 Q002 요청 한 번만 발생했고 `AI_AUTH` 이후 멈췄다.

## 7. 산출물

새 결과는 기존 artifacts를 덮어쓰지 않고 다음 위치에 저장했다.

`artifacts/2026-09-13_rag-groq-evaluation/`

- `groq_evaluation_report.json`
- `q002_statements.csv`
- `review.html`

## 8. 코드 및 설정 변경 범위

평가를 위해 `tools/rag_groq_evaluate.py`만 추가했다. 인증 실패도 재시도 없이 구조화된 결과로 기록할 수 있도록 평가 도구에 오류 처리 경계를 포함했다.

다음은 변경하지 않았다.

- BM25, tokenizer, query expansion과 temporal rerank
- embedding, RRF와 reranker
- context/evidence 정책
- prompt serializer와 system prompt
- `GROQ_REQUEST_TOKEN_BUDGET=3500`
- 원본 문서와 기존 artifacts

## 9. 승인 판단과 작업 중지

Q006의 zero-call 안전 경계와 Q002의 단일 요청 제한은 확인됐다. 그러나 실제 Groq 응답 품질과 citation은 인증 실패로 평가하지 못했으므로 제한적 Groq 평가를 완료 또는 승인 가능한 상태로 판단하지 않는다.

API 키와 해당 모델의 이용 권한을 사용자가 로컬에서 확인한 뒤, 새로운 실제 호출을 원한다면 별도 승인이 필요하다. 이번 요청에서 허용된 최대 1회는 이미 사용했으므로 자동으로 다시 시도하지 않는다.

이 결과 작성으로 작업을 멈춘다.

## 10. 외장하드 재연결 후 재평가 결과

- 재평가 시각: 2026-09-13T19:45:34+09:00
- 산출물: `artifacts/2026-09-13_rag-groq-evaluation-02/`
- 실행 결과: 정상 종료 코드 0
- 실제 Groq 요청: Q002 1회
- 결론: Groq HTTP 401로 응답 생성 및 citation 평가 미도달

외장하드 재연결 후 D: 프로젝트의 디렉터리 생성·삭제 쓰기 검사를 통과했다. 실제 호출 전 MockTransport dry-run도 종료 코드 0으로 통과했으며 Q006 호출 0회, Q002 mock 호출 1회, mock answerable 및 exact citation validation 통과를 확인했다.

그 뒤 같은 평가 도구와 새 artifacts 경로로 실제 평가를 한 번 실행했다. Q006은 먼저 고정 근거 부족 문구를 반환했으며 transport 호출은 0회였다. Q002에 대해서만 실제 Groq 요청이 정확히 1회 발생했고 자동 retry, fallback 모델, web search 및 tool 호출은 없었다.

### Q006

| 항목 | 결과 |
|---|---|
| `llm_called` | false |
| transport 호출 | 0회 |
| 반환 | `등록된 지침서에서 확인할 수 없습니다.` |
| abstention reason | `pre_llm:domain_or_clarification` |

### Q002

| 항목 | 결과 |
|---|---|
| 실제 요청 | 1회 |
| HTTP status | 401 |
| 애플리케이션 오류 | `AI_AUTH` |
| 요청 모델 | `openai/gpt-oss-20b` |
| 반환 모델 | 없음 |
| API latency | 343.45 ms |
| input/output/total token | Groq usage 없음, 확인 불가 |
| prompt schema | 2 |
| prompt budget / 예약 token / headroom | 3500 / 3130 / 370 |
| pre-budget 필수 gold recall | 10/10 |
| post-budget 필수 gold recall | 10/10 |
| selected evidence | 00013, 00015, 00016, 00019~00027 |
| statement 수 | 0 |
| answerable | false |
| citation coverage | 응답 statement가 없어 평가 불가 |
| exact citation validation | `not_reached` |
| source order 검증 | 생성 응답이 없어 평가 불가 |
| 근거 없는 단계·조건·숫자·행동 | 생성 응답이 없어 평가 불가 |

HTTP 401은 모델 응답 품질이나 evidence 품질 실패가 아니라 Groq 인증 경계에서 요청이 거부된 결과다. 따라서 statement 수 0과 `answerable=false`를 근거 부족 판단으로 해석하지 않는다. retrieval, evidence gate와 prompt budget은 실제 요청 직전까지 필수 gold 10/10을 유지했다.

새 결과는 다음 파일에 분리 저장했다.

- `artifacts/2026-09-13_rag-groq-evaluation-02/groq_evaluation_report.json`
- `artifacts/2026-09-13_rag-groq-evaluation-02/q002_statements.csv`
- `artifacts/2026-09-13_rag-groq-evaluation-02/review.html`

API 키, Authorization header, 전체 prompt와 원시 오류 응답은 artifacts 또는 결과 문서에 기록하지 않았다. 기존 artifacts와 BM25, embedding, RRF, reranker, prompt budget 및 system prompt도 변경하지 않았다.

이번 실행은 정상적으로 결과를 저장했지만 HTTP 401이 유지됐다. 실제 Q002 답변, token usage와 citation 검증은 여전히 완료되지 않았으므로 Groq 평가의 최종 승인을 권고하지 않는다. 이번 승인 범위의 실제 호출 1회를 사용했으며 추가 호출 없이 작업을 멈춘다.

## 11. 인증 성공 후 Q002 단일 호출 최신 결과

- 평가 시각: 2026-09-13T21:11:04+09:00
- 산출물: `artifacts/2026-09-13_rag-groq-evaluation-03/`
- 실행 결과: 종료 코드 0
- 실제 Groq 호출: Q002 1회, Q006 0회
- 최신 판단: 인증·모델 접근 성공, 구조화 답변 검증 실패

### 호출 및 usage

| 항목 | 결과 |
|---|---|
| HTTP status | 200 |
| 요청 모델 | `openai/gpt-oss-20b` |
| 실제 반환 모델 | `openai/gpt-oss-20b` |
| input token | 2185 |
| output token | 768 |
| total token | 2953 |
| latency | 1182.48 ms |
| 실제 호출 수 | Q002 1회, Q006 0회 |
| 자동 retry / fallback / web search | 모두 없음 |
| prompt budget | 3500, 변경 없음 |
| prompt schema | compact evidence schema v2 |

### Evidence 및 생성 검증

| 항목 | 결과 |
|---|---|
| pre-budget 필수 gold recall | 10/10 |
| post-budget 필수 gold recall | 10/10 |
| selected evidence | 00013, 00015, 00016, 00019~00027, 총 12개 |
| selected evidence 정확 목록 | 00013, 00015, 00016, 00019, 00020, 00021, 00022, 00023, 00024, 00025, 00026, 00027 |
| statement 수 | 0 |
| answerable | false |
| request error | `AI_REQUEST_FAILED` |
| citation coverage | 생성 statement가 없어 평가 불가 |
| exact quote/chunk citation | `not_reached` |
| source order | 생성 statement가 없어 평가 불가 |
| 근거 없는 숫자·단위·조건·행동 | 생성 statement가 없어 평가 불가 |

선택 evidence는 실제 요청 직전 검증된 12개 chunk뿐이며 필수 gold stage 10/10과 3500-token budget을 유지했다. HTTP 200이므로 이번 결과는 인증 실패가 아니다. 다만 Groq 응답이 서버 측 `Answer` 구조와 citation 검증 단계에 도달하기 전에 처리 실패했다. 원시 응답과 전체 prompt를 저장하지 않는 평가 조건 때문에 JSON 형식, completion 종료 또는 schema 불일치 중 어느 하나라고 임의로 단정하지 않는다.

새 결과는 다음 파일에 저장했다.

- `artifacts/2026-09-13_rag-groq-evaluation-03/groq_evaluation_report.json`
- `artifacts/2026-09-13_rag-groq-evaluation-03/q002_statements.csv`
- `artifacts/2026-09-13_rag-groq-evaluation-03/review.html`

API key, Authorization header, 전체 prompt와 원시 응답은 기록하지 않았다. 기존 BM25, embedding, RRF, reranker, system prompt, prompt budget과 이전 artifacts도 변경하지 않았다. 이번 승인 범위의 실제 Q002 호출 1회를 사용했으므로 추가 호출 없이 작업을 멈춘다.
