"""Local-only human semantic review contract for Groq case UAT-S01.

Raw generated content and SourceUnit evidence remain in memory.  The only
persisted record is a reviewer decision plus cryptographic hashes.
"""

from __future__ import annotations

import json
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

CASE_ID = "UAT-S01"
DECISIONS = ("PASS", "FAIL", "UNCERTAIN")
REVIEW_SCHEMA_VERSION = 1
REVIEW_KEYS = {
    "schema_version",
    "case_id",
    "decision",
    "reviewer",
    "reviewed_at",
    "generated_answer_sha256",
    "evidence_sha256",
    "human_semantic_support_approved",
}
SNAPSHOT_SCHEMA_VERSION = 1
SNAPSHOT_SAFETY_FIELDS = (
    "schema_pass",
    "citation_pass",
    "number_pass",
    "unit_pass",
    "time_pass",
    "condition_pass",
    "negation_pass",
    "action_pass",
)
SNAPSHOT_KEYS = {
    "schema_version",
    "case_id",
    "provider",
    "model",
    "reviewer",
    "reviewed_at",
    "generated_answer_sha256",
    "evidence_sha256",
    "http_status",
    "provider_latency_ms",
    "deterministic_critical_fact_preservation",
    "critical_fact_preservation",
    "human_semantic_support_approved",
    "branch_phase_pass",
    "unsupported_clinical_claim_pass",
    *SNAPSHOT_SAFETY_FIELDS,
}


def generated_answer_sha256(generated_content: str) -> str:
    """Hash the normalized provider content without persisting it."""
    if not isinstance(generated_content, str) or not generated_content:
        raise ValueError("generated_content_required")
    return sha256(generated_content.encode("utf-8")).hexdigest()


def parse_generated_statements(generated_content: str) -> list[dict[str, Any]]:
    """Return validated statement/source mappings for in-memory display."""
    try:
        payload = json.loads(generated_content)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("generated_content_json") from exc
    statements = payload.get("statements") if isinstance(payload, Mapping) else None
    if not isinstance(statements, list) or not statements:
        raise ValueError("generated_statements_required")
    parsed: list[dict[str, Any]] = []
    for statement in statements:
        if not isinstance(statement, Mapping):
            raise ValueError("generated_statement_shape")
        text = statement.get("text")
        source_ids = statement.get("supporting_source_unit_ids")
        if (
            not isinstance(text, str)
            or not text.strip()
            or not isinstance(source_ids, list)
            or not source_ids
            or not all(isinstance(value, str) and value for value in source_ids)
        ):
            raise ValueError("generated_statement_shape")
        parsed.append(
            {
                "text": text.strip(),
                "supporting_source_unit_ids": list(source_ids),
            }
        )
    return parsed


def build_human_review_record(
    *,
    case_id: str,
    decision: str,
    reviewer: str,
    generated_content: str,
    evidence_sha256: str,
    reviewed_at: str | None = None,
) -> dict[str, Any]:
    """Build a metadata-only decision record for one exact answer/evidence pair."""
    if case_id != CASE_ID:
        raise ValueError("case_id_not_allowed")
    if decision not in DECISIONS:
        raise ValueError("decision_not_allowed")
    reviewer_value = reviewer.strip()
    if not reviewer_value:
        raise ValueError("reviewer_required")
    if (
        not isinstance(evidence_sha256, str)
        or len(evidence_sha256) != 64
        or any(character not in "0123456789abcdef" for character in evidence_sha256)
    ):
        raise ValueError("evidence_sha256_invalid")
    timestamp = reviewed_at or datetime.now().astimezone().isoformat(timespec="seconds")
    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "case_id": CASE_ID,
        "decision": decision,
        "reviewer": reviewer_value,
        "reviewed_at": timestamp,
        "generated_answer_sha256": generated_answer_sha256(generated_content),
        "evidence_sha256": evidence_sha256,
        "human_semantic_support_approved": decision == "PASS",
    }


def _validate_record(record: Mapping[str, Any]) -> dict[str, Any]:
    if set(record) != REVIEW_KEYS:
        raise ValueError("review_record_fields")
    if record.get("schema_version") != REVIEW_SCHEMA_VERSION:
        raise ValueError("review_schema_version")
    if record.get("case_id") != CASE_ID:
        raise ValueError("case_id_not_allowed")
    decision = record.get("decision")
    if decision not in DECISIONS:
        raise ValueError("decision_not_allowed")
    if not isinstance(record.get("reviewer"), str) or not record["reviewer"].strip():
        raise ValueError("reviewer_required")
    if not isinstance(record.get("reviewed_at"), str) or not record["reviewed_at"]:
        raise ValueError("reviewed_at_required")
    for field in ("generated_answer_sha256", "evidence_sha256"):
        value = record.get(field)
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError(f"{field}_invalid")
    approved = record.get("human_semantic_support_approved")
    if approved is not (decision == "PASS"):
        raise ValueError("human_semantic_support_approval_mismatch")
    return dict(record)


