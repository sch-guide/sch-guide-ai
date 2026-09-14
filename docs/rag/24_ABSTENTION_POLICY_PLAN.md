# Source Unit Selection Abstention 책임 분리 계획

- 작성일: 2026-09-14
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 Live 결과: `docs/rag/24_SELECTION_CARDINALITY_LIVE_RESULT.md`
- 확인된 상태: `model_declared_abstention`, g1~g5 selected count 모두 0
- 실제 Groq 호출: 0회
- production 코드 수정: 0건
- 최종 권고: **D. 모델은 source-unit selection만 수행하고, answerable은 서버가 결정**

## 1. 결론

현재 `SOURCE_UNIT_SYSTEM`은 evidence가 불충분하면 모델이 `answerable=false`를 반환하도록 하고, 최소 충분 selection이 16개를 넘는다고 판단해도 같은 abstention을 반환하도록 한다. 이 두 조건은 각각 안전 의도가 있지만, 이미 pre/post evidence gate를 통과한 요청에서도 모델이 **입력 근거의 충분성**을 다시 독립적으로 판단하게 만든다.

Q002는 서버의 pre-budget 및 post-budget gate를 모두 통과했고 required gold recall 10/10, selected evidence 12 chunks, required group 5개, adult/pediatric branch, parent atomicity와 source order가 유지됐다. 사람 검수 fixture에서는 14 source units로 required gold 10/10을 표현할 수 있고, 동일 14-ID Mock은 reconstruction과 기존 `validate_answer()`까지 통과했다. 따라서 현재 승인된 Q002 fixture는 **생성 시도를 허용할 수 있는 근거 입력**이며, 유효한 14-ID selection이 반환되면 최종 `answerable=true`가 될 수 있다.

다만 pre/post gate만으로 곧바로 최종 Answer를 확정해서는 안 된다. 최종 answerability에는 모델이 선택한 unit이 AnswerCoverage의 required group·branch·query action·source order를 충족하고 reconstruction 후 citation 및 기존 안전 validator를 통과하는 조건도 필요하다.

권고 D는 이 책임을 다음처럼 분리한다.

- 서버 evidence gate: LLM 호출 가능 여부 결정
- 모델: 질문에 필요한 최소 충분 source-unit subset 선택
- 서버 selection/AnswerCoverage validator: 선택의 완전성·한도·순서 결정
- 서버 reconstruction 및 `validate_answer()`: 최종 grounded Answer 여부 결정

모델이 유효한 selection을 만들지 못하면 서버가 계속 fail closed한다. 모델의 `answerable` boolean을 제거하는 것은 abstention을 금지하거나 답변을 강제하는 것이 아니라, 최종 answerability의 소유권을 서버 검증 경계로 일원화하는 것이다.

## 2. 확정 사실과 추정 경계

### 확정 사실

- Q002 Live는 HTTP 200, `finish_reason=stop`, `response_parse_stage=complete`였다.
- Provider response schema와 server selection parser가 정상 처리됐다.
- `failure_code`와 `validation_reason`은 없었다.
- 모델 응답은 `answerable=false`였고 g1~g5 arrays가 모두 비어 있었다.
- 선택 source unit은 0개이며 required group 충족은 0/5였다.
- Q002 pre/post required gold recall은 각각 10/10이다.
- Q002 gold 10/10은 14 source units로 표현되며 limit 16 안에 있다.
- 같은 14 IDs를 사용한 Mock은 AnswerCoverage, reconstruction, citation과 `validate_answer()`를 통과했다.

### 확정할 수 없는 사실

Raw response/content와 reasoning을 저장하지 않았으므로 모델이 다음 중 무엇을 판단했는지는 확정할 수 없다.

- 최소 충분 subset이 16개를 넘는다고 판단했는지
- evidence 자체가 불충분하다고 판단했는지
- procedure 완전성을 catalog 전체 선택과 동일하게 해석했는지
- 새 최소 선택 instruction을 과도하게 보수적으로 적용했는지

