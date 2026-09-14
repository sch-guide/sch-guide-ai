# Groq `unsupported sentence` schema description 최소 수정 결과

- 구현·검증일: 2026-09-13
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 계획: `docs/rag/14_UNSUPPORTED_SENTENCE_PLAN.md` B안
- 검증 방식: 기존 Q002 fixture 및 합성 `httpx.MockTransport`
- 실제 Groq 호출: 0회
- 상태: 최소 수정 및 Mock 검증 완료, 실제 재평가 승인 대기

## 1. 결론

기존 Pydantic Answer 모델의 네 필드에 extractive 의미 계약을 description으로 추가했다. `Answer.model_json_schema()`에서 네 description이 모두 생성되고, Groq용 `_strict_schema_node()`를 통과한 최종 strict schema에도 유지되는 것을 확인했다.

System prompt와 validator는 변경하지 않았다. 원문 완전 일치는 통과했고 어미, fragment, punctuation, paraphrase와 불완전 quote는 기존 계약대로 `AI_EVIDENCE / unsupported sentence`로 차단됐다. 잘못된 chunk 및 quote는 `citation`, 근거 없는 숫자·단위·행동과 절차 순서 역전도 기존 고정 reason으로 차단됐다.

Q002는 selected evidence 12 chunks, pre/post 필수 gold recall 10/10, parent partial inclusion 0건과 성인/소아 branch 및 source order를 모두 유지했다. schema description 추가 후 실제 request reservation은 4410으로 5120 admission cap 안에 있고 headroom은 710 tokens다. Q006은 MockTransport 호출 0회를 유지했다.

## 2. 구현 내용

`mvp/ai.py`의 JSON 구조나 validator 로직을 바꾸지 않고 다음 설명만 추가했다.

| 필드 | 추가한 의미 계약 |
|---|---|
| `Statement.text` | selected evidence에서 그대로 복사한 정확히 하나의 완전한 원문 문장 또는 표 행이며 paraphrase, fragment, 어미 및 punctuation 변경 금지 |
| `Statement.evidence` | `statement.text` 전체를 지지하는 1~4개의 정확한 evidence 연결 |
| `Evidence.chunk_id` | 제공된 selected evidence의 정확한 chunk ID이며 생성 또는 변경 금지 |
| `Evidence.quote` | 지정 Chunk에 그대로 존재하고 완전한 `statement.text` 문장을 포함하는 exact excerpt이며 요약·paraphrase 금지 |

`AI_VERSION`은 11에서 12로 올렸다. 현재 Streamlit generation cache key가 `AI_VERSION`을 포함하므로, description이 없는 이전 생성 계약과 강화된 strict schema 계약을 분리하기 위한 변경이다.

다음 값과 구조는 유지했다.

- `OUTPUT_LIMIT=2048`
- `GROQ_REQUEST_TOKEN_BUDGET=5120`
- compact evidence schema v2
- `response_format=json_schema`, `strict=true`
- Answer JSON 필드, 자료형, 길이 및 cardinality

## 3. Strict schema 확인

| 확인 항목 | 결과 |
|---|---|
| `Answer.model_json_schema()`의 `Statement.text.description` | 포함 |
| `Statement.evidence.description` | 포함 |
| `Evidence.chunk_id.description` | 포함 |
| `Evidence.quote.description` | 포함 |
| `_strict_schema_node()` 적용 후 description | 4개 모두 유지 |
| object required 필드 계약 | 유지 |
| `additionalProperties=false` | 유지 |
| runtime default 동작 | 유지 |

별도 hand-written provider schema는 만들지 않았다. Pydantic schema가 계속 단일 원본이며 strict 변환은 description을 제거하지 않는다.

## 4. MockTransport grounding 결과

모든 호출은 합성 로컬 response envelope에 대한 MockTransport 호출이다. 외부 네트워크나 Groq API는 사용하지 않았다.

| 사례 | Mock 호출 | 결과 | validation reason |
|---|---:|---|---|
| 원문 완전 일치 | 1 | 통과, answerable | 없음 |
| 어미 변경 | 1 | 안전 차단 | `unsupported sentence` |
| fragment | 1 | 안전 차단 | `unsupported sentence` |
| punctuation 변경 | 1 | 안전 차단 | `unsupported sentence` |
| paraphrase | 1 | 안전 차단 | `unsupported sentence` |
| 정확 text + partial quote | 1 | 안전 차단 | `unsupported sentence` |
| 잘못된 chunk ID | 1 | 안전 차단 | `citation` |
| Chunk에 없는 quote | 1 | 안전 차단 | `citation` |
| 근거 없는 숫자 | 1 | 안전 차단 | `number` |
| 단위 변경 | 1 | 안전 차단 | `unit` |
| 근거 없는 행동 | 1 | 안전 차단 | `unsupported action` |
| procedure source order 역전 | 1 | 안전 차단 | `procedure source order` |

