"""Raw-free sanitization helpers for historical workspace artifacts.

The module never prints artifact values.  It only replaces approved question
fields with stable identifiers and fails closed for every other raw field.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

QUESTION_FIELD = "question"
QUESTION_HASH_FIELD = "question_sha256"
UNAPPROVED_RAW_FIELDS = frozenset(
    {
        "answer",
        "chunk_text",
        "content",
        "documents",
        "evidence_text",
        "full_prompt",
        "prompt",
        "raw_request",
        "raw_response",
        "raw_text",
        "source_text",
        "source_unit_text",
        "text",
    }
)
Q002_LOADER_FIELDS = (
    "chunk_id",
    "semantic_score",
    "bm25_score",
    "rrf_score",
    "rerank_score",
    "context_only",
    "context_complete",
)


class UnsafeArtifactFieldError(ValueError):
    """Raised when an artifact contains a field outside the approved contract."""


@dataclass(frozen=True)
class SanitizationSummary:
    file_type: str
    case_count: int
    question_count: int


@dataclass(frozen=True)
class VerificationResult:
    verified: bool
    case_count: int
    question_count: int


def stable_identifier(value: str) -> str:
    """Return a deterministic identifier without retaining the input value."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _q002_loader_rows(payload: Any) -> list[dict[str, Any]]:
    """Extract only the ordered fields consumed by ``_load_q002``."""

    try:
        rows = payload["funnel"]["improved_evidence"]
    except (KeyError, TypeError) as exc:
        raise UnsafeArtifactFieldError("missing q002 loader contract") from exc
    if not isinstance(rows, list):
        raise UnsafeArtifactFieldError("q002 improved_evidence must be a list")

    projection: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise UnsafeArtifactFieldError("q002 loader row must be an object")
        missing = [field for field in Q002_LOADER_FIELDS if field not in row]
        if missing:
            raise UnsafeArtifactFieldError(
                "missing q002 loader fields: " + ",".join(missing)
            )
        projection.append({field: row[field] for field in Q002_LOADER_FIELDS})
    return projection


