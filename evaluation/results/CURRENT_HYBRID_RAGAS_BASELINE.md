# Current Hybrid RAGAS Baseline

Retrieval: Current Hybrid Retrieval

Evaluator: `groq/openai/gpt-oss-20b`; RAGAS 0.4.3; LiteLLM 1.101.0.
Stored contexts only. N/A excluded from means. Pending is not a score.

| Metric | Mean | Valid Questions |
| --- | ---: | ---: |
| Context Precision | 0.687500 | 8 |
| Context Recall | 0.708333 | 8 |
| Faithfulness | N/A | 0 |
| Answer Relevancy | N/A | 0 |

| ID | Context Precision | Context Recall | Status |
| -- | --: | --: | -- |
| TRF-001 | 1.000000 | 0.833333 | context_precision: completed (-); context_recall: completed (-) |
| TRF-002 | 1.000000 | 0.000000 | context_precision: completed (-); context_recall: completed (-) |
| TRF-003 | N/A | N/A | context_precision: error (evaluator_error_PermissionError); context_recall: error (evaluator_error_InstructorRetryException) |
| TRF-004 | 0.000000 | 0.500000 | context_precision: completed (-); context_recall: completed (-) |
| TRF-005 | 0.916667 | 1.000000 | context_precision: completed (-); context_recall: completed (-) |
| TRF-006 | 1.000000 | 1.000000 | context_precision: completed (-); context_recall: completed (-) |
| TRF-007 | N/A | N/A | context_precision: na (no_retrieved_contexts); context_recall: na (no_retrieved_contexts) |
| TRF-008 | 0.750000 | 1.000000 | context_precision: completed (-); context_recall: completed (-) |
| TRF-009 | 0.333333 | 1.000000 | context_precision: completed (-); context_recall: completed (-) |
| TRF-010 | 0.500000 | 0.333333 | context_precision: completed (-); context_recall: completed (-) |

Empty contexts (not scored): TRF-007

Faithfulness/Answer Relevancy are intentionally not evaluated.
Resume restores stale in_progress metrics and reuses persisted judge results.
