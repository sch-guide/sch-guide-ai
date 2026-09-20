"""Local-only projection of failed required-coverage diagnostics for review."""

from __future__ import annotations

from typing import Any, Mapping

from tools.controlled_generation_review import (
    CoverageReviewSourceSlot,
    build_coverage_review_slot,
)
from tools.groq_live_ragas_evaluate import _semantic_tokens


def project_failed_coverage_slot(
    *, case: Any, diagnostic: Mapping[str, Any]
) -> CoverageReviewSourceSlot | None:
    """Build display-only text for the first failed required coverage slot."""
    error_code = diagnostic.get("error_code")
    prefix = "required_coverage:"
    if not isinstance(error_code, str) or not error_code.startswith(prefix):
        return None
    slot_id = error_code.removeprefix(prefix)
    slot = next(
        (value for value in case.required_coverage if value.slot_id == slot_id),
        None,
    )
    if slot is None:
        raise ValueError("coverage_slot_missing")
    by_id = {unit.source_unit_id: unit for unit in case.units}
    try:
        source_text = "\n\n".join(
            by_id[identifier].exact_text
            for identifier in slot.supporting_source_unit_ids
        )
    except KeyError as exc:
        raise ValueError("coverage_source_unit_missing") from exc
    related: list[str] = []
    for statement in diagnostic.get("statements", ()):
        if not isinstance(statement, Mapping):
            raise ValueError("coverage_diagnostic_statement")
        identifiers = statement.get("supporting_source_unit_ids")
        text = statement.get("text")
        if not isinstance(identifiers, list) or not isinstance(text, str):
            raise ValueError("coverage_diagnostic_statement")
        if set(identifiers) & set(slot.supporting_source_unit_ids):
            related.append(text)
    if not related:
        raise ValueError("coverage_candidate_statement_missing")
    candidate_statement = "\n\n".join(related)
    required_tokens = _semantic_tokens(slot.requirement)
    candidate_tokens = _semantic_tokens(candidate_statement)
    token_coverage = (
        len(required_tokens & candidate_tokens) / len(required_tokens)
        if required_tokens
        else 0.0
    )
    return build_coverage_review_slot(
        case_id=case.case_id,
        slot_id=slot.slot_id,
        supporting_source_unit_ids=slot.supporting_source_unit_ids,
        source_text=source_text,
        candidate_statement=candidate_statement,
        token_coverage=token_coverage,
    )
