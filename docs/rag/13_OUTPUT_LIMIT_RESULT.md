# Groq Completion Output Limit 조정 결과

- 구현·검증일: 2026-09-13
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 계획: `docs/rag/12_OUTPUT_LIMIT_PLAN.md`
- 검증 방식: MockTransport 및 로컬 fixture만 사용
- 실제 Groq 호출: 0회
- 상태: 구현·Mock 검증 완료, 실제 단일 재평가 승인 대기

## 1. 결론

승인된 최소 변경을 적용했다.

- `OUTPUT_LIMIT`: 768 → 2048
- `GROQ_REQUEST_TOKEN_BUDGET`: 3500 → 5120
- `AI_VERSION`: 10 → 11

Q002의 선택 evidence는 12 chunks로 유지됐고 pre/post 필수 gold recall은 모두 10/10이다. 예상 reservation은 4410, admission cap 대비 headroom은 710 tokens로 계획의 최소 요구량 353을 충족한다. `Quota.reserve()`에는 cap 5120이 아니라 실제 계산된 4410이 전달됐다.

MockTransport의 `finish_reason=stop` 응답은 5개 extractive statement와 exact citation 검증을 정상 통과했다. 반대로 completion 2048과 `finish_reason=length` 조합은 기존 계약대로 `AI_INCOMPLETE`로 차단됐다. 잘못된 citation은 `AI_EVIDENCE`, Q006은 transport 0회를 유지했다.

집중 테스트 27개와 전체 테스트 266개가 통과했고 1개는 기존 조건에 따라 skip됐다. Ruff도 통과했다. 실제 Groq는 호출하지 않았다.

## 2. 구현 범위

### Production 변경

`mvp/ai.py`의 세 상수만 변경했다.

```python
OUTPUT_LIMIT = 2048
GROQ_REQUEST_TOKEN_BUDGET = 5120
AI_VERSION = 11
```

`OUTPUT_LIMIT=2048`은 Groq payload의 `max_completion_tokens`와 기존 요청 예약량 계산에 사용된다. `GROQ_REQUEST_TOKEN_BUDGET=5120`은 동일한 12개 evidence group과 새 completion 예약을 admission하기 위한 내부 cap이다. `AI_VERSION=11`은 이전 generation/cache 계약과 새 output-limit 계약을 구분한다.

### Mock 평가 변경

`tools/rag_prompt_budget_evaluate.py`의 Mock envelope를 현재 Groq parser 계약에 맞췄다.

- 요청 payload의 `max_completion_tokens=2048` 확인
- `response_format=json_schema` 및 `strict=true` 확인
- Mock 응답의 `finish_reason=stop`, 반환 model과 전체 usage 제공
- strict Answer 필드와 기존 exact citation 검증 유지
- 실제 `Quota.reserve()` 전달값을 report에 기록
- common/adult/pediatric 선택 branch를 report에 기록
- 새 artifacts 기본 경로 사용

외부 transport는 사용하지 않았다.

## 3. Budget 및 quota 결과

| 항목 | 결과 | 판정 |
|---|---:|---|
| output limit | 2048 | 통과 |
| request admission cap | 5120 | 통과 |
| 예상 reservation | 4410 | 계획값 일치 |
| `Quota.reserve()` 전달값 | 4410 | cap 5120 자체를 전달하지 않음 |
| headroom | 710 | 통과 |
| 최소 요구 headroom | 353 | `max(256, ceil(4410×8%))` |
| headroom 충족 | true | 통과 |

Mock usage가 반환되면 기존 `Quota.settle()` 경로가 `usage.total_tokens`를 사용한다. minute/day quota 상수와 quota 알고리즘은 수정하지 않았다.

## 4. Q002 evidence 불변성

| 검증 항목 | 결과 | 판정 |
|---|---:|---|
| 선택 evidence | 12 chunks | 통과 |
| pre-budget 필수 gold recall | 10/10, 100% | 통과 |
| post-budget 필수 gold recall | 10/10, 100% | 통과 |
| required group 유지 | 5/5 | 통과 |
| parent partial inclusion | 0건 | 통과 |
| 선택 branch | common, adult, pediatric | 통과 |
| required explicit branch | adult, pediatric | 통과 |
| source order | 유지 | 통과 |
| exact duplicate | 0건 | 통과 |
| compact schema | version 2 | 통과 |
| system prompt hash | 이전 승인본과 동일 | 통과 |

선택 chunk ID와 원문, required/optional 판정, parent group 및 branch 구조는 변경하지 않았다.

