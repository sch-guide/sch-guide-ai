# Branch-aware Source Unit Selection 구현 및 Mock 검증 결과

- 구현·검증일: 2026-09-14
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 계획: `docs/rag/19_BRANCH_SELECTION_PLAN.md`
- 구현안: request-scoped 고정 group slot 기반 계층형 selection
- 검증 방식: Q002 fixture, 로컬 JSON Schema validation, `httpx.MockTransport`
- 실제 Groq 호출: 0회
- 상태: 구현 및 Mock 검증 완료, 실제 호출 승인 대기

## 1. 결론

기존 flat `selected_source_unit_ids` 응답을 요청별 `group_selections` 응답으로 교체했다. Q002의 현재 다섯 required EvidenceGroup은 `g1`~`g5` 고정 slot으로 생성되며, answerable=true schema에서 각 slot은 필수 property이자 최소 한 개의 해당 group source-unit ID를 요구한다. Adult와 pediatric branch는 모델이 출력하지 않고 각각의 required group slot에서 서버가 파생한다.

Q002 Mock에서는 14개 source-unit ID가 다섯 slot에 배치됐고 서버 reconstruction과 기존 `validate_answer()` 전체 검증을 통과했다. Verified statement 14개, exact citation 및 citation coverage 100%, source order 역전 0건이다. Selected evidence 12 chunks, pre/post required gold 10/10, source-unit gold 10/10과 PromptCoverage required input units 23개도 유지됐다.

동적 true/false `anyOf` schema는 로컬 Draft 2020-12 validator에서 유효했다. Required group 빈 배열, wrong-slot 및 answerable=false 상태의 ID 선택은 schema에서 거부되며, 서버 validator도 duplicate, unknown, non-selectable, 전역 16개 한도, group 내부·전체 source order와 contract drift를 계속 fail closed한다. 누락 ID 자동 보충, 자동 dedup 또는 자동 재정렬은 추가하지 않았다.

Request reservation은 4528/5120, headroom은 592 tokens로 최소 요구 363을 충족했다. Response schema serialized estimate는 별도 503 tokens로 기록했다. 전체 테스트는 288 passed, 1 skipped이고 Ruff와 `git diff --check`가 통과했다.

## 2. 구현 구조

`mvp/ai.py`에 다음 요청별 immutable 계약을 추가했다.

```python
@dataclass(frozen=True)
class SelectionGroupSlot:
    prompt_group_id: str
    group_key: str
    branch: str
    required: bool
    selectable_source_unit_ids: tuple[str, ...]
    source_order: int

@dataclass(frozen=True)
class BranchAwareSelectionContract:
    slots: tuple[SelectionGroupSlot, ...]
    maximum_selected_units: int = 16
```

`build_selection_contract()`가 prompt에 실제 포함되는 EvidenceGroup과 SourceUnit catalog에서 contract를 만든다. 동일 contract가 compact catalog의 `group_id`, Groq response schema의 slot/enum, 서버 parser와 reconstruction에 공통으로 사용되므로 별도 group mapping을 만들지 않는다.

Q002의 요청별 topology는 다음과 같다.

| Slot | Branch | Required | Selectable units | Mock selected | Source order |
|---|---|---:|---:|---:|---:|
| g1 | common | yes | 1 | 1 | 12 |
| g2 | common | yes | 1 | 1 | 14 |
| g3 | common | yes | 1 | 1 | 15 |
| g4 | adult | yes | 6 | 6 | 18 |
| g5 | pediatric | yes | 12 | 5 | 22 |

Group key와 branch는 서버 contract에만 존재한다. 모델 응답에는 request-local `gN`과 `suNNN`만 허용된다.

## 3. Dynamic strict response schema

Provider 내부 응답은 다음 두 필드만 허용한다.

```json
{
  "answerable": true,
  "group_selections": {
    "g1": ["su002"],
    "g2": ["su003"],
    "g3": ["su005"],
    "g4": ["su012", "su015"],
    "g5": ["su024", "su026"]
  }
}
```

Schema는 요청마다 contract에서 생성된다.

- 모든 object는 모든 property를 required로 두며 `additionalProperties=false`다.
- Answerable=true variant의 required group array는 `minItems=1`이다.
- Optional group array는 비어 있을 수 있다.
- 각 array item enum은 해당 slot의 selectable source-unit ID만 포함한다.
- Answerable=false variant는 모든 group array를 `maxItems=0`으로 제한한다.
- 두 variant는 root `anyOf`로 분리된다.
- Statement text, quote, chunk ID, branch, group key, source order와 label을 모델이 반환할 필드는 없다.

