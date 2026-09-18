# SCHAT v1 RAGAS·Gold·UAT Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 명시적 단일 지침 문맥을 안전하게 domain/topic admission에 결합하고, 고정 Retrieval·RAGAS·Gold·UAT 기준으로 Before/After를 검증한다.

**Architecture:** QueryPlan은 질문 domain과 사용자 선택 문서 context를 분리한다. Evidence layer가 실제 scoped hits를 확인한 뒤에만 hospital admission을 허용한다. 별도 evaluation 도구가 기존 90문항 retrieval/RAGAS와 36문항 UAT/Gold/Table/TF027 상태를 raw-free artifact로 통합한다.

**Tech Stack:** Python 3.12, dataclasses, pytest, Streamlit, SQLite catalog, 기존 FastEmbed/BM25/RRF/reranker 및 table evaluation 도구

**Spec:** `docs/superpowers/specs/2026-09-18-schat-v1-ragas-gold-uat-design.md`

## Global Constraints

- `v0.9` tag와 production retrieval 알고리즘·임계값을 변경하지 않는다.
- provider/vision/LLM-judge 실제 호출과 병원 데이터 외부 전송은 0회다.
- fixture 질문과 기존 Gold ID를 production 코드에 넣지 않는다.
- 모든 production behavior는 실패 테스트를 먼저 확인한 뒤 최소 구현한다.
- 사용자 요청에 따라 commit/tag/push 단계는 실행하지 않는다.

---

### Task 1: Before baseline 동결

**Files:**
- Create: `artifacts/2026-09-18_schat-v1-ragas-gold-uat-final/baseline/`

- [x] 기존 36문항 evaluator를 별도 경로에 실행한다.
- [x] UAT 28/36, table Hit@10, zero-call, latency와 security 결과를 저장한다.
- [ ] 기존 retrieval/RAGAS metric과 dataset fingerprint를 baseline manifest에 연결한다.

### Task 2: Scoped document admission TDD

**Files:**
- Modify: `tests/test_schat_v1_final_validation.py`
- Modify: `tests/test_rag_contract.py`
- Modify: `mvp/query.py`
- Modify: `mvp/evidence.py`

**Interfaces:**
- Produces: `QueryPlan.context_document_ids`, `QueryPlan.context_topics`
- Produces: evidence assessment의 scoped admission metadata

- [ ] 명시 단일 scope의 임상 생략형 질문이 검색 근거 없이는 admission되지 않는 실패 테스트를 작성한다.
- [ ] scoped clinical question과 Q006/날씨/진단 요청 safety 테스트를 실행해 RED를 확인한다.
- [ ] QueryPlan context와 evidence-backed admission을 최소 구현한다.
- [ ] focused tests를 실행해 GREEN을 확인한다.

### Task 3: 실제 웹 scope 전달 TDD

**Files:**
- Modify: `mvp/app.py`
- Test: `tests/test_schat_v1_final_validation.py`

**Interfaces:**
- Consumes: registered document metadata
- Produces: 전체 검색 또는 한 개의 명시적 document context

- [ ] 전체/단일/잘못된 선택을 결정하는 pure helper 테스트를 먼저 작성한다.
- [ ] 기본 전체 검색과 단일 지침 선택 UI를 최소 구현한다.
- [ ] scope 변경 시 conversation reset과 document isolation을 검증한다.

### Task 4: Gold label 계약과 통합 evaluator

**Files:**
- Create: `tests/fixtures/schat_v1_operational_gold.json`
- Create: `tools/schat_v1_ragas_gold_uat_evaluate.py`
- Create: `tests/test_schat_v1_ragas_gold_uat.py`

**Interfaces:**
- Consumes: 기존 approved retrieval/Q002/table/TF027 fixture
- Produces: raw-free Before/After retrieval, ID-RAGAS, Gold, UAT, table, provider/image 상태

- [ ] retrieval 결과로 Gold를 생성하지 못하게 하는 fixture contract 테스트를 RED로 작성한다.
- [ ] 명확히 재사용 가능한 기존 승인 ID만 label하고 나머지는 human review로 둔다.
- [ ] 동일 36 UAT와 동일 90 retrieval dataset 비교를 구현한다.
- [ ] Faithfulness/Answer Relevancy를 `pending_external_llm_judge_approval`로 기록한다.

### Task 5: After 평가와 안전 회귀

**Files:**
- Create: `artifacts/2026-09-18_schat-v1-ragas-gold-uat-final/`

- [ ] operational UAT 36문항을 재실행한다.
- [ ] 기존 retrieval/RAGAS metric을 독립 재검산하고 regression 여부를 기록한다.
- [ ] Table 5/5, TF027 pending, provider zero-call, Q006 zero-call을 확인한다.
- [ ] artifact exact source/secret/forbidden-key audit를 실행한다.

### Task 6: 전체 검증과 코드 리뷰

**Files:**
- Modify: `artifacts/2026-09-18_schat-v1-ragas-gold-uat-final/test_results.json`
- Modify: `artifacts/2026-09-18_schat-v1-ragas-gold-uat-final/code_review.json`

- [ ] focused tests를 실행한다.
- [ ] full pytest를 실행한다.
- [ ] Ruff와 `git diff --check`를 실행한다.
- [ ] 변경 diff를 correctness/safety/regression 기준으로 review한다.

### Task 7: 결과·정본·progress 갱신

**Files:**
- Create: `docs/rag/64_SCHAT_V1_RAGAS_GOLD_UAT_FINAL_RESULT.md`
- Create: `docs/superpowers/progress/2026-09-18-schat-v1-ragas-gold-uat-final.md`
- Modify: `docs/현재정본/*.md`
- Modify: `변경이력.md`
- Modify: `복구기준점.md`
- Modify: `artifacts/프로젝트현황/index.html`

- [ ] Before/After와 pending 이유를 문서화한다.
- [ ] 실제 metric에 따라 v1.0/v1.0-rc1/v0.9 중 하나만 제안한다.
- [ ] commit/tag/push 없이 새 복구 기준점 저장 준비 여부를 기록한다.
