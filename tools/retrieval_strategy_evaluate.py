"""Shared offline comparison helpers for Chroma, BM25, and RRF Hybrid."""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import platform
import sys
import time
from collections import defaultdict
from pathlib import Path
from statistics import fmean
from typing import Any, Sequence

import numpy as np

from src.library import Chunk, Embedder, bounded_embedding_question
from src.retrieval import BM25Index
from src.settings import DIMENSIONS, MODEL
from tools.chroma_baseline_evaluate import (
    CatalogChunk,
    _directory_size,
    _package_version,
    _ragas_id_scores,
    ensure_raw_text_free,
    load_catalog,
    query_chroma,
    validate_evaluation_cases,
    validate_fixture_document,
)
from tools.retrieval_baseline_metrics import id_based_context_scores, ranked_retrieval_metrics
from tools.schat_mvp_stabilize import (
    prior_chroma_case_identity_matches,
    split_reusable_chroma_cases,
    validate_review_audit,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "library" / "catalog.sqlite3"
DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "transfusion_retrieval_baseline.json"
DEFAULT_PRIOR_CHROMA = ROOT / "workspace" / "RAGAS" / "2026-09-16_transfusion-chromadb-ragas-baseline"
DEFAULT_OUTPUT = ROOT / "workspace" / "과거작업" / "평가산출물" / "2026-09-17_transfusion-expanded-retrieval"

COMMON_ROW_FIELDS = (
    "dataset_version",
    "document_version",
    "chunk_version",
    "retriever",
    "question_id",
    "question_type",
    "expected_answerable",
    "needs_human_review",
    "k",
    "rank",
    "retrieved_chunk_id",
    "score",
    "is_gold",
    "latency_ms",
)
CORE_SELECTION_METRICS = ("hit_at_5", "hit_at_10", "mrr", "recall_at_10")


def _evaluated_result(
    *,
    case: dict[str, Any],
    retrieved_ids: Sequence[str],
    scores: Sequence[float],
    latency_ms: float,
) -> dict[str, Any]:
    if len(retrieved_ids) != len(scores) or len(retrieved_ids) != len(set(retrieved_ids)):
        raise ValueError("ranked IDs and scores must be aligned and distinct")
    references = list(case["reference_context_ids"])
    metrics: dict[str, float] = {}
    ragas: dict[str, float] = {}
    if case["expected_answerable"]:
        metrics = ranked_retrieval_metrics(retrieved_ids, references)
        ragas = id_based_context_scores(retrieved_ids, references)
    return {
        "question_id": case["question_id"],
        "question": case["question"],
        "question_type": case["question_type"],
        "expected_answerable": case["expected_answerable"],
        "needs_human_review": case["needs_human_review"],
        "review_reasons": list(case["review_reasons"]),
        "reference_context_ids": references,
        "retrieved_context_ids": list(retrieved_ids),
        "scores": [float(score) for score in scores],
        "latency_ms": float(latency_ms),
        "metrics": metrics,
        "ragas": ragas,
    }


def load_reused_chroma_results(
    cases: Sequence[dict[str, Any]], prior_results_path: Path, *, allowed_ids: set[str]
) -> list[dict[str, Any]]:
    """Reuse the completed positive Chroma run only after exact case identity checks."""
    payload = json.loads(prior_results_path.read_text(encoding="utf-8"))
    prior_by_id = {row.get("case_id"): row for row in payload.get("cases", [])}
    positive = [case for case in cases if case["expected_answerable"]]
    if len(prior_by_id) != len(positive):
        raise ValueError("prior Chroma positive case count drift")
    results = []
    for case in positive:
        prior = prior_by_id.get(case["question_id"])
        if prior is None or not prior_chroma_case_identity_matches(prior, case):
            raise ValueError(f"prior Chroma case drift: {case['question_id']}")
        ids = prior.get("retrieved_context_ids")
        distances = prior.get("distances")
        if (
            not isinstance(ids, list)
            or not isinstance(distances, list)
            or len(ids) != 10
            or len(ids) != len(distances)
            or len(ids) != len(set(ids))
        ):
            raise ValueError(f"prior Chroma ranking drift: {case['question_id']}")
        if not set(ids).issubset(allowed_ids):
            raise ValueError(f"prior Chroma ranking is outside current catalog: {case['question_id']}")
        latency = prior.get("latency_ms", {}).get("total")
        if not isinstance(latency, (int, float)) or latency < 0:
            raise ValueError(f"prior Chroma latency drift: {case['question_id']}")
        results.append(
            _evaluated_result(
                case=case,
                retrieved_ids=ids,
                scores=[1.0 - float(distance) for distance in distances],
                latency_ms=float(latency),
            )
        )
    return results


def validate_dataset_identity(fixture: dict[str, Any], metadata: dict[str, Any]) -> None:
    expected_document_version = f"sha256:{metadata.get('file_hash', '')}"
    if fixture.get("document_version") != expected_document_version:
        raise ValueError("document version drift")
    if fixture.get("dataset_version") != "transfusion-retrieval-v3":
        raise ValueError("dataset version drift")


def expand_common_rows(
    *, contract: dict[str, Any], retriever: str, result: dict[str, Any]
) -> list[dict[str, Any]]:
    """Expand one ranked result into the frozen K/rank comparison rows."""
    retrieved = result["retrieved_context_ids"]
    scores = result["scores"]
    if len(retrieved) != len(scores) or len(retrieved) != len(set(retrieved)):
        raise ValueError("ranked IDs and scores must be aligned and distinct")
    gold = set(result["reference_context_ids"])
    rows = []
    for cutoff in contract["cutoffs"]:
        for rank, (chunk_id, score) in enumerate(zip(retrieved[:cutoff], scores[:cutoff], strict=True), 1):
            rows.append({
                "dataset_version": contract["dataset_version"],
                "document_version": contract["document_version"],
                "chunk_version": contract["chunk_version"],
                "retriever": retriever,
                "question_id": result["question_id"],
                "question_type": result["question_type"],
                "expected_answerable": result["expected_answerable"],
                "needs_human_review": result["needs_human_review"],
                "k": cutoff,
                "rank": rank,
                "retrieved_chunk_id": chunk_id,
                "score": float(score),
                "is_gold": chunk_id in gold,
                "latency_ms": float(result["latency_ms"]),
            })
    return rows


def _distribution(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0, "mean": 0.0, "minimum": 0.0, "p50": 0.0, "p95": 0.0, "maximum": 0.0}
    return {
        "count": len(values),
        "mean": round(fmean(values), 12),
        "minimum": min(values),
        "p50": float(np.percentile(values, 50)),
        "p95": float(np.percentile(values, 95)),
        "maximum": max(values),
    }


def negative_score_diagnostics(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Compare positive and negative Top-1 scores without mixing negative IR metrics."""
    positive_scores = [
        float(row["scores"][0])
        for row in results
        if row["expected_answerable"] and not row["needs_human_review"] and row["scores"]
    ]
    negative = [row for row in results if not row["expected_answerable"]]
    negative_scores = [float(row["scores"][0]) for row in negative if row["scores"]]
    counts = [len(row["retrieved_context_ids"]) for row in negative]
    positive_summary = _distribution(positive_scores)
    negative_summary = _distribution(negative_scores)
    return {
        "positive_top1": positive_summary,
        "negative_top1": negative_summary,
        "mean_score_separation": round(
            float(positive_summary["mean"]) - float(negative_summary["mean"]), 12
        ),
        "negative_returned_count": {
            "mean": fmean(counts) if counts else 0.0,
            "minimum": min(counts) if counts else 0,
            "maximum": max(counts) if counts else 0,
        },
    }


def _source_chunk(chunk: CatalogChunk) -> Chunk:
    return Chunk(
        id=chunk.chunk_id,
        document_id=chunk.document_id,
        document_name=chunk.document_name,
        page=chunk.page,
        title=chunk.title,
        section=chunk.section,
        updated_date=None,
        text=chunk.text,
        index=chunk.position,
        source_type=chunk.source_type,
        location=chunk.location,
        parent_id=chunk.parent_id,
    )


def bm25_ranking(
    question: str,
    chunks: Sequence[CatalogChunk],
    *,
    limit: int,
    index: BM25Index | None = None,
) -> dict[str, Any]:
    """Score with the existing BM25 index and stable source-position tiebreak."""
    source_chunks = [_source_chunk(chunk) for chunk in chunks]
    index = index or BM25Index(source_chunks)
    started = time.perf_counter()
    scores = index.scores(question)
    positions = sorted(range(len(chunks)), key=lambda position: (-float(scores[position]), position))[:limit]
    latency_ms = (time.perf_counter() - started) * 1000
    return {
        "retrieved_context_ids": [chunks[position].chunk_id for position in positions],
        "scores": [float(scores[position]) for position in positions],
        "latency_ms": latency_ms,
    }


def rrf_ranking(
    rankings: Sequence[Sequence[str]],
    *,
    source_order: dict[str, int],
    limit: int,
    rrf_k: int = 60,
) -> tuple[list[str], list[float]]:
    if limit < 1 or rrf_k < 1:
        raise ValueError("limit and rrf_k must be positive")
    scores: defaultdict[str, float] = defaultdict(float)
    for ranking in rankings:
        if len(ranking) != len(set(ranking)):
            raise ValueError("RRF rankings must contain distinct IDs")
        for rank, identifier in enumerate(ranking, 1):
            if identifier not in source_order:
                raise ValueError("RRF identifier is outside the catalog")
            scores[identifier] += 1.0 / (rrf_k + rank)
    ranked = sorted(scores, key=lambda identifier: (-scores[identifier], source_order[identifier]))[:limit]
    return ranked, [scores[identifier] for identifier in ranked]


def standalone_results_are_complementary(
    chroma_cases: Sequence[dict[str, Any]], bm25_cases: Sequence[dict[str, Any]]
) -> bool:
    chroma = {row["question_id"]: row["metrics"]["hit_at_10"] for row in chroma_cases}
    bm25 = {row["question_id"]: row["metrics"]["hit_at_10"] for row in bm25_cases}
    if chroma.keys() != bm25.keys():
        raise ValueError("standalone case sets differ")
    chroma_wins = any(chroma[key] > bm25[key] for key in chroma)
    bm25_wins = any(bm25[key] > chroma[key] for key in chroma)
    return chroma_wins and bm25_wins


def select_retrieval_strategy(summaries: dict[str, dict[str, float]]) -> dict[str, Any]:
    if not summaries:
        raise ValueError("retrieval summaries are required")
    complexity_order = {"bm25": 0, "chromadb": 1, "hybrid_rrf": 2}

    def score(item: tuple[str, dict[str, float]]) -> tuple[float, int]:
        name, metrics = item
        mean_metric = fmean(float(metrics[key]) for key in CORE_SELECTION_METRICS)
        return mean_metric, -complexity_order.get(name, 99)

    selected, metrics = max(summaries.items(), key=score)
    return {
        "selected": selected,
        "selection_metrics": list(CORE_SELECTION_METRICS),
        "mean_core_metric": score((selected, metrics))[0],
        "tie_break": "lower implementation complexity",
        "production_applied": False,
    }


def _engine_summary(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    from tools.retrieval_baseline_metrics import aggregate_metrics

    aggregate_rows = []
    for row in results:
        metrics = dict(row["metrics"])
        if row["ragas"]:
            metrics.update(
                {
                    "ragas_id_context_precision": row["ragas"]["context_precision"],
                    "ragas_id_context_recall": row["ragas"]["context_recall"],
                }
            )
        aggregate_rows.append({**row, "metrics": metrics})
    summary = aggregate_metrics(aggregate_rows)
    latencies = [float(row["latency_ms"]) for row in results]
    summary["latency_ms"] = _distribution(latencies)
    summary["negative_diagnostics"] = negative_score_diagnostics(results)
    for kind, metrics in summary["by_question_type"].items():
        type_latencies = [
            float(row["latency_ms"])
            for row in results
            if row["question_type"] == kind
            and row["expected_answerable"]
            and not row["needs_human_review"]
        ]
        metrics["mean_latency_ms"] = fmean(type_latencies)
    return summary


def _open_chroma_collection(index_dir: Path):
    import chromadb
    from chromadb.config import Settings

    client = chromadb.PersistentClient(
        path=str(index_dir), settings=Settings(anonymized_telemetry=False)
    )
    collection = client.get_collection(name="transfusion_baseline", embedding_function=None)
    return client, collection


def _chroma_new_results(
    cases: Sequence[dict[str, Any]], *, collection: Any, embedder: Embedder
) -> list[dict[str, Any]]:
    results = []
    for case in cases:
        started = time.perf_counter()
        query_text = bounded_embedding_question(case["question"], embedder)
        vector = embedder.encode([query_text])[0]
        ids, distances = query_chroma(collection, vector, limit=10)
        latency_ms = (time.perf_counter() - started) * 1000
        results.append(
            _evaluated_result(
                case=case,
                retrieved_ids=ids,
                scores=[1.0 - distance for distance in distances],
                latency_ms=latency_ms,
            )
        )
    return results


def _order_results(
    cases: Sequence[dict[str, Any]], results: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_id = {row["question_id"]: row for row in results}
    if set(by_id) != {case["question_id"] for case in cases}:
        raise ValueError("retrieval result case coverage drift")
    return [by_id[case["question_id"]] for case in cases]


def _bm25_results(
    cases: Sequence[dict[str, Any]], chunks: Sequence[CatalogChunk]
) -> tuple[list[dict[str, Any]], float]:
    source_chunks = [_source_chunk(chunk) for chunk in chunks]
    started = time.perf_counter()
    index = BM25Index(source_chunks)
    build_ms = (time.perf_counter() - started) * 1000
    results = []
    for case in cases:
        ranked = bm25_ranking(case["question"], chunks, limit=10, index=index)
        results.append(
            _evaluated_result(
                case=case,
                retrieved_ids=ranked["retrieved_context_ids"],
                scores=ranked["scores"],
                latency_ms=ranked["latency_ms"],
            )
        )
    return results, build_ms


def _hybrid_results(
    cases: Sequence[dict[str, Any]],
    chroma_results: Sequence[dict[str, Any]],
    bm25_results: Sequence[dict[str, Any]],
    *,
    chunks: Sequence[CatalogChunk],
) -> list[dict[str, Any]]:
    chroma_by_id = {row["question_id"]: row for row in chroma_results}
    bm25_by_id = {row["question_id"]: row for row in bm25_results}
    source_order = {chunk.chunk_id: chunk.position for chunk in chunks}
    results = []
    for case in cases:
        chroma = chroma_by_id[case["question_id"]]
        bm25 = bm25_by_id[case["question_id"]]
        started = time.perf_counter()
        ids, scores = rrf_ranking(
            [chroma["retrieved_context_ids"], bm25["retrieved_context_ids"]],
            source_order=source_order,
            limit=10,
        )
        fusion_ms = (time.perf_counter() - started) * 1000
        results.append(
            _evaluated_result(
                case=case,
                retrieved_ids=ids,
                scores=scores,
                latency_ms=chroma["latency_ms"] + bm25["latency_ms"] + fusion_ms,
            )
        )
    return results


def _validate_ragas(results_by_engine: dict[str, Sequence[dict[str, Any]]]) -> int:
    validated = 0
    for results in results_by_engine.values():
        for row in results:
            if not row["expected_answerable"] or row["needs_human_review"]:
                continue
            actual = _ragas_id_scores(row["retrieved_context_ids"], row["reference_context_ids"])
            if actual != row["ragas"]:
                raise RuntimeError("RAGAS ID score drift")
            validated += 1
    return validated


def _write_csv(path: Path, rows: Sequence[dict[str, Any]], fieldnames: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _common_contract(fixture: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "dataset_version": fixture["dataset_version"],
        "document_version": fixture["document_version"],
        "chunk_version": fixture["document"]["chunk_version"],
        "cutoffs": fixture["cutoffs"],
        "row_fields": list(COMMON_ROW_FIELDS),
        "latency_definition": (
            "per-question local retrieval wall time; Chroma includes local query embedding and "
            "vector query, BM25 includes lexical scoring/ranking, Hybrid includes both plus RRF fusion"
        ),
        "score_semantics": {
            "chromadb": "cosine similarity = 1 - Chroma cosine distance",
            "bm25": "existing BM25Index lexical score",
            "hybrid_rrf": "sum(1 / (60 + rank))",
        },
        "metric_formulas": {
            "hit_at_k": "1 if any reference_context_id occurs in first k results else 0",
            "mrr": "1 / rank of first gold result, else 0",
            "recall_at_k": "gold IDs in first k / number of gold IDs",
            "precision_at_k": "gold IDs in first k / number of returned IDs in first k",
            "ragas_id_context_precision": "gold IDs in Top-10 / 10",
            "ragas_id_context_recall": "gold IDs in Top-10 / number of gold IDs",
        },
        "multi_gold_policy": (
            "A hit requires any gold; recall counts all unique gold IDs; precision uses literal k."
        ),
        "aggregate_policy": (
            "Only expected_answerable=true and needs_human_review=false cases enter IR/RAGAS means."
        ),
        "negative_policy": "Excluded from IR/RAGAS; reported as score-distribution diagnostics only.",
        "preprocessing": "Same raw question; each baseline uses only its native local scoring path.",
    }


def _results_payload(
    *, retriever: str, contract: dict[str, Any], results: Sequence[dict[str, Any]], summary: dict[str, Any]
) -> dict[str, Any]:
    safe_results = [
        {key: value for key, value in result.items() if key != "question"}
        for result in results
    ]
    return {
        "schema_version": contract["schema_version"],
        "dataset_version": contract["dataset_version"],
        "document_version": contract["document_version"],
        "chunk_version": contract["chunk_version"],
        "retriever": retriever,
        "summary": summary,
        "cases": safe_results,
    }


def _review_html(report: dict[str, Any]) -> str:
    cards = []
    for name, summary in report["summaries"].items():
        overall = summary["overall"]
        cards.append(
            "<article><h2>"
            + html.escape(name)
            + "</h2><p>Hit@5 <b>"
            + f"{overall['hit_at_5']:.3f}"
            + "</b> · Hit@10 <b>"
            + f"{overall['hit_at_10']:.3f}"
            + "</b> · MRR <b>"
            + f"{overall['mrr']:.3f}"
            + "</b> · Recall@10 <b>"
            + f"{overall['recall_at_10']:.3f}"
            + "</b> · mean latency <b>"
            + f"{summary['latency_ms']['mean']:.2f} ms</b></p></article>"
        )
    rows = []
    for row in report["type_rows"]:
        rows.append(
            "<tr>"
            + "".join(
                f"<td>{html.escape(str(row[key]))}</td>"
                for key in (
                    "retriever",
                    "question_type",
                    "case_count",
                    "hit_at_5",
                    "hit_at_10",
                    "mrr",
                    "recall_at_10",
                    "mean_latency_ms",
                )
            )
            + "</tr>"
        )
    selection = report["selection"]
    manifest = report["manifest"]
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'>
<title>수혈 retrieval baseline 비교</title><style>
body{{font-family:system-ui,sans-serif;margin:32px;color:#16324a;background:#f7fafc}}
header,article,section{{background:white;border:1px solid #d7e1e8;border-radius:12px;padding:18px;margin:12px 0}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}}
table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{border:1px solid #d7e1e8;padding:7px}}th{{background:#eaf3f8}}
</style></head><body><header><h1>수혈 retrieval baseline 및 전략 선택</h1>
<p>총 {manifest['case_count']}문항 · 승인 gold {manifest['approved_case_count']} · 사람 검토 {manifest['human_review_case_count']} · negative {manifest['negative_case_count']}</p>
<p>선택: <b>{html.escape(selection['selected'])}</b> · production 반영: 없음 · 생성 API 호출: 0</p></header>
<div class='cards'>{''.join(cards)}</div><section><h2>질문 유형별 비교</h2><table><thead><tr>
<th>Retriever</th><th>Type</th><th>N</th><th>Hit@5</th><th>Hit@10</th><th>MRR</th><th>Recall@10</th><th>Mean latency (ms)</th>
</tr></thead><tbody>{''.join(rows)}</tbody></table></section></body></html>"""


def evaluate_strategy(
    *,
    catalog_path: Path,
    fixture_path: Path,
    prior_chroma_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
    os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    metadata, chunks = load_catalog(catalog_path, document_name=fixture["document"]["name"])
    validate_fixture_document(fixture["document"], metadata, chunks)
    validate_dataset_identity(fixture, metadata)
    audit = validate_evaluation_cases(
        fixture["cases"], chunks, minimum_cases=80, maximum_cases=100
    )
    review_audit = validate_review_audit(fixture["cases"], chunks)
    if tuple(fixture["cutoffs"]) != (1, 3, 5, 10):
        raise ValueError("comparison cutoffs must be 1,3,5,10")
    if metadata["model"] != MODEL or DIMENSIONS != 384 or metadata["chunk_version"] != 4:
        raise ValueError("embedding or chunk contract drift")
    contract = _common_contract(fixture)

    reusable_cases, pending_cases = split_reusable_chroma_cases(
        fixture["cases"], prior_chroma_dir / "chroma_results.json"
    )
    reused_chroma = load_reused_chroma_results(
        reusable_cases,
        prior_chroma_dir / "chroma_results.json",
        allowed_ids={chunk.chunk_id for chunk in chunks},
    )
    index_dir = prior_chroma_dir / "chroma_index"
    _, collection = _open_chroma_collection(index_dir)
    if collection.count() != len(chunks):
        raise ValueError("reused Chroma collection count drift")
    if set(collection.get(include=[])["ids"]) != {chunk.chunk_id for chunk in chunks}:
        raise ValueError("reused Chroma collection ID drift")
    new_chroma = _chroma_new_results(
        pending_cases, collection=collection, embedder=Embedder()
    )
    chroma_results = _order_results(fixture["cases"], [*reused_chroma, *new_chroma])
    bm25_results, bm25_build_ms = _bm25_results(fixture["cases"], chunks)

    approved_chroma = [
        row for row in chroma_results if row["expected_answerable"] and not row["needs_human_review"]
    ]
    approved_bm25 = [
        row for row in bm25_results if row["expected_answerable"] and not row["needs_human_review"]
    ]
    results_by_engine: dict[str, list[dict[str, Any]]] = {
        "chromadb": chroma_results,
        "bm25": bm25_results,
    }
    hybrid_run = standalone_results_are_complementary(approved_chroma, approved_bm25)
    if hybrid_run:
        results_by_engine["hybrid_rrf"] = _hybrid_results(
            fixture["cases"], chroma_results, bm25_results, chunks=chunks
        )
    ragas_validated_cases = _validate_ragas(results_by_engine)
    summaries = {name: _engine_summary(rows) for name, rows in results_by_engine.items()}
    selection = select_retrieval_strategy(
        {name: summary["overall"] for name, summary in summaries.items()}
    )
    selection["reason"] = "highest mean of Hit@5, Hit@10, MRR, and Recall@10"
    selection["production_applied"] = False
    selection["production_reason"] = (
        "Standalone BM25 wins the comparison, but production already includes BM25 candidate "
        "generation and this evaluation does not justify bypassing its RRF/rerank safety path."
    )

    output_dir.mkdir(parents=True, exist_ok=False)
    type_rows = []
    common_rows_by_engine: dict[str, list[dict[str, Any]]] = {}
    for name, results in results_by_engine.items():
        common_rows = [
            common
            for result in results
            for common in expand_common_rows(contract=contract, retriever=name, result=result)
        ]
        common_rows_by_engine[name] = common_rows
        _write_csv(output_dir / f"{name.replace('chromadb', 'chroma')}_results.csv", common_rows, COMMON_ROW_FIELDS)
        payload = _results_payload(
            retriever=name, contract=contract, results=results, summary=summaries[name]
        )
        (output_dir / f"{name.replace('chromadb', 'chroma')}_results.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        for kind, metrics in summaries[name]["by_question_type"].items():
            type_rows.append({"retriever": name, "question_type": kind, **metrics})

    chroma_prior_summary = json.loads(
        (prior_chroma_dir / "chroma_summary.json").read_text(encoding="utf-8")
    )
    summaries["chromadb"]["index_build_ms"] = chroma_prior_summary["index_build_ms"]
    summaries["chromadb"]["storage_bytes"] = _directory_size(index_dir)
    summaries["chromadb"]["results_reused"] = len(reused_chroma)
    summaries["chromadb"]["results_newly_queried"] = len(new_chroma)
    summaries["bm25"]["index_build_ms"] = bm25_build_ms
    summaries["bm25"]["storage_bytes"] = 0
    if "hybrid_rrf" in summaries:
        summaries["hybrid_rrf"]["index_build_ms"] = (
            summaries["chromadb"]["index_build_ms"] + bm25_build_ms
        )
        summaries["hybrid_rrf"]["storage_bytes"] = summaries["chromadb"]["storage_bytes"]

    manifest = {
        "schema_version": fixture["schema_version"],
        "dataset_id": fixture["dataset_id"],
        "dataset_version": fixture["dataset_version"],
        "document_version": fixture["document_version"],
        "document": fixture["document"],
        **audit,
        "question_type_counts": {
            kind: sum(case["question_type"] == kind for case in fixture["cases"])
            for kind in sorted({case["question_type"] for case in fixture["cases"]})
        },
        "cutoffs": fixture["cutoffs"],
        "gold_policy": fixture["gold_policy"],
        "reference_fingerprint_algorithm": "sha256",
        "review_audit": review_audit,
    }
    ragas_payload = {
        "mode": "RAGAS ID-based/non-LLM",
        "llm_judge_metrics": "deferred",
        "generation_api_calls": 0,
        "official_ragas_validated_case_engine_pairs": ragas_validated_cases,
        "by_retriever": {
            name: {
                "context_precision": summary["overall"]["ragas_id_context_precision"],
                "context_recall": summary["overall"]["ragas_id_context_recall"],
            }
            for name, summary in summaries.items()
        },
    }
    comparison_template = {
        "contract": contract,
        "retriever": "bm25",
        "required_row_count": len(fixture["cases"]) * sum(fixture["cutoffs"]),
        "rows": [],
        "note": "Populate rows with the frozen contract; this run also emits evaluated BM25 rows.",
    }
    environment = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "chromadb": _package_version("chromadb"),
        "ragas": _package_version("ragas"),
        "fastembed": _package_version("fastembed"),
        "embedding_model": MODEL,
        "embedding_dimensions": DIMENSIONS,
        "chroma_space": "cosine",
        "chroma_collection_count": collection.count(),
        "generation_api_calls": 0,
        "production_requirements_changed": False,
        "production_rag_changed": False,
    }
    report = {
        "manifest": manifest,
        "summaries": summaries,
        "type_rows": type_rows,
        "hybrid_evaluated": hybrid_run,
        "selection": selection,
        "bm25_prior_comparison_status": (
            "No schema-compatible team artifact was reused; BM25 was evaluated locally under the "
            "same frozen contract."
        ),
        "ragas": ragas_payload,
        "generation_api_calls": 0,
    }

    forbidden_texts = {chunk.text for chunk in chunks}
    for payload in (manifest, summaries, ragas_payload, comparison_template, environment, report):
        ensure_raw_text_free(payload, forbidden_exact_texts=forbidden_texts)
    for name, rows in common_rows_by_engine.items():
        ensure_raw_text_free(rows, forbidden_exact_texts=forbidden_texts)

    files = {
        "dataset_manifest.json": manifest,
        "chroma_summary.json": summaries["chromadb"],
        "ragas_results.json": ragas_payload,
        "bm25_comparison_template.json": comparison_template,
        "environment.json": environment,
        "comparison_summary.json": report,
        "test_results.json": {
            "status": "pending final verification",
            "evaluation_generation_api_calls": 0,
        },
    }
    for filename, payload in files.items():
        (output_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    metric_fields = sorted({key for row in type_rows for key in row})
    ordered_type_fields = [
        "retriever",
        "question_type",
        "case_count",
        *[key for key in metric_fields if key not in {"retriever", "question_type", "case_count"}],
    ]
    _write_csv(output_dir / "metrics_by_question_type.csv", type_rows, ordered_type_fields)
    (output_dir / "review.html").write_text(_review_html(report), encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--prior-chroma-dir", type=Path, default=DEFAULT_PRIOR_CHROMA)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = evaluate_strategy(
        catalog_path=args.catalog,
        fixture_path=args.fixture,
        prior_chroma_dir=args.prior_chroma_dir,
        output_dir=args.output_dir,
    )
    print(
        json.dumps(
            {
                "case_count": report["manifest"]["case_count"],
                "human_review_case_count": report["manifest"]["human_review_case_count"],
                "retrievers": list(report["summaries"]),
                "selected": report["selection"]["selected"],
                "generation_api_calls": 0,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
