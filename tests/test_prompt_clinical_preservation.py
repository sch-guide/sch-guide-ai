from __future__ import annotations

import ast
import json
import socket
from pathlib import Path

import pytest
import yaml

from src.controlled_generation import (
    ControlledGenerationError,
    validate_controlled_paraphrase,
)
from src.evidence import SourceUnit
from src.prompt_config import load_evaluation_prompt
from tools.controlled_generation_diagnostics import diagnose_controlled_candidate
from tools.evaluation_action_strength import validate_action_strength
from tools.groq_live_ragas_evaluate import PreparedLiveCase, RequiredCoverageSlot
from tools.provider_controlled_generation_evaluate import build_provider_blueprints

EXPECTED_SCHEMA_SHA256 = (
    "973452ab0730167972ae78b923952d7b0118ada88ebb610684b4883d2afa1889"
)


def _unit(identifier: str, text: str, *, order: int = 1) -> SourceUnit:
    return SourceUnit(
        source_unit_id=identifier,
        chunk_id=f"private-{identifier}",
        source_order=(order, 0),
        branch="common",
        exact_text=text,
        group_key=f"private-group-{identifier}",
        required=True,
        selectable=True,
        phase="unspecified",
    )


def _content(*statements: tuple[str, list[str]]) -> str:
    return json.dumps(
        {
            "statements": [
                {"text": text, "supporting_source_unit_ids": source_ids}
                for text, source_ids in statements
            ]
        },
        ensure_ascii=False,
    )


def test_prompt_contract_prioritizes_clinical_preservation_over_naturalness() -> None:
    config = load_evaluation_prompt()

    assert "행위 종류" in config.instruction
    assert "행위 강도" in config.instruction
    assert "대상·제제·행위와 결합된 하나의 사실" in config.instruction
    assert "서로 다른 제제나 SourceUnit" in config.instruction
    assert "전제와 결론 행위를 모두 보존" in config.instruction
    assert "같은 제제라도 서로 다른 조건" in config.instruction
    assert "자연스러움보다 임상 의미와 필수 coverage 보존을 우선" in config.instruction

    coverage = config.runtime_templates["required_coverage_instruction"]
    assert "행위 종류와 강도" in coverage
    assert "숫자·단위·시간·속도·빈도·간격" in coverage
    assert "서로 다른 제제 또는 SourceUnit" in coverage
    assert "결론 행위를 생략하지 않는다" in coverage
    assert "같은 제제의 수치라도 서로 다른 조건" in coverage

    assert len(config.safety_rules) == 12
    assert "대상·제제·행위와 결합된 하나의 사실" in config.safety_rules[2]
    assert "자연스러움보다 임상 의미와 필수 AnswerCoverage 보존" in config.safety_rules[8]
    assert "행위 종류" in config.safety_rules[9]
    assert "행위 강도" in config.safety_rules[9]
    assert "전제와 결론 행위" in config.safety_rules[7]


def test_prompt_change_keeps_output_schema_and_evaluation_only_contract() -> None:
    unit = _unit("su001", "15분 간격으로 상태를 확인한다.")
    blueprint = build_provider_blueprints(
        case_id="PROMPT-SAFETY-001",
        intent="summary",
        units=(unit,),
        groq_model="openai/gpt-oss-20b",
        gemini_model="gemini-2.5-flash",
    )[0]
    config = load_evaluation_prompt()
    raw_config = yaml.safe_load(
        Path("src/config/prompts/v1.0-natural-grounded.yaml").read_text(
            encoding="utf-8"
        )
    )

    assert blueprint.schema_fingerprint == EXPECTED_SCHEMA_SHA256
    assert config.publish_controlled is False
    assert config.semantic_support_status == "pending"
    assert raw_config["publication"]["fallback_to_extractive"] is True
    assert raw_config["publication"]["retry_count"] == 0


@pytest.mark.parametrize(
    ("source", "candidate", "expected"),
    [
        ("검사를 시행한다.", "검사를 수행한다.", True),
        ("검사를 시행한다.", "검사를 확인한다.", False),
        ("즉시 중단한다.", "중단을 고려한다.", False),
        ("반드시 검사를 시행한다.", "검사 시행을 권고한다.", False),
        ("사용을 금지한다.", "사용에 주의한다.", False),
    ],
)
def test_prompt_mock_action_kind_and_strength_safety_net(
    source: str, candidate: str, expected: bool
) -> None:
    result = validate_action_strength(
        statement_text=candidate,
        cited_sources={"su001": source},
    )

    assert result.passed is expected


def test_numeric_time_and_rate_remain_bound_to_each_source_unit() -> None:
    units = (
        _unit("su001", "적혈구제제는 15분 동안 50 mL/hr로 투여한다."),
        _unit("su002", "혈소판제제는 30분 동안 100 mL/hr로 투여한다.", order=2),
    )
    exact = _content(
        ("적혈구제제는 15분 동안 50 mL/hr로 투여한다.", ["su001"]),
        ("혈소판제제는 30분 동안 100 mL/hr로 투여한다.", ["su002"]),
    )
    mixed = _content(
        ("적혈구제제는 30분 동안 100 mL/hr로 투여한다.", ["su001"]),
        ("혈소판제제는 15분 동안 50 mL/hr로 투여한다.", ["su002"]),
    )

    assert validate_controlled_paraphrase(exact, units, intent="procedure")
    with pytest.raises(ControlledGenerationError, match="unsupported_number_or_unit"):
        validate_controlled_paraphrase(mixed, units, intent="procedure")


def test_required_coverage_rejects_partial_time_rate_omission() -> None:
    unit = _unit("su001", "적혈구제제는 15분 동안 50 mL/hr로 투여한다.")
    case = PreparedLiveCase(
        case_id="PROMPT-SAFETY-002",
        intent="procedure",
        document_scope="private-scope",
        evidence_type="text",
        units=(unit,),
        critical={
            "critical_facts": (),
            "critical_numbers": ("15", "50"),
            "critical_units": ("분", "mL/hr"),
            "critical_times": ("15분",),
            "critical_conditions": (),
            "critical_negations": (),
            "critical_steps": (),
        },
        context_precision=1.0,
        context_recall=1.0,
        required_coverage=(
            RequiredCoverageSlot(
                slot_id="numeric_relation_001",
                category="numeric_relation",
                requirement="적혈구제제는 15분 동안 50 mL/hr로 투여한다.",
                supporting_source_unit_ids=("su001",),
            ),
        ),
    )

    result = diagnose_controlled_candidate(
        case=case,
        content=_content(("적혈구제제는 15분 동안 투여한다.", ["su001"])),
        http_response_received=True,
    )

    assert result.final_validator_pass is False
    assert result.first_failure_stage == "answer_coverage_validation"
    assert result.error_code == "required_coverage:numeric_relation_001"
    assert result.fallback_to_extractive is True


def test_prompt_safety_net_has_no_production_import_or_network_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket, "create_connection", forbidden_network)
    assert validate_action_strength(
        statement_text="검사를 수행한다.",
        cited_sources={"su001": "검사를 시행한다."},
    ).passed

    imported_modules: set[str] = set()
    for path in (*Path("src").rglob("*.py"), Path("streamlit_app.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)

    assert "tools.evaluation_action_strength" not in imported_modules
