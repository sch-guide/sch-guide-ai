"""Compare frozen BM25-only, ChromaDB-only, and Current Hybrid artifacts."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from tools import chromadb_only_evaluate as common

ROOT = Path(__file__).resolve().parents[1]
ENGINE_ORDER = ("bm25_only", "chromadb_only", "current_hybrid")
METRIC_ORDER = ("hit_at_10", "mrr", "recall_at_10", "precision_at_10")
DEFAULT_INPUTS = {
    "bm25_only": ROOT / "workspace" / "검색_성능평가" / "BM25" / "2026-09-19_bm25-only-eval",
    "chromadb_only": ROOT / "workspace" / "검색_성능평가" / "ChromaDB" / "2026-09-19_chromadb-only-eval",
    "current_hybrid": ROOT / "workspace" / "검색_성능평가" / "Hybrid" / "2026-09-19_current-hybrid-eval",
}
DEFAULT_OUTPUT = ROOT / "workspace" / "검색_성능평가" / "3방식_비교" / "2026-09-19_retrieval-3way-comparison"


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def validate_contracts(contracts: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Reject comparisons whose frozen evaluation contract has drifted."""
    if set(contracts) != set(ENGINE_ORDER):
        raise ValueError("three comparison engine contracts are required")
    fingerprints = {str(contracts[engine]["corpus"]["fingerprint"]) for engine in ENGINE_ORDER}
    if len(fingerprints) != 1:
        raise ValueError("corpus fingerprint drift")
    gold_hashes = {str(contracts[engine]["gold"]["fixture_sha256"]) for engine in ENGINE_ORDER}
    gold_counts = {
        int(contracts[engine]["gold"]["approved_positive_case_count"])
        for engine in ENGINE_ORDER
    }
    if len(gold_hashes) != 1 or len(gold_counts) != 1:
        raise ValueError("approved Gold contract drift")
    top_k_values = {tuple(contracts[engine]["top_k"]) for engine in ENGINE_ORDER}
    if top_k_values != {(1, 3, 5, 10)}:
        raise ValueError("Top-k contract drift")
    return {
        "corpus_fingerprint": next(iter(fingerprints)),
        "gold_fixture_sha256": next(iter(gold_hashes)),
        "approved_positive_case_count": next(iter(gold_counts)),
        "top_k": [1, 3, 5, 10],
    }


def _winners(values: Mapping[str, float]) -> list[str]:
    maximum = max(values.values())
    return [
        engine
        for engine in ENGINE_ORDER
        if math.isclose(values[engine], maximum, rel_tol=0.0, abs_tol=1e-12)
    ]


