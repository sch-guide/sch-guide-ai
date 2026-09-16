# Facet-slot SourceUnit Selection 설계 계획

- 작성일: 2026-09-14
- 상태: **설계 및 읽기 전용 Q002 계산 완료 / 구현 전 승인 대기**
- 대상 브랜치: `boha-rag`
- 실제 Groq 호출: **0회**
- production 코드 수정: **0건**

## 1. 목적과 결론

이번 Live에서 Groq 요청 자체는 정상 종료됐지만, 실제 모델이 SourceUnit을 하나도 선택하지 않아 `selection_empty`로 차단됐다. 현재 방식은 21개 후보 중 최대 16개의 전역 subset을 모델이 자유롭게 구성한다. 과거 Live 결과가 21개 과선택, 5개 under-selection, 0개 empty selection으로 변한 것은 이 전역 조합 작업이 실제 모델에 불안정하다는 증거다.

다음 contract는 모델의 역할을 줄인다.

1. 서버가 `ProcedureAnswerRequirement`의 필수 facet을 계산한다.
2. 서버가 각 facet을 만족할 수 있는 selectable SourceUnit ID allowlist를 계산한다.
3. Groq strict Structured Outputs는 각 필수 facet 속성에 대해 allowlist의 ID 하나를 반드시 반환하게 한다.
4. 모델은 text, quote, `chunk_id`, branch, phase, action을 만들지 않는다.
5. 서버는 facet별 선택 관계를 검증한 후 동일 SourceUnit ID의 반복 참조를 하나의 canonical SourceUnit으로 투영한다.
6. 서버는 새로운 ID를 보충하지 않고, 기존 selection limit·AnswerCoverage·reconstruction·`validate_answer()`를 그대로 실행한다.

**최종 권고:** required facet마다 scalar enum 하나를 요구하는 facet-slot contract를 구현 후보로 채택한다. 이 구조는 Q002의 empty selection과 facet under-selection을 schema 단계에서 막고 Gold 14개도 표현할 수 있다. 다만 41개 독립 속성의 서로 다른 값 개수는 최소 10개, 최대 21개이므로 JSON Schema만으로 selection limit 16을 보장할 수 없다. 17~21개가 선택되면 기존 `selection_limit`으로 fail closed해야 한다. 자동 보충, 의미 기반 자동 dedup, 자동 재정렬은 사용하지 않는다.

## 2. 확인한 현재 동작

현재 production 흐름은 다음과 같다.

```text
PromptCoverage 및 SourceUnit catalog
→ procedure_answer_requirement()
→ build_selection_contract()의 group별 ID 배열 schema
→ Groq strict Structured Outputs
→ validate_source_unit_selection()
→ answer_coverage()
→ reconstruct_source_unit_answer()
→ validate_answer()
```

현재 response schema는 `group_selections.gN`마다 ID 배열을 받는다. 배열은 비어 있을 수 있고, 모델이 전체 선택 규모와 여러 coverage 조건을 함께 판단해야 한다. `validate_source_unit_selection()`은 빈 전체 선택을 `selection_empty`, 16개 초과를 `selection_limit`, 같은 ID 반복을 `selection_duplicate_id`, 순서 역전을 `selection_source_order`로 차단한다. broad procedure에서는 이어서 `answer_coverage()`가 group, branch, phase, phase-action, action diversity, query action과 source order를 검증한다.

현재 확인한 버전과 한도는 다음과 같다.

- `AI_VERSION=18`
- `PROMPT_EVIDENCE_SCHEMA_VERSION=5`
- `RESPONSE_SELECTION_SCHEMA_VERSION=4`
- selection 및 statement 최대 16
- `OUTPUT_LIMIT=2048`
- `GROQ_REQUEST_TOKEN_BUDGET=5120`

이번 계획은 이 의미와 validator를 완화하지 않는다. 구현 시 response contract가 바뀌므로 response schema version은 별도 승인 후 증가시켜야 한다.

## 3. Q002 required facet 수

현재 Q002 `ProcedureAnswerRequirement`에서 계산된 필수 항목은 다음과 같다.

