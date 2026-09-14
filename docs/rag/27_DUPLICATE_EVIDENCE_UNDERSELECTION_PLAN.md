# Q002 `duplicate_evidence`와 5-unit under-selection 분석 및 개선 계획

- 작성일: 2026-09-14
- 기준 결과: `docs/rag/26_RAG_PILOT_LIVE_RESULT.md`
- 분석 범위: 현재 코드, Q002 평가 fixture, Mock 및 Live의 비식별 집계값
- 실제 Groq 호출: **0회**
- production 코드 수정: **0건**
- validator 변경: **0건**
- 상태: **분석 및 계획 완료 · 사용자 승인 대기**

## 1. 결론

`citation_assessment=duplicate_evidence`와 5-unit under-selection은 같은 판정이 아니다.

- `duplicate_evidence`는 현재 코드에서 **같은 문서와 같은 재계산 branch에 속한 cited hit 둘 이상의 정규화된 인용 텍스트가 완전히 같은 경우** 발생한다.
- 이 reason은 source unit ID 중복, 임상 단계 중복 또는 의미 중복을 직접 뜻하지 않는다.
- Q002 Live의 5-unit 선택은 기존 `AnswerCoverage`를 통과할 수 있다. 현재 검사는 required group, adult/pediatric branch, 질문 문자열에 직접 등장하는 action root와 source order만 확인한다.
- `진정간호 절차는?`에서 `절차`는 `_QUERY_ACTION_ROOTS`에 없으므로 `required_query_actions=()`다. 다섯 required group에서 각각 한 unit만 선택해도 group 5/5와 branch 조건을 만족하면 `AnswerCoverage.complete=true`가 될 수 있다.
- Mock gold는 같은 다섯 group에서 14 units를 요구한다. Live count 5는 g4와 g5에서 각각 1개만 선택했으므로, gold의 g4 6개와 g5 5개 구조를 count만으로도 충족할 수 없다. 다만 raw response와 Live ID가 없으므로 어떤 단계와 문장이 선택·누락됐는지는 판정하지 않는다.

최종 권고는 **B와 C를 결합**하는 것이다. Broad procedure 질문에서는 서버가 Q002 gold나 chunk ID를 사용하지 않고 문서의 명시적 구조에서 phase·action·branch·group coverage requirement를 계산한다. Provider 선택 후 서버가 이를 fail closed로 검사한다. 기존 validator는 그대로 유지하며 부족한 ID를 자동 보충하거나 dedup·재정렬하지 않는다.

## 2. 확정된 Live 사실과 분석 한계

이번 분석은 다음 확정값만 사용한다.

- HTTP 200
- `finish_reason=stop`
- response parsing 성공
- group slot 5개
- selected source units 5개
- group별 count: g1=1, g2=1, g3=1, g4=1, g5=1
- required group 5/5 충족
- adult/pediatric branch 충족
- post-reconstruction `citation_assessment=duplicate_evidence`
- 최종 `answerable=false`

사용하지 않은 정보:

- 저장되지 않은 raw response
- 실제 Live source unit ID
- 실제 Live statement text
- 실제 Live에서 중복된 두 citation의 내용

따라서 “어떤 두 unit이 중복이었다”거나 “어떤 임상 단계가 빠졌다”는 결론을 내리지 않는다.

## 3. `duplicate_evidence` 정확한 코드 경로

현재 경로는 다음과 같다.

```text
Groq response content
  → validate_source_unit_selection()
  → reconstruct_source_unit_answer()
  → validate_answer()
  → cited chunk ID 집합 생성
  → chunk별 quote를 합쳐 cited_hits 생성
  → assess_evidence(plan, cited_hits)
  → evidence_groups()
  → procedure_coverage()
  → duplicate_count 계산
  → duplicate_count > 0이면 duplicate_evidence
  → generate()가 citation:duplicate_evidence로 answerable=false 반환
```

관련 위치:

- `mvp/ai.py:1008-1013`: selection 검증, 서버 reconstruction, 기존 `validate_answer()` 실행
- `mvp/ai.py:1024-1043`: 인용된 chunk만 남기고 같은 chunk의 quote를 줄바꿈으로 합쳐 `cited_hits` 생성
- `mvp/ai.py:1046-1050`: `assess_evidence(plan, cited_hits)` 재실행 후 불충분하면 안전 차단
- `mvp/evidence.py:114-179`: evidence group과 `PromptCoverage` 생성
- `mvp/evidence.py:267-297`: procedure evidence 재평가와 `duplicate_evidence` 반환