## 5. Structured Answer 및 안전 차단

### 정상 완료 Mock

| 항목 | 결과 |
|---|---|
| `finish_reason` | `stop` |
| Answer | `answerable=true` |
| statement 수 | 5 |
| exact citation | 통과 |
| source order | 통과 |
| Mock HTTP 호출 | 정확히 1회 |

Mock statement는 실제 선택 Chunk의 완전한 원문 문장과 chunk ID를 사용했다. citation validator는 compact prompt metadata가 아니라 서버측 Chunk를 기준으로 기존과 동일하게 검증했다.

### 불완전 응답

- completion usage 2048
- `finish_reason=length`
- 결과: `AI_INCOMPLETE`
- Answer/citation 검증 전 차단
- 자동 retry 또는 output limit 자동 증가 없음

### 잘못된 citation

schema-valid Answer라도 quote가 실제 Chunk 원문과 일치하지 않으면 기존과 같이 `AI_EVIDENCE`로 차단된다.

### Q006

| 항목 | 결과 |
|---|---|
| transport 호출 | 0회 |
| LLM 호출 | 0회 |
| 차단 단계 | LLM 전 evidence/domain gate |
| 판단 | 통과 |

## 6. 테스트 결과

추가·갱신된 테스트는 다음을 검증한다.

- 승인 상수 `2048`, `5120`, `AI_VERSION=11`
- Groq payload `max_completion_tokens=2048`
- strict JSON Schema 계약 유지
- Q002 12 chunks 및 필수 gold 10/10
- required group, parent 원자성 및 세 branch 유지
- source order 및 duplicate 0건
- reservation 4410과 cap 5120 분리
- headroom 710 및 최소 요구량 353
- `finish_reason=stop` 정상 Answer/citation 통과
- `finish_reason=length`, completion 2048의 `AI_INCOMPLETE`
- 잘못된 citation의 `AI_EVIDENCE`
- Q006 transport 0회
- gzip/deflate/비압축 response parser 회귀

실행 결과:

- 집중 Mock 테스트: `27 passed`
- 전체 테스트: `266 passed, 1 skipped`
- Ruff: 통과
- diff 검수: 승인 범위 밖 production 변경 없음
- 코드 검수: 추가 조치가 필요한 결함 없음

전체 테스트에는 FastEmbed의 MiniLM pooling 기본값 변경을 알리는 기존 런타임 경고 1건이 있었다. 이번 output-limit 변경으로 발생한 실패는 아니며 embedding 설정을 수정하지 않았다.

첫 red 실행에서 Windows 기본 pytest 임시 디렉터리 접근 오류가 함께 발생했다. 이후 D: 저장소 내부의 격리된 `--basetemp`를 사용해 집중 및 전체 테스트를 정상 완료했다.

## 7. 변경 및 비변경 확인

변경:

- `mvp/ai.py`: 승인된 상수 3개
- `tools/rag_prompt_budget_evaluate.py`: 현재 Mock response 계약과 acceptance/report 보강
- `tests/test_output_limit.py`: Q002 budget·quota·coverage 통합 계약
- `tests/test_groq_structured_output.py`: 2048/5120/version 및 strict stop/length/citation 계약
- `tests/test_live_response_transport.py`: completion 2048 length 회귀

유지:

- Q002 evidence 12 chunks
- Answer schema와 citation validator
- system prompt와 compact evidence schema v2
- `response_format=json_schema`, `strict=true`
- BM25, tokenizer, query expansion과 temporal rerank
- embedding, semantic search, RRF와 reranker
- context/evidence required·optional 정책
- 자동 retry 없음
- fallback 없음
- web search 없음
- Q006 외부 호출 0회

## 8. 산출물

기존 artifacts는 덮어쓰지 않았다. 새 Mock 결과는 다음 경로에 저장했다.

`artifacts/2026-09-13_rag-output-limit-mock/`

- `prompt_budget_report.json`
- `q002_group_tokens.csv`
- `review.html`

## 9. 승인 대기

output-limit 변경은 계획의 Mock 합격 기준을 모두 충족했다. 실제 Groq 재평가는 수행하지 않았다.

이 결과 작성으로 작업을 멈춘다. Q006 0회 확인과 Q002 실제 단일 호출은 사용자의 별도 승인 전까지 실행하지 않는다. 실제 호출에서 다시 `finish_reason=length`가 발생하더라도 자동 retry, fallback 또는 추가 상향 없이 `AI_INCOMPLETE`로 차단하는 계약을 유지한다.
