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
Error metrics can be attempted again by an explicit --resume. Stale in_progress metrics become pending. Completed judge results are replayed locally; unpersisted responses cannot be recovered and may require a repeated API call.
Input hashes, evaluator settings, versions, and runner hash must match for resume. Existing output cannot be overwritten by a fresh run; a lock prevents concurrent runs.

HTTP 429 retry-after seconds or HTTP-date are honored through nested exceptions. Without usable retry-after, wait 60 seconds. At most two additional retries are shared across all subrequests of a question/metric. Exhausted rate limits remain pending with rate_limit_retry_exhausted. Other errors are not automatically retried. SDK transport retries are disabled; Instructor gets one attempt. Raw exception messages/headers are not saved.
The transport estimates input tokens locally (LiteLLM token_counter), adds 25% margin and 2048 output reserve. A persisted rolling 60-second ledger reserves the larger of that estimate and actual returned usage. Only when the next estimate would exceed 8000 does it wait until enough entries expire. There is no fixed first-call delay. Estimates over 8000 fail safely without changing prompts or calling the provider. The estimate is not a guarantee of provider accounting; unknown external activity or larger outputs can still cause 429. Provider Retry-After remains authoritative.
The known pre-fix runner hash is explicitly accepted on resume, while input hashes, package versions, evaluator and metric settings must still match. Original identity is retained and the new runner hash is appended to resume_runner_history. Unknown runner revisions remain blocked.
Historical evaluator_error_InstructorRetryException is ambiguous: it is retryable as an error but is not relabeled as a confirmed rate limit without evidence. Existing checkpoints are not migrated during setup.
A metric may need multiple LLM requests. context_progress[metric] stores each ordered judge call's index, input hash, status and structured result. The existing RAGAS metric executes unchanged but its agenerate calls reuse saved results. Precision stores the verdict; Recall stores its structured classifications for the single combined-context call. Aggregate scores are still calculated by RAGAS, not by a replacement formula. A backup (.json.pre-context-v2.bak) is created on first resume before migration. Current checkpoint is unchanged during implementation.
Logs distinguish normal pacing, provider Retry-After, fallback after 429 and context i/n running/completed. No key or prompt text is logged.

## Future comparison

Use the same runner, pinned packages, evaluator/model/temperature, labels/reference, and metric definitions. Supply another saved retrieval result with --input, a new --output path, and --retrieval-name. The format must retain question_id, question, and retrieved_contexts with content strings. No ChromaDB implementation is included.

Official references:

- https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/
- https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_recall/
- https://docs.ragas.io/en/stable/howtos/llm-adapters/

Environment note: installing RAGAS/LiteLLM changes .venv dependencies, although service source/configuration files remain unchanged. datasets requires fsspec<=2026.6.0; installation uses fsspec 2026.6.0 in place of the previously installed 2026.7.0.

Compatibility: langchain-community 0.4.2 removed a module imported by RAGAS 0.4.3. Pin 0.4.1, with instructor 1.17.0. The LiteLLM transport is wrapped with instructor.from_litellm(..., mode=JSON) before passing to llm_factory. A test uses the actual installed adapters and synthetic mocked HTTP responses to verify both metric paths without API access.