`procedure_coverage()`는 relevant hit마다 다음 signature를 만든다.

```python
(document_id, branch, clean(chunk.text).casefold())
```

그리고 다음과 같이 계산한다.

```python
duplicate_count = len(signatures) - len(set(signatures))
```

`clean()`은 NFC 정규화 후 연속 공백과 줄바꿈을 한 칸으로 합치고 앞뒤 공백을 제거한다. `casefold()`는 영문 대소문자 차이를 없앤다. 문장 의미, action, phase와 group key는 signature에 들어가지 않는다.

Post-reconstruction에서는 `chunk.text`가 원래 전체 chunk가 아니라 해당 chunk에서 인용된 quote들의 결합값으로 바뀐다. 따라서 이 단계의 `duplicate_evidence`는 원본 chunk 전체 중복이 아니라 **인용된 텍스트 묶음의 exact normalized duplicate**를 판정한다.

## 4. reason이 의미할 수 있는 것과 의미하지 않는 것

| 후보 해석 | 현재 `duplicate_evidence` 의미 | 이유 |
|---|---|---|
| 동일 source unit ID 반복 | 직접 의미하지 않음 | 같은 ID 반복은 앞 단계에서 `selection_duplicate_id`로 차단된다. |
| 동일 source unit text가 서로 다른 ID·chunk에 반복 | 가능 | 같은 문서·같은 재계산 branch이고 정규화된 인용 텍스트가 같으면 발생한다. |
| 동일 문장을 같은 chunk에서 여러 statement가 반복 citation | 그 자체만으로는 보장되지 않음 | `cited_hits`는 chunk당 한 행이고 quote들이 한 텍스트로 합쳐진다. 한 chunk 내부 반복만으로 signature 행 수가 늘지 않는다. |
| 동일 문장이 서로 다른 chunk에서 citation | 가능 | document와 재계산 branch가 같고 텍스트가 동일하면 duplicate다. |
| 같은 임상 단계만 반복 선택 | 직접 의미하지 않음 | 단계 ID나 phase가 signature에 없다. 표현이 다르면 검출하지 않는다. |
| 서로 다른 group이 동일 의미를 지지 | 의미만 같으면 검출하지 않음 | group key와 semantic similarity를 비교하지 않는다. exact normalized text가 같을 때만 가능하다. |
| 서로 다른 group에 동일 문장이 복제됨 | 가능 | group key는 signature에 없으므로 같은 document·branch·text면 duplicate다. |
| 같은 문장이 서로 다른 문서에 존재 | 발생하지 않음 | `document_id`가 signature에 포함된다. |
| 같은 문장이 명확히 다른 branch에 존재 | 발생하지 않음 | 재계산된 branch가 다르면 signature가 다르다. |

주의할 경계가 있다. Citation 재검사 시 branch는 SourceUnit의 서버 metadata를 그대로 쓰지 않고 축약된 quote text에 대해 `_branch()`로 다시 계산한다. Adult/pediatric heading이 quote에 포함되지 않으면 원래 branch unit이 `common`으로 재분류될 수 있다. 이 경우 서로 다른 원래 branch의 동일 문장이 재검사에서는 같은 branch signature가 될 가능성이 있다.

Live ID와 raw content가 없으므로 이번 실패가 실제 중복 원문, overlap 복제, 동일 문장 재인용 또는 branch 문맥 손실 중 어느 경우인지는 결정할 수 없다. 기존 duplicate 차단을 완화하거나 제거할 근거도 없다.

## 5. `duplicate_evidence`와 under-selection의 관계

두 현상은 코드상 독립적이다.

1. Under-selection은 `validate_source_unit_selection()`의 현재 `AnswerCoverage`가 phase와 action diversity를 요구하지 않아 통과할 수 있다.
2. `duplicate_evidence`는 그 뒤 reconstruction과 exact citation 검증을 통과한 citation 집합을 `assess_evidence()`가 다시 검사할 때 발생한다.
3. 따라서 `duplicate_evidence`가 under-selection을 증명하지 않고, under-selection이 반드시 duplicate를 만들지도 않는다.

다만 다음 관계는 가능한 가설이다.

