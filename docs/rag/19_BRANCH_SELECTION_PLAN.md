# Source Unit branch-aware selection schema 계획

- 작성일: 2026-09-14
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 결과: `docs/rag/19_SOURCE_UNIT_RAG_RESULT.md`
- 확인된 실패: Q002 Live `AI_EVIDENCE / selection_branch`
- 실제 Groq 호출: 0회
- production 코드 수정: 0건
- 최종 권고: **C 변형 — request-scoped 고정 group slot 기반 계층형 selection**

## 1. 결론

현재 flat `selected_source_unit_ids`는 JSON 형식과 최대 16개라는 조건만 strict schema로 강제한다. 각 ID가 어느 required group 또는 adult/pediatric branch를 대표해야 하는지는 prompt 문장과 서버 validator만 안다. 따라서 strict schema parsing이 성공해도 모델이 한 branch를 생략할 수 있으며, 이번 Q002 Live에서 그 상태가 `selection_branch`로 정확히 차단됐다.

Prompt 문구만 강화하는 A는 가장 작은 변경이지만 branch 준수는 여전히 모델의 의미 판단에 의존한다. Adult/pediatric 필드를 고정하는 B는 Q002의 branch 누락을 schema에 드러내지만 branch 종류가 없는 일반 질문과 다른 분기 체계에 대한 일반성이 낮고, required group 5개 중 일부가 빠지는 문제는 별도 validator에 남는다.

권고안은 C를 그대로 자유 배열로 구현하지 않고, 현재 request의 evidence groups에서 `g1`~`gN` 고정 slot을 생성하는 방식이다. Strict schema의 answerable=true variant에서는 모든 required group slot을 non-empty array로 요구하고, 각 slot의 items는 그 group에 속한 selectable source-unit ID enum으로 제한한다. Optional group slot은 빈 배열을 허용한다. Answerable=false variant에서는 모든 slot이 빈 배열이어야 한다.

이 구조에서는 required adult/pediatric branch가 각각 하나 이상의 required group에 의해 표현되므로 branch 누락이 곧 required group slot 위반이 된다. 서버는 빠진 branch나 ID를 자동 보충하지 않는다. Unknown, duplicate, non-selectable, 총 16개 초과, source order와 AnswerCoverage는 기존처럼 다시 검사한다.

## 2. 확정 원인과 분석 경계

### 확정 원인

Live 결과에서 확정할 수 있는 것은 다음뿐이다.

- 응답은 HTTP 200, `finish_reason=stop`이었다.
- Strict selection schema parsing은 성공했다.
- 서버가 선택 ID를 catalog에 연결한 뒤 required branch 검사를 수행했다.
- Required `adult`, `pediatric` 중 하나 이상이 선택 집합에 없어서 `selection_branch`가 발생했다.

Raw response/content와 선택 ID 목록을 저장하지 않았으므로 어느 branch와 ID가 빠졌는지는 추정하지 않는다.

### 현재 구조가 허용한 원인

현재 response schema는 다음 두 필드뿐이다.

```json
{
  "answerable": true,
  "selected_source_unit_ids": ["su002", "su003"]
}
```

`selected_source_unit_ids`에는 `maxItems=16`만 있고 branch/group별 최소 개수, 허용 ID enum 또는 group 연결이 없다. Catalog에는 group의 `required`와 `branch`가 표시되고 system instruction도 required groups와 explicit branches를 선택하라고 안내하지만, strict schema는 이 의미 조건을 표현하지 않는다.