Mock request가 실제 사용하는 schema에 대해 `Draft202012Validator.check_schema()`와 정상/거부 payload validation을 실행했다. 실제 Groq strict endpoint 호환성은 이번 무호출 단계에서 주장하지 않으며, 별도 live 승인 전까지 네트워크 검증하지 않는다.

## 4. Validation과 reconstruction

서버는 contract의 canonical slot 순서로 group을 순회한다. 이는 모델이 선택한 ID를 추가하거나 array 내부 순서를 고치는 동작이 아니라 승인된 request topology의 고정 순회다. 각 slot array의 ID 순서는 모델 반환 순서 그대로 검사한다.

다음 상태를 모두 차단한다.

| 상태 | 고정 validation reason |
|---|---|
| Required adult/pediatric slot empty | `selection_branch` |
| Required common slot empty | `selection_missing_group` |
| 다른 group ID | `selection_wrong_group` |
| Unknown ID | `selection_unknown_id` |
| Duplicate ID | `selection_duplicate_id` |
| Non-selectable ID | `selection_non_selectable` |
| 전체 16개 초과 | `selection_limit` |
| answerable/선택 불일치 | `selection_inconsistent` |
| Group 내부 또는 전체 order 역전 | `selection_source_order` |
| PromptCoverage/catalog/contract drift | `selection_contract_drift` |
| Query action 또는 AnswerCoverage 부족 | 기존 `selection_missing_action`/coverage reason |

Branch 값은 model output을 신뢰하지 않고 slot의 server-side metadata에서 파생한다. 정상 선택만 다음 public Answer로 복원한다.

- `Statement.text = SourceUnit.exact_text`
- `Evidence.chunk_id = SourceUnit.chunk_id`
- `Evidence.quote = SourceUnit.exact_text`
- `label = ""`
- `format`과 `conflict`는 서버가 결정

그 뒤 기존 exact source sentence, citation, number, unit, action, condition/negation, branch/coverage와 procedure source-order validator를 그대로 다시 실행한다.

Branch 없는 일반 질문에서는 PromptCoverage가 없는 상태를 허용하는 대신, contract의 모든 group이 required/common인지 명시적으로 검사한다. Procedure 질문은 PromptCoverage의 required group/branch tuple과 contract가 정확히 일치해야 한다. 이 구분으로 일반 질문 호환성을 유지하면서 procedure validator를 완화하지 않았다.

## 5. Q002 Mock 결과

| 항목 | 결과 | 판정 |
|---|---:|---|
| Selected evidence | 12 chunks | 유지 |
| Raw/selectable source units | 40 / 21 | 유지 |
| PromptCoverage required input units | 23 | 유지 |
| Required groups | 5/5 non-empty | 통과 |
| Required branches | adult, pediatric | 통과 |
| Selected source units | 14/16 | 통과 |
| Pre-budget required gold | 10/10 | 통과 |
| Post-budget required gold | 10/10 | 통과 |
| Source-unit gold stages | 10/10 | 통과 |
| Parent partial inclusion | 0건 | 통과 |
| Server reconstruction | 14 statements | 통과 |
| Reconstructed text/quote exact match | 100% / 100% | 통과 |
| Exact citation validation | supported | 통과 |
| Citation coverage | 100% | 통과 |
| Source order reversal | 0건 | 통과 |
| Response parse stage | complete | 통과 |
| Failure code/reason | 없음 | 통과 |
| Q002 MockTransport | 1회 | 통과 |
| 외부 Groq 호출 | 0회 | 통과 |

Gold stage/chunk mapping은 기존 평가 fixture에만 있으며 production contract/schema/validator는 Q002 chunk ID나 진정간호 단계명을 읽지 않는다.

## 6. Q006와 일반 회귀

Q006은 evidence가 없으므로 SourceUnit catalog 0건, transport 0회와 고정 근거 부족 응답을 유지했다. Response schema를 생성하거나 Mock provider를 호출하지 않았다.

기존 selected evidence, segmentation, SourceUnit eligibility, PromptCoverage/AnswerCoverage, BM25, embedding, RRF와 reranker는 변경하지 않았다. `SEARCH_VERSION=12`, `CHUNK_VERSION=4`, `OUTPUT_LIMIT=2048`, `GROQ_REQUEST_TOKEN_BUDGET=5120`, selection/statement limit 16도 유지했다.

