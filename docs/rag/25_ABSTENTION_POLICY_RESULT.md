# Source Unit Selection Abstention 책임 분리 구현 결과

- 구현·검증일: 2026-09-14
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 계획: `docs/rag/24_ABSTENTION_POLICY_PLAN.md`
- 구현안: Selection-only provider contract + server-derived answerability
- 검증 방식: Q002 fixture, 로컬 strict schema 검증, `httpx.MockTransport`
- 실제 Groq 호출: 0회
- 상태: 구현·Mock·전체 회귀·코드 검수 완료, 실제 호출 승인 대기

## 1. 결론

Groq provider 내부 응답 계약에서 `answerable` boolean을 제거했다. 모델은 이제 request-scoped `group_selections`만 반환하며, 근거 충분성을 corpus 전체 관점에서 다시 판단하지 않는다. Q002의 pre/post evidence gate를 통과한 catalog에서 질문에 필요한 최소 source-unit ID 집합을 선택하는 역할만 맡는다.

최종 `Answer.answerable`은 서버가 결정한다. 모든 group array가 비면 `AI_EVIDENCE / selection_empty`로 안전 종료한다. 유효한 non-empty selection만 원문으로 reconstruction하고, 기존 `validate_answer()` 전체 검증까지 통과한 경우에만 public Answer를 `answerable=true`로 만든다. Unknown, duplicate, non-selectable, wrong-slot, 16개 초과, required group/branch, query action, AnswerCoverage와 source-order 검사는 삭제하거나 완화하지 않았다.

Q002 Mock은 14 IDs, required group 5/5, adult/pediatric branch와 source-unit gold 10/10을 충족했다. 서버는 14 statements를 복원했고 exact text·quote와 citation coverage가 모두 100%였다. 빈 selection은 `selection_empty`, 21-ID 과선택은 기존 `selection_limit`으로 차단됐다. Q006은 catalog 0건, transport 0회를 유지했다.

Request reservation은 4665/5120이고 headroom은 455 tokens로 최소 요구 374를 충족했다. 전체 테스트는 `292 passed, 1 skipped`, Ruff와 `git diff --check`도 통과했다.

## 2. Provider selection-only 계약

변경 전 provider 응답:

```json
{
  "answerable": true,
  "group_selections": {"g1": ["su001"]}
}
```

변경 후 provider 응답:

```json
{
  "group_selections": {"g1": ["su001"]}
}
```

실제 request에서는 현재 contract의 모든 `gN` property가 required이며 `group_selections`와 root object 모두 `additionalProperties=false`다. 각 array item은 해당 group의 selectable request-local ID enum만 허용한다. 모델이 `answerable`, statement text, quote, chunk ID, branch, group key, source order 또는 label을 추가하면 strict schema에서 거부된다.

Provider schema는 허용된 shape와 group별 ID 범위만 표현한다. Required group의 non-empty 여부, branch coverage, 전역 selection 한도와 순서는 서버 validator가 계속 fail closed한다.

## 3. 서버 answerability 결정 순서

1. Pre/post evidence gate가 실패하면 Groq를 호출하지 않고 `answerable=false`를 반환한다.
2. Provider의 모든 group array가 비면 `selection_empty`로 차단한다. 자동 재호출하지 않는다.
3. Unknown, duplicate, non-selectable, wrong-slot, 16개 초과 또는 source-order 오류는 기존 `AI_EVIDENCE`로 차단한다.
4. Required group/branch, query action 또는 AnswerCoverage가 부족해도 기존 `AI_EVIDENCE`로 차단한다.
5. 유효한 non-empty selection만 서버 catalog에서 원문 statement와 citation으로 복원한다.
6. 복원 결과에 기존 `validate_answer()`를 다시 실행한다.
7. Exact citation, source sentence, number, unit, condition/negation, action 및 source-order 검증까지 모두 통과한 경우에만 `answerable=true`가 된다.

빈 selection이나 validation 실패 시 ID를 보충·제거·dedup·재정렬하지 않는다. Retry와 fallback도 없다.

