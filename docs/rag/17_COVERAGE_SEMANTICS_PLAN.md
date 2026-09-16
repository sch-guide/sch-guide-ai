# RAG Prompt Coverage / Answer Coverage 의미 분리 계획

- 작성일: 2026-09-14
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준선: `docs/rag/17_SOURCE_UNIT_SELECTION_BLOCKED.md`
- 검증 방식: 현재 코드·Q002 fixture·기존 artifacts의 read-only 분석
- 실제 Groq 호출: 0회
- production 코드 수정: 0건
- 단계: 의미 분리 설계 완료, Source Unit D안 구현은 계속 보류

## 1. 결론

`ProcedureCoverage.required_procedural_unit_keys`는 **최종 답변이 모두 출력해야 하는 문장 목록이 아니다.** 현재 구현과 최초 설계에서 이 값은 pre-LLM evidence에 들어 있던 절차 action unit이 group 단위 prompt budget을 지난 뒤에도 빠지지 않았는지를 검사하는 **prompt 입력 완전성 지표**로 만들어졌다. 이를 Source Unit Selection의 최종 출력 의무로 재사용하면 입력에 포함된 23개 action unit을 최대 10개 답변 ID로 모두 선택해야 하므로 정상 답변도 항상 차단된다.

따라서 다음 두 의미를 분리하는 것을 권고한다.

1. `PromptCoverage`: LLM에 제공할 근거가 budget 전후에 손실되지 않았는지 검사한다. 현재 required group 5개, required procedural unit 23개, adult/pediatric branch, parent 원자성, source order를 그대로 보수적으로 유지한다.
2. `AnswerCoverage`: 모델이 선택한 source unit이 질문에 필요한 답변 구조를 충족하는지 검사한다. 모든 prompt unit 선택을 요구하지 않고, request-scoped hierarchy의 required group·required branch·질문에 명시된 action requirement를 만족하는지 별도로 검사한다.

운영 권고안은 **D. hierarchical group → unit selection**이다. 다만 현재 Q002 gold 10개는 chunk-level retrieval gold이며 일부 stage가 여러 source unit에 걸친 복합 의미다. 보수적으로 source-unit-level gold를 매핑하면 최소 15개 unit reference가 필요하고, `chunk-00025#1/#2`는 현재 `source_sentences()` 경계상 각각 완결된 문장/표 행이 아니다. 따라서 현재 최대 10개·unit 병합 금지·segmentation 변경 금지 조건에서는 Q002 required gold 10/10을 안전하게 표현할 수 없다.

이 설계는 의미 충돌의 원인을 해결하지만 Source Unit D안 구현 승인은 아직 충족하지 않는다. 먼저 평가 fixture에 정확한 source-unit-level gold를 추가하고, Q002의 복합 stage 및 불완전 unit 경계를 별도 승인으로 해결해야 한다.

## 2. 현재 `required_procedural_unit_keys`의 실제 목적

### 생성 경로

현재 `mvp/evidence.py`의 흐름은 다음과 같다.

1. `assess_evidence()`가 procedure 질문의 substantive seed와 관련 context hit를 고른다.
2. `evidence_groups()`가 `parent_id` 단위로 hit를 묶고 seed, seed neighbor, 명시적 branch group을 `required=True`로 표시한다.
3. `procedure_coverage()`가 required group 안의 각 chunk를 `source_sentences()`로 나눈다.
4. 각 source sentence에서 `PROCEDURE_ACTION` 정규식이 일치하면 `chunk_id#position` 형태의 `required_procedural_unit_keys`를 만든다.

이 값은 임상 gold stage를 나타내지 않는다. action 정규식에 걸린 구조적 원문 단위를 열거할 뿐이며, 중요도·질문별 필요성·동일 단계의 중복 설명을 구분하지 않는다.

### 사용 경로

| 단계 | 사용 방식 | 의미 |
|---|---|---|
| pre-LLM `assess_evidence()` | 하나 이상의 required procedural unit이 존재하는지 확인 | procedure 근거 존재성 |
| `prompt_messages()` | required group을 optional group보다 먼저 원자적으로 budget에 포함 | prompt group 보존 |
| post-budget `assess_evidence()` | 선택된 hit로 coverage를 다시 계산 | budget 후 입력 상태 |
| `required_coverage_loss(before, after)` | pre required groups/units/branches가 post 집합에 포함되는지 확인 | prompt 입력 손실 차단 |
| parent partial 검사 | 같은 parent 일부만 남았는지 확인 | prompt 원자성 |
| `validate_answer()` | `required_procedural_unit_keys`를 사용하지 않음 | 최종 답변 완전성과 직접 연결되지 않음 |

