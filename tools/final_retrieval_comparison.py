"""Build a raw-free final comparison from three frozen retrieval evaluations."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from tools import chromadb_only_evaluate as artifact_policy

ROOT = Path(__file__).resolve().parents[1]
ENGINE_ORDER = ("bm25", "gemini_chroma", "current_hybrid")
METRIC_ORDER = ("hit_at_10", "mrr", "recall_at_10", "precision_at_10")
AGGREGATE_METRICS = (
    "hit_at_1",
    "hit_at_3",
    "hit_at_5",
    "hit_at_10",
    "mrr",
    "recall_at_1",
    "recall_at_3",
    "recall_at_5",
    "recall_at_10",
    "precision_at_1",
    "precision_at_3",
    "precision_at_5",
    "precision_at_10",
    "ragas_id_context_precision",
    "ragas_id_context_recall",
    "latency_mean_ms",
)
DEFAULT_INPUTS = {
    "bm25": ROOT
    / "workspace"
    / "검색_성능평가"
    / "BM25"
    / "2026-09-19_bm25-only-eval",
    "gemini_chroma": ROOT
    / "workspace"
    / "검색_성능평가"
    / "ChromaDB"
    / "2026-09-20-gemini-embedding-2-3072-chromadb-only-eval",
    "current_hybrid": ROOT
    / "workspace"
    / "검색_성능평가"
    / "Hybrid"
    / "2026-09-19_current-hybrid-eval",
}
DEFAULT_OUTPUT = (
    ROOT
    / "workspace"
    / "검색_성능평가"
    / "3방식_비교"
    / "2026-09-20_final-retrieval-comparison"
)
ENGINE_LABELS = {
    "bm25": "Pure BM25",
    "gemini_chroma": "ChromaDB + Gemini Embedding 2 (3072d)",
    "current_hybrid": "Current Hybrid",
}


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def _normalized_symbol(value: Any) -> str:
    symbol = str(value)
    for prefix in ("src.", "mvp."):
        if symbol.startswith(prefix):
            return symbol[len(prefix) :]
    return symbol


def load_input_contracts(
    input_dirs: Mapping[str, Path],
) -> dict[str, dict[str, Any]]:
    if set(input_dirs) != set(ENGINE_ORDER):
        raise ValueError("all three retrieval inputs are required")
    return {
        engine: _read(Path(input_dirs[engine]) / "evaluation_contract.json")
        for engine in ENGINE_ORDER
    }


def _require_same(
    contracts: Mapping[str, Mapping[str, Any]],
    section: str,
    fields: tuple[str, ...],
    message: str,
) -> dict[str, Any]:
    values = {
        field: {json.dumps(contracts[engine][section].get(field), sort_keys=True) for engine in ENGINE_ORDER}
        for field in fields
    }
    if any(len(distinct) != 1 for distinct in values.values()):
        raise ValueError(message)
    return {field: contracts[ENGINE_ORDER[0]][section].get(field) for field in fields}


def validate_contracts(
    contracts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate the common frozen comparison contract without rerunning retrieval."""
    if set(contracts) != set(ENGINE_ORDER):
        raise ValueError("all three retrieval contracts are required")
    corpus = _require_same(
        contracts,
        "corpus",
        (
            "catalog_revision",
            "fingerprint_algorithm",
            "fingerprint",
            "document_count",
            "chunk_count",
            "document_ids",
        ),
        "corpus contract drift",
    )
    gold = _require_same(
        contracts,
        "gold",
        (
            "fixture",
            "fixture_sha256",
            "dataset_version",
            "approved_positive_case_count",
            "deferred_positive_case_count",
            "approved_abstention_case_count",
            "negative_handling",
        ),
        "Gold contract drift",
    )
    top_k = {tuple(contracts[engine].get("top_k", ())) for engine in ENGINE_ORDER}
    if top_k != {(1, 3, 5, 10)}:
        raise ValueError("Top-k contract drift")
    planners = {
        _normalized_symbol(contracts[engine]["query_processing"].get("planner"))
        for engine in ENGINE_ORDER
    }
    if planners != {"query.plan_query"}:
        raise ValueError("QueryPlan planner drift")
    common_query = _require_same(
        contracts,
        "query_processing",
        ("follow_up_context", "document_scope_filter"),
        "QueryPlan behavior drift",
    )
    if not all(
        "plan.expanded"
        in str(
            contracts[engine]["query_processing"].get(
                "query_text",
                contracts[engine]["query_processing"].get("embedding_projection", ""),
            )
        )
        for engine in ENGINE_ORDER
    ):
        raise ValueError("QueryPlan expanded-query drift")
    return {
        "corpus": corpus,
        "gold": gold,
        "approved_positive_case_count": int(gold["approved_positive_case_count"]),
        "top_k": [1, 3, 5, 10],
        "query_plan": {"planner": "query.plan_query", **common_query},
        "engine_specific_differences": {
            engine: {
                "context_expansion": bool(
                    contracts[engine]["query_processing"].get("context_expansion")
                )
            }
            for engine in ENGINE_ORDER
        },
    }


