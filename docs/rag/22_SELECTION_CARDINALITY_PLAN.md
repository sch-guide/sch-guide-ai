# Q002 Source Unit Selection Cardinality 분석 및 개선 계획

- 작성일: 2026-09-14
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 Live 결과: `docs/rag/22_GROQ_SCHEMA_COMPATIBILITY_LIVE_RESULT.md`
- 현재 실패: `AI_EVIDENCE / selection_limit`
- 실제 Groq 호출: 0회
- production 코드 수정: 0건
- 최종 권고: **B 우선 + D의 안전한 최소 metadata 보강**

## 1. 결론

Q002의 현재 SourceUnit catalog에는 selectable unit이 총 21개다. 사람이 검수한 required gold 10 stages는 그중 14개 source unit으로 완전하게 표현된다. Live 모델이 반환한 선택 수도 정확히 21개였으므로 모든 selectable unit을 선택한 해석과 강하게 일치한다.

다만 raw response와 선택 ID 목록을 저장하지 않았고 서버는 21개라는 총량을 확인한 직후 duplicate 검사보다 먼저 `selection_limit`으로 중단했다. 따라서 모델이 실제로 21개 서로 다른 unit 전부를 골랐는지, 일부 ID를 중복해 21개가 됐는지는 확정할 수 없다.

현재 지시는 required group마다 “at least one”을 선택하고 전체 최대 16개라고 명시한다. 그러나 다음 핵심 의미는 직접 정의하지 않는다.

- `selectable=true`는 선택 가능한 후보이지 필수 unit이 아니다.
- `required=true`는 group에서 비어 있지 않은 충분한 부분집합을 요구하며 group 내부 전부를 요구하지 않는다.
- 답을 완성하는 **가장 작은 충분한 부분집합**을 골라야 한다.
- 관련 있어 보이는 보완·중복 unit을 모두 선택하면 안 된다.
- 최소 충분 집합이 16개를 넘는다고 판단하면 21개를 반환하는 대신 abstain해야 한다.

특히 Q002는 “진정간호 절차”라는 넓은 질문이고, Q002의 운영 `AnswerCoverage.required_query_actions`는 비어 있다. Production 코드는 평가 fixture의 14개 gold target을 알지 못하므로 모델이 g5의 12개 후보 중 필수 5개를 구분해야 한다. Group이 required이고 모든 후보가 selectable이라는 표시만 보면 “required group의 관련 unit을 모두 선택”하는 보수적 해석이 가능하다.

따라서 selection limit을 21 이상으로 늘리는 A는 기각한다. 다음 최소 구현은 B로 지시 의미를 명확히 하고, D 중 gold-derived expected/max count가 아닌 request-scoped 일반 policy metadata만 함께 제공하는 것이다. Limit 16과 모든 서버 validator는 그대로 유지한다.

## 2. 분석 자료와 경계

다음 기존 자료만 read-only로 사용했다.

- `artifacts/2026-09-14_rag-branch-aware-selection-mock-02/mock_report.json`
- `artifacts/2026-09-14_rag-groq-schema-compatibility-live/live_report.json`
- `tests/fixtures/q002_gold_stages.json`
- 현재 `mvp/ai.py::SOURCE_UNIT_SYSTEM`
- 현재 compact source-unit catalog v3 serialization
- 현재 `mvp/evidence.py::answer_coverage()`

이번 단계에서는 실제 Groq를 호출하지 않았고 system prompt, catalog, schema, validator와 production 코드를 수정하지 않았다. Gold fixture는 분석에만 사용했으며 production 코드가 읽지 않는다.

## 3. Group별 cardinality 비교

| Group | Branch | Selectable | Gold에 필요한 unit | Mock 선택 | 차이 | Gold stage 범위 |
|---|---|---:|---:|---:|---:|---|
| g1 | common | 1 | 1 | 1 | 0 | common-order |
| g2 | common | 1 | 1 | 1 | 0 | common-pre-assessment |
| g3 | common | 1 | 1 | 1 | 0 | common-explanation-consent |
| g4 | adult | 6 | 6 | 6 | 0 | common-record-scope, adult-before/during/after |
| g5 | pediatric | 12 | 5 | 5 | 7 | pediatric-before/during/after |
| 합계 | common/adult/pediatric | **21** | **14** | **14** | **7** | required gold 10/10 |