- 모델이 각 group에서 한 개의 대표 문장만 고르면 adult/pediatric 절차에서 공통적으로 반복되는 일반 문장을 선택할 수 있다.
- 이 선택은 SourceUnit metadata 기준 group/branch 검사를 통과할 수 있다.
- Citation 재검사에서 branch heading 문맥이 사라지고 두 quote가 동일한 `common` text로 보이면 `duplicate_evidence`가 될 수 있다.

이는 가능한 경로일 뿐 이번 Live의 원인으로 확정하지 않는다.

## 6. Mock gold 14 IDs와 Live 5 IDs의 구조 비교

Live는 ID가 없으므로 group count만 비교한다.

| Group | Branch | 선택 가능 units | Mock gold units | Live count | 구조적 판정 |
|---|---|---:|---:|---:|---|
| g1 | common | 1 | 1 | 1 | count 차이 없음 |
| g2 | common | 1 | 1 | 1 | count 차이 없음 |
| g3 | common | 1 | 1 | 1 | count 차이 없음 |
| g4 | adult | 6 | 6 | 1 | gold 대비 최소 5 units 부족 |
| g5 | pediatric | 12 | 5 | 1 | gold 대비 최소 4 units 부족 |
| 합계 | common/adult/pediatric | 21 | 14 | 5 | gold 대비 9 units 부족 |

Q002 평가 fixture의 required stage 구조:

- common: 처방 확인, 사전 평가·계획, 설명·동의, 전·중·후 기록 범위
- adult: 진정 전, 진정 중, 진정 후
- pediatric: 진정 전, 진정 중, 진정 후

총 10 required stages가 14 source units의 `all_of` 조건으로 표현된다. Adult during/after, pediatric before/after처럼 한 단계에 서로 다른 action을 담은 두 unit이 필요한 경우가 있다.

Group count만으로 실제 Live gold recall을 계산할 수는 없다. 그러나 g4의 gold 6 대 Live 1, g5의 gold 5 대 Live 1이므로 10/10 완전성은 불가능하다. 어떤 stage가 누락됐는지는 알 수 없다.

## 7. Required group에서 최소 1개가 임상적 완전성을 보장하지 못하는 이유

현재 group은 임상 단계 하나가 아니라 같은 parent block에 속한 여러 chunk와 source unit의 묶음이다.

- g4는 adult branch이지만 common 기록 범위와 성인 진정 전·중·후 내용을 함께 포함한다.
- g5는 pediatric branch의 진정 전·중·후와 추가 문맥을 함께 포함한다.
- 한 unit 선택은 해당 group과 branch가 한 번 등장했다는 사실만 증명한다.
- 한 unit으로 투약, 측정, 모니터링, 회복, 이동처럼 서로 다른 action을 모두 충족했다고 볼 수 없다.
- 한 unit으로 진정 전·중·후 phase 전체를 충족했다고 볼 수 없다.

따라서 group non-empty는 topology coverage에는 적합하지만 broad procedure의 임상적 완전성 기준으로는 부족하다.

## 8. 현재 `AnswerCoverage`가 under-selection을 통과시키는지

확인 결과 **통과시킬 수 있다**.

현재 `answer_coverage()`가 검사하는 것은 다음 네 가지다.

1. `required_group_keys`가 selected group에 모두 포함됐는가
2. `required_branches`가 selected branch에 모두 포함됐는가
3. 질문 문자열에 직접 포함된 `_QUERY_ACTION_ROOTS`가 selected text에 있는가
4. selected unit의 source order가 유지됐는가

Q002 질문은 `진정간호 절차는?`다. `절차`는 action root 목록에 없으므로 required action은 비어 있다. Live 집계처럼 다섯 group에서 각각 한 unit을 source order로 선택하면 group과 adult/pediatric branch를 모두 충족할 수 있고, 빈 action requirement도 자동 충족한다.

현재 `AnswerCoverage`에는 다음 항목이 없다.

- broad procedure 여부
- 진정 전·중·후 같은 phase requirement
- branch별 phase requirement
- group 내부 action diversity
- phase별 action coverage
- selected unit이 여러 임상 단계를 실제로 대표하는지

## 9. PromptCoverage와 AnswerCoverage의 역할 구분

### PromptCoverage

LLM에 전달하기 전 입력 근거가 손실되지 않았는지 확인한다.

- required/optional evidence group
- required procedural input unit
- adult/pediatric branch
- parent block 완전성
- source order
- exact duplicate hit