| 종류 | 개수 | 내용 |
|---|---:|---|
| required group | 5 | g1~g5 |
| required branch | 2 | adult, pediatric |
| required phase | 7 | common/before 및 성인·소아 before/during/after |
| required phase-action | 21 | 각 required phase의 assessment/execution/documentation/monitoring 조합 |
| required action family | 6 | assessment, execution, documentation, communication, monitoring, transfer |
| 합계 | **41** | 모든 required clinical facet |

group·branch·phase·phase-action만 세면 35개다. 그러나 현재 `AnswerCoverage`는 action family diversity 6개도 필수로 검증하므로 facet-slot contract가 기존 validator의 의미를 온전히 표현하려면 총 41개를 필수 slot으로 보아야 한다.

Q002에는 현재 `_QUERY_ACTION_ROOTS`와 일치하는 별도 query action이 없다. 일반화된 구현에서는 query action이 존재할 때 같은 서버 규칙으로 `query_action` facet을 추가해야 한다. 이는 validator 의미 추가가 아니라 기존 `selection_missing_action` 조건을 contract에 반영하는 작업이다.

## 4. Q002 facet별 eligible SourceUnit allowlist

아래 `f01`~`f41`은 이번 분석에서만 사용한 요청 범위 ID다. production에 Q002 이름, 실제 chunk ID 또는 아래 대응표를 하드코딩하지 않는다. 운영 시 `ProcedureAnswerRequirement`와 해당 요청의 selectable catalog로 매번 다시 만든다.

### 4.1 Group 및 branch

| facet | 의미 | eligible SourceUnit ID |
|---|---|---|
| f01 | group g1 | su002 |
| f02 | group g2 | su003 |
| f03 | group g3 | su005 |
| f04 | group g4 | su006, su012, su015, su017, su020, su021 |
| f05 | group g5 | su024, su026, su029, su030, su031, su032, su034, su036, su037, su038, su039, su040 |
| f06 | branch adult | su006, su012, su015, su017, su020, su021 |
| f07 | branch pediatric | su024, su026, su029, su030, su031, su032, su034, su036, su037, su038, su039, su040 |

### 4.2 Phase

| facet | 의미 | eligible SourceUnit ID |
|---|---|---|
| f08 | common/before | su003 |
| f09 | adult/before | su012, su015 |
| f10 | adult/during | su017 |
| f11 | adult/after | su020, su021 |
| f12 | pediatric/before | su024, su026 |
| f13 | pediatric/during | su029, su030, su037 |
| f14 | pediatric/after | su032, su034, su039 |

### 4.3 Phase-action

| facet | 의미 | eligible SourceUnit ID |
|---|---|---|
| f15 | common/before/assessment | su003 |
| f16 | common/before/execution | su003 |
| f17 | common/before/monitoring | su003 |
| f18 | adult/before/assessment | su012 |
| f19 | adult/before/documentation | su012, su015 |
| f20 | adult/before/monitoring | su012 |
| f21 | adult/during/assessment | su017 |
| f22 | adult/during/documentation | su017 |
| f23 | adult/during/monitoring | su017 |
| f24 | adult/after/assessment | su020 |
| f25 | adult/after/documentation | su020 |
| f26 | adult/after/monitoring | su020, su021 |
| f27 | pediatric/before/assessment | su024 |
| f28 | pediatric/before/documentation | su024, su026 |
| f29 | pediatric/before/monitoring | su024 |
| f30 | pediatric/during/assessment | su029, su030, su037 |
| f31 | pediatric/during/documentation | su029, su030, su037 |
| f32 | pediatric/during/monitoring | su029 |
| f33 | pediatric/after/assessment | su032 |
| f34 | pediatric/after/documentation | su032 |
| f35 | pediatric/after/monitoring | su032, su034 |

### 4.4 Action family diversity

| facet | 의미 | eligible SourceUnit ID |
|---|---|---|
| f36 | assessment | su002, su003, su006, su012, su017, su020, su024, su029, su030, su031, su032, su036, su037 |
| f37 | execution | su003, su006, su015, su026, su036 |
| f38 | documentation | su006, su012, su015, su017, su020, su024, su026, su029, su030, su031, su032, su036, su037, su038, su040 |
| f39 | communication | su005 |
| f40 | monitoring | su003, su006, su012, su017, su020, su021, su024, su029, su032, su034 |
| f41 | transfer | su021, su034 |