직전 Live의 21-unit 과선택이 prompt 강화 후 0-unit abstention으로 바뀐 현상은 과도한 보수성 가설과 일치하지만, 인과관계로 단정하지 않는다.

## 3. 현재 `SOURCE_UNIT_SYSTEM`의 abstention 조건

현재 instruction에는 다음 세 의미가 함께 존재한다.

1. Evidence가 불충분하면 `answerable=false`와 모든 빈 group array를 반환한다.
2. 최소 충분 selection이 16개를 넘으면 같은 abstention을 반환한다.
3. Required group, explicit branch, complete source units와 missing procedure step을 보수적으로 다룬다.

첫 번째 조건의 “evidence가 불충분”은 모델 관점에서 범위가 정의돼 있지 않다. 모델은 pre/post evidence gate가 이미 통과했다는 사실이나 그 검증 내용을 명시적으로 전달받지 않는다. 따라서 입력 근거의 전역 충분성을 다시 심사할 수 있다.

두 번째 조건은 16개 한도를 넘는 응답을 방지하므로 안전 경계 자체는 필요하다. 문제는 `smallest sufficient subset`, 넓은 procedure 질문, required group/branch와 catalog의 selectable 21개가 함께 제시되는 상황이다. 모델이 “전체 절차를 완전하게 답하려면 selectable unit을 대부분 또는 전부 사용해야 한다”고 해석하면 16개 초과 조건으로 바로 abstain할 수 있다.

따라서 현재 표현은 다음 두 책임을 하나의 boolean에 섞는다.

- Corpus/evidence가 답변 시도에 충분한가
- 모델이 제한 안에서 유효한 subset을 선택할 수 있는가

Q002 결과를 기준으로 보면 첫 책임까지 모델에 다시 맡기는 부분은 필요 이상으로 보수적이다.

## 4. PromptCoverage, AnswerCoverage와 answerability

현재 서버 경계는 세 단계로 나뉜다.

| 단계 | 소유자 | 판단 내용 | 실패 시 |
|---|---|---|---|
| Pre-budget evidence gate | 서버 | 주제·aspect·entity·procedure action, required units/groups/branches, semantic block 완전성 | Groq 호출 0회 |
| Post-budget evidence gate | 서버 | required groups/23 input units/branches 유지, parent atomicity, source order, budget completeness | Groq 호출 0회 |
| AnswerCoverage + reconstruction | 서버 | 선택 group/branch/action/order, source-unit ID 계약, citation/문장/숫자/단위/조건/부정/행동 | Answer 차단 |

`PromptCoverage.required_procedural_unit_keys` 23개는 **입력에 근거가 빠지지 않았는지** 확인하는 값이다. 23개를 최종 답변에서 전부 선택하라는 뜻이 아니다.

`AnswerCoverage`는 다음을 검사한다.

- 모든 required group이 선택에 나타나는가
- required adult/pediatric branch가 나타나는가
- 질문에 명시된 action root가 선택 unit에 나타나는가
- source order가 유지되는가

Q002 질문의 일반 단어 “절차”는 현재 구체 action root가 아니므로 `required_query_actions`는 비어 있다. Production AnswerCoverage는 평가 fixture의 gold 14개를 알지 못한다. 따라서 모델은 여전히 의미적으로 충분한 unit subset을 고르는 역할을 가져야 한다.

정확한 책임 표현은 다음과 같다.

```text
PromptCoverage 통과
  = 답변 생성을 시도할 입력 근거가 구조적으로 충분함

유효한 SourceUnit selection + AnswerCoverage 통과
  = 답변으로 복원할 선택이 구조적으로 충분함

reconstruction + validate_answer() 통과
  = 최종 answerable=true
```

즉 Q002는 현재 fixture와 Mock 증거상 `answerable=true`가 가능한 요청이다. 그러나 서버는 모델 선택 전부터 무조건 true로 확정하는 것이 아니라, **유효한 selection을 받으면 true로 결정할 준비가 된 상태**로 보는 것이 정확하다.