Groq의 strict Structured Outputs는 schema 형식 준수를 보장하지만 답변의 의미 정확성까지 보장하지는 않는다. 공식 문서도 strict mode에서 모든 필드를 required로 두고 모든 object에 `additionalProperties:false`를 요구하며, persistent한 의미 오류는 지시 강화 또는 작업 분해로 다루도록 설명한다. `openai/gpt-oss-20b`는 현재 strict mode 지원 모델이다. [Groq Structured Outputs](https://console.groq.com/docs/structured-outputs), [Groq GPT-OSS 20B](https://console.groq.com/docs/model/openai/gpt-oss-20b)

따라서 이번 실패는 JSON/schema parsing 문제가 아니라 **flat schema가 branch coverage를 표현하지 못한 모델링 문제**다.

## 3. 목표와 불변식

### 목표

- Required branch 누락을 가능한 한 response schema 자체에서 차단한다.
- Required group과 branch 관계를 모델 응답 구조에 드러낸다.
- 일반 fact 질문과 branch 없는 procedure 질문에도 같은 생성 경로를 사용할 수 있게 한다.
- SourceUnit exact text, server reconstruction과 public Answer API를 유지한다.
- 서버가 누락 branch나 source unit을 자동 보충하지 않는다.
- Schema 검사를 통과해도 기존 fail-closed validator를 다시 실행한다.

### 유지할 불변식

- Selected evidence 12 chunks
- SourceUnit catalog와 segmentation
- PromptCoverage required input units 23개
- Answer selection/statement 최대 16개
- `OUTPUT_LIMIT=2048`
- `GROQ_REQUEST_TOKEN_BUDGET=5120`
- BM25, embedding, RRF와 reranker
- 기존 `validate_answer()` 및 citation/number/unit/action/condition/negation/source-order 검증
- retry, fallback과 server-side 자동 보충 없음

## 4. 대안 비교

| 기준 | A. Flat IDs + prompt 강화 | B. Branch별 field | C. Group별 계층형 selection |
|---|---|---|---|
| Required branch 누락 방지 | 낮음~중간. 지시 준수에 의존 | 높음. 필드와 non-empty 제약으로 직접 표현 가능 | 가장 높음. 모든 required group을 강제하면 branch도 함께 보장 |
| Strict schema 강제력 | 낮음. shape만 보장 | 높음. branch field 존재·ID 범위를 제한 가능 | 가장 높음. required group slot 존재·ID 범위를 제한 가능 |
| 일반화 | 높음 | 낮음~중간. adult/pediatric vocabulary에 결합 | 높음. request의 group 구조에서 동적 생성 |
| Branch 없는 질문 | 현재와 동일 | 빈 branch field 또는 별도 schema 필요 | group slot만 생성하므로 자연스럽게 호환 |
| Prompt/input token 증가 | 가장 작음 | 작음~중간 | 중간 |
| Completion token 증가 | 거의 없음 | field 3개로 소폭 증가 | group ID 반복으로 B보다 증가 |
| Current Answer API | 그대로 유지 | 그대로 유지 | 그대로 유지 |
| Validator 변경 | prompt만 바꾸면 없음 | branch array parser와 flattening 필요 | group slot parser와 flattening 필요 |
| 구현 복잡도 | 낮음 | 중간 | 중간~높음 |
| Required group 누락 방지 | 불가 | 불가. 같은 branch의 일부 group 누락 가능 | 가능 |
| 최종 판단 | 예비 fallback | Q002 한정 차선 | **권고** |

## 5. A안: Flat IDs 유지 + prompt 강화

### 방식

현재 schema를 유지하고 system instruction에 다음을 더 명확히 적는다.

- `adult`와 `pediatric`가 required이면 양쪽에서 최소 한 ID를 선택한다.
- 모든 `required:true` group에서 최소 한 ID를 선택한다.
- 선택 전에 group/branch별 누락을 자체 확인한다.

### 장점

- Response parser와 reconstruction을 바꿀 필요가 없다.
- Prompt 증가가 가장 작다.
- Branch가 없는 일반 질문과 완전히 호환된다.

### 한계

- Schema는 여전히 어느 ID가 어느 branch인지 모른다.
- 이번 Live 실패와 같은 의미 누락이 다시 발생할 수 있다.
- Strict mode가 보장하는 것은 flat JSON 구조뿐이며 branch completeness가 아니다.
- Prompt 예시를 추가해도 validator가 최종 안전 경계를 계속 부담한다.

따라서 단독 권고하지 않는다. C 구현이 Groq strict subset 또는 budget 검증에서 막힐 때만 최소 fallback으로 검토한다.

## 6. B안: Branch별 output field

### 기본 구조

```json
{
  "answerable": true,
  "common_source_unit_ids": ["su002", "su003"],
  "adult_source_unit_ids": ["su012", "su015"],
  "pediatric_source_unit_ids": ["su024", "su026"]
}
```

Strict mode 요구에 맞춰 세 array를 모두 required field로 둔다. Request에 해당 branch가 없으면 빈 배열이어야 한다. Answerable=true이고 branch가 required라면 해당 array는 non-empty여야 하며, items는 그 branch의 selectable ID enum으로 제한하는 동적 schema가 필요하다.

### 강제 가능한 범위

- Adult ID를 pediatric field에 넣는 오류: branch별 enum으로 schema 차단 가능
- Required adult/pediatric 빈 배열: true variant의 non-empty 제약으로 차단 가능
- Unknown/non-selectable ID: enum으로 차단 가능
- 세 array 합계 16개 초과와 전역 source order: 서버 validator 필요
- 같은 branch 안의 required group 누락: branch field만으로는 차단 불가

### 평가

Q002의 직접 실패에는 효과적이다. 그러나 현재 branch taxonomy인 common/adult/pediatric가 response API에 고정된다. 향후 시술 전/후, 기기 유형, 조건별 하위 분기처럼 다른 구조가 생기면 field를 늘리거나 별도 schema를 만들어야 한다.

일반 질문에서도 세 field를 모두 반환해야 하므로 불필요한 빈 배열이 생긴다. Public Answer API에는 영향이 없지만 provider 내부 모델과 parser가 branch vocabulary를 소유하게 된다. 현재 문제만 빠르게 해결하는 차선책으로는 가능하나 최종 구조로 권고하지 않는다.

## 7. C안: Group별 계층형 selection

### 자유 배열형의 한계

다음 자유 배열은 shape는 명확하지만 strict schema만으로 각 required group이 정확히 한 번 나타나는지 보장하기 어렵다.

```json
{
  "answerable": true,
  "groups": [
    {"group_id": "g4", "selected_source_unit_ids": ["su012"]}
  ]
}
```

`group_id` enum은 unknown group을 막지만, 배열에서 `g4` 중복이나 `g5` 누락은 별도 의미 검증이 필요하다. 따라서 예시 그대로의 자유 배열은 C의 구조적 장점을 충분히 사용하지 못한다.

### 권고 변형: Request-scoped 고정 group slots

Prompt catalog에 이미 있는 `g1`~`gN`을 response schema의 고정 property로 사용한다.

```json
{
  "answerable": true,
  "group_selections": {
    "g1": ["su002", "su003"],
    "g2": ["su005"],
    "g3": ["su006"],
    "g4": ["su012", "su015"],
    "g5": ["su024", "su026"]
  }
}
```

`g1`~`gN`은 request-scoped라 의료 단계명이나 Q002 ID가 아니다. 각 property는 schema에서 required이며 `additionalProperties:false`다.

Answerable=true variant:

- Required group slot: 최소 1개, 최대 16개
- Optional group slot: 0개 이상
- 각 slot의 items: 해당 group의 selectable source-unit ID enum

Answerable=false variant:

- 모든 group slot: 빈 배열
- 서버 reconstruction 없음

두 variant는 `anyOf`로 분리하고 `answerable`은 각각 단일 boolean enum으로 고정하는 방안을 먼저 Mock 검증한다. Groq 공식 문서는 strict mode에서 object/array/enum 및 `anyOf`를 지원한다고 설명한다. 다만 `minItems`, `maxItems`와 요청별 enum을 포함한 최종 schema는 실제 호출 전에 로컬 JSON Schema 검증과 Groq 무호출 payload fixture로 확인해야 한다. [Groq Structured Outputs schema requirements](https://console.groq.com/docs/structured-outputs)

### Branch 강제 방식

Branch를 모델이 다시 출력하게 하지 않는다. 서버가 각 `gN`을 기존 EvidenceGroup에 연결하고 branch를 파생한다.

- Required adult group slot이 non-empty이면 adult 대표 충족
- Required pediatric group slot이 non-empty이면 pediatric 대표 충족
- Required common group도 각각 non-empty이므로 branch뿐 아니라 group coverage까지 보장
- Optional group은 비어 있어도 answerable=true를 막지 않음

Schema가 ID를 만들어 주거나 빠진 ID를 채우는 것은 아니다. 모델이 schema 조건을 만족하는 ID를 직접 선택해야 한다.

## 8. Source order 처리

Group slot object의 JSON property 순서를 답변 순서로 신뢰하지 않는다. 대신 schema builder가 보유한 canonical group order와 각 array 안의 반환 순서를 사용한다.

권고 계약은 다음과 같다.

1. 각 group array 안의 ID는 catalog source order여야 한다.
2. 서버는 group slot을 prompt의 canonical group order로 읽는다.
3. 이는 빠진 ID를 추가하거나 array 안을 정렬하는 보정이 아니라 response topology의 고정 순회 계약이다.
4. 각 array 내부 역전은 `selection_source_order`로 차단한다.
5. Flatten한 전체 unit sequence에 기존 `procedure_citations_ordered()`를 다시 적용한다.

이 고정 순회 계약도 자동 정렬로 간주될 소지가 있으므로 구현 승인 시 명시적으로 확정해야 한다. 이를 허용하지 않는다면 자유 배열 C를 사용하고 group 순서·누락을 runtime에서 검사해야 하며, schema 자체의 branch 강제력은 낮아진다.

## 9. Answerable=false와 strict schema

Required group array를 항상 non-empty로 만들면 근거 부족 시 `answerable=false`를 표현할 수 없다. 따라서 true/false variant 분리가 필요하다.

```text
anyOf
  ├─ abstain: answerable=false, 모든 group array 비어 있음
  └─ answer:  answerable=true, required group array non-empty
```

모든 object property는 두 variant 각각 required이고 `additionalProperties:false`를 유지한다. Pydantic static model 하나로 request별 group property와 ID enum을 표현하기 어렵기 때문에, 다음 중 하나가 필요하다.

- Request-scoped Pydantic model을 생성하고 `model_json_schema()` 사용
- 작고 검증된 schema builder가 current catalog/PromptCoverage에서 JSON Schema 생성

권고는 후자다. Provider schema는 request 데이터의 ID enum을 포함하므로 request-scoped인 것이 자연스럽다. 단, hand-written schema drift를 막기 위해 별도 local validator와 fixture snapshot이 같은 builder 결과를 사용해야 한다.

## 10. 제안 타입과 인터페이스

다음은 미구현 설계다.

```python
@dataclass(frozen=True)
class SelectionGroupSlot:
    prompt_group_id: str          # g1, g2 ...
    group_key: str                # server-only canonical key
    branch: str
    required: bool
    selectable_source_unit_ids: tuple[str, ...]
    source_order: int

@dataclass(frozen=True)
class BranchAwareSelectionContract:
    slots: tuple[SelectionGroupSlot, ...]
    maximum_selected_units: int = 16

def build_selection_contract(
    groups: Sequence[EvidenceGroup],
    catalog: Sequence[SourceUnit],
) -> BranchAwareSelectionContract: ...

def groq_selection_json_schema(
    contract: BranchAwareSelectionContract,
) -> dict: ...

def validate_group_selection(
    raw: str,
    contract: BranchAwareSelectionContract,
    prompt_coverage: PromptCoverage,
    plan: QueryPlan,
    *,
    trace: dict | None = None,
) -> tuple[SourceUnit, ...]: ...
```

`SelectionGroupSlot`과 contract는 request-scoped이고 저장하거나 DB schema에 추가하지 않는다. Catalog serialization과 response schema가 같은 contract를 사용해 `gN` drift를 막는다.

## 11. Validation 책임

### Schema가 우선 차단할 항목

- 필수 response field 누락
- Unknown group property
- Unknown source-unit ID
- 다른 group/branch의 ID를 잘못된 slot에 배치
- Required group의 빈 selection(answerable=true variant)
- Non-selectable ID
- Answerable=false인데 ID가 존재하는 상태

### 서버 validator가 계속 차단할 항목

- 모든 group을 합친 duplicate ID
- 총 selection 16개 초과
- 각 group array 및 전체 source order 역전
- PromptCoverage required group/branch와 response contract drift
- 질문에 명시된 action requirement 누락
- AnswerCoverage 불충분
- Reconstruction 후 기존 exact citation, number, unit, action, condition/negation와 source-order 오류

Strict schema를 사용해도 서버 validator를 삭제하거나 완화하지 않는다. Schema는 모델이 잘못된 상태를 생성하기 어렵게 만들고, validator는 서버가 신뢰하는 최종 안전 경계로 남는다.

## 12. 일반 질문과 호환성

### Branch 없는 질문

Group slot은 존재하지만 branch가 모두 `common`일 수 있다. Required common group만 non-empty로 강제하고 adult/pediatric 전용 field는 만들지 않는다. 따라서 B처럼 불필요한 빈 branch field가 생기지 않는다.

### 단일 branch 질문

해당 request의 required group만 slot으로 생성한다. Catalog에 없는 branch를 미리 정의하지 않는다.

### 여러 문서와 조건 분기

Group key는 document/parent 구조에서 파생되므로 adult/pediatric가 아닌 분기에도 같은 schema를 사용할 수 있다. Branch 문자열은 모델 출력 대상이 아니어서 모델이 새 branch명을 만들 수 없다.

### Public Answer API/UI

Provider 내부 응답만 group selection 구조로 바뀐다. 서버 reconstruction 결과는 계속 기존 `Answer`다.

- `Statement.text = SourceUnit.exact_text`
- `Evidence.chunk_id = SourceUnit.chunk_id`
- `Evidence.quote = SourceUnit.exact_text`
- statements 최대 16

따라서 Streamlit UI, citation 카드와 public Answer JSON 구조는 바뀌지 않는다.

## 13. Token 및 budget 영향

현재 Q002 기준값은 다음과 같다.

| 항목 | 현재 값 |
|---|---:|
| Live prompt tokens | 2326 |
| Live completion tokens | 104 |
| Mock reservation | 4497 |
| Admission cap | 5120 |
| Headroom | 623 |
| 최소 요구 headroom | 360 |

정확한 증가량은 구현 전 확정할 수 없으므로 아래는 상대적 예상이다.

| 대안 | Prompt/input 예상 증가 | Completion 예상 증가 | 5120 충돌 위험 |
|---|---:|---:|---|
| A | 약 30~80 tokens | 거의 없음 | 낮음 |
| B | schema와 field 설명 약 60~160 tokens | 약 10~40 tokens | 낮음~중간 |
| C 고정 group slots | group별 enum/schema 약 120~300 tokens | 약 25~80 tokens | 중간, Mock 실측 필수 |

현재 `estimated_tokens(messages, "groq_free")`는 system/user message token에 10% 여유와 `OUTPUT_LIMIT=2048`을 더하지만 `response_format.json_schema` 본문을 직접 세지 않는다. Provider의 실제 prompt usage에는 schema 처리 비용이 반영될 수 있으므로 C 구현 시 다음 두 값을 모두 기록해야 한다.

1. 기존 admission reservation 방식의 값
2. Serialized response schema의 별도 token estimate

권고 합격 조건은 기존과 동일하다.

- 전체 reservation이 5120 이하
- headroom이 `max(256, reservation × 8%)` 이상
- 조건을 충족하지 못하면 evidence, output limit 또는 validator를 줄이지 않고 구현을 중단

Budget 변경은 이번 계획에 포함하지 않는다.

## 14. 최소 변경 범위

후속 구현 승인 시 예상 변경은 다음과 같다.

| 파일 | 변경 |
|---|---|
| `mvp/ai.py` | request-scoped group selection contract/schema, parser, group flattening |
| `mvp/evidence.py` | 기존 group/catalog를 contract에 전달하는 순수 adapter가 필요할 때만 최소 변경 |
| Source Unit 관련 테스트 | strict true/false variant, required group/branch, wrong-slot, order, limit 회귀 |
| 평가 도구 | schema/message token 분해와 raw-free Mock 결과 |

다음은 바꾸지 않는다.

- Segmentation 및 SourceUnit eligibility
- Selected evidence와 retrieval 계층
- PromptCoverage/AnswerCoverage 의미
- Reconstruction과 public Answer API
- 기존 `validate_answer()`

Schema와 prompt 의미가 바뀌므로 구현 시 `AI_VERSION`과 provider schema version 증가 여부를 명시적으로 검토해야 한다. Evidence catalog v3의 wire format을 바꾸지 않는다면 `PROMPT_EVIDENCE_SCHEMA_VERSION=3`은 유지 가능하고, response schema만 별도 version으로 추적하는 편이 역할 분리에 맞다.

## 15. Mock 검증 계획

실제 Groq 호출 전에 다음을 모두 검증한다.

### Schema

- 모든 object의 properties가 required이며 `additionalProperties=false`
- Answerable=true/false variant가 strict-compatible
- Required group slot은 true variant에서 비어 있을 수 없음
- Optional group slot은 빈 배열 허용
- 각 slot의 ID enum은 해당 group의 selectable catalog ID와 정확히 일치
- 다른 branch ID를 slot에 넣으면 schema validation 실패
- Model이 branch/group/source order/text/quote/chunk ID를 생성할 필드가 없음

### Selection validator

- Q002 14 IDs를 5 required group slot에 나눠 정상 통과
- Adult 또는 pediatric required group을 비우면 schema 또는 `selection_branch` 차단
- Required common group 하나를 비우면 차단
- Unknown, duplicate, non-selectable, wrong-slot, 총 16개 초과 차단
- 각 group 내부 및 전체 source order 역전 차단
- Server가 누락 ID를 추가하거나 순서를 수정하지 않음
- Answerable=false + 모든 빈 slot만 정상 abstention

### Reconstruction과 회귀

- Q002 selected evidence 12 chunks 유지
- Pre/post required gold recall 10/10
- Source-unit gold 10/10을 14 IDs로 표현
- Exact text/quote/citation 100%
- Adult/pediatric 및 required group 5개 유지
- Number/unit/action/condition/negation/source-order 기존 검증 유지
- Q006 catalog 0건, transport 0회
- 전체 테스트와 Ruff 통과

### Budget

- Compact catalog v3 원문 불변
- Response schema token estimate 별도 기록
- Reservation ≤ 5120
- Headroom ≥ `max(256, reservation × 8%)`

## 16. 합격 기준과 중단 조건

### Mock 합격 기준

1. Q002 required group 5개가 schema에 고정 slot으로 존재한다.
2. Adult/pediatric required slot은 answerable=true에서 각각 non-empty다.
3. Q002 14 IDs가 총 16개 제한 안에서 통과한다.
4. Server reconstruction과 기존 validator가 모두 통과한다.
5. Citation coverage 100%, source order 역전 0건이다.
6. Q006 transport는 0회다.
7. Budget/headroom 기준과 전체 테스트/Ruff를 통과한다.

### 구현 또는 live 전 중단 조건

- Groq strict subset에서 필요한 true/false union이나 group array 제약을 표현할 수 없음
- Request schema와 catalog group ID가 일치하지 않음
- Required group/branch 누락을 schema에서 차단할 수 없음
- Canonical group 순회가 기존 ‘자동 정렬 금지’ 계약과 충돌하고 별도 승인이 없음
- Reservation/headroom 기준 미충족
- 기존 validator 완화가 필요함

이 중 하나라도 발생하면 A로 임의 전환하거나 실제 Groq를 호출하지 않고 결과를 보고한다.

## 17. Rollback

변경은 provider 내부 response contract에 한정한다.

1. Request-scoped group selection schema와 parser를 제거한다.
2. 기존 flat `SourceUnitSelection`과 `selected_source_unit_ids`로 되돌린다.
3. 관련 response schema/AI version을 이전 값으로 복구한다.
4. SourceUnit catalog, segmentation, PromptCoverage/AnswerCoverage와 기존 validator는 유지한다.

Chunk, embedding, DB, retrieval와 기존 artifacts는 rollback 대상이 아니다.

## 18. 최종 권고 및 승인 대기

최종 권고는 **C의 request-scoped 고정 group slot 변형**이다. 자유 배열보다 strict schema 강제력이 높고, B처럼 adult/pediatric라는 현재 분기명에 provider API를 고정하지 않는다. Required group을 non-empty로 강제하면 그 group에서 파생된 required branch도 구조적으로 보장할 수 있다.

다만 schema는 의미 정확성을 완전히 대신하지 않는다. 서버는 기존 branch/group/action/order validator와 reconstruction 후 `validate_answer()`를 모두 유지해야 한다. 누락 branch를 자동 보충하거나 validator를 완화하지 않는다.

이번 작업에서는 실제 Groq 호출과 production 코드 수정을 수행하지 않았다. 다음 구현은 사용자 승인 후에만 진행한다.
