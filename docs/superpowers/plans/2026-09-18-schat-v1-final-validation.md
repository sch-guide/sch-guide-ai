# SCHAT v1.0 Final Validation Implementation Plan

> Workflow: Superpowers brainstorming -> planning -> TDD -> execution -> verification.

## Goal

Preserve the `v0.9` production system while completing every locally verifiable v1.0
readiness item. Provider Live and clinical image interpretation remain fail-closed
unless their separate approvals are present.

## Fixed safety boundaries

- Do not call Groq, Gemini, vision, web-search, or any other generation service.
- Do not add API-key handling or provider SDK/HTTP transport.
- Do not change production retrieval, embeddings, ranking, Facet-slot, limits, or validators.
- Do not infer TF027 workflow content.
- Do not persist source text, prompts, raw responses, secrets, or original PDFs in artifacts.
- Do not commit, tag, push, or move the `v0.9` tag.

## Task 1: Freeze the final-validation contracts with tests

Files:

- Create `tests/test_schat_v1_final_validation.py`.
- Create `tools/schat_v1_final_validate.py`.

Test first:

- Live readiness must fail closed without external-transfer approval.
- The 36-case UAT fixture must retain 14 sedation, 18 transfusion, and 4
  out-of-scope cases.
- TF027 must remain unapproved and all human-review values must remain empty.
- Result serialization must omit questions and source text.
- The final version decision must be `v1.0-rc1` when core local validation passes
  but provider/image approvals are pending.

## Task 2: Build the local operational-UAT evaluator

Files:

- Implement `tools/schat_v1_final_validate.py`.
- Add `tools/schat_v1_uat_app.py` for local Streamlit review.

Implementation:

- Load production catalog and existing local embeddings without rebuilding them.
- Use the current `plan_query`, BM25 + MiniLM + RRF + reranker, evidence assessment,
  table routing, and presentation contracts.
- Evaluate all 36 fixture cases without a provider transport.
- Record only case IDs, expected/actual metadata, counts, hashes, reasons, latency,
  and pass/fail; do not persist questions or evidence text.
- Treat approved-positive generation as `not_executed_approval_missing` and verify
  the extractive fail-closed boundary instead of claiming a Live answer.
- Require out-of-scope cases to return zero selected evidence and zero provider calls.
- Require TF027 to stay `needs_human_review=true` with zero provider/vision calls.

## Task 3: Create the TF027 human-review surface

Files:

- Create `tools/tf027_human_review_app.py`.
- Generate `artifacts/2026-09-18_schat-v1-final-validation/tf027_review.html`.

Implementation:

- Show the ten approved checklist labels, current empty values, and review status.
- Show only non-clinical identifiers/fingerprints and no inferred workflow.
- Keep the form local and non-submitting; instructions explain how a reviewer must
  complete the controlled fixture in a later approved workflow.

## Task 4: Verify table, multi-document, performance, and security boundaries

Files:

- Extend `tests/test_schat_v1_final_validation.py`.
- Generate raw-free JSON/CSV/HTML artifacts under the final artifact directory.

Checks:

- Existing five approved table cases remain Top-10 hits and MM003 remains fixed.
- Sedation and transfusion document scopes do not mix in per-document retrieval.
- Current source-conflict/document-filter/follow-up tests pass.
- Measure local planning/retrieval/evidence/table/validator time and process RSS;
  provider latency remains `not_measured_no_live_call`.
- Scan artifacts and staged-independent repository outputs for source-text matches,
  secret markers, authorization headers, prompts, and raw responses.

## Task 5: Full verification and review

Commands:

- Focused final-validation tests.
- Provider/controlled-generation, retrieval, sedation, transfusion, table, image
  pending, Q006, validator, AnswerCoverage, parent atomicity, presentation, and
  multi-document regressions.
- Full `pytest` using a workspace-local base temp.
- Ruff on `mvp`, `tests`, and `tools`.
- `git diff --check`.
- Repository code-review checklist and artifact security audit.

## Task 6: Final documentation

Files:

- Create `docs/rag/63_SCHAT_V1_FINAL_VALIDATION_RESULT.md`.
- Create `docs/superpowers/progress/2026-09-18-schat-v1-final-validation.md`.
- Update `docs/현재정본/00_현재상태.md` through `06_테스트현황.md`.
- Update `docs/PRD.md`, `변경이력.md`, `복구기준점.md`, and
  `artifacts/프로젝트현황/index.html`.

Decision:

- `v1.0` only if Provider Live and required image review are approved and verified.
- `v1.0-rc1` when text/table/search/local UAT pass and provider/image tracks remain
  safely pending.
- Keep `v0.9` when a critical local safety or regression defect cannot be resolved
  without weakening a validator or changing the approved production architecture.

No Git commit, tag, or push is part of this plan.