g1~g4의 selectable 합계는 9이고 gold도 9개를 모두 요구한다. Cardinality 판단이 필요한 곳은 g5뿐이다. g5는 12개가 selectable이지만 required gold stage를 표현하는 최소 단위는 5개다. 나머지 7개는 완전한 문장이라 선택 자격은 있으나 Q002 required gold를 충족하는 데 모두 필요하지는 않다.

Live의 21개는 catalog 전체 selectable 수와 같다. 이는 select-all 오해의 강한 정황이지만 다음 이유로 확정 원인은 아니다.

1. 선택 ID 목록을 보안 정책상 저장하지 않았다.
2. Current validator는 `selection_limit`을 duplicate 검사보다 먼저 수행한다.
3. 따라서 21개가 모두 고유한지 확인하지 못했다.

## 4. Q002 gold 10/10에 14 units가 충분한 이유

Gold fixture는 임상 단계명을 production 규칙으로 사용하지 않고 평가에서만 source-unit fingerprint로 연결한다.

| Required gold stage | Branch | 필요한 source units |
|---|---|---:|
| QSED 처방 확인 | common | 1 |
| 진정 전 환자 평가와 계획 | common | 1 |
| 설명과 동의서 확인 | common | 1 |
| 진정 전·중·후 기록 범위 | common | 1 |
| 성인 진정 전 확인 | adult | 1 |
| 성인 진정 중 투약·모니터링 | adult | 2 |
| 성인 진정 후 회복·이동 | adult | 2 |
| 소아 진정 전 평가·투약 | pediatric | 2 |
| 소아 진정 중 모니터링 | pediatric | 1 |
| 소아 진정 후 조건별 측정·회복 | pediatric | 2 |
| 합계 | common 4 + adult 5 + pediatric 5 | **14** |

한 stage가 두 개의 독립 action/condition 문장을 필요로 하면 두 source units를 유지한다. 반대로 하나의 완전 unit이 stage를 지지할 때 unit을 쪼개거나 반복하지 않는다. 14는 statement limit 16에 맞춰 역산한 수가 아니라 현재 gold 의미를 exact source units로 표현한 최소치다.

Group topology에서는 common-record-scope unit이 adult branch의 g4 parent group에 포함되므로 group별 합계는 g1 1 + g2 1 + g3 1 + g4 6 + g5 5 = 14다. Stage branch 합계와 group slot 합계가 다르게 보이는 이유는 평가 stage 분류와 원문 parent group 분류의 역할이 다르기 때문이다.

## 5. 현재 selection 지시의 명확성

현재 `SOURCE_UNIT_SYSTEM`에는 다음 지시가 있다.

- 모든 response group slot은 required property다.
- Answerable이면 모든 required group에서 최소 한 ID를 선택한다.
- Optional group은 필요하지 않으면 비운다.
- 전체에서 최대 16 IDs를 선택한다.
- 질문에 필요한 complete source units와 explicit branches를 선택한다.
- Source order와 중복 금지를 지킨다.

이는 group 누락, 전역 한도와 순서에 대해서는 명시적이다. 그러나 cardinality 의미는 **부분적으로만 명확하다**.

### 명확한 부분

- “at least one ID in every required group”은 group별 최소 한 개를 뜻한다.
- “at most 16”은 전역 최대치를 뜻한다.
- Optional group은 필요할 때만 선택한다고 한다.

### 빠진 부분

- `selectable`이 mandatory가 아니라 eligible이라는 정의가 없다.
- `required`가 group-level 조건이며 내부 unit 전체 선택을 뜻하지 않는다는 부정형 설명이 없다.
- “smallest sufficient subset” 또는 동등한 최소 선택 목표가 없다.
- 동일 질문을 지지하지만 중복·보완적인 unit을 제외하라는 기준이 약하다.
- 16개를 넘길 것 같으면 `answerable=false`로 전환하라는 명시적 절차가 없다.
- 반환 직전에 모든 group을 합쳐 ID 수를 다시 세라는 self-check가 없다.