모든 차단 사례의 failure code는 `AI_EVIDENCE`다. 원문 완전 일치 Q002 Mock은 answerable이며 검증된 5개 extractive statements와 exact citation 검증을 통과했다.

## 5. Q002 불변성

| 확인 항목 | 결과 | 판정 |
|---|---:|---|
| selected evidence | 12 chunks | 통과 |
| pre-budget 필수 gold recall | 10/10, 100% | 통과 |
| post-budget 필수 gold recall | 10/10, 100% | 통과 |
| required groups | 5 | 유지 |
| parent partial inclusion | 0건 | 통과 |
| required branch | adult, pediatric | 통과 |
| source order | 유지 | 통과 |
| exact Mock Answer | answerable=true, 5 statements | 통과 |
| exact citation | supported | 통과 |

선택 Chunk, 원문, required/optional 판정, parent group 및 branch 구조는 수정하지 않았다.

## 6. Budget 및 Q006

| 항목 | 결과 |
|---|---:|
| request admission cap | 5120 |
| 실제 계산 reservation | 4410 |
| headroom | 710 |
| 최소 요구 headroom | 353 |
| admission/headroom 기준 | 통과 |
| Q006 MockTransport 호출 | 0회 |
| Q006 answerable | false |

Schema description은 Groq request schema에 포함되지만 현재 내부 admission reservation은 승인된 계산 계약에 따라 messages와 completion reserve로 산정된다. `Quota.reserve()`에는 cap 5120 자체가 아니라 실제 계산값 4410이 전달되는 기존 동작을 유지했다.

## 7. 테스트와 정적 검사

추가·갱신한 테스트는 다음을 검증한다.

- strict schema의 네 description 존재와 strict 변환 후 보존
- Answer schema 구조 및 runtime default 불변
- extractive 문장 성공과 어미·fragment·punctuation·paraphrase 차단
- partial quote, 잘못된 chunk 및 quote 차단
- 숫자·단위·행동·source-order 기존 안전 검증
- Q002 12 chunks, 필수 gold 10/10, parent/branch/order 불변
- reservation/headroom과 Q006 transport 0회

실행 결과:

- 집중 테스트: `71 passed`
- 전체 테스트: `267 passed, 1 skipped`
- Ruff: 통과
- 경고: 기존 FastEmbed MiniLM pooling 기본값 변경 안내 2건. 이번 schema description 변경으로 발생한 실패가 아니며 embedding 설정은 수정하지 않았다.

## 8. Code review 결과

승인 범위의 production diff, schema 생성 경로, cache key, Mock evaluator와 artifacts 보안 필드를 검수했다.

- 추가 조치가 필요한 correctness 또는 regression 결함 없음
- System prompt SHA-256은 이전 승인 산출물과 동일
- `source_sentences()`, `sentence_evidence()`, `validate_answer()` 변경 없음
- raw prompt와 raw response/content 저장 없음
- API key 및 Authorization header 접근·기록 없음
- retry, fallback 및 web search 추가 없음

잔여 검증 경계는 실제 모델이 description을 따른 응답을 생성하는지 여부다. Mock 테스트는 schema 전달과 서버 검증 계약을 입증하지만 외부 모델 동작을 입증하지 않는다.

## 9. 변경 및 비변경 범위

변경:

- `mvp/ai.py`: 네 Pydantic Field description, `AI_VERSION=12`
- `tests/test_groq_structured_output.py`: strict schema description 및 version 계약
- `tests/test_unsupported_sentence_schema.py`: Q002/grounding Mock acceptance
- `tools/rag_unsupported_sentence_evaluate.py`: raw-free Mock 평가 및 HTML 생성

비변경:

- system prompt
- validator 로직 전체
- `source_sentences()`, `sentence_evidence()`, `validate_answer()`
- Answer JSON 구조
- output limit과 request budget
- compact evidence schema v2와 selected evidence 12 chunks
- BM25, embedding, RRF와 reranker
- 원본 문서, 기존 artifacts 및 live 평가 결과

## 10. 산출물 및 작업 중지

새 Mock 결과는 다음 디렉터리에 저장했다.

`artifacts/2026-09-13_rag-unsupported-sentence-mock/`

- `unsupported_sentence_mock_report.json`
- `review.html`

초기 생성본은 같은 작업 중 bad quote 회귀가 누락된 것을 확인해 제거하고, 해당 사례까지 포함한 최종 산출물로 교체했다. 기존 평가 artifacts는 삭제하거나 덮어쓰지 않았다.

이번 단계에서는 실제 Groq를 호출하지 않았다. 이 결과 문서 작성으로 작업을 멈추며 실제 단일 재평가는 사용자의 별도 승인 전까지 수행하지 않는다.
