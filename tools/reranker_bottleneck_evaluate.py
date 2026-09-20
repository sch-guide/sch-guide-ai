"""Evaluate frozen BM25/E5 reranker variants without provider or model calls.

Ranking functions receive only candidate features.  Gold IDs and question IDs
are introduced after ranking, when the ordinary retrieval metrics are applied.
"""

from __future__ import annotations

import argparse
import html
import json
import time
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean
from typing import Any, Sequence

from src.retrieval import lexical_tokens
from tools.bm25_vector_fairness_evaluate import (
    CORE_METRICS,
    _group_metrics,
    _metric_summary,
    _write_csv,
    ensure_fairness_artifact_safe,
)
from tools.chroma_baseline_evaluate import load_catalog
from tools.retrieval_strategy_evaluate import _evaluated_result

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "transfusion_retrieval_baseline.json"
DEFAULT_CATALOG = ROOT / "data" / "library" / "catalog.sqlite3"
DEFAULT_FROZEN = ROOT / "workspace" / "과거작업" / "평가산출물" / "2026-09-17_bm25-e5-retrieval-strategy-02"
DEFAULT_OUTPUT = ROOT / "workspace" / "과거작업" / "평가산출물" / "2026-09-18_schat-final-mvp-completion" / "retrieval"
STRATEGIES = (
    "no_reranker",
    "normalized_input",
    "candidate_aware",
    "rank_preserving",
    "top_n_rerank",
    "origin_feature",
)


@dataclass(frozen=True)
class CandidateRanks:
    chunk_id: str
    raw_rank: int
    raw_score: float
    bm25_rank: int | None
    e5_rank: int | None
    current_rerank_rank: int
    query_overlap: float


def _windowed(
    candidates: Sequence[CandidateRanks],
    *,
    width: int,
    key: Any,
) -> tuple[CandidateRanks, ...]:
    ranked: list[CandidateRanks] = []
    for start in range(0, len(candidates), width):
        ranked.extend(sorted(candidates[start : start + width], key=key))
    return tuple(ranked)


def rerank_candidates(
    *, candidates: Sequence[CandidateRanks], strategy: str
) -> tuple[CandidateRanks, ...]:
    """Apply a deterministic, gold-independent rerank to one frozen Top-10."""
    ordered = tuple(sorted(candidates, key=lambda item: item.raw_rank))
    if not ordered or len({item.chunk_id for item in ordered}) != len(ordered):
        raise ValueError("candidate identity contract failure")
    if strategy == "no_reranker":
        return ordered
    if strategy == "normalized_input":
        return _windowed(
            ordered,
            width=3,
            key=lambda item: (-item.query_overlap, item.raw_rank),
        )
    if strategy == "candidate_aware":
        return _windowed(
            ordered,
            width=3,
            key=lambda item: (
                -(item.bm25_rank is not None and item.e5_rank is not None),
                min(item.bm25_rank or 99, item.e5_rank or 99),
                item.raw_rank,
            ),
        )
    if strategy == "rank_preserving":
        return _windowed(
            ordered,
            width=3,
            key=lambda item: (item.current_rerank_rank, item.raw_rank),
        )
    if strategy == "top_n_rerank":
        head = sorted(
            ordered[:5], key=lambda item: (item.current_rerank_rank, item.raw_rank)
        )
        return tuple((*head, *ordered[5:]))
    if strategy == "origin_feature":
        def origin_score(item: CandidateRanks) -> float:
            reciprocal = sum(
                1 / (60 + rank)
                for rank in (item.bm25_rank, item.e5_rank)
                if rank is not None
            )
            dual = 0.0005 if item.bm25_rank is not None and item.e5_rank is not None else 0.0
            return item.raw_score + reciprocal * 0.05 + dual

        return tuple(
            sorted(ordered, key=lambda item: (-origin_score(item), item.raw_rank))
        )
    raise ValueError(f"unknown reranker strategy: {strategy}")


def _core(summary: dict[str, Any]) -> float:
    return fmean(float(summary["overall"][name]) for name in CORE_METRICS)


