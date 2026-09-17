# Transfusion ChromaDB + RAGAS Baseline Implementation Plan

1. Restore and run contract tests for deterministic IR/RAGAS formulas, fixture/catalog drift, review exclusion, comparison schema, and raw-text-free artifacts.
2. Curate 30-50 questions from the already inspected 17-page transfusion PDF and resolve manually chosen gold positions to stable chunk IDs/fingerprints.
3. Implement an evaluation-only catalog reader, Chroma cosine collection, query runner, RAGAS ID metrics, and safe artifact renderer.
4. Install pinned ChromaDB/RAGAS in an ignored evaluation-only virtual environment without changing production requirements.
5. Execute Top-K 1/3/5/10 evaluation and generate all requested JSON/CSV/HTML artifacts plus BM25 comparison schema.
6. Run focused tests, full pytest, Ruff, git diff check, code review, and write the final result document.
