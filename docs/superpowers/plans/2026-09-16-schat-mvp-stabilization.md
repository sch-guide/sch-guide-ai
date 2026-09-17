# SCHAT MVP Stabilization Implementation Plan

> Execute this user-approved plan without intermediate approval. Stop only at
> a request-defined safety condition and preserve all completed baseline work.

## Task 1: Freeze review-state and expansion contracts

1. Add failing tests for auditable review states, aggregate inclusion, source
   fingerprints, and the prohibition on opaque automatic approval.
2. Add failing tests for 30–50 new question IDs, allowed categories, preserved
   original cases/references, and separate negative diagnostics.
3. Implement evaluation-only fixture/audit helpers and make the tests pass.

## Task 2: Review the 14 pending references safely

1. Render only the relevant PDF pages into `tmp/pdfs` for local inspection.
2. Validate page, caption/title, row/column relationship, source shape, and
   reference fingerprints without saving full clinical text to artifacts.
3. Promote only structurally or textually unambiguous cases; retain image or
   layout ambiguity as `needs_human_review=true`.
4. Emit a raw-free human-review manifest and HTML section.

## Task 3: Author and validate the expanded paraphrase set

1. Add 30–50 independently authored variants mapped to existing reviewed gold.
2. Cover colloquial nursing language, abbreviations, time/speed/unit wording,
   table wording, adverse reactions, follow-up, and negatives.
3. Reject duplicate questions, invalid references, heading-only gold, and
   dataset/catalog drift.

## Task 4: Re-run fair retrieval comparison

1. Add failing tests for incremental Chroma querying of new positive and
   negative cases while validating reused baseline rows.
2. Extend the evaluation tools without changing production retrieval.
3. Evaluate ChromaDB, BM25, and conditional RRF Hybrid at K=1/3/5/10.
4. Calculate IR, RAGAS ID precision/recall, type breakdown, score diagnostics,
   latency, index/build/storage measurements, and the common comparison schema.
5. Select the strategy by the frozen decision rule.

## Task 5: Decide whether a production retrieval edit is warranted

1. Inspect the current production search pipeline and document its candidate
   generation/order.
2. Add offline characterization tests for the chosen strategy and Q006.
3. If a minimal change is both necessary and strictly improves approved UAT,
   implement it test-first; otherwise record `production_change_not_required`.
4. Re-run sedation and transfusion safety regression after any edit.

## Task 6: Build table-aware evaluation sidecars

1. Add tests for deterministic table IDs, page/table/row/header metadata,
   source linkage, and raw-free serialization.
2. Extract table units from the existing transfusion PDF with local tooling.
3. Create table lookup/comparison/numeric and mixed text-table cases only from
   approved structural gold.
4. Evaluate text-only versus table-aware BM25 under the common metrics and
   latency contract.
5. Keep the implementation evaluation-only unless the production gate is
   independently satisfied.

## Task 7: Build a conservative figure inventory

1. Add tests for deterministic figure IDs, bounding boxes, caption/nearby-text
   links, decorative-image exclusion, confidence, and absent unapproved vision
   descriptions.
2. Create a raw-free figure manifest.
3. Evaluate only figure cases whose nearby text/caption fully supports gold.
4. Leave interpretation-dependent cases pending and report the stop boundary.

## Task 8: Run offline SCHAT UAT

1. Add category-level evidence tests for sedation and transfusion questions.
2. Verify query/evidence behavior, citation prerequisites, parent atomicity,
   and zero provider calls for negative/out-of-scope cases.
3. Do not add per-question production exceptions.

## Task 9: Apply the external-provider security gate

1. Inventory existing provider adapters and compare their offline contracts.
2. Do not transmit hospital evidence to Groq or Gemini.
3. Record that latency/cost/structured-output/citation live comparison requires
   explicit hospital-data transmission policy and external-account approval.

## Task 10: Verify, review, and publish results

1. Run focused tests after each task, then the full pytest suite.
2. Run Ruff and `git diff --check`.
3. Use the repository code-review skill for correctness, metric fairness,
   privacy, production blast radius, and unsupported claims.
4. Use Superpowers verification-before-completion and cite fresh command output.
5. Generate raw-free artifacts and
   `docs/rag/42_SCHAT_MVP_STABILIZATION_RESULT.md`.