즉 최초 목적은 **prompt 입력 완전성**이다. 최종 Answer validator는 answerable/statements 일관성, exact sentence와 citation, 숫자·단위·행동, conflict 및 source order를 검사하지만 23개 required unit의 출력 여부를 검사하지 않는다.

Source Unit D안의 초기 설계가 이 값을 selection hard gate에 그대로 재사용하려 한 부분이 현재 충돌의 원인이다.

## 3. 현재 evidence gate별 책임

### Required group

- seed가 포함된 group, seed와 직접 연결된 neighbor group, 명시적 adult/pediatric 병렬 branch group을 나타낸다.
- pre-budget에서 회수된 parent 단위 의미 묶음을 prompt에 전부 전달하기 위한 경계다.
- post-budget에서 하나라도 사라지면 `missing_evidence_group`으로 Groq 호출을 차단한다.
- group 내부 일부 chunk만 포함하면 별도 parent partial 검사로 차단한다.

### Required procedural unit

- required group 안에서 action 패턴이 검출된 모든 `source_sentences()` 원소다.
- 질문에 반드시 답해야 할 임상 stage 목록이 아니라, budget 전후 동일 evidence 내용이 유지됐는지를 비교하는 세밀한 fingerprint다.
- 현재 Q002에서 23개이며, prompt에 23개 모두 존재해야 한다는 의미는 타당하지만 답변이 23개 모두 출력해야 한다는 의미는 아니다.

### Required branch

- required group 중 `common`이 아닌 명시적 branch를 보존한다.
- Q002에서는 `adult`, `pediatric`가 pre/post prompt에 모두 남아야 한다.
- 현재 final validator는 답변이 두 branch를 각각 대표하는지 별도 coverage로 확인하지 않는다. Source Unit Selection에는 이 출력 검사가 새로 필요하다.

### Source order / duplicate / completeness

- `source_ordered`, `duplicate_count`, group `complete`는 LLM 입력 근거의 구조적 건전성을 검사한다.
- final Answer에서는 별도로 `procedure_citations_ordered()`가 실제 citation 순서를 검사한다.
- 입력과 출력에 이름이 비슷한 검사가 있어도 대상 집합이 다르므로 같은 coverage 객체로 합치지 않는다.

## 4. Prompt completeness와 Answer completeness가 다른 이유

Prompt completeness는 모델이 판단할 수 있는 근거의 **상한**을 보장한다. 필요한 parent, branch 또는 action 문장이 입력에서 누락되면 모델은 올바른 답을 선택할 기회조차 없다. 따라서 budget 전후 집합 보존과 parent 원자성을 강하게 요구하는 것이 맞다.

Answer completeness는 사용자에게 보여 줄 답변의 **충분성**을 보장한다. 입력에 들어간 모든 문장은 다음 이유로 출력 대상이 아니다.

- 같은 절차를 설명하는 heading, 보조 문장, 기록 양식 설명이 함께 들어올 수 있다.
- 하나의 parent group에 핵심 단계와 조건부 보완 근거가 함께 있다.
- action 정규식은 중요도를 알지 못해 gold가 아닌 문장도 unit으로 센다.
- 한 source sentence가 여러 stage를 지지하거나, 반대로 한 stage가 여러 source sentence를 필요로 할 수 있다.
- prompt는 모델이 선택할 후보 catalog이고 답변은 그중 질문에 필요한 subset이다.

두 지표를 동일하게 쓰면 다음 문제가 발생한다.

1. 현재처럼 23개 입력 unit을 최대 10개 출력에 강제해 영구 false abstention이 된다.
2. action regex에 우연히 걸린 보조 문장을 임상 필수 단계처럼 취급한다.
3. 출력 길이 제한이 retrieval 품질이나 prompt completeness 실패로 잘못 보고된다.
4. 더 많은 안전 근거를 prompt에 넣을수록 답변 gate가 오히려 통과하기 어려워지는 역설이 생긴다.
5. 운영 구조 지표와 사람이 검수한 Q002 gold의 의미가 섞여, 무엇이 누락됐는지 설명할 수 없게 된다.