Q002는 pre/post budget required gold 10/10이고 required input unit 23개를 유지했다. 이는 필요한 원문이 prompt 후보 안에 있었다는 뜻이다. 23개를 답변으로 모두 선택하라는 뜻은 아니다.

### 현재 AnswerCoverage

Provider가 선택한 출력 source unit이 최소 topology 조건을 만족하는지 확인한다.

- group 대표
- branch 대표
- 질문에 명시된 action root
- source order

이 수준은 좁은 사실 질문에는 충분할 수 있지만 broad procedure 질문에는 부족하다.

### 새로 필요한 중간 계약

`PromptCoverage`를 출력 기준으로 재사용하지 않고 별도의 서버 소유 계약을 둔다.

```text
PromptCoverage
  입력에 필요한 근거가 존재하고 budget 뒤에도 유지됐는지 검사

ProcedureAnswerRequirement (신규 제안)
  broad procedure 답변이 반드시 포함해야 할 일반화된 coverage facet 정의

AnswerCoverage
  모델이 선택한 source units가 위 requirement를 충족하는지 검사
```

## 10. B/C 중심 설계안

### 10.1 Broad procedure 판정

다음 조건을 모두 만족할 때 broad procedure로 본다.

- `plan.kind == 'procedure'`
- 현재 질문에 `절차`, `순서`, `방법`, `어떻게` 같은 기존 procedure trigger가 있음
- 용량·주의사항·준비물처럼 하나의 좁은 aspect만 요청하는 질문이 아님

Q002 문구를 별도로 검사하지 않는다.

### 10.2 일반화된 coverage facet

서버는 선택된 evidence와 SourceUnit catalog에서만 다음 facet을 계산한다.

- `group_key`
- `branch`: common/adult/pediatric/기존 명시 branch
- `phase`: before/during/after/unspecified
- `action_family`: 확인, 평가, 설명, 동의, 기록, 투여, 측정, 모니터링, 이동 등 원문에 실제 존재하는 일반 action root
- `source_order`

Phase는 임의의 `전`, `중`, `후` 한 글자만 보고 판정하지 않는다. 같은 group 안의 명시적 heading, branch heading, 구조 경계와 procedure subject가 결합된 경우에만 상속한다. 명확하지 않으면 `unspecified`로 두고 추측하지 않는다.

Action은 SourceUnit exact text에 실제 존재하는 일반 action root만 사용한다. 새로운 의료 지식이나 Q002 stage명을 만들지 않는다.

### 10.3 `ProcedureAnswerRequirement`

제안 데이터 구조의 의미:

```text
broad_procedure
required_group_keys
required_branches
required_phase_slots: (group 또는 branch, phase)
required_action_slots: (group 또는 branch, phase, action_family)
source_order_required
capacity_valid
```

핵심은 “몇 개를 선택하라”가 아니라 “어떤 일반 의미 facet을 빠뜨리지 말라”다. 한 source unit이 여러 action을 원문에 명시하면 여러 facet을 충족할 수 있다. 동일 action을 반복한 여러 unit은 action diversity를 늘리지 않는다.

Requirement는 production gold, stage label, chunk ID와 expected count를 읽지 않는다. 현재 prompt에 포함된 명시적 문서 구조에서만 계산한다.

### 10.4 새 AnswerCoverage 검사 순서

기존 검사 뒤에 다음을 추가하는 방향으로 설계한다.

1. required group coverage
2. required branch coverage
3. broad procedure의 required phase coverage
4. phase별 required action-family coverage
5. action diversity
6. 기존 query action coverage
7. 기존 source order
8. 기존 reconstruction, exact citation과 `duplicate_evidence` 재검사

새 고정 reason 후보:

- `selection_missing_phase`
- `selection_missing_phase_action`
- `selection_insufficient_action_diversity`
- `selection_requirement_capacity`

Reason을 추가해도 기존 `selection_branch`, `selection_missing_group`, `selection_missing_action`, `selection_source_order`, `duplicate_evidence`는 삭제하거나 의미를 완화하지 않는다.

### 10.5 Selection 한도와 불명확한 구조

- 계산된 필수 facet을 16 units 이내에서 만족할 수 없으면 limit을 늘리지 않는다.
- Groq 호출 전에 구조적으로 불가능함을 확인하면 fail closed한다.
- Phase/action 구조가 불명확하면 임의 requirement를 만들지 않는다.
- Broad procedure인데 명시 구조가 불명확해 완전성을 증명할 수 없으면 좁은 질문을 요구하거나 안전하게 abstain한다.

