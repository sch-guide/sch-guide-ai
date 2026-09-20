from __future__ import annotations

import json

from src.evidence import SourceUnit
from tools.controlled_generation_diagnostics import diagnose_controlled_candidate
from tools.groq_live_ragas_evaluate import PreparedLiveCase


def _unit(identifier: str, text: str) -> SourceUnit:
    return SourceUnit(
        source_unit_id=identifier,
        chunk_id=f"internal-{identifier}",
        source_order=(1, 0),
        branch="common",
        exact_text=text,
        group_key="internal-group",
        required=True,
        selectable=True,
    )


def _case() -> PreparedLiveCase:
    return PreparedLiveCase(
        case_id="UAT-T01",
        intent="procedure",
        document_scope="internal-scope",
        evidence_type="text",
        units=(_unit("su001", "15분 동안 확인한다."),),
        critical={
            "critical_facts": (),
            "critical_numbers": ("15",),
            "critical_units": ("분",),
            "critical_times": ("15분",),
            "critical_conditions": (),
            "critical_negations": (),
            "critical_steps": (),
        },
        context_precision=1.0,
        context_recall=1.0,
    )


def _content(text: str, source_ids: list[str] | None = None) -> str:
    return json.dumps(
        {
            "statements": [
                {
                    "text": text,
                    "supporting_source_unit_ids": source_ids or ["su001"],
                }
            ]
        },
        ensure_ascii=False,
    )


def _action_case(*units: SourceUnit) -> PreparedLiveCase:
    return PreparedLiveCase(
        case_id="ACTION-001",
        intent="procedure",
        document_scope="internal-scope",
        evidence_type="text",
        units=units,
        critical={
            "critical_facts": (),
            "critical_numbers": (),
            "critical_units": (),
            "critical_times": (),
            "critical_conditions": (),
            "critical_negations": (),
            "critical_steps": (),
        },
        context_precision=1.0,
        context_recall=1.0,
    )


def test_diagnostic_rejects_action_change_before_existing_coverage_checks() -> None:
    result = diagnose_controlled_candidate(
        case=_action_case(_unit("su001", "검사를 시행한다.")),
        content=_content("검사를 확인한다."),
        http_response_received=True,
    )

    statuses = {stage.name: stage.status for stage in result.stages}
    assert result.first_failure_stage == "evaluation_action_strength_validation"
    assert result.first_failure_validator == "evaluation_action_strength"
    assert result.failure_statement_index == 0
    assert result.error_code == "evaluation_action_kind_changed"
    assert statuses["supporting_source_unit_id_validation"] == "PASS"
    assert statuses["evaluation_action_strength_validation"] == "FAIL"
    assert statuses["citation_support_validation"] == "NOT_RUN"
    assert all(
        stage.status == "NOT_RUN"
        for stage in result.stages
        if result.stages.index(stage)
        > next(
            index
            for index, value in enumerate(result.stages)
            if value.name == "evaluation_action_strength_validation"
        )
    )


def test_diagnostic_does_not_borrow_an_action_from_another_statement_source() -> None:
    first = _unit("su001", "검사를 시행한다.")
    second = _unit("su002", "결과를 확인한다.")
    content = json.dumps(
        {
            "statements": [
                {
                    "text": "검사를 확인한다.",
                    "supporting_source_unit_ids": ["su001"],
                },
                {
                    "text": "결과를 확인한다.",
                    "supporting_source_unit_ids": ["su002"],
                },
            ]
        },
        ensure_ascii=False,
    )

    result = diagnose_controlled_candidate(
        case=_action_case(first, second),
        content=content,
        http_response_received=True,
    )

    assert result.first_failure_stage == "evaluation_action_strength_validation"
    assert result.failure_statement_index == 0
    assert result.related_source_unit_ids == ("su001",)


def test_diagnostic_records_first_statement_failure_and_not_run_tail() -> None:
    result = diagnose_controlled_candidate(
        case=_case(),
        content=_content("30분 동안 확인한다."),
        http_response_received=True,
    )

    assert result.first_failure_stage == "number_validation"
    assert result.first_failure_validator == "number"
    assert result.failure_statement_index == 0
    assert result.error_code == "unsupported_number_or_unit"
    assert result.statements[0].supporting_source_unit_ids == ("su001",)
    statuses = {stage.name: stage.status for stage in result.stages}
    assert statuses["http_response_received"] == "PASS"
    assert statuses["json_decode"] == "PASS"
    assert statuses["structured_schema_validation"] == "PASS"
    assert statuses["number_validation"] == "FAIL"
    assert statuses["unit_validation"] == "NOT_RUN"
    assert statuses["final_candidate_validation"] == "NOT_RUN"


def test_diagnostic_separates_json_and_structured_schema_failures() -> None:
    decoded = diagnose_controlled_candidate(
        case=_case(),
        content=json.dumps({"statements": []}),
        http_response_received=True,
    )

    assert decoded.first_failure_stage == "structured_schema_validation"
    assert decoded.error_code == "controlled_schema"
    assert decoded.statement_count == 0
    assert decoded.stages[1].status == "PASS"
    assert decoded.stages[2].status == "FAIL"
    assert all(stage.status == "NOT_RUN" for stage in decoded.stages[3:])

    malformed = diagnose_controlled_candidate(
        case=_case(),
        content="not-json",
        http_response_received=True,
    )
    assert malformed.first_failure_stage == "json_decode"
    assert malformed.error_code == "provider_json"
    assert malformed.stages[1].status == "FAIL"
    assert all(stage.status == "NOT_RUN" for stage in malformed.stages[2:])


def test_diagnostic_reports_invalid_request_local_source_id() -> None:
    result = diagnose_controlled_candidate(
        case=_case(),
        content=_content("15분 동안 확인한다.", ["su999"]),
        http_response_received=True,
    )

    assert result.first_failure_stage == "supporting_source_unit_id_validation"
    assert result.first_failure_validator == "source_unit_id"
    assert result.failure_statement_index == 0
    assert result.related_source_unit_ids == ("su999",)
    assert result.error_code == "unknown_evidence_id"
    assert result.stages[3].status == "FAIL"
    assert all(stage.status == "NOT_RUN" for stage in result.stages[4:])


def test_diagnostic_passes_all_steps_without_publishing_candidate() -> None:
    result = diagnose_controlled_candidate(
        case=_case(),
        content=_content("15분 동안 확인한다."),
        http_response_received=True,
    )

    assert result.first_failure_stage is None
    assert result.error_code is None
    assert result.final_validator_pass is True
    assert result.fallback_to_extractive is True
    assert result.publish_controlled is False
    assert all(stage.status == "PASS" for stage in result.stages)


def test_diagnostic_preserves_existing_source_order_duplicate_rejection() -> None:
    content = json.dumps(
        {
            "statements": [
                {
                    "text": "15분 동안 확인한다.",
                    "supporting_source_unit_ids": ["su001"],
                },
                {
                    "text": "15분 동안 확인한다.",
                    "supporting_source_unit_ids": ["su001"],
                },
            ]
        },
        ensure_ascii=False,
    )

    result = diagnose_controlled_candidate(
        case=_case(), content=content, http_response_received=True
    )

    assert result.first_failure_stage == "action_source_order_validation"
    assert result.error_code == "source_order"
    statuses = {stage.name: stage.status for stage in result.stages}
    assert statuses["action_source_order_validation"] == "FAIL"
    failed_index = next(
        index
        for index, stage in enumerate(result.stages)
        if stage.name == "action_source_order_validation"
    )
    assert all(stage.status == "NOT_RUN" for stage in result.stages[failed_index + 1 :])
