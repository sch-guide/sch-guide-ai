from __future__ import annotations

import ast
import socket
from dataclasses import asdict
from pathlib import Path

import pytest

from tools.evaluation_action_strength import validate_action_strength


@pytest.mark.parametrize(
    ("source", "candidate"),
    [
        ("검사를 시행한다.", "검사를 시행한다."),
        ("검사를 시행한다.", "검사를 수행한다."),
        ("확인한다.", "확인한다."),
        ("즉시 중단한다.", "즉시 투여를 중단한다."),
    ],
)
def test_equivalent_action_kind_and_strength_pass(
    source: str, candidate: str
) -> None:
    result = validate_action_strength(
        statement_text=candidate,
        cited_sources={"su001": source},
    )

    assert result.passed is True
    assert result.error_code is None


@pytest.mark.parametrize(
    ("source", "candidate", "error_code"),
    [
        ("검사를 시행한다.", "검사를 확인한다.", "evaluation_action_kind_changed"),
        (
            "즉시 중단한다.",
            "중단을 고려한다.",
            "evaluation_action_strength_changed",
        ),
        (
            "반드시 검사를 시행한다.",
            "검사 시행을 권고한다.",
            "evaluation_action_strength_changed",
        ),
        (
            "약물을 투여한다.",
            "약물 투여 여부를 검토한다.",
            "evaluation_action_strength_changed",
        ),
        ("사용을 금지한다.", "사용에 주의한다.", "evaluation_action_kind_changed"),
        (
            "중단한다.",
            "즉시 중단한다.",
            "evaluation_action_strength_changed",
        ),
    ],
)
def test_changed_action_kind_or_strength_fails(
    source: str, candidate: str, error_code: str
) -> None:
    result = validate_action_strength(
        statement_text=candidate,
        cited_sources={"su001": source},
    )

    assert result.passed is False
    assert result.error_code == error_code
    assert result.related_source_unit_ids == ("su001",)


def test_action_from_an_uncited_source_cannot_mask_statement_mixing() -> None:
    result = validate_action_strength(
        statement_text="검사를 확인한다.",
        cited_sources={"su001": "검사를 시행한다."},
    )

    assert result.passed is False
    assert result.error_code == "evaluation_action_kind_changed"

    correctly_cited = validate_action_strength(
        statement_text="검사를 확인한다.",
        cited_sources={"su002": "검사를 확인한다."},
    )
    assert correctly_cited.passed is True


def test_temporal_action_noun_is_not_misread_as_a_new_directive() -> None:
    result = validate_action_strength(
        statement_text="투여 전 상태를 확인한다.",
        cited_sources={"su001": "상태를 확인한다."},
    )

    assert result.passed is True


def test_strength_modifier_is_bound_to_its_action_phrase() -> None:
    result = validate_action_strength(
        statement_text=(
            "혈액을 병동 도착 즉시 운반하고 의료인 2인이 환자와 혈액을 확인한다."
        ),
        cited_sources={
            "su001": (
                "혈액은 병동 도착 즉시 운반하며 의료인 2인이 환자와 혈액을 "
                "확인한다."
            )
        },
    )

    assert result.passed is True
    assert result.error_code is None
    assert result.source_strengths == ("required",)
    assert result.candidate_strengths == ("required",)


def test_strength_from_previous_action_cannot_justify_stronger_later_action() -> None:
    result = validate_action_strength(
        statement_text=(
            "혈액을 병동으로 운반하고 의료인 2인이 환자와 혈액을 즉시 확인한다."
        ),
        cited_sources={
            "su001": (
                "혈액은 병동 도착 즉시 운반하며 의료인 2인이 환자와 혈액을 "
                "확인한다."
            )
        },
    )

    assert result.passed is False
    assert result.error_code == "evaluation_action_strength_changed"


def test_result_metadata_contains_no_source_or_candidate_text() -> None:
    source = "민감한 원문에서 검사를 시행한다."
    candidate = "민감한 후보에서 검사를 확인한다."

    payload = repr(
        asdict(
            validate_action_strength(
                statement_text=candidate,
                cited_sources={"su001": source},
            )
        )
    )

    assert source not in payload
    assert candidate not in payload
    assert "민감한" not in payload


def test_checker_performs_no_network_access(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket, "create_connection", forbidden_network)

    result = validate_action_strength(
        statement_text="검사를 수행한다.",
        cited_sources={"su001": "검사를 시행한다."},
    )

    assert result.passed is True


def test_production_modules_do_not_import_evaluation_checker() -> None:
    imported_modules: set[str] = set()
    for path in (*Path("src").rglob("*.py"), Path("streamlit_app.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)

    assert "tools.evaluation_action_strength" not in imported_modules
