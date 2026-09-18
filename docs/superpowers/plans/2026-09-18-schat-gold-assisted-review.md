# SCHAT Gold Assisted Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate conservative local-only Gold drafts for the 30 non-approved operational cases and make human review substantially faster without automatic Gold approval.

**Architecture:** Add a pure rule-based draft module beside the existing review module, persist drafts only under the ignored `data/review/` path, and adapt the isolated Streamlit review UI to consume drafts. Existing reviewed Gold validation remains the only path into the aggregate.

**Tech Stack:** Python 3.12, Streamlit, pytest, existing SCHAT local retrieval and table evidence utilities

**Spec:** `docs/superpowers/specs/2026-09-18-schat-gold-assisted-review-design.md`

## Global Constraints

- Do not modify production retrieval, generation, validators, or provider code.
- Do not call Groq, Gemini, vision, HTTP, or another external service.
- Never alter a case whose existing review has `final_gold_approved=true`.
- A draft always has `draft_only=true` and `final_gold_approved=false`.
- Do not commit, tag, or push.

---

### Task 1: Draft contract and conservative extraction

**Files:**
- Create: `tools/schat_gold_draft.py`
- Create: `tests/test_schat_gold_draft.py`

**Interfaces:**
- Consumes: review input case dictionaries and memory-only candidate dictionaries
- Produces: `build_case_draft(case, candidates) -> dict[str, Any]` and draft validation

- [x] Write failing tests for non-approval, rank-independent Primary selection, exact-only invariant extraction, and out-of-scope empty draft.
- [x] Run the focused tests and confirm failures are caused by the missing draft module.
- [x] Implement deterministic candidate directness, exact sentence/invariant extraction, confidence, and fail-closed validation.
- [x] Run focused tests until green, then refactor without changing behavior.

### Task 2: Batch generation and safe persistence

**Files:**
- Modify: `tools/schat_gold_draft.py`
- Modify: `tests/test_schat_gold_draft.py`

**Interfaces:**
- Produces: `build_draft_fixture`, `save_draft_fixture`, `load_draft_fixture`, and `draft_progress`

- [x] Write failing tests proving approved cases are skipped, existing review bytes are unchanged, and only local draft data is written.
- [x] Implement the batch loader contract and atomic local-only save.
- [x] Verify that 32 input cases minus the 2 approved cases produces exactly 30 drafts.

### Task 3: Human approval workflow helpers

**Files:**
- Modify: `tools/schat_gold_human_review.py`
- Modify: `tests/test_schat_gold_human_review.py`

**Interfaces:**
- Produces: draft-to-review updates, final-approval separation, and next-unreviewed navigation

- [x] Write failing tests for draft acceptance without final approval, modified acceptance, hold, approved-case immutability, and next-case selection.
- [x] Implement minimal pure helpers while retaining the existing review validator and aggregate contract.
- [x] Verify that first approval alone remains excluded from `aggregate_approved_cases`.

### Task 4: Streamlit assisted-review UX

**Files:**
- Modify: `tools/schat_gold_human_review_app.py`
- Modify: `tests/test_schat_gold_human_review.py`

**Interfaces:**
- Consumes: local draft fixture and existing reviewed fixture
- Produces: draft panel, three review actions, progress, navigation, and automatic next-case movement

- [x] Write failing AppTest coverage for draft display, three actions, reviewer requirement, final checkbox separation, and navigation.
- [x] Implement per-case widget state and safe action handling.
- [x] Run AppTest using temporary reviewed/draft paths and confirm no real fixture is overwritten.

### Task 5: Generate the real local drafts and document operation

**Files:**
- Modify: `docs/rag/65_SCHAT_OPERATIONAL_GOLD_HUMAN_REVIEW_GUIDE.md`
- Local ignored output: `data/review/schat_v1_operational_gold_drafts.json`

**Interfaces:**
- Uses the same registered catalog and 30 non-approved operational cases
- Produces counts for ready, edit recommended, and manual review required

- [x] Generate drafts locally with zero provider calls.
- [x] Validate that `UAT-S01` and `UAT-S02` are absent and unchanged in the reviewed fixture.
- [x] Update the guide with the assisted workflow and local-only storage boundary.

### Task 6: Verification and review

**Files:**
- Review all files listed above

- [x] Run focused Gold draft/review tests.
- [x] Run relevant operational Gold and TF027 regression tests.
- [x] Run Ruff and `git diff --check`.
- [x] Perform evidence-based code review and verify no provider/network or production module changes.
- [x] Report actual draft counts and a conservative review-time reduction estimate.