### 오해를 유발할 수 있는 신호

1. 모든 g1~g5 property가 schema에서 required다.
2. Catalog는 각 group에 `required:true`를 표시한다.
3. Unit은 `selectable:true/false`만 가지며 “핵심/보완” 구분은 없다.
4. “Select complete source units needed”는 넓은 procedure 질문에서 포괄적으로 해석될 수 있다.
5. g5 안의 12개가 모두 완전하고 절차 관련성이 있어, 모델 입장에서는 전부가 “needed”로 보일 수 있다.

Strict schema v3는 array 길이를 제한하지 않으므로 “최대 16”은 현재 prompt 의미 제약이다. 모델이 이를 어겨도 서버가 안전하게 차단하지만 답변 가용성은 낮아진다.

## 6. 운영 AnswerCoverage와 gold coverage 차이

Q002 운영 AnswerCoverage는 다음만 요구한다.

- Required group 대표 unit
- Adult/pediatric required branch 대표 unit
- 질문 문자열에 명시된 action root
- Source order

Q002 query의 “절차”는 현재 action root 목록의 구체 동작이 아니므로 `required_query_actions=()`다. 따라서 운영 코드는 14개라는 정답 cardinality나 10개 gold stages를 모른다. 이는 gold를 production에 하드코딩하지 않는 올바른 경계지만, 모델이 최소 충분성을 판단할 지시가 더 중요해진다.

PromptCoverage의 23 required input units 역시 LLM 입력 완전성 지표이지 23개를 답변으로 선택하라는 뜻이 아니다. Catalog의 21 selectable units도 출력 목표가 아니라 안전하게 선택 가능한 후보 집합이다.

```text
Prompt input completeness: required input units 23
Answer candidate inventory: selectable source units 21
Evaluation minimum: required gold를 표현하는 units 14
Provider/server output maximum: selected units 16
```

이 네 수를 같은 의미로 사용하면 select-all 또는 false abstention이 발생한다.

## 7. 대안 비교

| 기준 | A. Limit ≥21 | B. 최소 선택 prompt 강화 | C. Schema/구조별 max | D. Expected/max metadata | E. 계층적 2단계 |
|---|---|---|---|---|---|
| 안전성 | 낮음. 과잉 출력을 허용 | 높음. 서버 limit 유지 | 높음. 구조 강제 가능 시 | 입력 산출 근거에 따라 중간~높음 | 높지만 단계별 오류 가능 |
| 과선택 방지 | 없음 | 중간. 모델 준수 의존 | 높음 | 중간~높음 | 높음 |
| 누락 위험 | 낮지만 불필요 근거 증가 | 중간 | cap 배분이 틀리면 높음 | expected count가 틀리면 높음 | 각 단계에서 누락 가능 |
| Q002 일반화 | 낮음 | 높음 | 중간 | gold 없이 산출 가능할 때만 높음 | 중간~높음 |
| 다른 문서 확장 | 낮음 | 높음 | group 구조별 재설계 가능성 | 의미 oracle 필요 | 호출·상태 복잡도 증가 |
| Groq strict 호환성 | schema 변화 없음 | schema 변화 없음 | `maxItems` 불명확, fixed slot은 비대 | 단순 metadata라 영향 낮음 | 각 call schema는 단순화 가능 |
| 구현 복잡도 | 낮음이나 Answer cap도 충돌 | 낮음 | 중간~높음 | 중간 | 가장 높음 |
| Token 영향 | Output 최대 증가 | Prompt 약 50~100 증가 예상 | Schema 수백~천 token 증가 가능 | Prompt 약 30~100 증가 예상 | Input·latency·비용 대폭 증가 |
| 권고 | 기각 | **1차 권고** | B 실패 시 별도 설계 | 안전한 최소 형태만 B와 병행 | 현재는 기각 |

## 8. 대안별 판단

### A. Selection limit을 21 이상으로 증가