따라서 prompt coverage는 **모든 제공 근거 보존**, answer coverage는 **질문에 필요한 선택 결과 보존**이라는 서로 다른 불변식을 가져야 한다.

## 5. 권고 데이터 모델

다음은 후속 구현 승인을 위한 미구현 설계다.

```python
@dataclass(frozen=True)
class PromptCoverage:
    required_group_keys: tuple[str, ...]
    optional_group_keys: tuple[str, ...]
    input_required_unit_keys: tuple[str, ...]
    input_optional_unit_keys: tuple[str, ...]
    required_branches: tuple[str, ...]
    source_ordered: bool
    parent_groups_complete: bool
    duplicate_count: int

@dataclass(frozen=True)
class AnswerCoverageRequirement:
    key: str
    group_key: str
    branch: str
    kind: Literal["group_representative", "branch_representative", "query_action"]
    eligible_source_unit_ids: tuple[str, ...]
    required: bool

@dataclass(frozen=True)
class AnswerCoverage:
    required_requirement_keys: tuple[str, ...]
    satisfied_requirement_keys: tuple[str, ...]
    selected_group_keys: tuple[str, ...]
    selected_branches: tuple[str, ...]
    source_ordered: bool
    complete: bool
    reason: str
```

`PromptCoverage.input_required_unit_keys`는 현재 23개 값을 의미 변경 없이 보존한다. 기존 `ProcedureCoverage`를 즉시 rename해야 하는지는 구현 시 호환성을 검토하되, 적어도 selection validator에 직접 전달하지 않는다.

`AnswerCoverageRequirement`는 runtime source-unit catalog가 만들어진 뒤 request-scoped로 파생한다. Q002 chunk ID나 진정 단계명은 운영 코드에 넣지 않는다. 운영 요구사항은 구조 신호와 현재 `QueryPlan`의 명시적 action에만 의존한다.

평가에서는 별도의 gold adapter가 fixture의 source-unit mapping을 읽어 `GoldAnswerCoverage`를 계산한다. 운영 `AnswerCoverage`와 평가 gold 결과를 한 객체로 섞지 않는다.

## 6. Prompt coverage 정책

Prompt coverage는 현재보다 느슨하게 만들지 않는다.

1. selected evidence 12 chunks를 유지한다.
2. required group 5개를 모두 prompt에 포함한다.
3. 현재 required procedural unit 23개를 input coverage fingerprint로 유지한다.
4. adult/pediatric required branch를 모두 유지한다.
5. parent group partial inclusion 0건을 요구한다.
6. source order와 exact duplicate 0건을 유지한다.
7. compact catalog v3에서도 각 input-required unit이 catalog에 존재하거나, `selectable=false`로 제외된 이유가 명시적으로 추적돼야 한다.

중요하게도 `selectable=false`는 prompt에서 원문을 삭제한다는 뜻이 아니다. heading/header/caption 또는 불완전 fragment는 group/chunk context로 보존할 수 있지만 LLM이 답변 ID로 선택하지 못하게 한다. 따라서 prompt completeness는 다음 두 수치를 따로 기록해야 한다.

- `catalog_present_input_units`: prompt에 문맥으로 존재하는 input unit
- `catalog_selectable_answer_units`: 최종 Answer 후보가 될 수 있는 unit

후자가 전자보다 작아도 prompt completeness 실패가 아니다. 다만 required answer requirement를 충족할 selectable unit이 없다면 AnswerCoverage 구성 단계에서 호출 전 fail closed한다.

## 7. Answer coverage 정책 대안 비교

