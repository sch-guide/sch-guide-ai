# Facet-slot SourceUnit Selection 구현·Mock 및 제한 Live 결과

- 구현·검증일: 2026-09-14
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 계획: `docs/rag/29_FACET_SLOT_SELECTION_PLAN.md`
- 구현안: request-scoped required facet별 scalar enum selection
- 실제 Groq 호출: Q002 1회, Q001 1회, Q003/Q004/Q005/Q006 0회
- 최종 상태: **Q002 기준 RAG 엔진 Live 완주 성공**

## 1. 결론

`ProcedureAnswerRequirement`에서 요청별 임상 facet을 생성하고, 각 facet을 만족할 수 있는 selectable SourceUnit ID allowlist를 계산하는 Facet-slot provider 계약을 구현했다. Q002에서는 required facet 41개가 생성됐고 빈 allowlist는 0개였다.

Groq 응답은 `facet_selections.fNN`마다 해당 facet allowlist의 scalar enum ID 하나만 반환한다. 동일 SourceUnit ID가 여러 facet에 선택되는 many-to-one 관계는 보존하고, 최종 statement에서는 exact ID 기준으로만 한 번 canonicalize한다. 의미 기반 dedup, 자동 보충, 자동 축소와 자동 재정렬은 추가하지 않았다.

Mock에서는 정확한 최소 witness 10개와 Gold 14개가 모두 AnswerCoverage를 통과했다. Gold 경로는 41개 facet, source-unit gold 10/10, reconstruction 14 statements, exact citation 100%와 server-derived `answerable=true`를 충족했다. 서로 다른 ID 17개와 21개는 기존 `selection_limit`으로 차단됐다.

모든 사전 검증이 통과한 뒤 Q006 zero-call을 먼저 확인하고 Q002 실제 Groq 호출을 정확히 한 번 수행했다. Q002는 HTTP 200, `finish_reason=stop`, response parsing과 모든 서버 검증을 통과했다. 모델이 41개 facet에 배정한 값은 11개 distinct SourceUnit으로 canonicalize됐고, reconstruction된 11 statements가 citation coverage 100%, duplicate evidence 0건, source order 역전 0건으로 최종 승인됐다.

Q002 성공 후 승인된 시범 범위도 실행했다. Q001은 실제 Groq 1회로 성공했다. Q003과 Q004는 `pre_llm:incomplete_semantic_block`, Q005는 `pre_llm:no_topic_evidence`로 외부 호출 전에 중단됐다. Q006도 근거 부족 고정 경로에서 0회를 유지했다. 전체 실제 Groq 호출은 허용 상한 5회 중 2회다.

## 2. 구현 내용

### Required facet 및 allowlist

`mvp/evidence.py`에 provider와 독립적인 `RequiredFacet` 및 다음 일반 생성 경계를 추가했다.

- required group
- required branch
- required phase
- required phase-action
- required action family
- 질문에 명시된 query action

각 facet allowlist는 현재 request의 required group에 속하면서 `selectable=true`인 SourceUnit 중 facet metadata를 실제로 만족하는 ID만 포함한다. Q002 stage명, chunk ID, Gold ID와 per-group 기대 개수는 production 코드에 넣지 않았다.

### Provider contract와 schema

`mvp/ai.py`에 다음 request-scoped 타입을 추가했다.

- `FacetSelectionSlot`
- `FacetSlotSelectionContract`
- `FacetSlotSelection`

Provider strict schema는 다음 성질을 가진다.

- root와 `facet_selections`는 closed object
- 모든 required facet property required
- 각 facet 값은 string scalar
- 각 값은 해당 facet의 selectable SourceUnit ID enum
- array, `minItems`, `maxItems` 없음
- text, quote, chunk ID, branch, phase, action, source order와 label 생성 필드 없음

버전은 다음과 같이 분리했다.

| 항목 | 값 |
|---|---:|
| `AI_VERSION` | 19 |
| `PROMPT_EVIDENCE_SCHEMA_VERSION` | 6 |
| `RESPONSE_SELECTION_SCHEMA_VERSION` | 5 |
| `OUTPUT_LIMIT` | 2048 |
| `GROQ_REQUEST_TOKEN_BUDGET` | 5120 |

`SEARCH_VERSION`과 `CHUNK_VERSION`은 변경하지 않았다.

### Selection validation과 reconstruction

서버는 schema 통과 후에도 다음을 다시 검증한다.