def decide_production_retrieval(summaries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Require material accuracy gain, no recall loss, and bounded latency."""
    if "bm25_current" not in summaries:
        raise ValueError("BM25 comparison is required")
    candidates = {name: value for name, value in summaries.items() if name != "bm25_current"}
    if not candidates:
        raise ValueError("at least one candidate strategy is required")
    best = max(candidates, key=lambda name: _core(candidates[name]))
    best_summary = candidates[best]
    bm25 = summaries["bm25_current"]
    reasons: list[str] = []
    if float(best_summary["latency_ms"]["mean"]) > 100.0:
        reasons.append("latency")
    if any(
        float(summary["overall"]["recall_at_10"])
        < float(bm25["overall"]["recall_at_10"])
        for summary in candidates.values()
    ):
        reasons.append("recall_regression")
    gain = _core(best_summary) - _core(bm25)
    if gain < 0.03:
        reasons.append("insufficient_material_gain")
    production_changed = not reasons and best not in {"no_reranker", "current_reranker"}
    if best == "no_reranker":
        reasons.append("requires_existing_safety_reranker_bypass")
        production_changed = False
    if best == "current_reranker":
        reasons.append("existing_production_already_selected")
        production_changed = False
    return {
        "selected": best if production_changed else "existing_production_hybrid",
        "best_evaluation_strategy": best,
        "best_core_score": _core(best_summary),
        "bm25_core_score": _core(bm25),
        "gain_over_bm25": gain,
        "reasons": list(dict.fromkeys(reasons)),
        "production_changed": production_changed,
    }


def _read_rows(path: Path) -> list[dict[str, Any]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or len(rows) != 90:
        raise ValueError(f"frozen result coverage failure: {path.name}")
    return rows


def _rank_map(row: dict[str, Any]) -> dict[str, int]:
    return {
        identifier: rank
        for rank, identifier in enumerate(row["retrieved_context_ids"], 1)
    }


def _overlap(question: str, text: str) -> float:
    query = set(lexical_tokens(question))
    return len(query.intersection(lexical_tokens(text))) / max(1, len(query))


def _review_html(report: dict[str, Any]) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(name)}</td>"
        f"<td>{summary['overall']['hit_at_5']:.4f}</td>"
        f"<td>{summary['overall']['hit_at_10']:.4f}</td>"
        f"<td>{summary['overall']['mrr']:.4f}</td>"
        f"<td>{summary['overall']['recall_at_10']:.4f}</td>"
        f"<td>{summary['overall']['precision_at_10']:.4f}</td>"
        f"<td>{summary['latency_ms']['mean']:.3f}</td>"
        "</tr>"
        for name, summary in report["summaries"].items()
    )
    decision = report["decision"]
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'>
<title>SCHAT reranker bottleneck</title><style>
body{{font-family:system-ui,sans-serif;margin:32px;background:#f6f9fc;color:#17324d}}
section{{background:white;border:1px solid #d5e0e8;border-radius:12px;padding:18px;margin:12px 0}}
table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #d5e0e8;padding:7px}}th{{background:#eaf3f8}}
</style></head><body><section><h1>Reranker bottleneck evaluation</h1>
<p>Frozen 90-question corpus · external API 0 · production change 0</p>
<p><b>Decision:</b> {html.escape(decision['selected'])}</p>
<p>{html.escape(', '.join(decision['reasons']))}</p></section><section><table><thead><tr>
<th>Strategy</th><th>Hit@5</th><th>Hit@10</th><th>MRR</th><th>Recall@10</th><th>Precision@10</th><th>Mean ms</th>
</tr></thead><tbody>{rows}</tbody></table></section></body></html>"""


def evaluate_reranker_bottleneck(
    *,
    fixture_path: Path,
    catalog_path: Path,
    frozen_results_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    metadata, chunks = load_catalog(
        catalog_path, document_name=fixture["document"]["name"]
    )
    del metadata
    chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    cases = {case["question_id"]: case for case in fixture["cases"]}
    frozen_names = {
        "bm25_current": "bm25_current_results.json",
        "e5_large": "e5_large_results.json",
        "no_reranker": "bm25_e5_rrf_results.json",
        "current_reranker": "bm25_e5_rrf_rerank_results.json",
    }
    frozen = {
        name: _read_rows(frozen_results_dir / filename)
        for name, filename in frozen_names.items()
    }
    indexed = {
        name: {row["question_id"]: row for row in rows}
        for name, rows in frozen.items()
    }
    if any(set(rows) != set(cases) for rows in indexed.values()):
        raise ValueError("frozen question identity drift")

    results: dict[str, list[dict[str, Any]]] = {
        name: list(rows) for name, rows in frozen.items()
    }
    details: list[dict[str, Any]] = []
    for strategy in STRATEGIES[1:]:
        strategy_rows = []
        for question_id, case in cases.items():
            raw = indexed["no_reranker"][question_id]
            bm25_ranks = _rank_map(indexed["bm25_current"][question_id])
            e5_ranks = _rank_map(indexed["e5_large"][question_id])
            current_ranks = _rank_map(indexed["current_reranker"][question_id])
            candidates = tuple(
                CandidateRanks(
                    chunk_id=identifier,
                    raw_rank=rank,
                    raw_score=float(score),
                    bm25_rank=bm25_ranks.get(identifier),
                    e5_rank=e5_ranks.get(identifier),
                    current_rerank_rank=current_ranks.get(identifier, 99),
                    query_overlap=_overlap(
                        case["question"],
                        f"{chunks_by_id[identifier].section} {chunks_by_id[identifier].text}",
                    ),
                )
                for rank, (identifier, score) in enumerate(
                    zip(raw["retrieved_context_ids"], raw["scores"], strict=True), 1
                )
            )
            started = time.perf_counter()
            ranked = rerank_candidates(candidates=candidates, strategy=strategy)
            rerank_ms = (time.perf_counter() - started) * 1000
            identifiers = [item.chunk_id for item in ranked]
            scores = [1.0 / rank for rank in range(1, len(ranked) + 1)]
            strategy_rows.append(
                _evaluated_result(
                    case=case,
                    retrieved_ids=identifiers,
                    scores=scores,
                    latency_ms=float(raw["latency_ms"]) + rerank_ms,
                )
            )
            details.append(
                {
                    "strategy": strategy,
                    "question_id": question_id,
                    "question_type": case["question_type"],
                    "raw_ids": list(raw["retrieved_context_ids"]),
                    "reranked_ids": identifiers,
                    "rerank_latency_ms": rerank_ms,
                }
            )
        results[strategy] = strategy_rows

    summaries = {name: _metric_summary(rows) for name, rows in results.items()}
    decision = decide_production_retrieval(summaries)
    report = {
        "dataset": {
            "dataset_version": fixture["dataset_version"],
            "document_version": fixture["document_version"],
            "chunk_count": len(chunks),
            "case_count": len(cases),
            "approved_positive_count": sum(
                case.get("expected_answerable", True)
                and not case.get("needs_human_review")
                for case in cases.values()
            ),
            "human_review_count": sum(
                bool(case.get("needs_human_review")) for case in cases.values()
            ),
        },
        "summaries": summaries,
        "decision": decision,
        "safety": {
            "gold_used_during_ranking": False,
            "question_id_used_during_ranking": False,
            "production_retrieval_changed": False,
            "external_api_calls": 0,
            "hospital_data_external_transfers": 0,
            "source_text_persisted": False,
            "question_text_persisted": False,
        },
    }
    type_rows = _group_metrics(list(cases.values()), results)
    forbidden = {chunk.text for chunk in chunks}
    for payload in (report, details, type_rows):
        ensure_fairness_artifact_safe(payload, forbidden)

    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "reranker_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "reranker_results.json").write_text(
        json.dumps(details, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    metric_rows = [
        {
            "strategy": name,
            **summary["overall"],
            "mean_latency_ms": summary["latency_ms"]["mean"],
            "p95_latency_ms": summary["latency_ms"]["p95"],
            "core_score": _core(summary),
        }
        for name, summary in summaries.items()
    ]
    _write_csv(output_dir / "reranker_metrics.csv", metric_rows)
    _write_csv(output_dir / "reranker_metrics_by_type.csv", type_rows)
    (output_dir / "review.html").write_text(_review_html(report), encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--frozen-results-dir", type=Path, default=DEFAULT_FROZEN)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = evaluate_reranker_bottleneck(
        fixture_path=args.fixture,
        catalog_path=args.catalog,
        frozen_results_dir=args.frozen_results_dir,
        output_dir=args.output_dir,
    )
    print(json.dumps({"decision": report["decision"], "external_api_calls": 0}))


if __name__ == "__main__":
    main()