## 11. 대안 비교

| 대안 | 장점 | 위험 | 판정 |
|---|---|---|---|
| A. Group별 최소 1개 유지 | 구현이 단순하고 짧은 답변 가능 | g4/g5처럼 한 group에 여러 phase/action이 있을 때 under-selection 통과 | **기각** |
| B. Broad procedure에서 group 내부 distinct phase/action 요구 | 실제 절차 구조를 직접 검증하고 count가 아닌 의미를 검사 | phase/action 추출 정확도와 문서 구조 품질에 의존 | **권고** |
| C. Gold 없이 서버가 일반화 가능한 coverage 계산 | Q002 하드코딩 없이 다른 지침에 적용 가능, provider를 신뢰하지 않고 fail closed | 일반 규칙이 supplemental 내용을 과도하게 요구할 수 있어 사전 평가 필요 | **권고** |
| D. Provider에 group별 recommended count 제공 | 모델의 1개씩 선택을 줄일 가능성 | 개수는 필요한 phase/action을 설명하지 못하며 일반 expected count의 근거가 없음 | **주요 해법으로 기각·보류** |
| E. 모델 선택 뒤 서버 자동 보충 | 답변 성공률을 높일 수 있음 | 모델 선택을 서버가 임의 변경하고 누락 판단 오류를 숨김 | **안전상 기각** |

D 대신 provider prompt/catalog에는 server-derived **required phase/action facet**을 명시하는 방안을 검토한다. 이는 추천 개수가 아니라 서버가 실제 검증할 계약이다. Provider가 이를 지키지 못하면 서버가 차단하며 자동 보충하지 않는다.

## 12. Broad procedure 일반화 가능성

### Group coverage

이미 구현되어 있고 유지한다. Parent block의 존재 여부만 보장한다.

### Branch coverage

이미 구현되어 있고 유지한다. Adult/pediatric 등 명시 branch마다 적어도 필요한 selection이 있는지 검사한다.

### Phase coverage

명시적 구조가 있는 문서에서는 일반화 가능하다.

- `진정 전`, `시술 전`, `투여 전`
- `진정 중`, `시술 중`, `투여 중`
- `진정 후`, `시술 후`, `투여 후`

단순히 문장 안의 모든 `전/중/후`를 phase로 보지 않고 heading과 group 구조에서 subject가 확인될 때만 적용한다.

### Action diversity

일반 action root로 제한하면 일반화 가능하다. 같은 phase에서 서로 다른 action family가 근거에 명시돼 있다면 선택 결과도 필요한 family를 포함해야 한다. 의미가 비슷한 문장을 임베딩으로 합치거나 LLM으로 분류하지 않는다.

### 검증 행렬

Broad procedure의 출력 완전성은 다음 조합으로 검사한다.

```text
(group coverage)
+ (branch coverage)
+ (branch/group × explicit phase coverage)
+ (branch/group × phase × required action-family coverage)
+ source order
```

이 구조는 진정간호 전용 stage명을 사용하지 않는다.

## 13. 구현 전 분석 및 테스트 계획

사용자가 별도로 구현을 승인한 뒤에도 바로 production을 바꾸지 않고 다음 순서로 진행한다.

### 단계 1. Offline facet 진단

- Q002 catalog의 모든 selectable unit에 일반 규칙으로 phase/action facet을 부여한다.
- Gold는 production 입력이 아니라 평가 정답으로만 사용한다.
- Gold 14 units가 모든 requirement를 충족하는지 확인한다.
- g1~g5에서 각 1개를 선택하는 가능한 count 패턴을 Mock으로 열거한다. Q002에서는 g1~g3가 각 1후보, g4 6후보, g5 12후보이므로 72개 조합을 검사할 수 있다.
- 실제 Live ID를 추정하지 않고 모든 1-per-group 조합이 새 requirement에서 차단되는지 확인한다.
- 일반 규칙이 14 gold를 차단하거나 supplemental 21개 전부를 요구하면 production 구현 전에 멈춘다.

### 단계 2. Duplicate 의미 계약 테스트

다음을 별도 fixture로 고정한다.

