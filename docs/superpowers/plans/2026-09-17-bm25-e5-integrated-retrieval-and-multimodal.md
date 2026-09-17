# BM25 + E5 Retrieval Strategy and Multimodal Completion Plan

- Date: 2026-09-17
- Branch: `boha-rag`
- Workflow: Superpowers planning → TDD → execution → verification
- External generation/vision API calls: prohibited
- Production retrieval baseline: existing BM25 + semantic/vector + RRF + reranker

## Existing completed work (do not repeat)

- Text answer style presentation and local web UAT
- Provider-neutral controlled paraphrasing schema/prompt/Mock validator
- Structured table extraction, row citation, local retrieval/UI
- Evidence-type trace routing
- Image/vision inventory with `TF027` held for human review
- BM25/MiniLM fairness evaluation and multilingual-E5-large standalone evaluation

## Phase 1 — Frozen retrieval strategy comparison

1. Reuse the frozen transfusion v3 fixture, 105 production chunks, 78 approved positives,
   11 negatives and one human-review exclusion.
2. Build or load an ignored local E5 passage-vector snapshot. The snapshot contains only
   chunk IDs, vectors and contract fingerprints; no source text.
3. Evaluate the same Top-K contract for:
   - current BM25;
   - multilingual-E5-large;
   - BM25 + E5 RRF (`k=60`);
   - BM25 + E5 RRF + the existing deterministic SCHAT reranker;
   - BM25-primary selective E5 fallback.
4. Generate Top-40 candidates independently and evaluate Top-1/3/5/10. RRF merges exact
   chunk IDs and never combines raw scores.
5. The selective policy must not read gold IDs, question IDs or fixture labels. It may only
   use QueryPlan, temporal/colloquial structure, BM25 top score/gap, lexical overlap and
   exact numeric/Latin-token signals. When triggered it fuses BM25 and E5 by RRF; otherwise
   it returns BM25.
6. Record model load, passage build/snapshot reload, query embedding, search/fusion/rerank,
   RSS memory, cache and snapshot sizes, mean/p95 latency and per-question-type metrics.
7. Select a strategy using accuracy first, then operational latency/memory/complexity.
   Production changes are allowed only if the improvement is clear and safety regressions
   remain zero.

## Phase 2 — Production decision

- If no strategy provides a material, operationally justified improvement over the existing
  production hybrid, make no production change.
- If a strategy is clearly better, add the smallest generic integration with a persistent
  passage snapshot and preserve Facet-slot, AnswerCoverage, parent atomicity, validators,
  presentation, and Q006 zero-call.
- Roll back only the new Phase-2 change if any safety regression occurs.

## Phase 3–8 — Completed-track audit and safe completion

- Verify, rather than rebuild, text answer UX, controlled-generation Mock contract, table
  evidence/retrieval/citation/UI, and evidence-type routing.
- Keep provider controlled generation pending because hospital-data external transfer is not
  approved.
- Keep image/diagram clinical interpretation pending because `TF027` remains human-review.
- Run the existing local Streamlit UAT coverage and raw-free artifact audit.

## Phase 9–10 — Verification and reporting

- Focused retrieval tests and metric recomputation
- Sedation UAT, transfusion retrieval, Q006 zero-call, Facet-slot, AnswerCoverage, citation,
  parent atomicity, presentation and multimodal tests
- Full pytest, Ruff and `git diff --check`
- SCHAT code review and artifact security audit
- Progress checkpoints for every completed phase
- Final result: `docs/rag/50_SCHAT_RETRIEVAL_GENERATION_MULTIMODAL_RESULT.md`

## Stop conditions

Stop only for external hospital-data transfer/service approval, unreliable clinical image
interpretation or gold, validator relaxation, dependency conflict, Git/data-corruption risk,
or secret/source-text leakage. A blocked provider or image track remains pending while the
offline text/table/retrieval work continues.