def _winners(values: Mapping[str, float]) -> list[str]:
    best = max(values.values())
    return [
        engine
        for engine in ENGINE_ORDER
        if math.isclose(values[engine], best, rel_tol=0.0, abs_tol=1e-12)
    ]


def compare_case_metrics(rows: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    if set(rows) != set(ENGINE_ORDER):
        raise ValueError("all three case rows are required")
    raw_values = {
        metric: {
            engine: float(rows[engine]["metrics"][metric]) for engine in ENGINE_ORDER
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
    complete_tie = len(set(tuples.values())) == 1
    return {
        "metric_values": raw_values,
        "metric_winners": metric_winners,
        "overall_winners": overall_winners,
        "single_winner": overall_winners[0] if len(overall_winners) == 1 else None,
        "complete_tie": complete_tie,
    }


def _safe_ranked_results(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    allowed = {
        "rank",
        "chunk_id",
        "document_id",
        "page",
        "section",
        "score",
        "similarity",
        "distance",
        "bm25_score",
        "fusion_score",
        "rerank_score",
        "context_only",
        "context_complete",
        "gold_hit",
    }
    return [
        {key: value for key, value in result.items() if key in allowed}
        for result in row.get("ranked_results", [])
    ]


def _priority_cases(case_rows: list[Mapping[str, Any]]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {
        "gemini_only_success": [],
        "bm25_over_hybrid": [],
        "hybrid_mrr_over_gemini": [],
        "all_failed": [],
        "table_related": [],
        "same_hit_different_recall_or_precision": [],
    }
    for row in case_rows:
        retrievers = row["retrievers"]
        bm25 = retrievers["bm25"]["metrics"]
        gemini = retrievers["gemini_chroma"]["metrics"]
        hybrid = retrievers["current_hybrid"]["metrics"]
        case_id = str(row["case_id"])
        if gemini["hit_at_10"] == 1 and bm25["hit_at_10"] == 0 and hybrid["hit_at_10"] == 0:
            groups["gemini_only_success"].append(case_id)
        if tuple(bm25[m] for m in METRIC_ORDER) > tuple(hybrid[m] for m in METRIC_ORDER):
            groups["bm25_over_hybrid"].append(case_id)
        if hybrid["mrr"] > gemini["mrr"]:
            groups["hybrid_mrr_over_gemini"].append(case_id)
        if all(retrievers[engine]["metrics"]["hit_at_10"] == 0 for engine in ENGINE_ORDER):
            groups["all_failed"].append(case_id)
        if row["expected_evidence_type"] in {"table", "mixed"}:
            groups["table_related"].append(case_id)
        if len({retrievers[e]["metrics"]["hit_at_10"] for e in ENGINE_ORDER}) == 1 and (
            len({retrievers[e]["metrics"]["recall_at_10"] for e in ENGINE_ORDER}) > 1
            or len({retrievers[e]["metrics"]["precision_at_10"] for e in ENGINE_ORDER}) > 1
        ):
            groups["same_hit_different_recall_or_precision"].append(case_id)
    groups["uat_t11"] = ["UAT-T11"] if any(row["case_id"] == "UAT-T11" for row in case_rows) else []
    return groups


def _write_metrics_csv(path: Path, aggregate: Mapping[str, Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("retriever", *AGGREGATE_METRICS))
        writer.writeheader()
        for engine in ENGINE_ORDER:
            writer.writerow(
                {
                    "retriever": engine,
                    **{metric: aggregate[engine].get(metric) for metric in AGGREGATE_METRICS},
                }
            )


def _write_case_analysis(path: Path, summary: Mapping[str, Any]) -> None:
    lines = [
        "# SCHAT 최종 Retrieval 비교",
        "",
        "기존 frozen 평가 산출물만 비교했으며 검색과 외부 API 호출을 다시 실행하지 않았다.",
        "MiniLM ChromaDB는 주 3방식 비교에서 제외하고 Gemini embedding 개선 참고로만 사용했다.",
        "",
        "## 단독 우세",
        "",
    ]
    for engine in ENGINE_ORDER:
        lines.append(f"- {ENGINE_LABELS[engine]}: {summary['single_winner_counts'].get(engine, 0)}건")
    lines.extend(
        [
            f"- 완전 동률: {summary['complete_tie_case_count']}건",
            "",
            "## 우선 사람 검수 대상",
            "",
        ]
    )
    for category, case_ids in summary["priority_review_cases"].items():
        lines.append(f"- {category}: {', '.join(case_ids) if case_ids else '-'}")
    lines.extend(
        [
            "",
            "## 해석 경계",
            "",
            "- Hybrid는 전반적인 순위와 다중 Gold 회수에서 강하지만 context expansion을 포함해 운영 복잡도가 높다.",
            "- Gemini Chroma는 MiniLM Chroma보다 크게 개선됐지만 embedding 생성 지연과 외부 provider 비용이 있다.",
            "- 표 기반 UAT-T11 실패는 retriever 교체만으로 해결되지 않으며 corpus의 table 표현을 별도 검토해야 한다.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_index(summary: Mapping[str, Any], per_case: Mapping[str, Any]) -> str:
    cards = "".join(
        f'<section class="card"><h2>{html.escape(ENGINE_LABELS[e])}</h2>'
        f'<p>단독 우세 {summary.get("single_winner_counts", {}).get(e, 0)}건</p></section>'
        for e in ENGINE_ORDER
    )
    rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            html.escape(str(row["case_id"])),
            html.escape(str(row.get("question_type", ""))),
            html.escape(", ".join(row.get("overall_winners", []))),
        )
        for row in per_case.get("cases", [])
    )
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SCHAT 최종 Retrieval 비교</title>
<style>body{{font-family:system-ui,sans-serif;margin:0;background:#f4f7f6;color:#18332d}}main{{max-width:1200px;margin:auto;padding:32px}}.notice,.card{{background:white;border:1px solid #dbe5e1;border-radius:12px;padding:18px}}.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:18px 0}}table{{width:100%;border-collapse:collapse;background:white}}th,td{{padding:10px;border-bottom:1px solid #e2e8e6;text-align:left}}code{{background:#e8efed;padding:3px 6px;border-radius:5px}}@media(max-width:760px){{.cards{{grid-template-columns:1fr}}}}</style></head>
<body><main><h1>SCHAT 최종 Retrieval 비교</h1><div class="notice"><strong>raw-free 정적 요약</strong><p>실제 질문과 병원 원문은 이 HTML에 저장하지 않습니다. 질문·근거 미리보기와 사람 검수는 로컬 검수 앱에서만 제공합니다.</p><p>실행: <code>streamlit run tools/final_retrieval_review_app.py</code></p></div><div class="cards">{cards}</div><h2>Case 비교</h2><table><thead><tr><th>Case ID</th><th>유형</th><th>종합 우세</th></tr></thead><tbody>{rows}</tbody></table></main></body></html>"""


def compare(
    *,
    input_dirs: Mapping[str, Path] | None = None,
    output_dir: Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    directories = {key: Path(value) for key, value in (input_dirs or DEFAULT_INPUTS).items()}
    contracts = load_input_contracts(directories)
    validated = validate_contracts(contracts)
    summaries = {engine: _read(directories[engine] / "summary.json") for engine in ENGINE_ORDER}
    payloads = {
        engine: _read(directories[engine] / "per_case_results.json")
        for engine in ENGINE_ORDER
    }
    cases = {
        engine: {str(row["case_id"]): row for row in payloads[engine]["cases"]}
        for engine in ENGINE_ORDER
    }
    case_sets = {tuple(sorted(cases[engine])) for engine in ENGINE_ORDER}
    if len(case_sets) != 1:
        raise ValueError("case ID contract drift")
    case_ids = list(next(iter(case_sets)))
    if len(case_ids) != validated["approved_positive_case_count"]:
        raise ValueError("case count contract drift")

    case_rows: list[dict[str, Any]] = []
    for case_id in case_ids:
        rows = {engine: cases[engine][case_id] for engine in ENGINE_ORDER}
        identities = {
            (
                str(row["question_type"]),
                str(row["document_scope"]),
                str(row["expected_evidence_type"]),
                tuple(sorted(str(item) for item in row["gold_ids"])),
            )
            for row in rows.values()
        }
        if len(identities) != 1:
            raise ValueError(f"{case_id}: case contract drift")
        question_type, scope, evidence_type, gold_ids = next(iter(identities))
        comparison = compare_case_metrics(rows)
        case_rows.append(
            {
                "case_id": case_id,
                "question_type": question_type,
                "document_scope": scope,
                "expected_evidence_type": evidence_type,
                "gold_ids": list(gold_ids),
                "retrievers": {
                    engine: {
                        "metrics": dict(rows[engine]["metrics"]),
                        "latency_ms": dict(rows[engine].get("latency_ms", {})),
                        "failure_category": rows[engine].get("failure_category"),
                        "ranked_results": _safe_ranked_results(rows[engine]),
                    }
                    for engine in ENGINE_ORDER
                },
                **comparison,
            }
        )

    aggregate = {
        engine: dict(summaries[engine]["metrics"]["overall"]) for engine in ENGINE_ORDER
    }
    metric_win_counts = {
        metric: dict(Counter(w for row in case_rows for w in row["metric_winners"][metric]))
        for metric in METRIC_ORDER
    }
    overall_win_counts = dict(
        Counter(w for row in case_rows for w in row["overall_winners"])
    )
    single_winner_counts = dict(
        Counter(row["single_winner"] for row in case_rows if row["single_winner"])
    )
    minilm_reference_path = directories["gemini_chroma"] / "comparison_with_minilm.json"
    minilm_reference = _read(minilm_reference_path)
    minilm_reference["primary_comparison"] = False
    priority = _priority_cases(case_rows)
    gemini_usage = summaries["gemini_chroma"].get("usage", {})
    hybrid_rows = list(cases["current_hybrid"].values())
    hybrid_embedding = sum(
        float(row.get("latency_ms", {}).get("embedding", 0.0)) for row in hybrid_rows
    ) / len(hybrid_rows)
    hybrid_retrieval = sum(
        float(row.get("latency_ms", {}).get("retrieval", 0.0)) for row in hybrid_rows
    ) / len(hybrid_rows)
    latency_breakdown = {
        "bm25": {
            "query": aggregate["bm25"]["latency_mean_ms"],
            "combined": aggregate["bm25"]["latency_mean_ms"],
        },
        "gemini_chroma": {
            "query_embedding": gemini_usage["query_embedding_latency_mean_ms"],
            "chromadb_query": gemini_usage["chromadb_query_latency_mean_ms"],
            "combined": aggregate["gemini_chroma"]["latency_mean_ms"],
        },
        "current_hybrid": {
            "embedding": round(hybrid_embedding, 3),
            "retrieval": round(hybrid_retrieval, 3),
            "combined": aggregate["current_hybrid"]["latency_mean_ms"],
        },
    }
    summary = {
        "schema_version": 1,
        "case_count": len(case_rows),
        "engine_order": list(ENGINE_ORDER),
        "aggregate_metrics": aggregate,
        "latency_breakdown_ms": latency_breakdown,
        "metric_win_counts": metric_win_counts,
        "overall_win_counts": overall_win_counts,
        "single_winner_counts": single_winner_counts,
        "complete_tie_case_count": sum(bool(row["complete_tie"]) for row in case_rows),
        "priority_review_cases": priority,
        "priority_review_case_ids": sorted({case for values in priority.values() for case in values}),
        "minilm_reference": minilm_reference,
        "retrieval_rerun": False,
        "external_api_calls": 0,
        "production_changed": False,
        "gold_changed": False,
        "raw_source_text_persisted": False,
        "raw_question_persisted": False,
    }
    per_case = {"schema_version": 1, "cases": case_rows}
    evaluation_contract = {
        "schema_version": 1,
        "evaluation_date": date.today().isoformat(),
        "evaluator_file": "tools/final_retrieval_comparison.py",
        **validated,
        "engines": list(ENGINE_ORDER),
        "winner_metrics": list(METRIC_ORDER),
        "winner_rule": "lexicographic_hit_at_10_then_mrr_then_recall_at_10_then_precision_at_10",
        "inputs": {
            engine: {
                "artifact_dir": _display_path(directories[engine]),
                "evaluation_contract_sha256": _sha256(directories[engine] / "evaluation_contract.json"),
                "summary_sha256": _sha256(directories[engine] / "summary.json"),
                "per_case_results_sha256": _sha256(directories[engine] / "per_case_results.json"),
            }
            for engine in ENGINE_ORDER
        },
        "minilm_reference_sha256": _sha256(minilm_reference_path),
        "human_review_created_only_on_first_save": True,
    }
    for payload in (summary, per_case, evaluation_contract):
        artifact_policy.ensure_artifact_safe(payload, forbidden_exact_texts=set())

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "summary.json", summary)
    _write_json(output_dir / "per_case_comparison.json", per_case)
    _write_json(output_dir / "evaluation_contract.json", evaluation_contract)
    _write_metrics_csv(output_dir / "metrics.csv", aggregate)
    _write_case_analysis(output_dir / "case_analysis.md", summary)
    (output_dir / "index.html").write_text(render_index(summary, per_case), encoding="utf-8")
    return {
        "summary": summary,
        "per_case": per_case,
        "contract": evaluation_contract,
        "output_dir": str(output_dir),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare frozen BM25, Gemini Chroma, and Current Hybrid artifacts."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = compare(output_dir=args.output)
    print(json.dumps(result["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
