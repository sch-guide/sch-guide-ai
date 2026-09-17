# SCHAT Final MVP Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a locally verified, approval-waiting SCHAT final MVP candidate while preserving the `v0.8` safety baseline and explicitly isolating provider/image/large table-storage work.

**Architecture:** Add raw-free evaluation tools around the frozen retrieval corpus, make one table-only scoring correction, add an explicit controlled-generation fallback decision, and verify existing presentation/routing/web boundaries. Production retrieval changes only if the frozen evaluation establishes a safe material gain.

**Tech Stack:** Python 3.12, pytest, Ruff, SQLite catalog, NumPy/FastEmbed evaluation cache, Streamlit AppTest/local UI, existing SCHAT validators.

**Spec:** `docs/superpowers/specs/2026-09-18-schat-final-mvp-completion-design.md`

## Global Constraints

- Keep `v0.8` unchanged and work only on `boha-rag`.
- Do not commit, tag, push, force-push, or modify `main`.
- Do not call Groq, Gemini, vision, web search, or any external clinical service.
- Do not send hospital data outside the machine or persist exact source/table text in artifacts.
- Do not relax validators, Facet-slot, selection limit 16, parent atomicity, or Q006 zero-call.
- Do not hardcode question strings, chunk IDs, table IDs, or gold IDs in production logic.
- Use TDD for every behavior change.

---

### Task 1: Frozen reranker bottleneck evaluator

**Files:**
- Create: `tools/reranker_bottleneck_evaluate.py`
- Create: `tests/test_reranker_bottleneck_evaluation.py`
- Create: `artifacts/2026-09-18_schat-final-mvp-completion/retrieval_*.json|csv|html`

**Interfaces:**
- Consumes: frozen fixture, catalog, and `2026-09-17_bm25-e5-retrieval-strategy-02` result rows.
- Produces: `evaluate_reranker_bottleneck(...) -> dict`, per-query raw-free deltas, full metrics, latency, and production decision.

- [ ] Write tests proving that variant ranking never reads gold during ranking, rank-preserving variants keep the raw RRF Top-10 set, and decision thresholds reject regressions.
- [ ] Run the new test file and confirm failure because the evaluator does not exist.
- [ ] Implement deterministic variants and metric aggregation using existing `retrieval_baseline_metrics` helpers.
- [ ] Run focused tests and the frozen 90-question evaluation.
- [ ] Save only IDs, ranks, scores, categories, timings, hashes, and decisions.
- [ ] Write `docs/superpowers/progress/2026-09-18-schat-final-mvp-phase-01-retrieval.md`.

### Task 2: MM003 table numeric retrieval

**Files:**
- Modify: `tools/structured_table_evidence.py`
- Modify: `tests/test_structured_table_evidence.py`
- Modify: `tests/test_schat_multimodal_mvp.py`
- Create/refresh: final artifact table results and UI review.

**Interfaces:**
- Consumes: `search_table_records(question, records, limit=10)`.
- Produces: the same `TableSearchHit` type and unchanged citation/link identities.

- [ ] Add a synthetic test in which a row sharing an exact `15분`-style token outranks a row with more generic Korean n-gram overlap.
- [ ] Run the test and confirm the current equal-weight scorer fails it.
- [ ] Add fixed token-class weights inside the table-only scorer; do not change BM25 or production retrieval.
- [ ] Run the synthetic test and MM001-MM005 regression; require MM003 Hit@10 and no loss in the other approved cases.
- [ ] Verify table citations, row IDs, fingerprints, and exact cell rendering remain unchanged.
- [ ] Write `docs/superpowers/progress/2026-09-18-schat-final-mvp-phase-02-table.md`.

### Task 3: Controlled-generation publish/fallback boundary

**Files:**
- Modify: `mvp/controlled_generation.py`
- Modify: `tests/test_controlled_paraphrasing.py`
- Verify: `mvp/presentation.py`, `mvp/answer_ui.py`, `tests/test_answer_presentation.py`.

**Interfaces:**
- Produces: `ControlledGenerationDecision` and a deterministic decision function that never edits clinical text.

- [ ] Add failing tests for schema failure, invariant failure, and semantic-support-pending all choosing the existing extractive Answer with retry count zero.
- [ ] Implement the minimal immutable decision API.
- [ ] Re-run controlled-generation and presentation tests.
- [ ] Record provider Live and semantic entailment as pending, external calls zero.
- [ ] Write `docs/superpowers/progress/2026-09-18-schat-final-mvp-phase-03-generation.md`.

### Task 4: Multimodal, web UAT, and multi-document boundaries

**Files:**
- Modify or create focused tests under `tests/` only where an observable boundary is missing.
- Reuse: `mvp/evidence_routing.py`, `tools/table_evidence_app.py`, existing Streamlit AppTest helpers.

**Interfaces:**
- Existing `route_evidence(...) -> EvidenceRoute` remains advisory and fail-closed for image.

- [ ] Add/extend tests for conditional text/table/image routing, TF027 pending, local table UAT, and Q006 zero-call.
- [ ] Add architecture-level tests for document filter isolation and conflict metadata without adding hospital source data.
- [ ] Run sedation UAT, transfusion retrieval/table UAT, Facet-slot, AnswerCoverage, parent atomicity, citation, and presentation suites.
- [ ] Measure table lookup and answer-display assembly latency; reuse verified E5 runtime/snapshot metrics rather than re-embedding passages.
- [ ] Write `docs/superpowers/progress/2026-09-18-schat-final-mvp-phase-04-uat-performance.md`.

### Task 5: Final audit and project-state update

**Files:**
- Create: `docs/rag/60_SCHAT_FINAL_MVP_COMPLETION_RESULT.md`
- Create: `docs/superpowers/progress/2026-09-18-schat-final-mvp-completion.md`
- Update: `docs/현재정본/00_현재상태.md` through `06_테스트현황.md`
- Update: `변경이력.md`, `복구기준점.md`, `artifacts/프로젝트현황/index.html`
- Create: `artifacts/2026-09-18_schat-final-mvp-completion/` raw-free summary, metrics, tests, security report, and review HTML.

**Interfaces:**
- Produces a `v0.9` recovery-point candidate description only; it does not create the tag.

- [ ] Run the final evaluator and generate raw-free artifacts.
- [ ] Run focused regressions, full pytest, Ruff, and `git diff --check`.
- [ ] Audit artifacts for source-text matches, forbidden fields, secrets, prompts, and provider responses.
- [ ] Review the entire uncommitted diff for correctness, safety, scope, and rollback simplicity.
- [ ] Update current-state documents from fresh command output.
- [ ] Verify `v0.8` still resolves to its original commit and no Git commit/tag/push occurred.
- [ ] Report whether the tree is ready to be saved as the proposed `v0.9` recovery point.