def create_q002_compatibility_projection(
    source: Path, target: Path
) -> SanitizationSummary:
    """Create the minimal raw-free artifact needed by ``_load_q002``."""

    source = Path(source)
    target = Path(target)
    payload = json.loads(source.read_text(encoding="utf-8"))
    rows = _q002_loader_rows(payload)
    projection = {
        "artifact_type": "raw_free_compatibility_projection",
        "projection_version": "q002_phase1_hits_v1",
        "status": "compatible",
        "funnel": {"improved_evidence": rows},
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(projection, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return SanitizationSummary("json", len(rows), 0)


def verify_q002_compatibility_projection(
    source: Path, target: Path
) -> VerificationResult:
    """Verify ordered IDs, scores, and status flags against the source."""

    source_rows = _q002_loader_rows(
        json.loads(Path(source).read_text(encoding="utf-8"))
    )
    target_payload = json.loads(Path(target).read_text(encoding="utf-8"))
    expected = {
        "artifact_type": "raw_free_compatibility_projection",
        "projection_version": "q002_phase1_hits_v1",
        "status": "compatible",
        "funnel": {"improved_evidence": source_rows},
    }
    return VerificationResult(target_payload == expected, len(source_rows), 0)


def _sanitize_json(value: Any) -> tuple[Any, int]:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        question_count = 0
        for key, item in value.items():
            normalized = str(key).casefold()
            if normalized in UNAPPROVED_RAW_FIELDS:
                raise UnsafeArtifactFieldError(f"unapproved raw field: {key}")
            if normalized == QUESTION_FIELD:
                if QUESTION_HASH_FIELD in value or QUESTION_HASH_FIELD in result:
                    raise UnsafeArtifactFieldError("question hash field collision")
                if not isinstance(item, str):
                    raise UnsafeArtifactFieldError("question must be a string")
                result[QUESTION_HASH_FIELD] = stable_identifier(item)
                question_count += 1
                continue
            sanitized, nested_count = _sanitize_json(item)
            result[str(key)] = sanitized
            question_count += nested_count
        return result, question_count
    if isinstance(value, list):
        result_list: list[Any] = []
        question_count = 0
        for item in value:
            sanitized, nested_count = _sanitize_json(item)
            result_list.append(sanitized)
            question_count += nested_count
        return result_list, question_count
    return value, 0


def _sanitize_json_file(source: Path, target: Path) -> SanitizationSummary:
    payload = json.loads(source.read_text(encoding="utf-8"))
    sanitized, question_count = _sanitize_json(payload)
    if question_count == 0:
        raise UnsafeArtifactFieldError("artifact contains no question field")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(sanitized, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return SanitizationSummary("json", question_count, question_count)


def _sanitize_csv_file(source: Path, target: Path) -> SanitizationSummary:
    with source.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        normalized = {field.casefold() for field in fieldnames}
        unsafe = normalized & UNAPPROVED_RAW_FIELDS
        if unsafe:
            raise UnsafeArtifactFieldError(
                "unapproved raw fields: " + ",".join(sorted(unsafe))
            )
        if QUESTION_FIELD not in normalized:
            raise UnsafeArtifactFieldError("artifact contains no question field")
        if QUESTION_HASH_FIELD in normalized:
            raise UnsafeArtifactFieldError("question hash field collision")
        rows = list(reader)

    question_key = next(field for field in fieldnames if field.casefold() == QUESTION_FIELD)
    output_fields = [
        QUESTION_HASH_FIELD if field == question_key else field for field in fieldnames
    ]
    sanitized_rows: list[dict[str, str]] = []
    for row in rows:
        question = row.get(question_key)
        if question is None:
            raise UnsafeArtifactFieldError("missing question value")
        sanitized = {
            (QUESTION_HASH_FIELD if key == question_key else key): (
                stable_identifier(value or "") if key == question_key else value
            )
            for key, value in row.items()
        }
        sanitized_rows.append(sanitized)

    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=output_fields)
        writer.writeheader()
        writer.writerows(sanitized_rows)
    return SanitizationSummary("csv", len(rows), len(rows))


def sanitize_file(source: Path, target: Path) -> SanitizationSummary:
    """Create a sanitized copy without modifying the source."""

    source = Path(source)
    target = Path(target)
    suffix = source.suffix.casefold()
    if suffix == ".json":
        return _sanitize_json_file(source, target)
    if suffix == ".csv":
        return _sanitize_csv_file(source, target)
    raise ValueError(f"unsupported artifact type: {suffix}")


def verify_sanitized_file(source: Path, target: Path) -> VerificationResult:
    """Verify that sanitization changed only approved question fields."""

    source = Path(source)
    target = Path(target)
    suffix = source.suffix.casefold()
    if suffix == ".json":
        expected, question_count = _sanitize_json(
            json.loads(source.read_text(encoding="utf-8"))
        )
        actual = json.loads(target.read_text(encoding="utf-8"))
        return VerificationResult(expected == actual, question_count, question_count)
    if suffix == ".csv":
        with source.open(encoding="utf-8-sig", newline="") as handle:
            source_rows = list(csv.DictReader(handle))
        with target.open(encoding="utf-8", newline="") as handle:
            target_rows = list(csv.DictReader(handle))
        if len(source_rows) != len(target_rows):
            return VerificationResult(False, len(source_rows), len(source_rows))
        for original, sanitized in zip(source_rows, target_rows, strict=True):
            question_key = next(
                (key for key in original if key.casefold() == QUESTION_FIELD), None
            )
            if question_key is None:
                return VerificationResult(False, len(source_rows), len(source_rows))
            expected = {
                (QUESTION_HASH_FIELD if key == question_key else key): (
                    stable_identifier(value or "") if key == question_key else value
                )
                for key, value in original.items()
            }
            if expected != sanitized:
                return VerificationResult(False, len(source_rows), len(source_rows))
        return VerificationResult(True, len(source_rows), len(source_rows))
    raise ValueError(f"unsupported artifact type: {suffix}")