| 대안 | 안전성 | 누락 위험 | False abstention | 일반화 | 운영 복잡도 | 판단 |
|---|---|---|---|---|---|---|
| A. required group당 대표 unit 1개 | 중간 | 높음. 큰 group 내부 여러 단계 누락 가능 | 낮음 | 높음 | 낮음 | 단독 사용 기각 |
| B. required branch당 대표 unit 1개 | 낮음~중간 | 매우 높음. adult/pediatric 각 1문장만으로 절차 전체라고 오인 | 낮음 | 높음 | 낮음 | 단독 사용 기각 |
| C. query-relevant action unit coverage | 중간~높음 | action parser가 놓친 단계 또는 광범위 ‘절차’ 질문에서 누락 | 중간 | 중간 | 중간 | 보조 조건 |
| D. hierarchical group → unit selection | 가장 높음 | 구조 내부 의미 단계 추론 한계는 남음 | 중간 | 중간~높음 | 중간 | **권고** |
| E. 현재 23개 전부 선택 | 입력 보존은 높으나 출력 정책으로 부적합 | 낮음 | 확정 100% | 낮음 | 낮음 | 불가능·기각 |

### A. Required group 대표 unit

Q002의 5개 required group을 모두 대표할 수 있어 branch와 parent 다양성은 확보한다. 그러나 adult 4 chunks와 pediatric 5 chunks가 각각 하나의 parent group이므로 대표 1개만 선택하면 전·중·후 단계가 대부분 사라질 수 있다. D의 하위 최소 조건으로만 사용한다.

### B. Required branch 대표 unit

adult/pediatric 누락 방지에는 필요하지만, 각 branch 한 문장만으로 전체 절차를 충분하다고 판단할 수 없다. 역시 D의 하위 최소 조건이다.

### C. Query-relevant action unit coverage

질문이 ‘세척’, ‘교체’, ‘투약’처럼 명시적 action을 요구하면 해당 action이 포함된 selectable unit을 최소 하나 요구할 수 있다. 하지만 Q002의 ‘절차’처럼 광범위한 질문에서는 `PROCEDURE_ACTION` 23개 전체가 relevant로 보이므로 이것만으로 subset을 정할 수 없다. 명시 action 질문에 한정한 보조 조건으로 사용한다.

### D. Hierarchical group → unit selection

먼저 required group과 required branch를 누락 없이 대표하게 하고, 그 안에서 질문에 명시된 action requirement를 충족하는 selectable unit을 선택한다. 모델은 최대 10개 ID를 source order대로 반환한다. 서버는 group, branch, action requirement를 계층적으로 확인하며 자동 정렬·dedup·보정하지 않는다.

이 방식은 운영 코드가 임상 단계명을 추론하지 않으면서 구조적 누락을 가장 강하게 줄인다. 다만 ‘절차’처럼 포괄적인 질문의 임상적 완전성은 구조만으로 증명할 수 없으므로 Q002 같은 release fixture의 source-unit gold 검증을 별도로 통과해야 한다.

### E. 23개 전부 선택

PromptCoverage를 AnswerCoverage로 그대로 재사용하는 현재 충돌안이다. 최대 10개 제한에서 통과할 수 없고, action regex가 잡은 보조 문장까지 출력하게 한다. 채택하지 않는다.

## 8. 권고 AnswerCoverage 흐름

후속 구현 시 다음 순서를 권고한다.

1. pre/post budget은 기존 PromptCoverage로 검사한다. 23개 input unit과 5개 group, branch, parent 원자성을 모두 유지한다.
2. selected 12 chunks에서 `source_sentences()` 기반 SourceUnit catalog를 만든다.
3. 별도 eligibility가 heading/header/caption/incomplete fragment를 `selectable=false`로 표시한다.
4. required group마다 최소 하나의 selectable substantive action unit이 있는지 확인한다.
5. required adult/pediatric branch마다 최소 하나의 selectable unit이 있는지 확인한다.
6. 질문에 명시적 action이 있으면 해당 action requirement를 추가한다. 일반 ‘절차’라는 단어만으로 23개 전부를 요구하지 않는다.
7. LLM selection을 strict schema로 읽고 unknown, duplicate, non-selectable, 10개 초과, order 역전을 차단한다.
8. 선택된 ID가 모든 runtime `AnswerCoverageRequirement`를 만족하는지 검사한다.
9. 평가 실행에서는 운영 gate와 별도로 source-unit-level gold coverage를 계산한다.
10. 서버가 Answer를 복원한 뒤 기존 `validate_answer()` 전체를 다시 실행한다.

운영 gate가 통과해도 gold fixture가 실패하면 release acceptance는 실패다. 반대로 평가 gold는 운영 코드에 import하거나 하드코딩하지 않는다.

## 9. Q002 source-unit-level gold mapping 설계