## 7. Budget

| 항목 | 결과 |
|---|---:|
| 기존 request admission reservation | 4528 |
| Admission cap | 5120 |
| Headroom | 592 |
| 최소 headroom | `max(256, ceil(4528 × 8%)) = 363` |
| Response schema serialized estimate | 503 |
| Prompt schema version | 3 |
| Response selection schema version | 2 |

Response schema estimate는 요청 message 기반 기존 reservation과 섞지 않고 별도 관측값으로 기록했다. `Quota.reserve()`에는 cap 5120이 아니라 실제 계산 reservation 4528이 전달됐다.

## 8. 버전

- `PROMPT_EVIDENCE_SCHEMA_VERSION=3`: compact source-unit catalog wire format은 유지했다.
- `RESPONSE_SELECTION_SCHEMA_VERSION=2`: flat IDs에서 group slots로 바뀐 provider response 계약을 별도 추적한다.
- `AI_VERSION=14`: 생성 cache를 이전 flat response 계약과 분리한다.
- `SEARCH_VERSION=12`, `CHUNK_VERSION=4`: 변경하지 않았다.

## 9. 테스트와 코드 검수

검증한 핵심 사례:

- Dynamic true/false schema의 object closure, required properties, per-slot enum과 non-empty required arrays
- 정상 Q002 14-ID group selection과 reconstruction
- Adult, pediatric 및 common required slot 빈 배열 차단
- Wrong-group, unknown, duplicate, non-selectable 및 16개 초과 차단
- Group 내부 및 전체 source-order 역전 차단
- Answerable=false + ID, answerable=true + required empty 차단
- PromptCoverage/contract drift 차단
- 모델이 금지 필드를 추가한 payload의 strict schema 거부
- 기존 response envelope, output limit, citation과 Streamlit 경로 회귀
- Q006 catalog/transport 0건/0회

실행 결과:

- 집중 contract/response/budget 테스트: `48 passed`
- 관련 앱 회귀 포함 테스트: `91 passed`
- 전체 테스트: `288 passed, 1 skipped`
- Ruff 전체 검사: 통과
- `git diff --check`: 통과

FastEmbed multilingual MiniLM pooling 기본값에 관한 기존 경고 2건이 있었으며 embedding 설정은 변경하지 않았다.

코드 검수 결과 추가 조치가 필요한 correctness, safety 또는 regression 결함은 발견되지 않았다. API key, Authorization, 전체 prompt, raw response/content를 artifacts에 저장하는 경로도 추가하지 않았다.

## 10. 변경 범위

이번 단계의 핵심 변경:

- `mvp/ai.py`: request-scoped group contract, dynamic strict schema, group parser/validator, schema trace, `AI_VERSION=14`
- `tests/test_source_unit_selection.py`: Q002 happy/negative/schema/contract drift 검증
- `tests/test_groq_structured_output.py`, `tests/test_live_response_transport.py`, fixture: group response envelope 회귀
- `tests/test_mvp_chat.py`: provider 내부 group response fixture
- `tools/rag_prompt_budget_evaluate.py`, `tools/rag_source_unit_evaluate.py`: group-slot Mock 응답 및 검수 artifacts

변경하지 않은 범위:

- Evidence 12 chunks와 retrieval/context 결과
- Segmentation과 SourceUnit eligibility
- PromptCoverage/AnswerCoverage 의미
- 기존 public Answer JSON과 UI citation 계약
- 기존 validator의 허용 범위
- BM25, tokenizer, query expansion, temporal rerank
- Embedding, RRF와 reranker
- Output limit과 request budget
- Retry, fallback 및 web search

## 11. 산출물 및 작업 중지

최종 Mock 산출물:

`artifacts/2026-09-14_rag-branch-aware-selection-mock-02/`

- `mock_report.json`
- `test_results.json`
- `review.html`

같은 작업 중 먼저 생성한 `artifacts/2026-09-14_rag-branch-aware-selection-mock/`은 덮어쓰지 않았으며, `-02`가 최종 검수본이다. 기존 artifacts도 변경하지 않았다.

이번 단계에서는 실제 Groq를 호출하지 않았다. 구현, Mock, 전체 회귀와 결과 문서 작성을 완료했으며 실제 Groq 재평가는 사용자의 별도 승인 전까지 수행하지 않는다.