Live 응답을 그대로 수용할 수 있지만 문제를 해결하지 않고 숨긴다. Public Answer statements cap 16과 다시 충돌하며, 21개 전송은 사용자에게 불필요하게 긴 절차를 노출하고 citation/UI 부담을 늘린다. 다음 문서에서는 30개를 선택할 수도 있어 확장성이 없다.

사용자가 limit 증가를 금지했고 안전성·최소 근거 목표에도 맞지 않으므로 기각한다.

### B. Prompt에서 최소 필요 unit 선택 강화

다음 의미를 Q002나 의료 문구 없이 명시한다.

1. `selectable=true` means eligible, not required.
2. `required=true` applies to the group, not every unit inside it.
3. For each required group, choose the smallest non-empty subset sufficient for the question.
4. Never select all selectable units merely because the group is required.
5. Exclude redundant, contextual, or supplemental units that are not needed to answer the question.
6. Count IDs across all groups before returning; the total must be 16 or fewer.
7. If the smallest sufficient selection would exceed 16, return `answerable=false` with every group empty.

장점은 response schema v3와 validator를 바꾸지 않고 오해의 직접 원인을 제거한다는 점이다. 단점은 의미 지시이므로 strict schema처럼 준수를 수학적으로 강제하지 못한다는 점이다.

### C. Group별 max selection을 schema/구조로 제한

`maxItems`는 이전 Live에서 포함됐을 때 HTTP 400이었고 Groq strict 공식 지원이 명확하지 않다. 다시 넣지 않는다.

문서화된 object/enum/anyOf만으로 16개를 구조적으로 제한하려면 nullable fixed selection slots를 여러 개 만드는 방식이 가능하다. 그러나 각 slot마다 request-local ID enum을 반복하면 schema가 크게 증가하고, group별 slot 수를 어떻게 배분할지 별도 정책이 필요하다. Q002에서 g4는 6개가 모두 필요하지만 다른 문서에서는 후반 group에 더 많은 units가 필요할 수 있어 canonical greedy 배분은 안전하지 않다.

따라서 B가 실제 모델에서도 실패할 때 response 구조를 별도 설계하는 fallback으로 둔다. `maxItems` 재도입이나 Q002용 g4=6/g5=5 고정값은 사용하지 않는다.

### D. Group별 expected/max selection metadata

Q002 평가 fixture는 g4=6, g5=5를 알고 있지만 production에 이 수를 넣는 것은 gold hardcoding이다. 현재 AnswerCoverage만으로는 일반 질문의 임상적으로 충분한 per-group max를 계산할 수 없다. 임의 proportional allocation도 필수 후반 단계 누락을 만들 수 있다.

따라서 다음 일반 policy metadata만 B와 함께 허용한다.

```json
{
  "selection_policy": {
    "goal": "smallest_sufficient_subset",
    "maximum_total": 16,
    "selectable_means": "eligible_not_mandatory",
    "required_group_means": "non_empty_sufficient_subset"
  }
}
```

이 metadata는 gold count나 chunk ID를 포함하지 않고 이미 존재하는 계약을 기계적으로 명시한다. Group별 `expected_count`나 `maximum_count`는 일반화 가능한 산출 근거가 생기기 전에는 추가하지 않는다.

### E. 계층적 2단계 selection

현재 response는 이미 group→unit 구조다. 별도 LLM 단계로 required group을 다시 선택해도 서버가 이미 아는 g1~g5 topology를 재판단할 뿐이다. Group별 후속 호출을 하면 호출 수, latency, 비용과 partial failure 지점이 증가한다.

한 번의 응답 안에서 대표 unit과 추가 unit을 나누는 구조도 가능하지만, “추가 unit 몇 개가 필요한가”라는 같은 cardinality 문제가 남는다. B/D 최소 변경이 실패한 뒤에만 재검토한다.

## 9. 최종 권고안

다음 구현 승인이 있을 때 **B + D-min**을 한 변경 단위로 적용한다.

### System instruction

- `selectable`과 `required group`의 의미를 명시적으로 분리한다.
- Smallest sufficient subset을 최우선 목표로 둔다.
- Select-all 금지와 전체 count self-check를 명시한다.
- 16개를 넘는다면 초과 응답 대신 fail-closed abstention을 요구한다.