**검증 결과:** 41개 모든 facet의 allowlist에 최소 1개 이상의 ID가 있다. 또한 41개 모두 Gold 14개 중 최소 1개를 허용한다. 따라서 빈 enum이 필요한 facet은 없다.

## 5. 읽기 전용 조합 계산 결과

계산 입력은 `offline_facet_report.json`의 21개 selectable SourceUnit과 현재 requirement뿐이다. Live raw response나 실제 Live ID를 사용하거나 추정하지 않았다.

계산 방법은 다음과 같다.

- 최소 distinct ID: 21개 후보의 조합을 작은 크기부터 전수 검사해 41개 facet 전체를 덮는 최초 조합을 찾았다.
- 최대 distinct ID: SourceUnit과 facet 사이의 이분 매칭으로 각 SourceUnit이 서로 다른 facet 속성에 최소 한 번 나타날 수 있는 최대 수를 계산했다.
- Gold 표현 가능성: Gold 14개만으로 같은 이분 매칭을 계산하고, 나머지 facet에도 Gold allowlist가 존재하는지 확인했다.

| 항목 | 결과 |
|---|---:|
| selectable SourceUnit | 21 |
| required facet slot | 41 |
| 빈 allowlist | 0 |
| Gold가 하나도 없는 allowlist | 0 |
| 가능한 최소 distinct selected ID | **10** |
| 가능한 최대 distinct selected ID | **21** |
| Gold distinct ID | **14** |
| Gold 14개 전체를 한 번 이상 사용하는 배정 | **가능** |
| selection limit 16 이내의 유효 배정 | **존재** |
| 독립 facet schema가 모든 응답을 16 이내로 보장 | **불가능** |

최소 10개 witness는 다음과 같다.

```text
su002, su003, su005,
su012, su017, su020, su021,
su024, su029, su032
```

현재 production의 `capacity_witness_size=11`은 facet을 순서대로 추가하는 greedy witness다. 이번 10은 조합 전수 검사로 얻은 정확한 최소값이다. 기존 값 11도 16 이하이므로 현재 `capacity_valid=true` 판정은 맞다. 이번 계획에서 capacity validator를 변경하지 않는다.

## 6. Gold 14개 표현 가능성

Gold 14개는 다음과 같다.

```text
su002, su003, su005, su006, su012, su015, su017,
su020, su021, su024, su026, su029, su031, su032
```

41개 필수 속성에 아래처럼 배정하면 14개가 모두 최소 한 번 나타난다.

| SourceUnit | 배정 가능한 분석 facet |
|---|---|
| su002 | f01 |
| su003 | f02, f08, f15, f16, f17, f37, f40 |
| su005 | f03, f39 |
| su006 | f36, f38 |
| su012 | f18, f19, f20 |
| su015 | f09 |
| su017 | f10, f21, f22, f23 |
| su020 | f06, f11, f24, f25, f26 |
| su021 | f04, f41 |
| su024 | f27, f28, f29 |
| su026 | f12 |
| su029 | f13, f30, f31, f32 |
| su031 | f07 |
| su032 | f05, f14, f33, f34, f35 |

이는 **Gold를 production 선택 규칙으로 사용하자는 뜻이 아니다.** facet-slot contract가 Mock Gold 14개를 구조적으로 배제하지 않는다는 읽기 전용 증명이다. production에서는 Gold ID와 이 배정표를 읽지 않는다.

또한 위 표는 집합 표현 가능성 증명이다. 단순히 f01→f41 순서로 처음 나타난 ID를 평탄화하면 source order가 보장되지 않는다. 실제 contract는 request-scoped slot 순서를 별도로 소유해야 하며, 순서 역전은 기존 validator가 계속 차단해야 한다.

## 7. 제안 데이터 구조

아래는 구현 전 설계용 의사코드다.

```python
FacetKind = Literal[
    "group", "branch", "phase", "phase_action",
    "action_family", "query_action",
]

@dataclass(frozen=True)
class RequiredFacet:
    facet_key: tuple[str, ...]       # 서버 내부 의미 키
    kind: FacetKind
    group_key: str = ""
    branch: str = ""
    phase: str = ""
    action_family: str = ""

@dataclass(frozen=True)
class FacetSelectionSlot:
    prompt_facet_id: str            # 요청 범위 f01, f02, ...
    facet: RequiredFacet
    eligible_source_unit_ids: tuple[str, ...]
    ordinal: int

@dataclass(frozen=True)
class FacetSlotSelectionContract:
    slots: tuple[FacetSelectionSlot, ...]
    maximum_distinct_source_units: int = 16
```