def save_human_review_record(path: Path, record: Mapping[str, Any]) -> None:
    """Atomically persist the hash-only record; never persist review display data."""
    validated = _validate_record(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(validated, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_human_review_record(path: Path) -> dict[str, Any] | None:
    """Load a metadata-only review, returning None when no review exists."""
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("review_record_shape")
    return _validate_record(payload)


def human_review_allows_subset_start(review: Mapping[str, Any] | None) -> bool:
    """Allow real subset execution only after an explicit hash-only PASS record."""
    if review is None:
        return False
    validated = _validate_record(review)
    return bool(
        validated["decision"] == "PASS"
        and validated["human_semantic_support_approved"] is True
    )


def build_reviewed_case_snapshot(
    *,
    review: Mapping[str, Any],
    call_record: Mapping[str, Any],
    safety: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a raw-free snapshot for the exact answer reviewed in memory."""
    validated_review = _validate_record(review)
    if not human_review_allows_subset_start(validated_review):
        raise ValueError("reviewed_snapshot_requires_pass")
    if call_record.get("case_id") != CASE_ID:
        raise ValueError("reviewed_snapshot_case")
    if (
        call_record.get("generated_answer_sha256")
        != validated_review["generated_answer_sha256"]
    ):
        raise ValueError("reviewed_answer_hash_mismatch")
    if call_record.get("evidence_sha256") != validated_review["evidence_sha256"]:
        raise ValueError("reviewed_evidence_hash_mismatch")
    if any(safety.get(field) is not True for field in SNAPSHOT_SAFETY_FIELDS):
        raise ValueError("reviewed_snapshot_safety_failed")
    deterministic = safety.get("critical_fact_preservation") is True
    allowed_failures = set() if deterministic else {"critical_fact_preservation"}
    if set(safety.get("failure_codes", ())) != allowed_failures:
        raise ValueError("reviewed_snapshot_failure_codes")
    latency = call_record.get("latency_ms")
    if not isinstance(latency, (int, float)) or latency < 0:
        raise ValueError("reviewed_snapshot_latency")
    snapshot = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "case_id": CASE_ID,
        "provider": str(call_record.get("provider", "")),
        "model": str(call_record.get("model", "")),
        "reviewer": validated_review["reviewer"],
        "reviewed_at": validated_review["reviewed_at"],
        "generated_answer_sha256": validated_review["generated_answer_sha256"],
        "evidence_sha256": validated_review["evidence_sha256"],
        "http_status": call_record.get("http_status"),
        "provider_latency_ms": round(float(latency), 3),
        "deterministic_critical_fact_preservation": deterministic,
        "critical_fact_preservation": True,
        "human_semantic_support_approved": True,
        "branch_phase_pass": True,
        "unsupported_clinical_claim_pass": True,
        **{field: True for field in SNAPSHOT_SAFETY_FIELDS},
    }
    return validate_reviewed_case_snapshot(snapshot)


def validate_reviewed_case_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the closed metadata-only reviewed-result contract."""
    if set(snapshot) != SNAPSHOT_KEYS:
        raise ValueError("reviewed_snapshot_fields")
    if snapshot.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError("reviewed_snapshot_schema_version")
    if snapshot.get("case_id") != CASE_ID:
        raise ValueError("reviewed_snapshot_case")
    for field in ("provider", "model", "reviewer", "reviewed_at"):
        if not isinstance(snapshot.get(field), str) or not snapshot[field].strip():
            raise ValueError(f"reviewed_snapshot_{field}")
    for field in ("generated_answer_sha256", "evidence_sha256"):
        value = snapshot.get(field)
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError(f"reviewed_snapshot_{field}")
    if snapshot.get("http_status") != 200:
        raise ValueError("reviewed_snapshot_http_status")
    latency = snapshot.get("provider_latency_ms")
    if not isinstance(latency, (int, float)) or latency < 0:
        raise ValueError("reviewed_snapshot_latency")
    boolean_fields = (
        "deterministic_critical_fact_preservation",
        "critical_fact_preservation",
        "human_semantic_support_approved",
        "branch_phase_pass",
        "unsupported_clinical_claim_pass",
        *SNAPSHOT_SAFETY_FIELDS,
    )
    if not isinstance(snapshot.get("deterministic_critical_fact_preservation"), bool):
        raise ValueError("reviewed_snapshot_deterministic_fact")
    for field in boolean_fields[1:]:
        if snapshot.get(field) is not True:
            raise ValueError(f"reviewed_snapshot_{field}")
    return dict(snapshot)


def save_reviewed_case_snapshot(path: Path, snapshot: Mapping[str, Any]) -> None:
    """Atomically persist only the closed raw-free reviewed-result snapshot."""
    validated = validate_reviewed_case_snapshot(snapshot)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(validated, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_reviewed_case_snapshot(path: Path) -> dict[str, Any] | None:
    """Load a raw-free reviewed result, or None when it has not been saved."""
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("reviewed_snapshot_shape")
    return validate_reviewed_case_snapshot(payload)


def apply_human_semantic_review(
    safety: Mapping[str, Any],
    *,
    case_id: str,
    generated_content: str,
    evidence_sha256: str,
    review: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Clear only critical-fact failure for an exact, human-approved hash match."""
    updated = dict(safety)
    updated["human_semantic_support_approved"] = False
    if review is None:
        return updated
    validated = _validate_record(review)
    matches = (
        validated["case_id"] == case_id
        and validated["decision"] == "PASS"
        and validated["human_semantic_support_approved"] is True
        and validated["generated_answer_sha256"]
        == generated_answer_sha256(generated_content)
        and validated["evidence_sha256"] == evidence_sha256
    )
    if not matches:
        return updated
    failures = [
        str(value)
        for value in updated.get("failure_codes", ())
        if value != "critical_fact_preservation"
    ]
    updated["critical_fact_preservation"] = True
    updated["semantic_support"] = "human_approved_exact_hash_match"
    updated["human_semantic_support_approved"] = True
    updated["failure_codes"] = failures
    updated["critical_safety_error_count"] = len(failures)
    return updated
