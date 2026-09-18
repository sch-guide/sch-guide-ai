"""Hash-only local human review contract for Groq case UAT-T01."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

CASE_ID = "UAT-T01"
DECISIONS = ("PASS", "FAIL", "UNCERTAIN")
UNSUPPORTED_CLAIM_DECISIONS = ("NONE", "PRESENT", "UNCERTAIN")
SCHEMA_VERSION = 1
RECORD_KEYS = {
    "schema_version",
    "case_id",
    "reviewer",
    "reviewed_at",
    "generated_answer_sha256",
    "evidence_sha256",
    "critical_fact_decision",
    "condition_decision",
    "action_steps_decision",
    "unsupported_clinical_claim_decision",
    "failure_categories",
    "human_semantic_support_approved",
}


def _validate_sha256(value: Any, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field}_invalid")
    return value


def _failure_categories(
    *,
    critical_fact_decision: str,
    condition_decision: str,
    action_steps_decision: str,
    unsupported_clinical_claim_decision: str,
) -> list[str]:
    failures: list[str] = []
    for prefix, decision in (
        ("critical_fact", critical_fact_decision),
        ("condition", condition_decision),
        ("action_steps", action_steps_decision),
    ):
        if decision != "PASS":
            failures.append(f"{prefix}_{decision.casefold()}")
    if unsupported_clinical_claim_decision != "NONE":
        failures.append(
            "unsupported_clinical_claim_"
            + unsupported_clinical_claim_decision.casefold()
        )
    return failures


def build_human_review_record(
    *,
    case_id: str,
    reviewer: str,
    generated_answer_sha256: str,
    evidence_sha256: str,
    critical_fact_decision: str,
    condition_decision: str,
    action_steps_decision: str,
    unsupported_clinical_claim_decision: str,
    reviewed_at: str | None = None,
) -> dict[str, Any]:
    """Build a closed review record without answer, evidence, prompt, or notes."""
    if case_id != CASE_ID:
        raise ValueError("case_id_not_allowed")
    reviewer_value = reviewer.strip()
    if not reviewer_value:
        raise ValueError("reviewer_required")
    for decision in (
        critical_fact_decision,
        condition_decision,
        action_steps_decision,
    ):
        if decision not in DECISIONS:
            raise ValueError("decision_not_allowed")
    if unsupported_clinical_claim_decision not in UNSUPPORTED_CLAIM_DECISIONS:
        raise ValueError("unsupported_claim_decision_not_allowed")
    answer_hash = _validate_sha256(
        generated_answer_sha256, field="generated_answer_sha256"
    )
    evidence_hash = _validate_sha256(evidence_sha256, field="evidence_sha256")
    failures = _failure_categories(
        critical_fact_decision=critical_fact_decision,
        condition_decision=condition_decision,
        action_steps_decision=action_steps_decision,
        unsupported_clinical_claim_decision=unsupported_clinical_claim_decision,
    )
    timestamp = reviewed_at or datetime.now().astimezone().isoformat(
        timespec="seconds"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": CASE_ID,
        "reviewer": reviewer_value,
        "reviewed_at": timestamp,
        "generated_answer_sha256": answer_hash,
        "evidence_sha256": evidence_hash,
        "critical_fact_decision": critical_fact_decision,
        "condition_decision": condition_decision,
        "action_steps_decision": action_steps_decision,
        "unsupported_clinical_claim_decision": unsupported_clinical_claim_decision,
        "failure_categories": failures,
        "human_semantic_support_approved": not failures,
    }


def validate_human_review_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Reject records containing extra fields or inconsistent approval state."""
    if set(record) != RECORD_KEYS:
        raise ValueError("review_record_fields")
    if record.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("review_schema_version")
    expected = build_human_review_record(
        case_id=str(record.get("case_id", "")),
        reviewer=str(record.get("reviewer", "")),
        generated_answer_sha256=str(record.get("generated_answer_sha256", "")),
        evidence_sha256=str(record.get("evidence_sha256", "")),
        critical_fact_decision=str(record.get("critical_fact_decision", "")),
        condition_decision=str(record.get("condition_decision", "")),
        action_steps_decision=str(record.get("action_steps_decision", "")),
        unsupported_clinical_claim_decision=str(
            record.get("unsupported_clinical_claim_decision", "")
        ),
        reviewed_at=str(record.get("reviewed_at", "")),
    )
    if dict(record) != expected:
        raise ValueError("review_record_inconsistent")
    return expected


def save_human_review_record(path: Path, record: Mapping[str, Any]) -> None:
    """Atomically overwrite the local hash-only review result."""
    validated = validate_human_review_record(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(validated, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_human_review_record(path: Path) -> dict[str, Any] | None:
    """Load an existing local decision without any display content."""
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("review_record_shape")
    return validate_human_review_record(payload)