소유권은 다음처럼 나눈다.

- `mvp/evidence.py`: requirement를 required facet으로 펼치고 catalog metadata로 eligibility를 계산한다. 임상 coverage 의미의 단일 소유자다.
- `mvp/ai.py`: 내부 facet을 `fNN` transport slot으로 바꾸고 request-scoped JSON Schema, parser와 adapter를 소유한다.
- 기존 `answer_coverage()`와 `validate_answer()`: 최종 권위 validator로 유지한다.

`group_key`, branch, phase, action metadata는 서버가 계산한다. Provider 응답에는 `prompt_facet_id → source_unit_id`만 존재한다.

## 8. Groq strict Structured Outputs schema

권고 schema는 배열 없이 object, required, scalar enum만 사용한다.

```json
{
  "type": "object",
  "properties": {
    "facet_selections": {
      "type": "object",
      "properties": {
        "f01": {"type": "string", "enum": ["su002"]},
        "f02": {"type": "string", "enum": ["su003"]},
        "f09": {"type": "string", "enum": ["su012", "su015"]}
      },
      "required": ["f01", "f02", "f09"],
      "additionalProperties": false
    }
  },
  "required": ["facet_selections"],
  "additionalProperties": false
}
```

실제 schema에는 모든 request-scoped required slot이 들어간다.

- 모든 `fNN`은 required property다.
- 각 값은 문자열 하나다.
- enum은 해당 facet의 eligible selectable ID만 포함한다.
- 빈 enum이 하나라도 생기면 Groq를 호출하지 않고 `selection_requirement_capacity` 계열로 fail closed한다.
- 모델은 배열 길이, 전체 subset 크기, 임상 metadata나 인용문을 생성하지 않는다.
- extra property를 허용하지 않는다.

Groq 공식 Structured Outputs 문서는 `openai/gpt-oss-20b`에서 strict mode를 지원하며, strict mode의 모든 필드는 required여야 하고 모든 object에 `additionalProperties:false`가 필요하다고 설명한다. string `enum`, object와 `anyOf`도 지원 범위에 포함된다. 따라서 위 object + required + enum 구조는 문서상 지원되는 형태다.

참고: <https://console.groq.com/docs/structured-outputs>

이번 Q002 Live에서도 provider/schema/parser는 정상이었으므로 request-scoped strict schema 자체를 사용할 기반은 확인됐다. 다만 41개 property와 enum을 넣은 schema의 직렬화 토큰은 구현 단계에서 현재 `o200k_harmony` encoder로 반드시 측정해야 한다. 기존 request admission budget 5,120을 늘리지 않고 통과해야 한다.

## 9. 서버 처리 순서와 불변조건

권고 end-to-end 흐름은 다음과 같다.

```text
QueryPlan + PromptCoverage + selectable SourceUnit catalog
→ ProcedureAnswerRequirement
→ RequiredFacet 목록
→ facet별 eligible ID allowlist
→ request-scoped FacetSlotSelectionContract
→ strict JSON schema 생성
→ Groq가 facet마다 ID 하나 선택
→ exact key/type/enum 재검증
→ facet assignment 보존
→ 동일 SourceUnit ID의 identity canonicalization
→ distinct ID 1~16 및 source order 검증
→ 기존 AnswerCoverage 전체 실행
→ 서버 reconstruction
→ 기존 validate_answer() 전체 실행
```

필수 불변조건은 다음과 같다.

1. contract 생성 입력은 현재 요청의 requirement와 catalog뿐이다.
2. 각 facet allowlist는 required, selectable이고 해당 facet을 실제로 만족하는 unit만 포함한다.
3. response의 key 집합은 contract의 `fNN` 집합과 정확히 같아야 한다.
4. 각 값은 해당 slot enum에 있어야 한다. schema가 보장해도 서버가 다시 확인한다.
5. 서버가 모델이 선택하지 않은 ID를 추가하지 않는다.
6. canonical SourceUnit은 모델이 적어도 한 facet에서 직접 선택한 ID만 포함한다.
7. 서로 다른 ID가 16개를 넘으면 기존 `selection_limit`으로 차단한다.
8. source order가 맞지 않으면 기존 `selection_source_order`로 차단한다. 서버가 정렬해 통과시키지 않는다.
9. `answer_coverage()`가 group, branch, phase, phase-action, action diversity, query action을 다시 계산한다.
10. reconstruction 뒤 `validate_answer()`의 citation, exact sentence, number, unit, condition, negation, action, order 검사를 전부 실행한다.