### Compact catalog

- 원문, group, SourceUnit과 selected evidence는 바꾸지 않는다.
- Gold-derived per-group count는 추가하지 않는다.
- Catalog root에 위의 일반 `selection_policy`만 추가한다.
- Wire format이 바뀌므로 구현 시 `PROMPT_EVIDENCE_SCHEMA_VERSION`을 3→4로 검토한다.

### Provider와 server

- Provider-compatible response schema v3는 유지한다.
- Selection limit과 Answer statements cap은 16으로 유지한다.
- `validate_source_unit_selection()`과 `validate_answer()`는 변경하지 않는다.
- Automatic truncation, dedup, order correction과 missing unit 보충은 하지 않는다.
- Prompt 의미가 바뀌므로 generation cache 분리를 위해 `AI_VERSION` 증가를 검토한다.

## 10. 구현 전후 검증 계획

### Static/Mock

- System instruction에 eligible-not-mandatory, group-level required, smallest sufficient subset, select-all 금지와 count self-check가 모두 존재한다.
- Catalog v4 policy는 Q002 ID, gold stage 및 per-group gold count를 포함하지 않는다.
- Selected evidence 12 chunks와 exact source-unit text는 v3와 동일하다.
- Q002 gold 14 IDs Mock은 통과한다.
- 21 IDs Mock은 기존 `selection_limit`으로 차단된다.
- Required group/branch 누락, duplicate와 order 오류는 기존 reason으로 차단된다.
- Reconstruction과 citation coverage 100%를 유지한다.
- Q006 catalog/transport 0건/0회를 유지한다.
- Reservation ≤5120과 required headroom을 다시 측정한다.
- 전체 테스트, Ruff와 code review를 통과한다.

Mock은 prompt 문구와 서버 계약만 검증하며 실제 모델이 최소 subset을 따르는지는 증명하지 않는다.

### 별도 승인 후 단일 Live 합격 기준

- Q006 실제 호출 0회
- Q002 실제 호출 정확히 1회
- HTTP 200, `finish_reason=stop`
- Selected source units 1~16
- Required group 5/5와 adult/pediatric branch 충족
- Selection/source order 검증 통과
- Reconstruction과 기존 `validate_answer()` 통과
- Citation coverage 100%
- Unsupported number/unit/condition/negation/action 0건

Live가 다시 `selection_limit`이면 limit을 늘리거나 결과를 자동 축소하지 않는다. 선택 ID 원문을 저장하지 않는 범위에서 group별 returned count를 안전 metadata로 수집하는 별도 진단 설계를 먼저 승인받는다.

## 11. 중단 조건

후속 구현 중 다음이 확인되면 실제 Groq 호출 전에 멈춘다.

- Gold 또는 Q002-specific count 없이는 policy metadata를 만들 수 없음
- Prompt 변경이 selected evidence나 validator 의미 변경을 요구함
- Catalog metadata로 reservation/headroom 기준을 충족하지 못함
- 14-ID gold Mock이 실패함
- 기존 branch/citation/order validator 완화가 필요함

## 12. 비변경 확인 및 승인 대기

이번 분석에서 다음을 수행하지 않았다.

- 실제 Groq 호출
- Selection/statement limit 증가
- System prompt 또는 production 코드 수정
- Q002 gold fixture 수정
- Selected evidence 12 chunks 변경
- BM25, embedding, RRF와 reranker 변경
- SourceUnit segmentation/eligibility 변경
- PromptCoverage/AnswerCoverage 또는 validator 변경
- Automatic truncation, 선택 축소, 보충, dedup 또는 재정렬
- 기존 artifacts 덮어쓰기

권고는 B를 우선 적용하고 D의 일반 policy metadata만 보조하는 것이다. 정확한 per-group expected/max count, fixed-slot response schema와 다단계 LLM selection은 현재 근거로 구현하지 않는다. 사용자 승인 전에는 구현이나 Live 재평가로 진행하지 않는다.
