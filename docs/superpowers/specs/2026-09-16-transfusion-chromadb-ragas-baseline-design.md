# Transfusion ChromaDB + RAGAS Baseline Design

## Goal

Evaluate the already registered 105 transfusion chunks with an isolated ChromaDB index and non-LLM RAGAS ID metrics, without changing production retrieval or copying clinical source text into artifacts.

## Boundaries

- Read the ready transfusion document, chunk payloads, and 384-dimensional vectors from the production SQLite catalog in read-only mode.
- Preserve the registered `document_id`, `chunk_id`, chunk order, `CHUNK_VERSION`, and embedding model.
- Build an evaluation-only Chroma collection from IDs, vectors, and non-text metadata. Do not store documents.
- Curate 30-50 Korean clinical questions independently of retrieval results. Reference contexts are stable chunk IDs plus fingerprints and structural metadata, never chunk text.
- Mark ambiguous, heading-only, table/image-dependent, or otherwise uncertain cases with `needs_human_review=true`; exclude them from approved-gold aggregate metrics while reporting them separately.
- Calculate IR metrics at k=1,3,5,10 and RAGAS ID-based precision/recall without an LLM judge or generation API.
- Emit a comparison schema that a later BM25 evaluator can populate with the same case IDs, questions, reference IDs, cutoffs, and metric fields.

## Data flow

1. Validate catalog identity, 105 unique stable chunk IDs, finite 105x384 vectors, model, section/parent metadata, and substantive-body classification.
2. Validate each fixture reference against that catalog, including fingerprints and review flags.
3. Create an evaluation-only Chroma collection with cosine distance and no documents.
4. Encode questions with the current local embedding model and query top 10 once per case.
5. Derive cutoff metrics from ranked IDs and compute RAGAS `IDBasedContextPrecision`/`IDBasedContextRecall` on the same IDs.
6. Write raw-free JSON/CSV/HTML summaries and environment/test records.

## Safety

The evaluator fails before index/query execution for catalog drift, missing/duplicate IDs, wrong vector shape/model, non-finite vectors, reference fingerprint drift, approved non-substantive gold, or unreviewed image/table-dependent gold. Dependencies live in an ignored evaluation-only virtual environment.