## 10. 동일 ID가 여러 facet을 충족하는 경우

같은 SourceUnit이 여러 facet을 만족하는 것은 정상이다. 예를 들어 Q002의 su003은 group g2, common/before phase와 assessment/execution/monitoring을 동시에 충족한다.

권고 처리 방식은 다음과 같다.

- `facet_assignments`에는 `f02→su003`, `f08→su003`, `f15→su003`처럼 관계를 모두 보존한다.
- 답변 statement 입력은 SourceUnit ID별 canonical identity를 하나만 만든다.
- canonical 순서는 contract slot의 서버 정의 순서에서 해당 ID가 처음 나타난 위치를 사용한다.
- 동일 ID를 두 개의 statement로 만들지 않는다.
- text 유사도나 의미 유사도로 서로 다른 ID를 합치지 않는다.
- 서버가 ID를 바꾸거나 추가하지 않는다.
- canonical 목록을 source order로 자동 정렬하지 않는다. 순서가 틀리면 기존 validator가 실패시킨다.

이는 기존 group 배열에서 금지된 “같은 ID를 두 번 선택한 flat subset”과 의미가 다르다. 새 응답에서 반복은 여러 required facet이 같은 서버 SourceUnit을 참조하는 many-to-one 관계다. 기존 `selection_duplicate_id`를 제거하거나 완화하지 않고, facet adapter가 관계를 canonical ID 목록으로 투영한 뒤 기존 flat validator 경계에 전달한다. legacy group-selection 경로의 duplicate 검사는 그대로 유지한다.

## 11. Selection limit 16 분석

Q002에서 facet-slot 방식은 **16개 안에서 표현 가능**하다.

- exact minimum은 10개다.
- Mock Gold는 14개이며 41개 facet에 모두 배정 가능하다.
- 둘 다 16개 이하다.

하지만 41개 scalar property가 서로 다른 값을 몇 개 썼는지는 일반 JSON Schema의 독립 enum만으로 제한할 수 없다. Q002에서는 21개 SourceUnit 모두가 서로 다른 facet에 한 번씩 배정 가능한 이분 매칭이 존재한다. 따라서 schema를 만족하면서 distinct ID가 21개인 응답도 가능하다.

이 설계가 보장하는 것과 보장하지 않는 것은 다음과 같다.

| 조건 | schema로 보장 | 서버 validator로 보장 |
|---|---:|---:|
| 모든 required facet에 값 존재 | 예 | 재검증 |
| 값이 해당 allowlist 안에 있음 | 예 | 재검증 |
| 빈 전체 selection 방지 | 예 | 재검증 |
| distinct ID 최대 16 | 아니오 | 예, 초과 시 차단 |
| source order | 아니오 | 예, 역전 시 차단 |
| AnswerCoverage 전체 충족 | facet 기준으로 유도 | 예, 최종 재계산 |
| exact citation과 문장 안전성 | 아니오 | 예 |

따라서 “Q002가 16개 안에서 표현 가능한가”의 답은 **예**다. “어떤 schema-valid 응답도 16개를 넘지 않는가”의 답은 **아니오**다. 이 차이를 숨기면 안 된다.

## 12. Source order 처리

JSON object의 property 순서는 의미적 계약이 아니다. 서버는 응답에 나타난 순서를 신뢰하지 않고 `FacetSlotSelectionContract.slots`의 고정 ordinal로 값을 읽어야 한다.

권고 ordinal은 Q002 stage명이나 Gold가 아니라 다음 일반 metadata로 안정적으로 만든다.

1. eligible unit의 최소 `source_order`
2. 더 구체적인 facet 우선: phase-action, phase, group/branch, action family, query action
3. facet 의미 tuple의 안정 정렬