## 4. SOURCE_UNIT_SYSTEM 변경

모델에게 corpus-wide answerability 판단을 맡기는 지시와 `answerable=false` 출력 지시를 제거했다. 현재 지시는 다음 의미를 명시한다.

- 서버 evidence checks가 catalog를 이미 selection 대상으로 승인했다.
- 모델은 corpus-wide answerability를 다시 평가하지 않는다.
- 전체 질문에 충분한 가장 작은 source-unit subset을 선택한다.
- `required=true`는 group의 충분한 non-empty subset을 뜻하며 모든 unit 선택을 뜻하지 않는다.
- `selectable=true`는 eligible이며 mandatory가 아니다.
- 총 선택은 16개 이하로 유지한다.
- 16개 안에서 contract-valid sufficient selection을 만들 수 없으면 모든 group array를 비운다.
- ID를 invent, truncate, auto-remove, deduplicate, reorder 또는 supplement하지 않는다.

문서와 사용자 입력을 untrusted data로 취급하는 지시, 올바른 group slot과 source order, 모델 생성 금지 필드 및 외부 의학 지식 금지는 그대로 유지했다.

## 5. 버전과 불변식

| 항목 | 결과 |
|---|---:|
| `AI_VERSION` | 17 |
| `RESPONSE_SELECTION_SCHEMA_VERSION` | 4 |
| `PROMPT_EVIDENCE_SCHEMA_VERSION` | 4, 유지 |
| `OUTPUT_LIMIT` | 2048, 유지 |
| `GROQ_REQUEST_TOKEN_BUDGET` | 5120, 유지 |
| Selection 최대 | 16, 유지 |
| Public Answer statements 최대 | 16, 유지 |
| `SEARCH_VERSION` | 12, 유지 |
| `CHUNK_VERSION` | 4, 유지 |

Provider response 계약과 생성 prompt 의미가 바뀌므로 response schema version과 AI cache version을 각각 증가시켰다. Public `Answer` JSON 구조는 변경하지 않았다.

다음도 유지했다.

- Selected evidence 12 chunks
- Segmentation과 SourceUnit eligibility/catalog
- PromptCoverage와 AnswerCoverage 의미
- Group-slot topology와 reconstruction 방식
- BM25, tokenizer, query expansion, temporal rerank
- Embedding, RRF와 reranker
- 기존 citation 및 임상 안전 validator

## 6. Q002 정상 Mock 결과

| 검증 항목 | 결과 | 판정 |
|---|---:|---|
| Selected evidence | 12 chunks | 유지 |
| Raw/selectable source units | 40 / 21 | 유지 |
| PromptCoverage required input units | 23 | 유지 |
| Selected source units | 14/16 | 통과 |
| Required groups | 5/5 | 통과 |
| Required branches | adult, pediatric | 통과 |
| Pre-budget required gold | 10/10 | 통과 |
| Post-budget required gold | 10/10 | 통과 |
| Source-unit gold | 10/10 | 통과 |
| Server reconstruction | 14 statements | 통과 |
| Server-derived answerable | true | 통과 |
| Reconstructed text exact match | 100% | 통과 |
| Reconstructed quote exact match | 100% | 통과 |
| Exact citation validation | supported | 통과 |
| Citation coverage | 100% | 통과 |
| Source-order reversal | 0건 | 통과 |
| Number/unit/condition/negation/action | 기존 validator 전체 통과 | 통과 |
| Response parse stage | complete | 통과 |
| Failure code/reason | 없음 | 통과 |

Group별 Mock 선택 수는 `g1=1`, `g2=1`, `g3=1`, `g4=6`, `g5=5`다. 이 값은 평가 fixture의 14-ID gold selection 결과이며 production 코드나 selection policy에 expected count로 하드코딩하지 않았다.

## 7. Fail-closed Mock 결과