- facet key 집합과 scalar type
- facet별 allowlist
- request contract drift
- distinct SourceUnit 최대 16
- source order
- 기존 AnswerCoverage의 group, branch, phase, phase-action, action diversity와 query action

동일 ID의 여러 facet assignment는 모두 유지한다. Statement projection만 첫 참조 순서의 exact ID identity로 canonicalize한다. 17개 이상은 `selection_limit`, 역전은 `selection_source_order`로 차단하며 선택값을 수정하지 않는다.

정상 선택은 서버가 다음처럼 복원한다.

- `Statement.text = SourceUnit.exact_text`
- `Evidence.chunk_id = SourceUnit.chunk_id`
- `Evidence.quote = SourceUnit.exact_text`
- `label = ""`

그 뒤 기존 `validate_answer()`를 그대로 실행해 exact citation, complete source sentence, number, unit, condition, negation, unsupported action, duplicate evidence와 source order를 다시 검증한다.

## 3. Mock 결과

### Facet contract와 표현 가능성

| 항목 | 결과 |
|---|---:|
| Q002 required facets | 41 |
| 빈 allowlist | 0 |
| selectable SourceUnits | 21 |
| 정확한 최소 witness | 10 distinct IDs |
| 최소 witness AnswerCoverage | 통과 |
| Gold 표현 | 14 distinct IDs |
| Source-unit gold recall | 10/10 |
| Selection limit | 16 유지 |

### Gold end-to-end Mock

| 항목 | 결과 |
|---|---:|
| Selected evidence | 12 chunks |
| Pre/post required gold | 10/10, 10/10 |
| Required facet assignments | 41/41 |
| Distinct selected IDs | 14 |
| Reconstructed statements | 14 |
| Exact text/quote | 100% |
| Citation coverage | 100% |
| Duplicate evidence | 0건 |
| Branch metadata | server EvidenceGroup 기준 유지 |
| Source order reversal | 0건 |
| Server answerable | true |

### Fail-closed Mock

- 17 distinct IDs: `selection_limit`
- 21 distinct IDs: `selection_limit`
- required facet 누락: schema 및 `selection_schema` 차단
- allowlist 밖 ID: schema 및 `selection_wrong_facet` 차단
- extra property: schema 및 `selection_schema` 차단
- source order 역전: `selection_source_order`
- Q006: catalog 0건, transport 0회

기존 duplicate signature는 같은 document와 server branch 및 exact normalized text일 때만 중복으로 판정하며, 서로 다른 document·branch 또는 서로 다른 exact text는 합치지 않는 계약을 유지했다.

## 4. Budget

| 항목 | 결과 |
|---|---:|
| Request reservation | 4259 |
| Admission cap | 5120 |
| Headroom | 861 |
| 최소 요구 headroom | 341 |
| Response schema serialized estimate | 956 tokens |
| 판정 | 통과 |

Response schema token은 request reservation에 10% 여유를 더해 포함된다. `Quota.reserve()`에는 admission cap 5120이 아니라 실제 reservation 4259가 전달된다.

## 5. Q002 단일 Live 결과

### Provider 및 parsing

| 항목 | 결과 |
|---|---|
| Q006 사전 실제 호출 | 0회 |
| Q002 실제 호출 | 정확히 1회 |
| HTTP status | 200 |
| 반환 모델 | `openai/gpt-oss-20b` |
| finish reason | `stop` |
| Prompt / completion / total tokens | 2058 / 578 / 2636 |
| Latency | 1669.46 ms |
| Response parse stage | `complete` |
| Failure code | 없음 |
| Validation reason | 없음 |

### Facet, coverage 및 Answer

| 항목 | 결과 |
|---|---:|
| Required facet assignment | 41/41 |
| Distinct selected SourceUnits | 11/16 |
| Required group | 5/5 |
| Adult / pediatric branch | 모두 충족 |
| Required phase | 7/7 |
| Required phase-action | 21/21 |
| Required action family | 6/6 |
| Reconstructed/verified statements | 11 |
| Server-derived answerable | true |
| Exact citation | 통과 |
| Citation coverage | 100% |
| Duplicate evidence | 0건 |
| Source order reversal | 0건 |
| Unsupported number/unit/condition/negation/action | 모두 0건 |

Group별 distinct statement 기여 수는 common `1/1/1`, adult `5`, pediatric `3`이었다. Facet별 assignment 41개가 exact ID canonicalization으로 11개 statement가 된 결과다.

**최종 판정: Q002 기준 RAG 엔진 Live 완주.**

