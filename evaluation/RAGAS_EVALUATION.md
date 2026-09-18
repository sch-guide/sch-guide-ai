# Retrieval evaluation contract

This runner scores saved contexts only. It does not import service retrieval or answer generation.

- RAGAS: 0.4.3; LiteLLM: 1.101.0 (evaluation/requirements-ragas.txt).
- API: ragas.metrics.collections.ContextPrecision / ContextRecall, ascore, llm_factory with adapter="litellm".
- Evaluator: groq/openai/gpt-oss-20b, temperature=0, existing GUIDE_LLM_API_KEY from mvp/.env. Keys are never serialized.
- question -> user_input; reference_answer -> reference; saved retrieved_contexts[].content -> retrieved_contexts, preserving list order and exact text.
- Empty contexts: N/A / no_retrieved_contexts, excluded from means. No replacement text and no zero score.
- Faithfulness and Answer Relevancy: N/A; not requested from RAGAS.
- Context Precision is rank-sensitive. Context Recall assesses support for reference claims, not final service-answer coverage.

## Run from project root

```powershell
.\.venv\Scripts\python.exe -m evaluation.run_current_hybrid_ragas --run
```

Resume completed metrics without recalling them:

```powershell
.\.venv\Scripts\python.exe -m evaluation.run_current_hybrid_ragas --run --resume
```

Without --run, only local input/version checks run; no evaluator or API is initialized.
Result JSON and Markdown are produced on execution, not populated with fabricated scores during setup.

## Checkpoints and limits

Each question/metric is processed sequentially. A transport semaphore also serializes internal requests.
Checkpoint is saved before a metric and immediately after completion/error. Completed and N/A metrics are skipped.
Error metrics can be attempted again by an explicit --resume. An in_progress metric has an uncertain outcome after interruption and is never replayed automatically; inspect it separately.
Input hashes, evaluator settings, versions, and runner hash must match for resume. Existing output cannot be overwritten by a fresh run; a lock prevents concurrent runs.

HTTP 429 retry-after seconds or HTTP-date are honored, with at most two retries per evaluator request. Without usable retry-after, the error is checkpointed. Other errors are not automatically retried. SDK transport retries are disabled; Instructor gets one attempt. Raw exception messages/headers are not saved.
A metric may need multiple LLM requests. If that metric fails partway through, explicit resume repeats the unfinished metric; previously completed metrics remain preserved.

## Future comparison

Use the same runner, pinned packages, evaluator/model/temperature, labels/reference, and metric definitions. Supply another saved retrieval result with --input, a new --output path, and --retrieval-name. The format must retain question_id, question, and retrieved_contexts with content strings. No ChromaDB implementation is included.

Official references:

- https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/
- https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_recall/
- https://docs.ragas.io/en/stable/howtos/llm-adapters/

Environment note: installing RAGAS/LiteLLM changes .venv dependencies, although service source/configuration files remain unchanged. datasets requires fsspec<=2026.6.0; installation uses fsspec 2026.6.0 in place of the previously installed 2026.7.0.

Compatibility: langchain-community 0.4.2 removed a module imported by RAGAS 0.4.3. Pin 0.4.1, with instructor 1.17.0. The LiteLLM transport is wrapped with instructor.from_litellm(..., mode=JSON) before passing to llm_factory. A test uses the actual installed adapters and synthetic mocked HTTP responses to verify both metric paths without API access.
