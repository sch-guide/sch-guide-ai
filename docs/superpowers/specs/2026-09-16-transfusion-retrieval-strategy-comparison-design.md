# Transfusion Retrieval Strategy Comparison Design

## Goal

Select the best retrieval strategy for SCHAT from Chroma dense, production-compatible BM25, and an evaluation-only RRF hybrid under one frozen transfusion dataset and metric contract. Do not change production retrieval, generation, selection, or validators in this task.

## Reused assets

- Production catalog document `실무지침서_수혈간호.pdf`: 17 pages, 105 chunks, chunk version 4.
- Existing 45 manually curated positive questions, stable reference chunk IDs, parent IDs, and SHA-256 drift checks.
- Existing ChromaDB 1.5.9 cosine collection and Top-10 results for those 45 cases.
- Existing local MiniLM 384-dimensional vectors and query embedder.

The comparison must not re-ingest the PDF, regenerate document embeddings, or replace the existing production catalog.

## Dataset v2

Upgrade the fixture without replacing the reviewed positive gold:

- Preserve the 45 positive questions and their reference contexts byte-for-byte.
- Reclassify them into the agreed evaluation taxonomy: `preparation`, `procedure`, `monitoring`, `adverse_reaction`, `product_specific`, `fact_specific`, `temporal`, and `paraphrase`.
- Add five `negative_out_of_scope` questions with `expected_answerable=false` and no gold contexts.
- Store `question_id`, `expected_answerable`, `reference_context_ids`, `reference_parent_ids`, fingerprinted `reference_contexts`, `needs_human_review`, and metadata.
- Keep table/image-dependent 14 positive cases marked for human review and excluded from aggregate positive IR metrics.

## Fair comparison contract

All retrievers use the same 50 questions, 105 chunks, stable IDs, gold, Top-K values `(1, 3, 5, 10)`, and latency boundary (query preprocessing/scoring/ranking, excluding one-time index construction).

Retriever-specific work is limited to:

- `chromadb`: existing local query embedding plus cosine similarity.
- `bm25`: existing `BM25Index` lexical scoring and stable score/order sort; no temporal rerank, RRF, or production evidence gate in the standalone baseline.
- `hybrid_rrf`: evaluation-only reciprocal-rank fusion with `k=60` over the Chroma and BM25 Top-10 rankings.

The shared long-form row contract is:

```json
{
  "dataset_version": "transfusion-retrieval-v2",
  "document_version": "<document id + file hash contract>",
  "chunk_version": 4,
  "retriever": "chromadb",
  "question_id": "TF001",
  "question_type": "fact_specific",
  "expected_answerable": true,
  "needs_human_review": false,
  "k": 5,
  "rank": 1,
  "retrieved_chunk_id": "...",
  "score": 0.82,
  "is_gold": true,
  "latency_ms": 12.4
}
```

Every retriever emits rows for each K. Per-question and aggregate JSON use the same metric formulas. Multi-gold recall is the fraction of unique reference IDs retrieved; precision is relevant returned IDs divided by the actual returned prefix length.

## Negative diagnostics

Negative questions have no gold and never enter Hit/MRR/Recall/Precision or RAGAS aggregates. For each retriever record:

- Top-1 score per negative question.
- Returned Top-10 count.
- Positive versus negative Top-1 distribution (count, mean, minimum, p50, p95, maximum).

Scores are compared only within the same retriever because cosine similarity, BM25, and RRF have different scales. This task does not invent an abstention threshold.

## Hybrid decision

Run Hybrid only after BM25 and Chroma are available. It is justified when each standalone retriever wins at least one approved positive question or question type at Top-10/MRR, so neither dominates all useful cases.

Select a strategy using approved positive Hit@5, Hit@10, MRR, Recall@10, type/paraphrase robustness, exact-term performance, latency, and implementation complexity. Prefer the simpler retriever on a metric tie. A selection is a recommendation only; the explicit production-change prohibitions prevent deployment in this task.

## Safety and artifacts

- No Groq/Gemini/LLM judge calls.
- No source text in JSON/CSV/HTML artifacts or Chroma `documents`.
- No production dependency or module change.
- Preserve the old baseline directory; write the comparison to a new artifact directory.
- Validate the current catalog, fixture fingerprints, and common schema before scoring.