## 6. 제한 Pilot 결과

Q002 완주 뒤에만 Q001/Q003/Q004/Q005 시범 경로를 실행했다.

| Case | 실제 Groq 호출 | 결과 | 근거/차단 경계 |
|---|---:|---|---|
| Q001 `진정간호 목적은?` | 1회 | 통과 | 1 statement, citation 100% |
| Q002 `진정간호 절차는?` | 1회 | 통과 | Facet 41/41, 11 statements |
| Q003 `진정 전 준비사항은?` | 0회 | 안전 abstention | `pre_llm:incomplete_semantic_block` |
| Q004 `진정 간호의 목적은 무엇인가요?` | 0회 | 안전 abstention | `pre_llm:incomplete_semantic_block` |
| Q005 `진정간호 주의사항은?` | 0회 | 안전 abstention | `pre_llm:no_topic_evidence` |
| Q006 `화성 우주선의 궤도 계산 공식은?` | 0회 | 안전 abstention | 고정 근거 부족 경로 |

Q001 Live 기록:

- HTTP 200, `finish_reason=stop`
- Prompt/completion/total: 474/35/509
- Latency: 753.30 ms
- 1 selected SourceUnit, 1 verified statement
- Citation coverage 100%, duplicate evidence 없음

Q003~Q005의 0회는 누락된 실행이 아니라 pre-LLM evidence gate가 외부 호출을 막은 결과다. 전체 실제 Groq 호출은 Q001과 Q002의 2회이며 retry, fallback, web search와 두 번째 case 호출은 없었다.

## 7. 테스트 및 코드리뷰

- Facet/Mock 집중 테스트: 57 passed
- Live transport 및 SourceUnit 테스트: 40 passed
- Output-limit 회귀: 4 passed
- 최종 전체 pytest: **309 passed, 1 skipped**
- Ruff (`mvp`, `tests`, `tools`): 통과
- `git diff --check`: 통과

FastEmbed multilingual MiniLM pooling 기본값 안내 warning 2건이 있었다. 이번 변경으로 발생한 실패가 아니며 embedding 설정은 변경하지 않았다.

AGENTS.md의 code-review 절차로 production diff, provider trust boundary, server validation, cache/version, evaluation transport와 artifacts 보안을 검토했다.

- 추가 조치가 필요한 correctness·safety·regression 결함 없음
- Q002 stage/chunk/Gold production 하드코딩 없음
- 기존 validator 삭제·완화 없음
- 자동 보충·축소·dedup·재정렬과 retry/fallback/web search 추가 없음
- 평가 transport는 Facet catalog의 request-local ID와 exact text SHA-256을 외부 요청 전에 대조함

## 8. 보안 및 비변경 범위

- API key와 Authorization header 미기록
- 전체 prompt와 raw response/content 미저장
- Selected SourceUnit exact text 미저장
- Live 결과에는 검증 메타데이터와 최종 답변 SHA-256만 저장
- 승인된 Q002 selected evidence에서 생성된 SourceUnit 외 원문 미전송
- BM25, tokenizer, query expansion, temporal rerank 미변경
- Embedding, RRF, reranker와 selected evidence 미변경
- SourceUnit segmentation/eligibility와 PromptCoverage 의미 미변경
- Selection/Answer statement limit 16 유지
- `OUTPUT_LIMIT=2048`, request budget 5120 유지

## 9. 산출물

Mock:

`artifacts/2026-09-14_rag-facet-slot-selection-mock/`

- `mock_report.json`
- `test_results.json`
- `review.html`

Live 및 pilot:

`artifacts/2026-09-14_rag-facet-slot-selection-live/`

- `live_report.json`
- `pilot_report.json`
- `review.html`

기존 artifacts는 덮어쓰지 않았다.

## 10. 작업 중지와 다음 단계

승인된 Facet-slot production 구현, Mock, 전체 회귀, Q002 단일 Live와 조건부 Q001~Q005 pilot을 완료했다. Q002 기준 RAG 엔진은 처음으로 provider selection부터 reconstruction과 citation까지 전 구간을 Live 완주했다.

다음 단계는 Q001~Q006의 질문별 abstention 기대값을 사람이 확정하고, 더 다양한 표현·답 없음 질문 및 다른 병원 지침서로 retrieval/evidence/AnswerCoverage 일반화를 검증하는 것이다. 이번 결과 문서 작성 후 추가 Groq 호출이나 코드 변경 없이 작업을 멈춘다.