def compare_case_metrics(rows_by_engine: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Preserve raw values and apply the approved lexicographic winner rule."""
    if set(rows_by_engine) != set(ENGINE_ORDER):
        raise ValueError("all three engine rows are required")
    raw_values = {
        metric: {
            engine: float(rows_by_engine[engine]["metrics"][metric])
            for engine in ENGINE_ORDER
        }
        for metric in METRIC_ORDER
    }
    metric_winners = {metric: _winners(values) for metric, values in raw_values.items()}
    tuples = {
        engine: tuple(raw_values[metric][engine] for metric in METRIC_ORDER)
        for engine in ENGINE_ORDER
    }
    best = max(tuples.values())
    overall_winners = [engine for engine in ENGINE_ORDER if tuples[engine] == best]
    complete_tie = all(
        len({raw_values[metric][engine] for engine in ENGINE_ORDER}) == 1
        for metric in METRIC_ORDER
    )
    return {
        "raw_values": raw_values,
        "metric_winners": metric_winners,
        "overall_winners": overall_winners,
        "complete_tie": complete_tie,
    }


def _write_metrics_csv(path: Path, summaries: Mapping[str, Mapping[str, Any]]) -> None:
    rows = []
    for engine in ENGINE_ORDER:
        overall = summaries[engine]["metrics"]["overall"]
        rows.append({"engine": engine, **overall})
    fieldnames = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_case_analysis(path: Path, rows: list[Mapping[str, Any]]) -> None:
    lines = [
        "# Retrieval 3-way case별 우열 분석",
        "",
        "종합 우열은 Hit@10 → MRR → Recall@10 → Precision@10 순의 사전식 비교다.",
        "질문 및 병원 원문은 저장하지 않는다.",
        "",
        "| Case ID | Hit@10 | MRR | Recall@10 | Precision@10 | 종합 | 완전 동률 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        winners = row["metric_winners"]
        lines.append(
            "| {case_id} | {hit} | {mrr} | {recall} | {precision} | {overall} | {tie} |".format(
                case_id=row["case_id"],
                hit=", ".join(winners["hit_at_10"]),
                mrr=", ".join(winners["mrr"]),
                recall=", ".join(winners["recall_at_10"]),
                precision=", ".join(winners["precision_at_10"]),
                overall=", ".join(row["overall_winners"]),
                tie="yes" if row["complete_tie"] else "no",
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def compare(
    *,
    input_dirs: Mapping[str, Path] | None = None,
    output_dir: Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    """Validate three frozen runs and write aggregate/case comparison artifacts."""
    directories = dict(input_dirs or DEFAULT_INPUTS)
    if set(directories) != set(ENGINE_ORDER):
        raise ValueError("all three engine artifact directories are required")
    contracts = {
        engine: _read(directories[engine] / "evaluation_contract.json")
        for engine in ENGINE_ORDER
    }
    frozen = validate_contracts(contracts)
    summaries = {
        engine: _read(directories[engine] / "summary.json")
        for engine in ENGINE_ORDER
    }
    per_case_payloads = {
        engine: _read(directories[engine] / "per_case_results.json")
        for engine in ENGINE_ORDER
    }
    cases_by_engine = {
        engine: {str(row["case_id"]): row for row in per_case_payloads[engine]["cases"]}
        for engine in ENGINE_ORDER
    }
    case_sets = {tuple(sorted(cases_by_engine[engine])) for engine in ENGINE_ORDER}
    if len(case_sets) != 1:
        raise ValueError("case ID contract drift")
    case_ids = list(next(iter(case_sets)))
    if len(case_ids) != frozen["approved_positive_case_count"]:
        raise ValueError("case count contract drift")

    case_rows = []
    for case_id in case_ids:
        rows = {engine: cases_by_engine[engine][case_id] for engine in ENGINE_ORDER}
        identities = {
            (
                str(row["question_type"]),
                str(row["document_scope"]),
                tuple(row["gold_ids"]),
            )
            for row in rows.values()
        }
        if len(identities) != 1:
            raise ValueError(f"{case_id}: case contract drift")
        question_type, document_scope, _ = next(iter(identities))
        case_rows.append(
            {
                "case_id": case_id,
                "question_type": question_type,
                "document_scope": document_scope,
                **compare_case_metrics(rows),
            }
        )

    metric_win_counts = {
        metric: dict(
            Counter(
                winner
                for row in case_rows
                for winner in row["metric_winners"][metric]
            )
        )
        for metric in METRIC_ORDER
    }
    overall_win_counts = dict(
        Counter(winner for row in case_rows for winner in row["overall_winners"])
    )
    aggregate = {
        engine: summaries[engine]["metrics"]["overall"] for engine in ENGINE_ORDER
    }
    summary_payload = {
        "schema_version": 1,
        "case_count": len(case_rows),
        "engine_order": list(ENGINE_ORDER),
        "aggregate_metrics": aggregate,
        "metric_win_counts": metric_win_counts,
        "overall_win_counts": overall_win_counts,
        "complete_tie_case_count": sum(bool(row["complete_tie"]) for row in case_rows),
        "production_changed": False,
        "gold_changed": False,
    }
    per_case_payload = {"schema_version": 1, "cases": case_rows}
    comparison_contract = {
        "schema_version": 1,
        "evaluation_date": date.today().isoformat(),
        "evaluator_file": "tools/retrieval_3way_compare.py",
        **frozen,
        "engines": list(ENGINE_ORDER),
        "winner_metrics": list(METRIC_ORDER),
        "overall_winner_rule": "lexicographic_hit_at_10_then_mrr_then_recall_at_10_then_precision_at_10",
        "complete_tie_rule": "all_four_case_metrics_equal_across_all_engines",
        "inputs": {
            engine: {
                "artifact_dir": _display_path(directories[engine]),
                "evaluation_contract_sha256": _sha256(
                    directories[engine] / "evaluation_contract.json"
                ),
                "summary_sha256": _sha256(directories[engine] / "summary.json"),
                "per_case_results_sha256": _sha256(
                    directories[engine] / "per_case_results.json"
                ),
            }
            for engine in ENGINE_ORDER
        },
    }
    for payload in (summary_payload, per_case_payload, comparison_contract):
        common.ensure_artifact_safe(payload, forbidden_exact_texts=set())

    output_dir.mkdir(parents=True, exist_ok=True)
    common._write_json(output_dir / "summary.json", summary_payload)
    common._write_json(output_dir / "per_case_comparison.json", per_case_payload)
    common._write_json(output_dir / "comparison_contract.json", comparison_contract)
    _write_metrics_csv(output_dir / "metrics.csv", summaries)
    _write_case_analysis(output_dir / "case_analysis.md", case_rows)
    return {
        "summary": summary_payload,
        "per_case": per_case_payload,
        "contract": comparison_contract,
        "output_dir": str(output_dir),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare SCHAT three-way retrieval artifacts.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = compare(output_dir=args.output)
    print(json.dumps(result["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
