"""Deterministic, provider-neutral retrieval metrics for offline baselines."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from statistics import fmean

FINGERPRINT_ALGORITHM_ID = "mvp.library.fingerprint_sha256"


def _unique_ids(values: Iterable[str], *, label: str) -> tuple[str, ...]:
    result = tuple(values)
    if not result or any(not isinstance(value, str) or not value for value in result):
        raise ValueError(f"{label} must contain non-empty strings")
    if len(set(result)) != len(result):
        raise ValueError(f"duplicate {label[:-1] if label.endswith('s') else label}")
    return result


def ranked_retrieval_metrics(
    retrieved_context_ids: Sequence[str],
    reference_context_ids: Sequence[str],
    *,
    cutoffs: Sequence[int] = (1, 3, 5, 10),
) -> dict[str, float]:
    """Calculate literal-cutoff hit, recall, precision, and reciprocal rank."""
    retrieved = _unique_ids(retrieved_context_ids, label="retrieved context ids")
    references = _unique_ids(reference_context_ids, label="reference context ids")
    if any(not isinstance(cutoff, int) or cutoff <= 0 for cutoff in cutoffs):
        raise ValueError("cutoffs must be positive integers")
    if len(set(cutoffs)) != len(cutoffs):
        raise ValueError("cutoffs must be unique")

    reference_set = set(references)
    result: dict[str, float] = {}
    for cutoff in cutoffs:
        prefix = retrieved[:cutoff]
        matches = len(reference_set.intersection(prefix))
        result[f"hit_at_{cutoff}"] = float(matches > 0)
        result[f"recall_at_{cutoff}"] = matches / len(reference_set)
        # If fewer than k results exist, precision uses the actual returned count.
        result[f"precision_at_{cutoff}"] = matches / len(prefix) if prefix else 0.0
    first_rank = next((rank for rank, value in enumerate(retrieved, 1) if value in reference_set), None)
    result["mrr"] = 1.0 / first_rank if first_rank else 0.0

    ordered: dict[str, float] = {}
    for cutoff in cutoffs:
        ordered[f"hit_at_{cutoff}"] = result[f"hit_at_{cutoff}"]
    ordered["mrr"] = result["mrr"]
    for prefix in ("recall", "precision"):
        for cutoff in cutoffs:
            ordered[f"{prefix}_at_{cutoff}"] = result[f"{prefix}_at_{cutoff}"]
    return ordered


def id_based_context_scores(
    retrieved_context_ids: Sequence[str], reference_context_ids: Sequence[str]
) -> dict[str, float]:
    """Calculate the set formulas used by RAGAS ID context precision/recall."""
    retrieved = _unique_ids(retrieved_context_ids, label="retrieved context ids")
    references = _unique_ids(reference_context_ids, label="reference context ids")
    overlap = len(set(retrieved).intersection(references))
    return {
        "context_precision": overlap / len(retrieved),
        "context_recall": overlap / len(references),
    }


def _mean_metric_maps(rows: Sequence[Mapping[str, object]]) -> dict[str, float]:
    values: defaultdict[str, list[float]] = defaultdict(list)
    for row in rows:
        for key, value in row["metrics"].items():  # type: ignore[union-attr]
            values[key].append(float(value))
    return {key: fmean(metric_values) for key, metric_values in values.items()}


def aggregate_metrics(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Aggregate only approved-gold cases and retain pending-review counts."""
    approved = [
        row
        for row in rows
        if row.get("expected_answerable", True) and not row.get("needs_human_review")
    ]
    pending = [row for row in rows if row.get("needs_human_review")]
    negative = [row for row in rows if not row.get("expected_answerable", True)]
    by_type: defaultdict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in approved:
        by_type[str(row["question_type"])].append(row)
    return {
        "evaluated_case_count": len(approved),
        "human_review_case_count": len(pending),
        "negative_case_count": len(negative),
        "overall": _mean_metric_maps(approved) if approved else {},
        "by_question_type": {
            question_type: {"case_count": len(group), **_mean_metric_maps(group)}
            for question_type, group in sorted(by_type.items())
        },
    }
