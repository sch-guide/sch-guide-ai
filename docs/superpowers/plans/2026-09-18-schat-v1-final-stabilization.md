# SCHAT v1 Final Stabilization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze the 21 human-approved positive Gold cases, evaluate four retrieval strategies under one contract, diagnose the 11 deferred cases without approving them, and produce a local-only v1.0 readiness decision.

**Architecture:** Add one evaluation-only stabilization module beside the existing Gold/UAT tools. It validates reviewed Gold independently of retrieval, runs local chunk and table retrieval without provider calls, writes only IDs/metrics/reasons to artifacts, and reuses the existing operational UAT and safety validators. Production modules remain unchanged unless an independently reproducible generalized defect requires a tested minimal fix.

**Tech Stack:** Python 3.12, pytest, existing SCHAT catalog/BM25/MiniLM/RRF/reranker, local FastEmbed multilingual-E5-large, Streamlit review contracts

**Spec:** The user-approved “SCHAT v1.0 최종 안정화” request dated 2026-09-18.

## Global Constraints

- Keep `v0.9` unchanged and do not commit, tag, or push.
- Never auto-approve deferred Gold or TF027 image Gold.
- Do not call Groq, Gemini, a vision provider, an LLM judge, or another external service.
- Keep production retrieval, generation, validators, citation, number/unit/time safety, Facet-slot, and selection limit unchanged.
- Persist no hospital source text, prompt, provider response, secret, or PDF in artifacts.

---

### Task 1: Reviewed Gold aggregate contract

**Files:**
- Create: `tools/schat_v1_final_stabilize.py`
- Create: `tests/test_schat_v1_final_stabilization.py`

**Interfaces:**
- Produces `load_and_validate_reviewed_gold(...)` and a frozen 21-approved/11-deferred manifest.

- [x] Write failing tests for exact approved/deferred counts, duplicate IDs, duplicate evidence, Primary/Acceptable overlap, empty approved critical facts, and approved out-of-scope retention.
- [x] Run the focused tests and confirm the new module is missing.
- [x] Implement fail-closed validation without modifying the reviewed fixture.
- [x] Run the focused tests to green.

### Task 2: Four-strategy retrieval evaluation

**Files:**
- Modify: `tools/schat_v1_final_stabilize.py`
- Modify: `tests/test_schat_v1_final_stabilization.py`

**Interfaces:**
- Produces per-case ID-only rows and aggregate metrics for `bm25_current`, `production_hybrid`, `e5_large_eval`, and `bm25_e5_rrf_eval`.

- [x] Write failing metric tests with literal ranked IDs and multi-Gold expectations.
- [x] Implement the shared planned-query, document scope, Top-10, metric, and table-subset contracts.
- [x] Verify E5 uses only the cached local model and writes only a local ignored vector snapshot.
- [x] Run all approved cases and persist raw-free metrics.

### Task 3: Deferred-case diagnosis and safe queues

**Files:**
- Modify: `tools/schat_v1_final_stabilize.py`
- Modify: `tests/test_schat_v1_final_stabilization.py`

**Interfaces:**
- Produces one of `retrieval_miss`, `table_structure_or_mapping`, `ambiguous_question`, `insufficient_candidate_evidence`, `image_human_review_required`, or `other` for every deferred case.

- [x] Write failing tests proving classification uses review/evidence signals rather than question or Gold-specific hardcoding.
- [x] Implement local candidate diagnosis, actual-guideline-evidence status, improvement layer, and human-review queue.
- [x] Apply only evaluation-tool fixes that do not alter Gold or production behavior; otherwise record zero safe automatic improvements.
- [x] Re-evaluate deferred candidates and preserve all 11 as non-approved.

### Task 4: Image and Live-RAGAS readiness gates

**Files:**
- Modify: `tools/schat_v1_final_stabilize.py`
- Modify: `tests/test_schat_v1_final_stabilization.py`

**Interfaces:**
- Produces TF027 review queue metadata and provider readiness status `BLOCKED_BY_EXTERNAL_TRANSFER_APPROVAL`.

- [x] Test that TF027 remains human-review pending and no image Gold is emitted.
- [x] Test that absent external-transfer approval yields zero provider/LLM-judge calls.
- [x] Reuse the existing offline provider contract and record only hashes/counts/status.

### Task 5: UAT, regression, security, and reporting

**Files:**
- Create: `docs/rag/66_SCHAT_V1_FINAL_STABILIZATION_REPORT.md`
- Create: `docs/superpowers/progress/2026-09-18-schat-v1-final-stabilization.md`
- Create: `artifacts/2026-09-18_schat-v1-final-stabilization/`
- Modify: `docs/현재정본/00_현재상태.md`
- Modify: `docs/현재정본/06_테스트현황.md`
- Modify: `변경이력.md`

**Interfaces:**
- Produces the final readiness state and raw-free HTML/JSON/CSV review artifacts.

- [x] Run operational UAT, focused safety suites, and table/image/provider pending contracts.
- [x] Run full pytest, Ruff, and `git diff --check`.
- [x] Audit artifacts for source text, prompts, provider responses, secrets, Authorization headers, and PDFs.
- [x] Perform evidence-based code review and update current-state/history documents.
- [x] Mark every plan item complete and report without Git operations.
