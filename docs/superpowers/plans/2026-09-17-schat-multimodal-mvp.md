# SCHAT Multimodal MVP Text/Table 실행 계획

## Phase 01 — Text style routing

1. presentation sidecar의 intent/format routing red tests를 추가한다.
2. 원순서 유지, clinical text 비포함, 일반/branch/phase heading 규칙을 구현한다.
3. AI attachment 경계에서 `plan.kind`를 전달한다.
4. UI renderer를 display rows 기반으로 전환한다.
5. 집중 테스트와 기존 presentation/Facet-slot/Q006 회귀를 실행한다.
6. progress checkpoint를 작성한다.

## Phase 02 — Controlled paraphrasing preparation

1. synthetic evidence 기반 schema/validator red tests를 추가한다.
2. provider-neutral contract/schema/prompt builder를 구현한다.
3. number/unit/time, condition/negation, branch/phase, evidence ID validator를 구현한다.
4. 실제 provider path에 연결되지 않았음을 테스트한다.
5. progress checkpoint를 작성한다.

## Phase 03 — Structured table track

1. synthetic table과 실제 catalog/PDF structural red tests를 추가한다.
2. deterministic table/header/row/cell record와 safe manifest를 구현한다.
3. table citation과 exact-cell Markdown renderer를 구현한다.
4. evaluation-only table lookup과 local Streamlit UI를 구현한다.
5. approved table cases로 offline 평가하고 raw text가 artifact에 없는지 검사한다.
6. progress checkpoint를 작성한다.

## Phase 04 — Evidence-type routing

1. generic text/table/mixed/image routing red tests를 추가한다.
2. QueryPlan trace metadata를 구현하되 retrieval에는 연결하지 않는다.
3. TF027/image route가 pending이고 aggregate에서 제외되는지 검증한다.
4. progress checkpoint를 작성한다.

## Phase 05 — 통합 검증 및 결과

1. Text/table/citation/negative UAT를 실행한다.
2. Q001~Q006, 수혈 retrieval/table fixtures, Q006 zero-call을 회귀 검증한다.
3. 전체 pytest, Ruff, `git diff --check`를 실행한다.
4. repository code review와 artifact raw-text/secret 감사를 수행한다.
5. `docs/rag/50_SCHAT_MULTIMODAL_MVP_RESULT.md`와 raw-free artifacts를 생성한다.
6. 최종 progress checkpoint를 작성하고 commit 권고만 보고한다.

## 공통 중단 조건

- text/table gold 신뢰성 실패
- catalog/chunk identity 무결성 실패
- 기존 validator 완화 필요
- dependency 충돌
- Git 데이터 손상 위험
- 병원 원문 또는 민감정보 artifact 유출 위험

Provider 미승인과 image-dependent gold 부족은 해당 track만 pending으로 유지한다.
