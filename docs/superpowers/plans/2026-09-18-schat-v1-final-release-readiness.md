# SCHAT v1 Final Release Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close every automation-safe v1.0 readiness task while preserving TF027 and hospital-data external transfer as explicit human approval gates.

**Architecture:** Add one evaluation-only release-readiness module that reads the existing human-review, Gold, UAT, stabilization, and provider-harness contracts. It emits only IDs, hashes, counts, states, and instructions; it neither changes production modules nor supplies a network transport. The TF027 Streamlit page gains a clearer ten-item human checklist and stop banner without changing its persisted review schema.

**Tech Stack:** Python 3.12, pytest, Streamlit testing, existing SCHAT evaluation helpers, JSON/HTML raw-free artifacts

**Spec:** User-approved “SCHAT v1.0 최종 마무리 작업” request dated 2026-09-18.

## Global Constraints

- Preserve the production BM25 + MiniLM + RRF + existing reranker path and all validators.
- Do not approve or modify the 11 deferred Gold cases.
- Do not approve TF027 or infer its clinical image meaning.
- Do not call Groq, Gemini, Vision, RAGAS LLM judge, or another external service.
- Do not add a provider credential or network transport.
- Do not commit, tag, or push; preserve `v0.9`.
- Persist no hospital source text, prompt, response, PDF, secret, or patient/staff identifier.

---

### Task 1: Release-readiness contracts

**Files:**
- Create: `tools/schat_v1_release_readiness.py`
- Create: `tests/test_schat_v1_release_readiness.py`

**Interfaces:**
- Produces `build_tf027_readiness(...)`, `build_live_ragas_plan(...)`, `build_deferred_queue(...)`, and `decide_release_readiness(...)`.

- [x] Write failing tests for TF027 stop state, 21/11/4 Gold boundary, provider zero-call gate, staged synthetic/minimal/expanded plan, raw-free output, and final readiness.
- [x] Run focused tests and verify failure because the module is absent.
- [x] Implement the smallest local-only evaluator that satisfies the tests.
- [x] Run focused tests to green.

### Task 2: TF027 human-review screen clarity

**Files:**
- Modify: `tools/tf027_human_review_app.py`
- Modify: `tests/test_tf027_human_review.py`

**Interfaces:**
- Preserves the reviewed fixture schema and writes only after explicit form submission.
- Displays `STOP_FOR_TF027_HUMAN_REVIEW` and the ten user-facing review concepts on one page.

- [x] Add a failing Streamlit test for the stop flag and ten separate human-review concepts.
- [x] Verify the existing app fails that test for separate start/end and arrow/connectivity guidance.
- [x] Add a read-only checklist summary without changing the ten persisted source checks.
- [x] Run TF027 tests to green and verify opening the app creates no reviewed fixture.

### Task 3: Raw-free readiness artifacts and documentation

**Files:**
- Create: `artifacts/2026-09-18_schat-v1-final-release-readiness/`
- Create: `docs/rag/67_SCHAT_V1_FINAL_RELEASE_READINESS.md`
- Create: `docs/superpowers/progress/2026-09-18-schat-v1-final-release-readiness.md`
- Modify: `docs/현재정본/00_현재상태.md`
- Modify: `docs/현재정본/06_테스트현황.md`
- Modify: `변경이력.md`
- Modify: `복구기준점.md`

**Interfaces:**
- Emits TF027, provider, deferred, ChromaDB roadmap, readiness, test, security, and HTML review files using metadata only.

- [x] Run the evaluator and confirm 21 approved, 11 deferred, 4 approved abstention, TF027 stop, provider block, and zero external calls.
- [x] Generate the release-readiness report and update current-state/history/recovery documents.
- [x] Audit the new artifact directory for source text, prompts, responses, secrets, identifiers, and PDFs.

### Task 4: Final regression and review

**Files:**
- Verify only; no production changes.

**Interfaces:**
- Preserves operational UAT 36/36 and all production safety contracts.

- [x] Run focused provider/image/Gold/readiness tests.
- [x] Run operational UAT and safety regression suites.
- [x] Run full pytest, Ruff, and `git diff --check`.
- [x] Perform evidence-based code review and record actionable defects.
- [x] Mark every plan item complete without Git operations.
