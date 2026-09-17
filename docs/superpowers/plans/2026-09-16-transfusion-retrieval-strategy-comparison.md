# Transfusion Retrieval Strategy Comparison Implementation Plan

> Execute this pre-approved plan without intermediate approval unless a user-defined stop condition occurs.

## Task 1: Freeze dataset v2 contract

1. Add failing tests for 50 cases, 45 positive/5 negative, required fields, empty negative gold, preserved positive fingerprints, question taxonomy, and dataset/document version drift.
2. Upgrade `tests/fixtures/transfusion_retrieval_baseline.json` mechanically while preserving all existing positive references.
3. Extend fixture validation and make the tests pass.

## Task 2: Freeze common rows and negative diagnostics

1. Add failing tests for the exact long-form row schema, K/rank expansion, multi-gold formulas, and negative exclusion/distribution logic.
2. Extend `tools/retrieval_baseline_metrics.py` with deterministic shared aggregation and diagnostics.
3. Verify RED then GREEN.

## Task 3: Add evaluation-only BM25 and comparison orchestration

1. Add failing tests for BM25 stable ranking, zero-score retention, shared row output, and reuse validation of the existing Chroma results.
2. Implement `tools/retrieval_strategy_evaluate.py` using the current `BM25Index` without production edits.
3. Reuse the existing Chroma index/results for the 45 positive cases and query only the five new negative cases.
4. Emit Chroma and BM25 metrics under the same schema.

## Task 4: Evaluate conditional RRF Hybrid and select strategy

1. Add failing tests for RRF(k=60), distinct-ID ranking, complementarity detection, and deterministic recommendation logic.
2. Run Hybrid only when standalone results are complementary.
3. Produce overall/type/negative/latency comparisons and a documented selection without production deployment.

## Task 5: Produce artifacts and documentation

1. Write the required artifacts under `artifacts/2026-09-16_transfusion-retrieval-baseline/`, including a BM25 comparison template and raw-free review HTML.
2. Write `docs/rag/40_RETRIEVAL_BASELINE_AND_SELECTION_RESULT.md` with dataset, metrics, breakdown, comparison, strategy selection, deployment status, and limitations.

## Task 6: Verify and review

1. Run focused tests in the evaluation environment.
2. Run full pytest in the production environment.
3. Run Ruff and formatting checks for changed Python files.
4. Run `git diff --check` and confirm no production module/requirements diff.
5. Use the repository code-review skill to inspect correctness, safety, metric fairness, artifact privacy, and claims.
6. Run the Superpowers verification-before-completion checklist and report only verified results.
