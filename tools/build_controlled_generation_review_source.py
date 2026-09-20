"""Build an ignored local review source with zero provider calls.

The controlled side is an explicitly labelled deterministic SourceUnit projection.
It preserves exact validated statements and is intended to exercise the human-review
workflow before any separately approved provider evaluation supplies natural rewrites.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterable, Protocol

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evidence import SourceUnit  # noqa: E402
from src.prompt_config import load_evaluation_prompt  # noqa: E402
from tools.controlled_generation_review import (  # noqa: E402
    ReviewSourceCaseRecord,
    build_review_source_case,
    write_review_source,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_ROOT = (
    ROOT / "workspace" / "임시작업" / "controlled_generation_review_source"
)
DEFAULT_SOURCE_PATH = DEFAULT_SOURCE_ROOT / "review_source.json"


class PreparedCase(Protocol):
    case_id: str
    units: tuple[SourceUnit, ...]


def build_source_records(
    cases: Iterable[PreparedCase],
    *,
    prompt_version: str,
) -> tuple[ReviewSourceCaseRecord, ...]:
    """Project approved in-memory SourceUnits without adding clinical language."""
    records: list[ReviewSourceCaseRecord] = []
    case_ids: set[str] = set()
    for case in cases:
        if case.case_id in case_ids:
            raise ValueError("case_id")
        case_ids.add(case.case_id)
        units = tuple(case.units)
        if not units or len({unit.source_unit_id for unit in units}) != len(units):
            raise ValueError("source_unit_mapping")
        paragraphs: list[str] = []
        citation_rows: list[tuple[str, ...]] = []
        for unit in units:
            unit_paragraphs = tuple(
                value.strip()
                for value in re.split(r"\n\s*\n", unit.exact_text)
                if value.strip()
            )
            if not unit_paragraphs:
                raise ValueError("source_unit_text")
            paragraphs.extend(unit_paragraphs)
            citation_rows.extend((unit.source_unit_id,) for _ in unit_paragraphs)
        exact_projection = "\n\n".join(paragraphs)
        records.append(
            build_review_source_case(
                case_id=case.case_id,
                prompt_version=prompt_version,
                model="deterministic-source-unit-projection",
                candidate_status="deterministic_projection",
                extractive_answer=exact_projection,
                controlled_candidate=exact_projection,
                supporting_source_unit_ids=citation_rows,
            )
        )
    if not records:
        raise ValueError("review_source_cases")
    return tuple(records)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_SOURCE_PATH)
    args = parser.parse_args()

    # Importing the evaluator is deliberately delayed so importing this module has
    # no model, database, storage, provider, or network side effects.
    from tools.groq_live_ragas_evaluate import prepare_approved_live_cases

    config = load_evaluation_prompt()
    records = build_source_records(
        prepare_approved_live_cases(),
        prompt_version=config.prompt_version,
    )
    write_review_source(
        args.output,
        records,
        allowed_root=DEFAULT_SOURCE_ROOT,
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "case_count": len(records),
                "prompt_version": config.prompt_version,
                "external_calls": 0,
                "candidate_mode": "deterministic_exact_source_unit_projection",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
