import json
import shutil
from pathlib import Path

import pytest

from tools import current_hybrid_evaluate as hybrid_evaluator
from tools import retrieval_3way_compare as comparator

ROOT = Path(__file__).resolve().parents[1]


def test_three_way_comparator_exists_as_an_evaluation_only_tool() -> None:
    assert (ROOT / "tools" / "retrieval_3way_compare.py").is_file()


def _case(engine: str, *, hit: float, mrr: float, recall: float, precision: float) -> dict:
    return {
        "case_id": "A",
        "engine": engine,
        "metrics": {
            "hit_at_10": hit,
            "mrr": mrr,
            "recall_at_10": recall,
            "precision_at_10": precision,
        },
    }


def test_case_comparison_preserves_raw_values_and_each_metric_winner() -> None:
    comparison = comparator.compare_case_metrics(
        {
            "bm25_only": _case("bm25_only", hit=1.0, mrr=0.5, recall=0.4, precision=0.2),
            "chromadb_only": _case("chromadb_only", hit=0.0, mrr=0.0, recall=0.0, precision=0.0),
            "current_hybrid": _case(
                "current_hybrid", hit=1.0, mrr=0.25, recall=0.6, precision=0.3
            ),
        }
    )

    assert comparison["raw_values"] == {
        "hit_at_10": {"bm25_only": 1.0, "chromadb_only": 0.0, "current_hybrid": 1.0},
        "mrr": {"bm25_only": 0.5, "chromadb_only": 0.0, "current_hybrid": 0.25},
        "recall_at_10": {"bm25_only": 0.4, "chromadb_only": 0.0, "current_hybrid": 0.6},
        "precision_at_10": {"bm25_only": 0.2, "chromadb_only": 0.0, "current_hybrid": 0.3},
    }
    assert comparison["metric_winners"] == {
        "hit_at_10": ["bm25_only", "current_hybrid"],
        "mrr": ["bm25_only"],
        "recall_at_10": ["current_hybrid"],
        "precision_at_10": ["current_hybrid"],
    }
    assert comparison["overall_winners"] == ["bm25_only"]
    assert comparison["complete_tie"] is False


def test_case_comparison_marks_a_complete_tie() -> None:
    rows = {
        engine: _case(engine, hit=1.0, mrr=0.5, recall=0.4, precision=0.2)
        for engine in comparator.ENGINE_ORDER
    }

    comparison = comparator.compare_case_metrics(rows)

    assert comparison["overall_winners"] == list(comparator.ENGINE_ORDER)
    assert comparison["complete_tie"] is True


def test_compare_rejects_contract_drift() -> None:
    contracts = {
        engine: {
            "corpus": {"fingerprint": "same"},
            "gold": {"fixture_sha256": "gold", "approved_positive_case_count": 21},
            "top_k": [1, 3, 5, 10],
        }
        for engine in comparator.ENGINE_ORDER
    }
    contracts["current_hybrid"]["corpus"]["fingerprint"] = "different"

    with pytest.raises(ValueError, match="corpus fingerprint drift"):
        comparator.validate_contracts(contracts)


def test_full_three_way_comparison_writes_metric_and_case_artifacts(tmp_path: Path) -> None:
    inputs = {}
    for engine, source in comparator.DEFAULT_INPUTS.items():
        destination = tmp_path / "inputs" / engine
        if engine == "current_hybrid":
            hybrid_evaluator.evaluate(output_dir=destination)
        else:
            shutil.copytree(source, destination)
        inputs[engine] = destination

    result = comparator.compare(input_dirs=inputs, output_dir=tmp_path / "output")

    assert result["summary"]["case_count"] == 21
    assert len(result["per_case"]["cases"]) == 21
    assert all(
        {
            "raw_values",
            "metric_winners",
            "overall_winners",
            "complete_tie",
        }.issubset(row)
        for row in result["per_case"]["cases"]
    )
    assert {
        "summary.json",
        "per_case_comparison.json",
        "metrics.csv",
        "case_analysis.md",
        "comparison_contract.json",
    } == {path.name for path in (tmp_path / "output").iterdir()}
    stored = json.loads(
        (tmp_path / "output" / "per_case_comparison.json").read_text(encoding="utf-8")
    )
    assert stored == result["per_case"]