- 동일 source unit ID 반복은 `selection_duplicate_id`
- 같은 chunk에서 동일 quote 반복이 `duplicate_evidence`를 만드는지 여부
- 서로 다른 chunk의 동일 text·동일 document·동일 branch는 `duplicate_evidence`
- 동일 text라도 document가 다르면 중복이 아님
- 동일 text라도 명확한 branch가 다르면 중복이 아님
- 의미만 같고 text가 다르면 중복이 아님
- 서로 다른 group의 exact duplicate 처리
- Citation 축약 뒤 branch marker가 사라지는 경우

현재 동작을 먼저 테스트로 설명한 뒤에만 metadata 보존 개선을 별도 승인 대상으로 제시한다. Duplicate validator는 제거하거나 완화하지 않는다.

### 단계 3. B/C 구현

승인될 경우 예상 수정 범위:

- `mvp/evidence.py`: general phase/action facet과 `ProcedureAnswerRequirement`, 강화된 `AnswerCoverage`
- `mvp/ai.py`: selection contract에 서버 requirement 연결, prompt에 count가 아닌 facet metadata 제공, 새 fail-closed reason 전달
- `tests/test_rag_evidence_groups.py`: duplicate signature 의미와 branch 문맥 테스트
- `tests/test_source_unit_selection.py`: Q002 14-unit 통과, 72개 1-per-group under-selection 차단, 기존 negative contract 유지
- 평가 도구와 결과 문서: raw response나 Live ID 없이 coverage count/reason만 저장

다음은 변경하지 않는다.

- BM25, embedding, RRF와 reranker
- selected evidence 12 chunks
- Q002 gold의 production 접근 금지
- SourceUnit ID를 모델 대신 서버가 선택하는 동작
- selection/statement 최대 16
- 기존 citation, number, unit, condition, negation, action, order validator
- 자동 보충, 자동 dedup, 자동 재정렬, truncation, retry와 fallback

### 단계 4. Mock 및 전체 회귀

합격 조건:

- Q002 gold 14 units가 required phase/action/group/branch를 모두 충족
- 모든 1-per-group 조합이 구체적 missing phase/action reason으로 차단
- 21-unit 과선택은 기존 `selection_limit`
- duplicate cases는 기존 exact duplicate 의미로 fail closed
- citation coverage 100%
- Q006 catalog/transport 0건/0회
- request budget 5120과 output 2048 유지
- 전체 테스트, Ruff와 `git diff --check` 통과

Mock 성공 뒤에도 실제 Groq는 별도 사용자 승인 전까지 호출하지 않는다.

## 14. 위험과 중단 조건

다음 중 하나가 확인되면 구현 또는 Live 진행을 멈춘다.

- 일반 규칙이 Q002 gold 14 units를 통과시키지 못함
- Gold나 Q002 stage/chunk ID 없이는 required facet을 만들 수 없음
- 문서 구조만으로 core와 supplemental action을 구분할 수 없어 21개 전체를 요구함
- Required facet이 selection 한도 16을 초과함
- 기존 validator 완화나 자동 ID 보충이 필요함
- Prompt budget 5120을 만족하지 못함
- 현재 리베이스 충돌이 안전하게 정리되지 않음

## 15. 최종 권고

1. **A는 기각한다.** Required group의 non-empty 여부는 유지하되 broad procedure 완전성 판정으로 사용하지 않는다.
2. **B와 C를 결합한다.** 서버가 명시적 문서 구조에서 group·branch·phase·action requirement를 계산하고 selection을 fail closed로 검증한다.
3. **D의 recommended count는 사용하지 않는다.** 필요하면 서버가 검증할 phase/action facet을 provider에 제공한다.
4. **E는 기각한다.** 부족한 unit을 보충·dedup·재정렬하지 않는다.
5. `duplicate_evidence`는 exact normalized citation duplication 검사로 유지한다. 이번 Live만으로 원인을 특정하거나 validator를 완화하지 않는다.
6. 먼저 offline facet 진단과 duplicate 의미 테스트를 설계·실행하고, 성공한 경우에만 별도 승인 후 production 구현을 검토한다.

## 16. 이번 작업의 비변경 확인

- 실제 Groq 호출: **0회**
- production 코드 수정: **0건**
- 테스트 실행: **0회**
- validator 수정·완화: **0건**
- 자동 보충·dedup·재정렬: **0건**
- Live ID 또는 raw response 추정: **0건**
- 기존 artifacts 변경: **0건**
- 현재 Git 리베이스 및 충돌 해결: **수행하지 않음**

이 문서 작성 후 구현, 테스트와 실제 호출로 진행하지 않고 사용자 승인을 기다린다.