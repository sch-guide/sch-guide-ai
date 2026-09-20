from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.evidence import SourceUnit
from tools.build_controlled_generation_review_source import build_source_records


@dataclass(frozen=True)
class FakePreparedCase:
    case_id: str
    units: tuple[SourceUnit, ...]


def _unit(identifier: str, text: str, order: int) -> SourceUnit:
    return SourceUnit(
        source_unit_id=identifier,
        chunk_id=f"chunk-{order}",
        source_order=(order, 0),
        branch="common",
        exact_text=text,
        group_key=f"group-{order}",
        required=True,
        selectable=True,
    )


def test_builder_creates_local_deterministic_projection_without_network() -> None:
    records = build_source_records(
        (
            FakePreparedCase(
                case_id="CASE-001",
                units=(
                    _unit("su001", "첫 근거 문장", 1),
                    _unit("su002", "둘째 근거 문장", 2),
                ),
            ),
        ),
        prompt_version="v1.0-natural-grounded",
    )

    assert len(records) == 1
    record = records[0]
    assert record.case_id == "CASE-001"
    assert record.extractive_answer == "첫 근거 문장\n\n둘째 근거 문장"
    assert record.controlled_candidate == record.extractive_answer
    assert record.model == "deterministic-source-unit-projection"
    assert record.candidate_status == "deterministic_projection"
    assert record.supporting_source_unit_ids == [["su001"], ["su002"]]
    assert len(record.extractive_answer_sha256) == 64
    assert record.extractive_answer_sha256 == record.controlled_candidate_sha256


def test_builder_has_no_provider_or_production_generation_connection() -> None:
    source = Path("tools/build_controlled_generation_review_source.py").read_text(
        encoding="utf-8"
    )

    assert "src.ai" not in source
    assert "generate(" not in source
    assert "requests" not in source
    assert "httpx" not in source
    assert "call_groq" not in source
    assert "call_gemini" not in source


def test_builder_maps_each_internal_paragraph_to_its_source_unit() -> None:
    records = build_source_records(
        (
            FakePreparedCase(
                case_id="CASE-001",
                units=(_unit("su001", "첫 문단\n\n둘째 문단", 1),),
            ),
        ),
        prompt_version="v1.0-natural-grounded",
    )

    assert records[0].controlled_candidate == "첫 문단\n\n둘째 문단"
    assert records[0].supporting_source_unit_ids == [["su001"], ["su001"]]