## 5. 모델 answerable 판단과 서버 gate의 중복

현재 provider response의 `answerable`은 다음 서버 판단과 중복된다.

- `answerable=false`로 호출 자체를 막아야 하는 근거 부족은 pre/post gate가 이미 판단한다.
- `answerable=true`로 복원 가능한 선택인지 여부는 selection validator와 AnswerCoverage가 다시 판단한다.
- 복원된 문장이 실제 원문·citation·숫자·단위·행동을 지키는지는 `validate_answer()`가 판단한다.

따라서 모델 boolean이 추가로 제공하는 고유한 안전 보장은 제한적이다. 모델이 false라고 하면 서버의 충분성 판단을 veto하지만, 모델이 true라고 해도 서버 검증을 생략할 수 없다.

반대로 모델의 고유하고 필요한 역할은 **어떤 source units가 질문 답변에 의미적으로 필요한지 선택하는 것**이다. 이 역할은 단순 정규식 gate가 완전히 대체할 수 없다. 그러므로 모델의 의미 판단을 없애는 것이 아니라, 판단 대상을 “전역 answerability”에서 “최소 충분 source-unit subset”으로 좁혀야 한다.

## 6. 대안 비교

| 기준 | A. 현재 prompt 유지 | B. False 조건 제한 | C. Provider answerable=true 고정 | D. Selection만 모델, answerable은 서버 |
|---|---|---|---|---|
| 중복 책임 제거 | 없음 | 일부 | 대부분 | **완전 분리** |
| False abstention 위험 | 높음 | 중간 | 낮음 | 낮음 |
| 근거 부족 fail closed | 유지 | 유지 | 서버 gate/validator 의존 | **서버 gate/validator 유지** |
| 모델의 semantic selection | 유지 | 유지 | 유지 | 유지 |
| 모델에 답변 강제 위험 | 없음 | 낮음 | 상대적으로 높음 | 낮음. 빈 selection은 안전 실패 |
| Schema 복잡도 | 현재와 동일 | 현재와 동일 | boolean 단일값 제약 필요 | 더 단순. boolean 제거 |
| Public Answer API | 유지 | 유지 | 유지 | 유지 |
| Validator 변경 | 없음 | 없음 | 구조 변경 | 구조 변경, 안전 검사 유지 |
| 책임 명확성 | 낮음 | 중간 | 중간 | **가장 높음** |
| 권고 | 기각 | 차선 | 기각 | **권고** |

## 7. A안: 현재 prompt 유지

현재 Live에서 모델은 모든 group을 비운 유효한 abstention을 반환했다. 같은 prompt를 유지하면 동일한 false abstention이 반복될 가능성을 줄일 근거가 없다.

서버 안전성은 높지만, 이미 승인된 evidence가 있어도 모델 veto로 가용성이 떨어진다. 최종 권고로 선택하지 않는다.

## 8. B안: `answerable=false` 조건 제한

모델에게 다음처럼 범위를 좁힐 수 있다.

- 서버가 evidence admission을 완료했으므로 corpus 전체의 충분성을 다시 판정하지 않는다.
- Required group마다 충분한 non-empty subset을 선택한다.
- 16개 안에서 contract-valid selection을 만들 수 없을 때만 false/empty를 반환한다.

현재 schema와 parser를 유지하는 가장 작은 변경이다. 그러나 모델은 여전히 “16개 안에서 충분한가”와 최종 answerable boolean을 동시에 결정한다. 같은 의미 판단이 boolean과 array 선택에 이중으로 표현되므로 불일치 가능성이 남는다.

Minimal fallback으로는 가능하지만 최종 책임 구조로는 D보다 약하다.

## 9. C안: Provider `answerable=true` 고정