이 순서는 역전 가능성을 줄이는 목적이며 통과 보장은 아니다. 넓은 allowlist의 이른 slot에서 늦은 unit을 고르면 이후 slot과 역전될 수 있다. JSON Schema는 속성 간 source order 비교를 표현하지 못하므로 기존 `selection_source_order`가 최종 권위를 유지해야 한다. 서버 자동 재정렬은 하지 않는다.


## 13. Optional facet 비교

required clinical facet은 반드시 scalar enum 하나를 선택하게 한다. `null`을 허용하지 않는다.

### A. required property + nullable enum

```json
{
  "type": ["string", "null"],
  "enum": ["su010", "su011", null]
}
```

구조가 단순하고 strict mode의 “모든 property required” 조건과 맞는다. `null`은 의도적인 미선택을 명확히 나타낸다.

### B. 별도 `optional_facet_selections` object

필수와 선택 facet을 시각적으로 나눌 수 있지만 strict mode에서는 바깥 property와 내부 property를 여전히 required로 선언하고 내부 값에 null을 허용해야 한다. schema가 더 깊고 커진다.

**권고:** 첫 버전에는 optional facet을 response contract에서 제외한다. 나중에 필요해지면 A를 사용한다. required clinical facet에는 어떤 경우에도 null을 넣지 않는다.

## 14. 검토한 구조 대안

### A. 41개 독립 required facet scalar

- empty selection과 facet under-selection을 구조적으로 방지한다.
- Gold 14개 전체를 표현할 수 있다.
- provider가 text나 배열을 생성하지 않는다.
- distinct 17~21개와 source-order 역전은 schema만으로 막지 못한다.
- **이번 구현 후보로 권고한다. 기존 validator의 안전 차단을 유지한다.**

### B. 포함 관계가 있는 facet을 합쳐 10개 안팎의 compound slot으로 축약

- Q002를 16개 이하로 구조적으로 제한할 수 있다.
- 서버가 facet 묶음과 allowlist 교집합을 결정하면서 선택 자유를 크게 줄인다.
- Q002 Gold 14개 전체는 10개 scalar slot에 모두 나타날 수 없다.
- coarse facet이 표현하지 못한 임상 세부 문장을 잃을 수 있다.
- **현재는 기각한다.** 향후 실제 facet-slot Mock이 반복해서 16개를 초과할 때 별도 설계 승인을 받아 검토한다.

### C. canonical ID 16칸과 facet→canonical-slot 참조를 함께 반환

- 최대 16칸을 구조에 넣을 수 있다.
- JSON Schema가 facet의 eligible ID와 canonical slot 값의 일치라는 교차 필드 조건을 보장하지 못한다.
- provider 출력과 서버 검증이 복잡해져 현재 문제를 다른 조합 문제로 바꾼다.
- **기각한다.**

### D. 모델 선택 뒤 서버가 부족한 ID 보충

- provider 실패를 숨기고 서버가 임상 선택자가 된다.
- 사용자가 금지한 동작이며 현재 안전 모델과 맞지 않는다.
- **기각한다.**

## 15. 구현 승인 후 예상 변경 경계

아직 아래 파일은 수정하지 않았다. 구현 승인이 별도로 주어지면 최소 범위는 다음과 같다.

### `mvp/evidence.py`

- 일반화된 `RequiredFacet` 생성 함수
- facet별 eligibility 계산 함수
- 빈 allowlist와 capacity 사전 검증
- 기존 `ProcedureAnswerRequirement`와 `answer_coverage()` 의미 유지

### `mvp/ai.py`

- `FacetSelectionSlot`, `FacetSlotSelectionContract`
- request-scoped scalar enum schema 생성
- facet response parser와 exact allowlist 재검증
- facet assignment → canonical unique SourceUnit adapter
- 기존 reconstruction과 `validate_answer()` 호출 유지
- 새 trace 필드는 ID·count·reason만 저장하고 raw response, 전체 prompt, API key는 저장하지 않음

### 테스트

- `tests/test_source_unit_selection.py`
- `tests/test_groq_structured_output.py`
- 필요 시 facet contract 전용 테스트 파일 하나

BM25, embedding, RRF, reranker, selected evidence, SourceUnit segmentation/eligibility, PromptCoverage와 기존 validator 의미는 변경 대상이 아니다.

## 16. 구현 승인 후 검증 계획

