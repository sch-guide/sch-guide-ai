"""Evaluation-only, raw-free stage diagnostics for controlled generation.

This module does not publish candidates and does not replace or relax the
existing validator.  It records which validation stage was actually reached,
then invokes the unchanged final controlled-generation decision.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Literal, Sequence

from pydantic import ValidationError

from src.controlled_generation import (
    _CONDITION,
    _EXCEPTION,
    _NEGATION,
    ControlledResponsePayload,
    _condition_case_targets,
    _marker_state,
    _normalized_numeric_tokens,
    decide_controlled_generation,
)
from tools.evaluation_action_strength import validate_action_strength
from tools.groq_live_ragas_evaluate import (
    PreparedLiveCase,
    _field_pass,
    _phase_scope_pass,
    _requirement_present,
)

StageStatus = Literal["PASS", "FAIL", "NOT_RUN"]

STAGE_NAMES = (
    "http_response_received",
    "json_decode",
    "structured_schema_validation",
    "supporting_source_unit_id_validation",
    "evaluation_action_strength_validation",
    "citation_support_validation",
    "answer_coverage_validation",
    "number_validation",
    "unit_validation",
    "time_validation",
    "condition_exception_validation",
    "negation_prohibition_validation",
    "action_source_order_validation",
    "branch_phase_validation",
    "phase_scope_validation",
    "final_candidate_validation",
)


@dataclass(frozen=True)
class DiagnosticStage:
    name: str
    status: StageStatus
    error_code: str | None = None
    statement_index: int | None = None
    supporting_source_unit_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiagnosticStatement:
    index: int
    text: str
    supporting_source_unit_ids: tuple[str, ...]


@dataclass(frozen=True)
class ControlledCandidateDiagnostic:
    case_id: str
    candidate_sha256: str
    statement_count: int
    statements: tuple[DiagnosticStatement, ...]
    stages: tuple[DiagnosticStage, ...]
    first_failure_stage: str | None
    first_failure_validator: str | None
    failure_statement_index: int | None
    related_source_unit_ids: tuple[str, ...]
    error_code: str | None
    final_validator_pass: bool
    fallback_to_extractive: bool
    publish_controlled: bool


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _number_parts(tokens: set[str]) -> set[str]:
    return {
        match.group(0)
        for token in tokens
        if (match := re.match(r"\d+(?:\.\d+)?", token)) is not None
    }


def _all_source_ids(statements: Sequence[DiagnosticStatement]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            identifier
            for statement in statements
            for identifier in statement.supporting_source_unit_ids
        )
    )


def _validator_name(stage: str) -> str:
    return {
        "http_response_received": "http",
        "json_decode": "json_decode",
        "structured_schema_validation": "structured_schema",
        "supporting_source_unit_id_validation": "source_unit_id",
        "evaluation_action_strength_validation": "evaluation_action_strength",
        "citation_support_validation": "citation_support",
        "answer_coverage_validation": "answer_coverage",
        "number_validation": "number",
        "unit_validation": "unit",
        "time_validation": "time",
        "condition_exception_validation": "condition_exception",
        "negation_prohibition_validation": "negation_prohibition",
        "action_source_order_validation": "action_source_order",
        "branch_phase_validation": "branch_phase",
        "phase_scope_validation": "phase_scope",
        "final_candidate_validation": "final_candidate",
    }[stage]


def diagnose_controlled_candidate(
    *,
    case: PreparedLiveCase,
    content: str,
    http_response_received: bool,
) -> ControlledCandidateDiagnostic:
    """Record the first reached failure without changing validator behavior."""
    stage_rows = {
        name: DiagnosticStage(name=name, status="NOT_RUN") for name in STAGE_NAMES
    }
    statements: tuple[DiagnosticStatement, ...] = ()

    def mark_pass(name: str) -> None:
        stage_rows[name] = DiagnosticStage(name=name, status="PASS")

    def finish_failure(
        name: str,
        code: str,
        *,
        statement_index: int | None = None,
        source_ids: Sequence[str] = (),
    ) -> ControlledCandidateDiagnostic:
        related = tuple(source_ids)
        stage_rows[name] = DiagnosticStage(
            name=name,
            status="FAIL",
            error_code=code,
            statement_index=statement_index,
            supporting_source_unit_ids=related,
        )
        return ControlledCandidateDiagnostic(
            case_id=case.case_id,
            candidate_sha256=_sha256_text(content),
            statement_count=len(statements),
            statements=statements,
            stages=tuple(stage_rows[value] for value in STAGE_NAMES),
            first_failure_stage=name,
            first_failure_validator=_validator_name(name),
            failure_statement_index=statement_index,
            related_source_unit_ids=related,
            error_code=code,
            final_validator_pass=False,
            fallback_to_extractive=True,
            publish_controlled=False,
        )

    if not http_response_received:
        return finish_failure("http_response_received", "http_response_missing")
    mark_pass("http_response_received")

    try:
        raw = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return finish_failure("json_decode", "provider_json")
    mark_pass("json_decode")

    try:
        parsed = ControlledResponsePayload.model_validate(raw)
    except (ValidationError, TypeError):
        return finish_failure("structured_schema_validation", "controlled_schema")
    statements = tuple(
        DiagnosticStatement(
            index=index,
            text=value.text,
            supporting_source_unit_ids=tuple(value.supporting_source_unit_ids),
        )
        for index, value in enumerate(parsed.statements)
    )
    mark_pass("structured_schema_validation")

    by_id = {unit.source_unit_id: unit for unit in case.units}
    if not by_id or len(by_id) != len(case.units):
        return finish_failure(
            "supporting_source_unit_id_validation", "invalid_evidence_catalog"
        )
    for statement in statements:
        identifiers = statement.supporting_source_unit_ids
        if len(identifiers) != len(set(identifiers)):
            return finish_failure(
                "supporting_source_unit_id_validation",
                "duplicate_evidence_id",
                statement_index=statement.index,
                source_ids=identifiers,
            )
        if any(identifier not in by_id for identifier in identifiers):
            return finish_failure(
                "supporting_source_unit_id_validation",
                "unknown_evidence_id",
                statement_index=statement.index,
                source_ids=identifiers,
            )
    mark_pass("supporting_source_unit_id_validation")

    for statement in statements:
        action_result = validate_action_strength(
            statement_text=statement.text,
            cited_sources={
                identifier: by_id[identifier].exact_text
                for identifier in statement.supporting_source_unit_ids
            },
        )
        if not action_result.passed:
            return finish_failure(
                "evaluation_action_strength_validation",
                action_result.error_code or "evaluation_action_strength_changed",
                statement_index=statement.index,
                source_ids=action_result.related_source_unit_ids,
            )
    mark_pass("evaluation_action_strength_validation")

    generated_text = " ".join(statement.text for statement in statements)
    all_source_ids = _all_source_ids(statements)
    if not _field_pass(
        case.critical, "critical_facts", generated_text, semantic=True
    ):
        return finish_failure(
            "citation_support_validation",
            "critical_fact_preservation",
            source_ids=all_source_ids,
        )
    mark_pass("citation_support_validation")

    if set(all_source_ids) != set(by_id):
        return finish_failure(
            "answer_coverage_validation",
            "evidence_coverage",
            source_ids=all_source_ids,
        )
    for slot in case.required_coverage:
        linked_text = " ".join(
            statement.text
            for statement in statements
            if set(statement.supporting_source_unit_ids)
            & set(slot.supporting_source_unit_ids)
        )
        if not _requirement_present(slot.requirement, linked_text, semantic=True):
            return finish_failure(
                "answer_coverage_validation",
                f"required_coverage:{slot.slot_id}",
                source_ids=slot.supporting_source_unit_ids,
            )
    mark_pass("answer_coverage_validation")

    unsupported_by_statement: dict[int, set[str]] = {}
    for statement in statements:
        source_text = " ".join(
            by_id[identifier].exact_text
            for identifier in statement.supporting_source_unit_ids
        )
        candidate_tokens = _normalized_numeric_tokens(statement.text)
        source_tokens = _normalized_numeric_tokens(source_text)
        unsupported = candidate_tokens - source_tokens
        unsupported_by_statement[statement.index] = unsupported
        if unsupported and not _number_parts(unsupported).issubset(
            _number_parts(source_tokens)
        ):
            return finish_failure(
                "number_validation",
                "unsupported_number_or_unit",
                statement_index=statement.index,
                source_ids=statement.supporting_source_unit_ids,
            )
    if not _field_pass(
        case.critical, "critical_numbers", generated_text, semantic=False
    ):
        return finish_failure(
            "number_validation",
            "critical_number_preservation",
            source_ids=all_source_ids,
        )
    mark_pass("number_validation")

    for statement in statements:
        if unsupported_by_statement[statement.index]:
            return finish_failure(
                "unit_validation",
                "unsupported_number_or_unit",
                statement_index=statement.index,
                source_ids=statement.supporting_source_unit_ids,
            )
    if not _field_pass(
        case.critical, "critical_units", generated_text, semantic=False
    ):
        return finish_failure(
            "unit_validation",
            "critical_unit_preservation",
            source_ids=all_source_ids,
        )
    mark_pass("unit_validation")

    if not _field_pass(case.critical, "critical_times", generated_text, semantic=True):
        return finish_failure(
            "time_validation",
            "critical_time_preservation",
            source_ids=all_source_ids,
        )
    mark_pass("time_validation")

    for statement in statements:
        source_text = " ".join(
            by_id[identifier].exact_text
            for identifier in statement.supporting_source_unit_ids
        )
        condition_changed = (
            _marker_state(_EXCEPTION, statement.text)
            != _marker_state(_EXCEPTION, source_text)
            or _condition_case_targets(statement.text)
            != _condition_case_targets(source_text)
        )
        condition_changed = condition_changed or (
            _marker_state(_CONDITION, statement.text)
            != _marker_state(_CONDITION, source_text)
        )
        if condition_changed:
            return finish_failure(
                "condition_exception_validation",
                "condition_changed",
                statement_index=statement.index,
                source_ids=statement.supporting_source_unit_ids,
            )
    if not _field_pass(
        case.critical, "critical_conditions", generated_text, semantic=True
    ):
        return finish_failure(
            "condition_exception_validation",
            "critical_condition_preservation",
            source_ids=all_source_ids,
        )
    mark_pass("condition_exception_validation")

    for statement in statements:
        source_text = " ".join(
            by_id[identifier].exact_text
            for identifier in statement.supporting_source_unit_ids
        )
        if _marker_state(_NEGATION, statement.text) != _marker_state(
            _NEGATION, source_text
        ):
            return finish_failure(
                "negation_prohibition_validation",
                "negation_changed",
                statement_index=statement.index,
                source_ids=statement.supporting_source_unit_ids,
            )
    if not _field_pass(
        case.critical, "critical_negations", generated_text, semantic=True
    ):
        return finish_failure(
            "negation_prohibition_validation",
            "critical_negation_preservation",
            source_ids=all_source_ids,
        )
    mark_pass("negation_prohibition_validation")

    if not _field_pass(case.critical, "critical_steps", generated_text, semantic=True):
        return finish_failure(
            "action_source_order_validation",
            "critical_action_preservation",
            source_ids=all_source_ids,
        )
    source_order = {unit.source_unit_id: unit.source_order for unit in case.units}
    cited_ids = [
        identifier
        for statement in statements
        for identifier in statement.supporting_source_unit_ids
    ]
    cited_order = [source_order[identifier] for identifier in cited_ids]
    if len(cited_ids) != len(set(cited_ids)) or not all(
        left < right for left, right in zip(cited_order, cited_order[1:])
    ):
        return finish_failure(
            "action_source_order_validation",
            "source_order",
            source_ids=all_source_ids,
        )
    mark_pass("action_source_order_validation")

    for statement in statements:
        cited = tuple(by_id[value] for value in statement.supporting_source_unit_ids)
        branches = {unit.branch for unit in cited if unit.branch in {"adult", "pediatric"}}
        if len(branches) > 1:
            return finish_failure(
                "branch_phase_validation",
                "branch_mixing",
                statement_index=statement.index,
                source_ids=statement.supporting_source_unit_ids,
            )
        phases = {
            unit.phase for unit in cited if unit.phase not in {"", "unspecified"}
        }
        if len(phases) > 1:
            return finish_failure(
                "branch_phase_validation",
                "phase_mixing",
                statement_index=statement.index,
                source_ids=statement.supporting_source_unit_ids,
            )
    mark_pass("branch_phase_validation")

    phase_projection = SimpleNamespace(
        statements=tuple(SimpleNamespace(text=value.text) for value in statements)
    )
    if not _phase_scope_pass(phase_projection, case.requested_phase):
        return finish_failure(
            "phase_scope_validation",
            "phase_scope",
            source_ids=all_source_ids,
        )
    mark_pass("phase_scope_validation")

    decision = decide_controlled_generation(
        content,
        case.units,
        intent=case.intent,
        extractive_answer={"kind": "verified_extractive_fallback"},
    )
    if decision.validated_candidate is None:
        return finish_failure(
            "final_candidate_validation",
            decision.reason,
            source_ids=all_source_ids,
        )
    mark_pass("final_candidate_validation")
    return ControlledCandidateDiagnostic(
        case_id=case.case_id,
        candidate_sha256=_sha256_text(content),
        statement_count=len(statements),
        statements=statements,
        stages=tuple(stage_rows[value] for value in STAGE_NAMES),
        first_failure_stage=None,
        first_failure_validator=None,
        failure_statement_index=None,
        related_source_unit_ids=(),
        error_code=None,
        final_validator_pass=True,
        fallback_to_extractive=decision.fallback_to_extractive,
        publish_controlled=decision.publish_controlled,
    )