Pre/post gate를 통과한 요청에서는 provider schema가 `answerable=true`만 반환하도록 만들 수 있다. `const` 대신 지원되는 단일값 enum을 쓰거나 field를 유지한 채 서버에서 true만 허용하는 방식이 가능하다.

하지만 모델이 제한 안의 충분한 selection을 만들 수 없을 때도 true를 출력하도록 압박한다. 이 경우 invalid ID는 서버가 막지만, 의미적으로 불충분한 최소 group 대표만 선택할 위험은 남는다. Q002의 current AnswerCoverage는 gold 14개를 production에서 요구하지 않기 때문에 “각 group 1개” 같은 과소 선택이 통과할 가능성을 별도로 평가해야 한다.

또한 서버가 결정할 값을 모델이 형식적으로 다시 출력하는 중복은 해소되지 않는다. 권고하지 않는다.

## 10. D안: Selection-only provider contract

### Provider response

모델은 `answerable`을 출력하지 않고 group selection만 반환한다.

```json
{
  "group_selections": {
    "g1": ["su..."],
    "g2": ["su..."],
    "g3": ["su..."],
    "g4": ["su..."],
    "g5": ["su..."]
  }
}
```

모델이 판단하는 것은 다음뿐이다.

- 질문에 필요한 최소 충분 source units
- 각 unit의 올바른 group slot
- source order
- 총 16개 한도

### Server answerability

서버는 다음 순서로 최종 값을 결정한다.

1. Pre/post evidence gate 실패: Groq 호출 없이 `answerable=false`
2. Provider가 모든 arrays를 비움: `selection_empty`로 fail closed, `answerable=false`
3. Unknown/duplicate/non-selectable/wrong-slot/limit/order 오류: 기존 `AI_EVIDENCE`
4. Required group/branch/action 또는 AnswerCoverage 미충족: 기존 `AI_EVIDENCE`
5. 유효한 non-empty selection: 서버가 reconstruction을 수행
6. `validate_answer()` 통과: 서버가 최종 `answerable=true`
7. Citation 또는 안전 validator 실패: `answerable=false`

모든 빈 arrays를 안전 실패로 허용하되, 이를 모델이 내린 최종 임상 answerability 판정으로 취급하지 않는다. Trace에는 `selection_empty`처럼 고정된 운영 사유만 남긴다.

### Prompt 의미

향후 구현 시 system instruction은 다음 책임만 전달한다.

- Server evidence checks have already admitted this catalog for selection.
- Do not re-evaluate corpus-wide answerability.
- Select the smallest source-unit subset sufficient for the full user question.
- Required means a non-empty sufficient subset per required group, not every unit.
- Return all arrays empty only when no contract-valid sufficient selection can be produced within 16.

마지막 empty 경로는 답변을 강제하지 않는 안전 출구다. Server가 empty selection을 자동 보충하지 않는다.

## 11. D안이 validator 완화가 아닌 이유

D안은 기존 안전 검사를 삭제하지 않는다.

- Evidence gate의 topic/aspect/entity/procedure completeness 유지
- PromptCoverage required groups/23 input units/branches 유지
- Selection limit 16 유지
- Unknown, duplicate, non-selectable, wrong-slot 차단 유지
- Required group/branch/action/order 및 AnswerCoverage 유지
- Reconstruction 후 exact source sentence, citation, number, unit, condition/negation, action, source order 검증 유지
- 자동 보충, truncation, ID 제거, dedup 및 재정렬 금지 유지

변경되는 것은 provider boolean의 소유권뿐이다. 모델의 false를 무시하고 강제 답변하는 것이 아니라, empty 또는 invalid selection을 서버가 계속 `answerable=false`로 처리한다.

## 12. 잔여 위험과 제한

### 과소 선택

Q002 production AnswerCoverage는 평가 gold 14개를 직접 요구하지 않는다. D안에서 모델이 required group마다 대표 1개만 선택하면 구조 검사는 통과할 수 있지만 전체 임상 단계가 부족할 수 있다.

