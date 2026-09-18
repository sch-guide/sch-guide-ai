---
name: bm25-evaluation
description: Evaluate BM25 and hybrid retrieval quality for a RAG system using Recall@K, MRR, nDCG, and failure analysis.
---

# BM25 Evaluation

Use this skill when evaluating or improving search quality in a RAG system.

## Main goals

Evaluate whether the correct document or chunk appears near the top of search results.

Always consider:

- Recall@1
- Recall@3
- Recall@5
- MRR
- nDCG@5 when multiple relevant results exist

## Evaluation procedure

1. Identify or create a labeled evaluation query set.
2. Run the current retrieval system as the baseline.
3. Save the top-K results for every query.
4. Calculate Recall@K and MRR.
5. Review failed queries manually.
6. Classify the failure cause.
7. Make one retrieval change at a time.
8. Run the same evaluation again.
9. Compare before and after results.

## Failure categories

Check for:

- abbreviation mismatch
- synonym mismatch
- Korean/English terminology mismatch
- spelling or spacing differences
- chunk boundary problems
- poor tokenization
- missing document content
- weak title or metadata weighting
- duplicated chunks
- obsolete document versions
- overly broad queries

## BM25 tuning

Before changing k1 or b, inspect:

1. tokenization
2. synonyms
3. abbreviations
4. Korean spacing
5. chunk size
6. chunk overlap
7. titles and headings
8. metadata
9. duplicate documents

Do not blindly tune BM25 parameters.

## Hybrid retrieval

If the project supports vector search, compare:

- BM25 only
- Vector only
- Hybrid

Use the exact same evaluation queries.

Recommended output:

| System | Recall@1 | Recall@3 | Recall@5 | MRR | nDCG@5 |
|---|---:|---:|---:|---:|---:|
| BM25 | | | | | |
| Vector | | | | | |
| Hybrid | | | | | |

## Healthcare RAG considerations

For hospital or nursing knowledge search:

- Verify that the retrieved source actually supports the answer.
- Track document title and version.
- Detect outdated or duplicated guidance.
- Include real clinical abbreviations and natural staff questions.
- Include safety-critical preparation and procedure questions.
- Never invent missing clinical information.

## When asked to evaluate

When the user asks to evaluate retrieval quality:

1. Inspect the retrieval code.
2. Find the current index configuration.
3. Find or build the evaluation dataset.
4. Run baseline evaluation.
5. Calculate metrics.
6. Analyze failures.
7. Recommend improvements.
8. If approved, apply changes and rerun evaluation.
9. Report before/after results.