현재 fixture의 `allowed_chunk_ids`는 retrieval recall에는 충분하지만 Answer coverage에는 부족하다. 다음과 같은 평가 전용 구조를 추가한다.

```json
{
  "stage_id": "adult-during",
  "importance": "required",
  "branch": "adult",
  "source_order": 6,
  "source_unit_requirements": [
    {
      "all_of": [
        {"chunk_id": "...chunk-00021", "source_unit_position": 2},
        {"chunk_id": "...chunk-00021", "source_unit_position": 4}
      ]
    }
  ]
}
```

규칙은 다음과 같다.

- `(chunk_id, source_unit_position)`을 canonical 평가 key로 사용한다.
- 구현 후 catalog의 request-local `suNNN` ID는 실행마다 이 key에 연결해 평가한다.
- 한 stage가 여러 문장을 모두 필요로 하면 `all_of`로 표현한다.
- 동일 stage를 뒷받침하는 대체 원문이 있으면 `source_unit_requirements`에 여러 alternative를 두고, alternative 중 하나의 `all_of`가 전부 선택됐을 때만 stage recall로 센다.
- 한 source unit이 여러 stage를 완전히 지지하는 경우 여러 stage가 같은 key를 참조할 수 있다.
- source unit text 자체를 fixture에 복제하지 않고, source SHA-256과 position으로 drift를 검출한다. 검수 편의를 위한 text fingerprint는 추가할 수 있다.
- Q002 단계명과 ID는 `tests/fixtures` 및 평가 도구에만 존재하며 `mvp/`에서 읽지 않는다.

이 구조는 chunk가 검색됐다는 사실과 실제 답변 원문이 그 stage를 표현했다는 사실을 구분한다.

## 10. Q002 read-only source-unit 계산

현재 selected evidence 12 chunks를 현재 `source_sentences()`로 분리한 결과는 43개 raw unit, action regex 기준 input-required unit 23개다. 기존 required gold 10개를 의미 손실 없이 source unit에 연결하면 다음과 같다.

| Gold stage | 보수적 source-unit key | 필요 개수 |
|---|---|---:|
| common-order | `00013#2` | 1 |
| common-pre-assessment | `00015#1` | 1 |
| common-explanation-consent | `00016#1` | 1 |
| common-record-scope | `00019#1` | 1 |
| adult-before | `00020#4` | 1 |
| adult-during | `00021#2` + `00021#4` | 2 |
| adult-after | `00022#2` + `00022#3` | 2 |
| pediatric-before | `00023#3` + `00023#5` | 2 |
| pediatric-during | `00024#2` | 1 |
| pediatric-after | `00025#1` + `00025#2` + `00025#3` | 3 |
| 합계 | distinct source-unit references | **15** |

현재 gold label의 결합 의미를 약화해 각 stage에서 임의의 한 문장만 고르면 10개 ID로 10/10처럼 계산할 수 있다. 그러나 예를 들어 adult-during의 label은 투약과 주기적 모니터링을 함께 요구하고, pediatric-before는 사전 평가와 투약을 함께 요구한다. 한쪽만 선택하고 stage 전체를 recalled로 세는 것은 보수적 평가가 아니다.

추가로 `pediatric-after`의 조건부 혈압 행은 현재 `source_sentences()`에서 `00025#1`과 `00025#2`로 나뉜다. 각각은 독립적인 완전 문장/표 행으로 보기 어렵기 때문에 승인된 eligibility 원칙대로라면 `selectable=false`가 되어야 한다. `source_sentences()` 수정과 unit 병합이 금지된 현재 범위에서는 이 gold 의미를 Answer unit으로 안전하게 표현할 수 없다.

따라서 read-only 계산 결론은 다음과 같다.

- chunk-level retrieval gold 10/10: 가능, 기존 결과 유지
- 느슨한 ‘stage당 임의 대표 unit 1개’ 10/10: 수치상 가능하지만 안전성 때문에 기각
- 보수적 source-unit-level required gold 10/10: 최소 15개 reference이며 max 10 초과
- 완전 문장/표 행 selectable 조건까지 적용: `00025#1/#2` 경계 때문에 현재 구조로는 완전 표현 불가

## 11. 실패 처리와 trace

Prompt와 Answer의 reason code를 분리한다.

Prompt gate 예시:

- `prompt_missing_group`
- `prompt_missing_input_unit`
- `prompt_missing_branch`
- `prompt_partial_parent`
- `prompt_source_order`

Answer selection gate 예시:

- `selection_unknown_id`
- `selection_duplicate_id`
- `selection_non_selectable`
- `selection_missing_group_representative`
- `selection_missing_branch_representative`
- `selection_missing_query_action`
- `selection_source_order`
- `selection_limit`

평가 전용 reason:

- `gold_stage_missing`
- `gold_stage_partial`
- `gold_unit_unselectable`
- `gold_selection_limit_exceeded`

Trace에는 raw text 없이 group/unit count, requirement key, branch, 위반 위치와 reason만 남긴다. 평가 artifact에는 source-unit key와 stage mapping을 기록할 수 있지만 운영 코드나 일반 trace에 Q002 gold label을 넣지 않는다.

## 12. 호환성과 변경 예정 범위

이 문서에서는 어떤 파일도 구현 변경하지 않는다. 후속 설계가 승인되고 Q002 표현 가능성 문제가 해결될 경우 예상 범위는 다음과 같다.

| 파일 | 예상 책임 |
|---|---|
| `mvp/evidence.py` | PromptCoverage 의미 명시 및 runtime AnswerCoverage requirement 파생 |
| `mvp/ai.py` | SourceUnit catalog, selection validation, 서버 Answer 복원, 기존 validator 재실행 |
| `mvp/search_trace.py` | prompt/answer coverage reason 분리 |
| `tests/fixtures/q002_gold_stages.json` 또는 별도 v2 fixture | 평가 전용 source-unit requirement mapping |
| 평가 도구 | runtime coverage와 gold answer coverage를 별도 계산 |

Public `Answer`, UI, citation metadata와 `validate_answer()`는 그대로 유지한다. BM25, embedding, RRF, reranker, selected evidence, output limit과 request budget은 변경 대상이 아니다.

## 13. 후속 합격 기준

Source Unit D안 구현을 다시 승인하기 전 최소 선행 조건은 다음과 같다.

1. PromptCoverage가 selected 12 chunks, required groups 5개, input-required units 23개와 adult/pediatric branch를 그대로 보존한다.
2. 평가 fixture가 required stage별 source-unit `all_of`/alternative mapping을 가진다.
3. 각 mapped unit이 `source_sentences()` 결과와 정확히 연결되고 selectable 여부가 사람에게 검수된다.
4. max 10 selection으로 source-unit gold 10/10이 가능한지 다시 계산한다.
5. 불가능하면 selection limit 증가, stage 의미 축소, source segmentation 변경 또는 unit 병합 중 어느 것도 자동 선택하지 않고 별도 설계 승인을 받는다.
6. 운영 runtime AnswerCoverage는 Q002 ID/단계명을 참조하지 않는다.
7. 기존 exact citation, number, unit, condition/negation, unsupported action, source order와 branch 검증을 완화하지 않는다.

현재 계산에서는 4번을 충족하지 못하므로 구현 합격 기준에 아직 도달하지 않았다.

## 14. 최종 권고와 승인 대기

최종 권고는 다음 두 단계다.

1. 현재 `ProcedureCoverage`의 23개 unit을 PromptCoverage 전용으로 명확히 고정하고, selection validator에서 ‘23개 모두 선택’ 조건을 제거한다.
2. AnswerCoverage는 D안인 hierarchical group → unit selection으로 별도 구성하되, 운영 구조 gate와 평가 source-unit gold gate를 분리한다.

이 의미 분리는 필요한 수정 방향이지만 Q002를 max 10 source unit으로 완전하게 표현할 수 있다는 뜻은 아니다. 현재 보수적 매핑은 최소 15개 reference가 필요하며 일부 원문은 현재 segmentation에서 selectable 완전 unit이 아니다. 따라서 Source Unit D안 production 구현은 계속 보류한다.

이번 작업에서는 실제 Groq 호출, production 코드 변경, fixture 변경, BM25/embedding/RRF/reranker 변경과 기존 artifacts 덮어쓰기를 수행하지 않았다. 다음 단계는 사용자가 source-unit gold mapping 작성과 10개 제한/segmentation 충돌을 어떻게 다룰지 별도로 승인한 뒤 진행한다.