실제 Groq 호출 전에 다음 offline/Mock 검증을 모두 통과해야 한다.

1. Q002 facet 수가 41이고 모든 allowlist가 non-empty인지 확인
2. 누락 required property가 strict schema 또는 서버 parser에서 차단되는지 확인
3. allowlist 밖 ID가 차단되는지 확인
4. 같은 ID가 여러 facet에서 선택돼도 facet 관계는 보존되고 canonical statement는 하나인지 확인
5. exact minimum witness 10개가 기존 AnswerCoverage를 통과하는지 확인
6. Gold 14개 배정이 canonical 14개와 citation coverage 100%로 통과하는지 확인
7. 21개 distinct 배정이 기존 `selection_limit`으로 차단되는지 확인
8. 17개 distinct 배정도 같은 reason으로 차단되는지 확인
9. source order 역전이 자동 정렬되지 않고 `selection_source_order`로 차단되는지 확인
10. group, branch, phase, phase-action, action diversity 각각의 부정 fixture가 기존 reason으로 실패하는지 확인
11. response schema 직렬화 토큰과 전체 request admission이 5,120 이내인지 확인
12. Q006이 catalog 0, transport 0회인지 확인
13. 전체 테스트, Ruff, `git diff --check` 통과
14. production에 `Q002`, 고정 stage명, chunk ID, Gold ID가 없는지 정적 검사

실제 Groq Live는 위 결과를 별도 문서로 검토하고 사용자가 승인한 뒤에만 최대 1회 실행한다. retry, fallback, web search와 두 번째 호출은 사용하지 않는다.

## 17. 실패 처리

| 실패 | 처리 |
|---|---|
| required facet allowlist가 비어 있음 | Groq 0회, capacity 실패 |
| schema property 누락·추가 | `selection_schema` 계열 차단 |
| enum 밖 ID | allowlist 검증 실패 |
| distinct ID 0 | 구조상 불가능해야 하나 서버에서 `selection_empty` 유지 |
| distinct ID 17개 이상 | 기존 `selection_limit` |
| source order 역전 | 기존 `selection_source_order` |
| 최종 coverage 부족 | 기존 AnswerCoverage reason |
| reconstruction/citation 불일치 | 기존 `validate_answer()` reason |

어떤 실패에서도 서버가 ID를 보충·교체·재정렬하거나 두 번째 모델 호출을 하지 않는다.

## 18. 최종 권고안

1. global subset array 대신 request-scoped required facet scalar enum contract를 채택한다.
2. Q002의 required slot은 현재 validator 의미 기준 총 41개다.
3. 41개 모두 eligible ID가 있고 Gold eligible ID도 있다.
4. Q002의 distinct selection 범위는 정확히 10~21개이며 Gold 14개는 표현 가능하다.
5. 16개 이내 표현 가능성은 확인됐지만 schema 자체의 16개 보장은 불가능하다. 기존 `selection_limit`을 유지한다.
6. 동일 ID의 여러 facet 참조는 facet assignment로 보존하고, statement 경계에서는 exact ID identity 하나로 canonicalize한다. 의미 기반 dedup은 하지 않는다.
7. source order는 서버 contract ordinal과 기존 validator로 검증하며 자동 재정렬하지 않는다.
8. required facet은 object + required + scalar enum으로 강제한다. array/minItems/maxItems를 사용하지 않는다.
9. optional facet은 첫 버전에서 제외한다.
10. implementation 전에 schema token budget과 Q002 Mock 10/14/17/21 cases를 검증한다.

이 설계는 `selection_empty`와 5-unit under-selection을 구조적으로 줄이는 올바른 방향이다. 동시에 21-unit over-selection 가능성까지 schema만으로 없앴다고 주장하지 않는다. 기존 validator의 fail-closed 경계를 유지하는 조건으로 구현을 권고한다.


## 19. 이번 작업에서 변경하지 않은 것

- 실제 Groq 호출: **0회**
- production 코드 수정: **0건**
- validator 완화: **0건**
- 자동 보충·의미 기반 dedup·자동 재정렬: **0건**
- Q002 전용 production 하드코딩: **0건**
- 기존 변경 파일 수정: **0건**

이 문서 작성 후 구현으로 진행하지 않고 사용자 승인을 기다린다.