| 사례 | 결과 |
|---|---|
| 모든 group arrays empty | `AI_EVIDENCE / selection_empty`, `answerable=false` |
| Required adult/pediatric group empty | 기존 `selection_branch` |
| Required common group empty | 기존 `selection_missing_group` |
| Unknown ID | 기존 `selection_unknown_id` |
| Duplicate ID | 기존 `selection_duplicate_id` |
| Non-selectable ID | 기존 `selection_non_selectable` |
| Wrong-slot ID | 기존 `selection_wrong_group` |
| 21 IDs 또는 16개 초과 | 기존 `selection_limit`; 자동 축소 없음 |
| Group 내부/전체 source-order 역전 | 기존 `selection_source_order`; 자동 정렬 없음 |
| Provider가 `answerable` 또는 금지 필드 추가 | `selection_schema` 또는 strict schema 거부 |
| Reconstruction 후 잘못된 chunk citation | 기존 `AI_EVIDENCE / citation` |

Q006은 SourceUnit catalog 0건, transport 0회이고 기존 고정 근거 부족 응답을 유지했다.

## 8. Budget

| 항목 | 결과 |
|---|---:|
| Request admission reservation | 4665 |
| Admission cap | 5120 |
| Headroom | 455 |
| 최소 요구 headroom | `max(256, ceil(4665 × 8%)) = 374` |
| Response schema serialized estimate | 201 tokens |
| 판정 | 통과 |

`Quota.reserve()`에는 admission cap 자체가 아니라 실제 계산 reservation이 전달되는 기존 계약을 유지했다. Output limit, evidence 원문과 selected chunk 수를 줄이지 않았다.

## 9. 테스트와 코드 검수

- Selection/schema 집중 테스트: `34 passed`
- 전체 테스트: `292 passed, 1 skipped`
- Ruff 전체 검사: 통과
- `git diff --check`: 통과
- 경고: 기존 FastEmbed multilingual MiniLM pooling 기본값 안내 2건. 이번 변경과 무관하며 embedding 설정은 수정하지 않았다.

Code-review 절차로 provider schema, parser, server-derived reconstruction, 기존 validator 호출, version/cache와 artifacts 보안 경계를 검수했다.

- Public `Answer.answerable`과 UI/API 계약은 유지된다.
- Provider 응답의 boolean만 제거됐으며 valid reconstruction에서 서버가 true를 설정한다.
- `selection_empty`는 reconstruction 전에 fail closed한다.
- 기존 required group/branch/action/coverage/order 및 grounding validator 삭제·완화 없음
- 자동 보충, dedup, 재정렬, retry와 fallback 없음
- 추가 조치가 필요한 correctness, safety 또는 regression 결함 없음

## 10. 변경 범위

핵심 변경:

- `mvp/ai.py`: selection-only strict schema, parser의 `selection_empty`, server-derived reconstruction answerability, versions와 selection system instruction
- `tests/test_source_unit_selection.py`: 14-ID 성공, empty selection, 금지 boolean, bad reconstructed citation과 기존 negative contracts
- `tests/test_groq_structured_output.py`, provider fixture 및 앱 Mock: selection-only response envelope
- 평가 도구: selection-only Mock response, empty/overselection 및 provider/server 책임 관측

변경하지 않은 범위:

- Retrieval/context/evidence 선택
- Public Answer schema와 UI
- Segmentation, SourceUnit catalog와 eligibility
- 기존 validation 허용 범위
- BM25, embedding, RRF와 reranker
- 실제 provider 설정과 호출 정책

## 11. 보안, 산출물과 작업 중지

이번 단계에서는 실제 Groq를 호출하지 않았다. API key, Authorization header, 전체 prompt, raw response/content와 SourceUnit exact text를 artifacts 또는 일반 로그에 저장하지 않았다.

새 산출물 디렉터리:

`artifacts/2026-09-14_rag-server-answerability-mock/`

- `mock_report.json`
- `test_results.json`
- `review.html`

기존 artifacts는 덮어쓰지 않았다. 구현, Mock, 전체 회귀와 결과 문서 작성을 완료했으며 실제 Groq 재평가는 사용자의 별도 승인 전까지 수행하지 않는다.