이는 `answerable` boolean 유무와 독립적인 semantic selection 품질 문제다. 다음 검증을 유지해야 한다.

- Q002 source-unit gold 10/10 평가
- Required 14-ID fixture의 Mock 완주
- Live에서 selected count뿐 아니라 group별 count와 최종 statement coverage 수동 검수
- 다른 문서 확장 전 질문별 사람이 정의한 gold coverage

Production에 Q002 gold count나 chunk ID를 하드코딩하지 않는다.

### 빈 selection 반복

모델이 D안에서도 모든 arrays를 비울 수 있다. 이 경우 서버는 안전하게 중단하며 자동 재호출하지 않는다. D안은 false abstention 확률을 낮추는 책임 정리이지 모델 성공을 보장하는 장치가 아니다.

## 13. 후속 구현 최소 범위

사용자가 별도로 승인할 경우 예상되는 최소 변경은 다음과 같다.

| 영역 | 예상 변경 |
|---|---|
| Provider response schema | `answerable` 제거, `group_selections`만 유지 |
| `SOURCE_UNIT_SYSTEM` | 서버 admission 완료와 selection-only 책임 명시, corpus sufficiency 재판정 제거 |
| Selection parser | 모델 boolean 대신 non-empty/empty selection 상태를 서버가 파생 |
| Server result | valid selection + 전체 validator 통과 시에만 `answerable=true` |
| Trace | `selection_empty` 고정 사유 추가 |
| Version | Response selection schema 및 `AI_VERSION` 증가 검토 |

`PROMPT_EVIDENCE_SCHEMA_VERSION=4` catalog wire format은 바뀌지 않는다면 유지할 수 있다. Selected evidence, SourceUnit catalog, segmentation, eligibility, PromptCoverage, AnswerCoverage, reconstruction과 public `Answer` JSON은 유지한다.

## 14. Mock 검증 계획

실제 Groq 호출 전에 다음을 검증한다.

### 정상 경로

- Q002 14-ID group selection 통과
- Server-derived `answerable=true`
- Required group 5/5, adult/pediatric branch 충족
- Reconstruction 14 statements
- Exact text/quote/citation 100%
- Source order 역전 0건
- 기존 number/unit/condition/negation/action validator 통과

### Fail-closed 경로

- 모든 group arrays empty: `selection_empty`, Answer 생성 차단
- Required group 또는 branch 누락: 기존 reason 유지
- Unknown, duplicate, non-selectable, wrong-slot: 기존 reason 유지
- 16개 초과: `selection_limit`
- Source order 역전: `selection_source_order`
- Reconstruction 후 citation/안전 검증 실패: 기존 `AI_EVIDENCE`
- Q006: catalog 0건, transport 0회

### 불변 및 budget

- Selected evidence 12 chunks 유지
- Pre/post required gold 10/10 유지
- Selection/statement limit 16 유지
- `OUTPUT_LIMIT=2048`, request budget 5120 유지
- Reservation/headroom 재검증
- 전체 테스트, Ruff와 code review 통과

## 15. 최종 권고 및 승인 대기

최종 권고는 **D. Selection-only provider contract + server-derived answerability**다.

근거 충분성은 pre/post evidence gate가 소유하고, 모델은 의미적으로 필요한 source-unit subset만 고른다. 최종 answerability는 모델이 출력한 boolean이 아니라 유효한 non-empty selection, AnswerCoverage, reconstruction 및 기존 `validate_answer()` 전체 통과 여부로 서버가 결정한다.

모델이 선택하지 못하면 empty selection으로 안전 중단하며, 누락 unit을 자동 보충하거나 validator를 완화하지 않는다. 이 구조가 중복된 abstention 권한을 제거하면서 현재 fail-closed 안전성을 가장 명확하게 유지한다.

이번 단계에서는 실제 Groq 호출과 production 코드 수정을 수행하지 않았다. 다음 구현은 사용자의 별도 승인 후에만 진행한다.
