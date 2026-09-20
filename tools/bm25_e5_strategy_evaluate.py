"""Evaluation-only BM25/E5 hybrid, reranker, and selective-fallback comparison."""

from __future__ import annotations

import argparse
import csv
import ctypes
import html
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from statistics import fmean
from typing import Any, Sequence

import numpy as np

from src.library import Hit, clean, embedding_question
from src.query import plan_query
from src.retrieval import BM25Index, _temporal_phases, lexical_tokens, rerank, rrf
from tools.bm25_vector_fairness_evaluate import (
    CORE_METRICS,
    _group_metrics,
    _metric_summary,
    _summary_rows,
    _write_csv,
    ensure_fairness_artifact_safe,
    exact_vector_ranking,
)
from tools.chroma_baseline_evaluate import (
    _directory_size,
    load_catalog,
    validate_evaluation_cases,
    validate_fixture_document,
)
from tools.multilingual_e5_fairness_evaluate import (
    MODEL_DIMENSIONS,
    MODEL_NAME,
    _embed,
    _load_local_engine,
    e5_passage_text,
    e5_query_text,
)
from tools.retrieval_strategy_evaluate import _evaluated_result, _source_chunk

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "library" / "catalog.sqlite3"
DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "transfusion_retrieval_baseline.json"
DEFAULT_CACHE = ROOT / "data" / "models"
DEFAULT_SNAPSHOT = ROOT / "data" / "evaluation" / "e5-transfusion-v3.npz"
DEFAULT_OUTPUT = ROOT / "workspace" / "과거작업" / "평가산출물" / "2026-09-17_bm25-e5-retrieval-strategy"
DEFAULT_PRIOR_E5_AUDIT = (
    ROOT
    / "workspace"
    / "과거작업"
    / "평가산출물"
    / "2026-09-17_multilingual-e5-large-fairness-validation"
    / "model_audit.json"
)
CONFIGS = (
    "bm25_current",
    "e5_large",
    "bm25_e5_rrf",
    "bm25_e5_rrf_rerank",
    "bm25_selective_e5",
)
CANDIDATE_LIMIT = 40
RESULT_LIMIT = 10


@dataclass(frozen=True)
class SelectiveSignals:
    kind: str
    requested_phase: str
    bm25_top_score: float
    bm25_gap_ratio: float
    lexical_overlap: float
    has_exact_signal: bool
    follow_up: bool


@dataclass(frozen=True)
class SelectiveDecision:
    use_e5: bool
    reasons: tuple[str, ...]


def decide_selective_e5(signals: SelectiveSignals) -> SelectiveDecision:
    """Apply a generic, gold-independent lexical-confidence fallback policy."""
    if signals.has_exact_signal:
        return SelectiveDecision(False, ("exact_lexical_priority",))
    weak = []
    if signals.bm25_top_score < 3.0:
        weak.append("low_top_score")
    if signals.bm25_gap_ratio < 0.10:
        weak.append("weak_score_gap")
    if signals.lexical_overlap < 0.45:
        weak.append("low_lexical_overlap")
    semantic_intent = bool(
        signals.kind in {"procedure", "summary", "synthesis", "comparison"}
        or signals.requested_phase
        or signals.follow_up
    )
    reasons = list(weak)
    if semantic_intent:
        reasons.append("semantic_intent")
    if signals.follow_up:
        reasons.append("follow_up")
    use_e5 = signals.follow_up or (semantic_intent and bool(weak)) or len(weak) >= 2
    if not use_e5:
        return SelectiveDecision(False, ("bm25_confident",))
    return SelectiveDecision(True, tuple(dict.fromkeys(reasons)))


def fuse_rrf(
    rankings: Sequence[Sequence[str]],
    *,
    source_order: dict[str, int],
    limit: int,
) -> tuple[list[str], list[float]]:
    """Reuse SCHAT's k=60 RRF and apply deterministic source-order ties."""
    if limit < 1:
        raise ValueError("limit must be positive")
    for ranking in rankings:
        if len(ranking) != len(set(ranking)):
            raise ValueError("RRF rankings must contain distinct IDs")
        if not set(ranking).issubset(source_order):
            raise ValueError("RRF identifier is outside the catalog")
    scores = rrf(*rankings)
    ranked = sorted(
        scores,
        key=lambda identifier: (-float(scores[identifier]), source_order[identifier]),
    )[:limit]
    return ranked, [float(scores[identifier]) for identifier in ranked]


def save_vector_snapshot(
    path: Path,
    *,
    identifiers: Sequence[str],
    vectors: np.ndarray,
    contract: dict[str, Any],
) -> None:
    matrix = np.asarray(vectors, dtype=np.float32)
    ids = tuple(identifiers)
    if matrix.ndim != 2 or matrix.shape[0] != len(ids):
        raise ValueError("snapshot vector alignment failure")
    if len(set(ids)) != len(ids) or not np.isfinite(matrix).all():
        raise ValueError("snapshot vectors or identifiers are invalid")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        np.savez_compressed(
            handle,
            identifiers=np.asarray(ids),
            vectors=matrix,
            contract_json=np.asarray(json.dumps(contract, sort_keys=True)),
        )


def load_vector_snapshot(
    path: Path,
    *,
    expected_identifiers: Sequence[str],
    expected_contract: dict[str, Any],
) -> tuple[tuple[str, ...], np.ndarray]:
    with np.load(path, allow_pickle=False) as payload:
        identifiers = tuple(str(value) for value in payload["identifiers"].tolist())
        vectors = np.asarray(payload["vectors"], dtype=np.float32)
        contract = json.loads(str(payload["contract_json"].item()))
    if contract != expected_contract:
        raise ValueError("vector snapshot contract drift")
    if identifiers != tuple(expected_identifiers):
        raise ValueError("vector snapshot identifier drift")
    expected_dimensions = int(expected_contract.get("dimensions", MODEL_DIMENSIONS))
    if vectors.shape != (len(identifiers), expected_dimensions):
        raise ValueError("vector snapshot dimension drift")
    if not np.isfinite(vectors).all() or np.any(np.linalg.norm(vectors, axis=1) == 0):
        raise ValueError("vector snapshot integrity failure")
    return identifiers, vectors


def _rss_bytes() -> int:
    """Return current RSS without adding a production dependency."""
    if sys.platform == "win32":
        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        get_current_process = ctypes.windll.kernel32.GetCurrentProcess
        get_current_process.restype = ctypes.c_void_p
        handle = get_current_process()
        get_process_memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
        get_process_memory_info.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(ProcessMemoryCounters),
            ctypes.c_ulong,
        )
        get_process_memory_info.restype = ctypes.c_int
        ok = get_process_memory_info(
            handle, ctypes.byref(counters), counters.cb
        )
        return int(counters.WorkingSetSize) if ok else 0
    try:
        import resource

        scale = 1 if sys.platform == "darwin" else 1024
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale)
    except (ImportError, OSError):
        return 0


def _distribution(values: Sequence[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0, "mean": 0.0, "p95": 0.0, "minimum": 0.0, "maximum": 0.0}
    return {
        "count": len(values),
        "mean": fmean(values),
        "p95": float(np.percentile(values, 95)),
        "minimum": min(values),
        "maximum": max(values),
    }


def _has_exact_signal(question: str) -> bool:
    normalized = clean(question)
    return bool(
        re.search(r"\d", normalized)
        or re.search(r"(?<![A-Za-z])[A-Za-z][A-Za-z0-9+./-]+", normalized)
        or re.search(r"[%㎎㎖㏖℃]", normalized)
    )


def _overlap(question: str, text: str) -> float:
    query_tokens = {
        token for token in lexical_tokens(question) if not token.startswith("ko:")
    }
    body_tokens = {
        token for token in lexical_tokens(text) if not token.startswith("ko:")
    }
    return len(query_tokens & body_tokens) / max(1, len(query_tokens))


def _safe_result(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row[key]
        for key in (
            "question_id",
            "question_type",
            "expected_answerable",
            "needs_human_review",
            "reference_context_ids",
            "retrieved_context_ids",
            "scores",
            "latency_ms",
            "metrics",
            "ragas",
        )
    }


def _scorecard(summaries: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = _summary_rows(summaries)
    for row in rows:
        row["core_score"] = fmean(float(row[name]) for name in CORE_METRICS)
    return rows


def _select_final_strategy(
    summaries: dict[str, dict[str, Any]], *, selective_rate: float
) -> dict[str, Any]:
    overall = {name: value["overall"] for name, value in summaries.items()}
    core = {
        name: fmean(float(metrics[key]) for key in CORE_METRICS)
        for name, metrics in overall.items()
    }
    best = max(core, key=core.get)
    selective_near_best = core["bm25_selective_e5"] >= core[best] - 0.015
    safe_hybrid_gain = (
        core["bm25_e5_rrf_rerank"] - core["bm25_current"]
    )
    if (
        selective_near_best
        and selective_rate <= 0.60
        and core["bm25_selective_e5"] >= core["bm25_current"] + 0.03
    ):
        code = "D"
        selected = "BM25 primary + selective E5 fallback"
        reason = "selective strategy stays within 0.015 core score of the best with E5 on at most 60% of queries"
    elif safe_hybrid_gain >= 0.03:
        code = "C"
        selected = "BM25 + E5 always hybrid"
        reason = "RRF plus the existing reranker adds at least 0.03 core score over current BM25"
    elif core["e5_large"] >= core["bm25_current"] + 0.05 and summaries["e5_large"]["latency_ms"]["mean"] <= 50:
        code = "B"
        selected = "E5 only"
        reason = "E5 is materially more accurate and meets the 50 ms operational target"
    else:
        code = "E"
        selected = "existing production hybrid"
        reason = "accuracy gains do not justify always-on E5 latency/memory or the selective policy is not near-best"
    return {
        "code": code,
        "selected": selected,
        "reason": reason,
        "core_scores": core,
        "best_accuracy_config": best,
        "safe_reranked_hybrid_gain_over_bm25": safe_hybrid_gain,
        "selective_e5_rate": selective_rate,
        "production_changed": False,
    }


def _rerank_fused(
    question: str,
    *,
    documents: Sequence[dict[str, Any]],
    fused_ids: Sequence[str],
    fused_scores: Sequence[float],
    source_by_id: dict[str, Any],
    e5_scores: dict[str, float],
    bm25_scores: dict[str, float],
) -> tuple[list[str], list[float]]:
    plan = replace(plan_query(question, documents=documents), max_seeds=RESULT_LIMIT)
    candidates = [
        Hit(
            source_by_id[identifier],
            similarity=float(e5_scores.get(identifier, 0.0)),
            bm25_score=float(bm25_scores.get(identifier, 0.0)),
            fusion_score=float(score),
        )
        for identifier, score in zip(fused_ids, fused_scores, strict=True)
    ]
    selected = rerank(plan, candidates, minimum=0.38)
    return (
        [hit.chunk.id for hit in selected],
        [float(hit.rerank_score) for hit in selected],
    )


def _review_html(report: dict[str, Any]) -> str:
    rows = []
    for row in report["scorecard"]:
        rows.append(
            "<tr>"
            + "".join(
                f"<td>{html.escape(str(value))}</td>"
                for value in (
                    row["config"],
                    f"{row['hit_at_1']:.4f}",
                    f"{row['hit_at_3']:.4f}",
                    f"{row['hit_at_5']:.4f}",
                    f"{row['hit_at_10']:.4f}",
                    f"{row['mrr']:.4f}",
                    f"{row['recall_at_10']:.4f}",
                    f"{row['precision_at_10']:.4f}",
                    f"{row['mean_latency_ms']:.3f}",
                    f"{row['p95_latency_ms']:.3f}",
                    f"{row['core_score']:.4f}",
                )
            )
            + "</tr>"
        )
    decision = report["decision"]
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'>
<title>BM25 + E5 retrieval strategy</title><style>
body{{font-family:system-ui,sans-serif;margin:32px;color:#17324d;background:#f7fafc}}
header,section{{background:white;border:1px solid #d7e1e8;border-radius:12px;padding:18px;margin:12px 0}}
table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{border:1px solid #d7e1e8;padding:7px}}th{{background:#eaf3f8}}
</style></head><body><header><h1>BM25 + multilingual-E5-large strategy</h1>
<p>105 chunks · 90 questions · approved positives 78 · external API 0</p>
<p><b>{html.escape(decision['code'])} — {html.escape(decision['selected'])}</b></p>
<p>{html.escape(decision['reason'])}</p></header><section><table><thead><tr>
<th>Config</th><th>Hit@1</th><th>Hit@3</th><th>Hit@5</th><th>Hit@10</th><th>MRR</th>
<th>Recall@10</th><th>Precision@10</th><th>Mean ms</th><th>P95 ms</th><th>Core</th>
</tr></thead><tbody>{''.join(rows)}</tbody></table></section></body></html>"""


def evaluate_bm25_e5_strategy(
    *,
    catalog_path: Path,
    fixture_path: Path,
    cache_dir: Path,
    snapshot_path: Path,
    output_dir: Path,
    engine: Any | None = None,
    prior_e5_audit_path: Path = DEFAULT_PRIOR_E5_AUDIT,
) -> dict[str, Any]:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    metadata, chunks = load_catalog(
        catalog_path, document_name=fixture["document"]["name"]
    )
    validate_fixture_document(fixture["document"], metadata, chunks)
    audit = validate_evaluation_cases(
        fixture["cases"], chunks, minimum_cases=80, maximum_cases=100
    )
    if tuple(fixture["cutoffs"]) != (1, 3, 5, 10) or metadata["chunk_version"] != 4:
        raise ValueError("retrieval contract drift")

    identifiers = tuple(chunk.chunk_id for chunk in chunks)
    snapshot_contract = {
        "dataset_version": fixture["dataset_version"],
        "document_version": fixture["document_version"],
        "chunk_version": metadata["chunk_version"],
        "model": MODEL_NAME,
        "dimensions": MODEL_DIMENSIONS,
        "passage_representation": "body_only_with_passage_prefix",
    }
    rss_before = _rss_bytes()
    model_started = time.perf_counter()
    engine = engine or _load_local_engine(cache_dir)
    model_load_ms = (time.perf_counter() - model_started) * 1000
    rss_after_model = _rss_bytes()

    snapshot_reused = snapshot_path.is_file()
    if snapshot_reused:
        snapshot_started = time.perf_counter()
        _, passage_vectors = load_vector_snapshot(
            snapshot_path,
            expected_identifiers=identifiers,
            expected_contract=snapshot_contract,
        )
        passage_build_ms = 0.0
        snapshot_load_ms = (time.perf_counter() - snapshot_started) * 1000
    else:
        passage_started = time.perf_counter()
        passage_vectors = _embed(
            engine,
            [e5_passage_text(chunk.text) for chunk in chunks],
            batch_size=4,
        )
        passage_build_ms = (time.perf_counter() - passage_started) * 1000
        if passage_vectors.shape != (len(chunks), MODEL_DIMENSIONS):
            raise ValueError("E5 passage vector shape drift")
        save_vector_snapshot(
            snapshot_path,
            identifiers=identifiers,
            vectors=passage_vectors,
            contract=snapshot_contract,
        )
        snapshot_started = time.perf_counter()
        _, passage_vectors = load_vector_snapshot(
            snapshot_path,
            expected_identifiers=identifiers,
            expected_contract=snapshot_contract,
        )
        snapshot_load_ms = (time.perf_counter() - snapshot_started) * 1000
    rss_after_index = _rss_bytes()

    source_chunks = [_source_chunk(chunk) for chunk in chunks]
    source_by_id = {chunk.id: chunk for chunk in source_chunks}
    catalog_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    source_order = {chunk.chunk_id: chunk.position for chunk in chunks}
    documents = ({
        "id": metadata["id"],
        "document_name": metadata["document_name"],
        "title": metadata.get("title", ""),
    },)
    bm25_build_started = time.perf_counter()
    bm25_index = BM25Index(source_chunks)
    bm25_build_ms = (time.perf_counter() - bm25_build_started) * 1000

    results: dict[str, list[dict[str, Any]]] = {name: [] for name in CONFIGS}
    component_latency: dict[str, list[float]] = {
        "bm25_query": [],
        "e5_query_embedding": [],
        "e5_exact_search": [],
        "rrf": [],
        "rerank": [],
    }
    selective_decisions = []
    for case in fixture["cases"]:
        question = case["question"]
        bm25_started = time.perf_counter()
        raw_bm25 = bm25_index.scores(question)
        bm25_positions = sorted(
            range(len(chunks)), key=lambda position: (-float(raw_bm25[position]), position)
        )[:CANDIDATE_LIMIT]
        bm25_latency = (time.perf_counter() - bm25_started) * 1000
        bm25_ids = [chunks[position].chunk_id for position in bm25_positions]
        bm25_rank_scores = [float(raw_bm25[position]) for position in bm25_positions]

        query_started = time.perf_counter()
        query_vector = _embed(
            engine,
            [e5_query_text(embedding_question(question))],
            batch_size=1,
        )[0]
        query_embedding_ms = (time.perf_counter() - query_started) * 1000
        search_started = time.perf_counter()
        e5_ids, e5_scores = exact_vector_ranking(
            query_vector, passage_vectors, identifiers, limit=CANDIDATE_LIMIT
        )
        e5_search_ms = (time.perf_counter() - search_started) * 1000
        e5_latency = query_embedding_ms + e5_search_ms

        fusion_started = time.perf_counter()
        fused_ids, fused_scores = fuse_rrf(
            [bm25_ids, e5_ids], source_order=source_order, limit=CANDIDATE_LIMIT
        )
        fusion_ms = (time.perf_counter() - fusion_started) * 1000

        bm25_score_by_id = dict(zip(bm25_ids, bm25_rank_scores, strict=True))
        e5_score_by_id = dict(zip(e5_ids, e5_scores, strict=True))
        rerank_started = time.perf_counter()
        reranked_ids, reranked_scores = _rerank_fused(
            question,
            documents=documents,
            fused_ids=fused_ids,
            fused_scores=fused_scores,
            source_by_id=source_by_id,
            e5_scores=e5_score_by_id,
            bm25_scores=bm25_score_by_id,
        )
        rerank_ms = (time.perf_counter() - rerank_started) * 1000

        top = bm25_rank_scores[0] if bm25_rank_scores else 0.0
        second = bm25_rank_scores[1] if len(bm25_rank_scores) > 1 else 0.0
        gap = (top - second) / max(abs(top), 1e-9) if top else 0.0
        plan = plan_query(question, documents=documents)
        phases = _temporal_phases(question)
        requested_phase = plan.monitoring_phase or (
            next(iter(phases)) if len(phases) == 1 else ""
        )
        top_chunk = catalog_by_id[bm25_ids[0]]
        signals = SelectiveSignals(
            kind=plan.kind,
            requested_phase=requested_phase,
            bm25_top_score=top,
            bm25_gap_ratio=gap,
            lexical_overlap=_overlap(
                question, f"{top_chunk.text} {top_chunk.section}"
            ),
            has_exact_signal=_has_exact_signal(question),
            follow_up=" / 추가 질문: " in question,
        )
        decision = decide_selective_e5(signals)
        selective_ids = fused_ids if decision.use_e5 else bm25_ids
        selective_scores = fused_scores if decision.use_e5 else bm25_rank_scores
        selective_latency = (
            bm25_latency + e5_latency + fusion_ms if decision.use_e5 else bm25_latency
        )

        rows = {
            "bm25_current": (bm25_ids, bm25_rank_scores, bm25_latency),
            "e5_large": (e5_ids, e5_scores, e5_latency),
            "bm25_e5_rrf": (
                fused_ids,
                fused_scores,
                bm25_latency + e5_latency + fusion_ms,
            ),
            "bm25_e5_rrf_rerank": (
                reranked_ids,
                reranked_scores,
                bm25_latency + e5_latency + fusion_ms + rerank_ms,
            ),
            "bm25_selective_e5": (
                selective_ids,
                selective_scores,
                selective_latency,
            ),
        }
        for name, (ranked_ids, scores, latency) in rows.items():
            results[name].append(
                _evaluated_result(
                    case=case,
                    retrieved_ids=ranked_ids[:RESULT_LIMIT],
                    scores=scores[:RESULT_LIMIT],
                    latency_ms=latency,
                )
            )
        component_latency["bm25_query"].append(bm25_latency)
        component_latency["e5_query_embedding"].append(query_embedding_ms)
        component_latency["e5_exact_search"].append(e5_search_ms)
        component_latency["rrf"].append(fusion_ms)
        component_latency["rerank"].append(rerank_ms)
        selective_decisions.append(
            {
                "question_id": case["question_id"],
                "question_type": case["question_type"],
                "use_e5": decision.use_e5,
                "reasons": list(decision.reasons),
                "signals": asdict(signals),
            }
        )

    summaries = {name: _metric_summary(rows) for name, rows in results.items()}
    e5_startup_ms = snapshot_load_ms if snapshot_reused else passage_build_ms
    summaries["bm25_current"]["index_build_ms"] = bm25_build_ms
    summaries["e5_large"]["index_build_ms"] = e5_startup_ms
    summaries["bm25_e5_rrf"]["index_build_ms"] = bm25_build_ms + e5_startup_ms
    summaries["bm25_e5_rrf_rerank"]["index_build_ms"] = (
        bm25_build_ms + e5_startup_ms
    )
    summaries["bm25_selective_e5"]["index_build_ms"] = (
        bm25_build_ms + e5_startup_ms
    )
    selective_rate = sum(row["use_e5"] for row in selective_decisions) / len(selective_decisions)
    decision = _select_final_strategy(summaries, selective_rate=selective_rate)
    scorecard = _scorecard(summaries)
    type_rows = _group_metrics(fixture["cases"], results)
    prior_build_ms = None
    if prior_e5_audit_path.is_file():
        prior_build_ms = float(
            json.loads(prior_e5_audit_path.read_text(encoding="utf-8"))[
                "index_build_ms"
            ]
        )
    runtime = {
        "model_load_ms": model_load_ms,
        "passage_index_build_ms_this_run": passage_build_ms,
        "passage_index_build_reference_ms": (
            passage_build_ms if passage_build_ms else prior_build_ms
        ),
        "snapshot_reused": snapshot_reused,
        "snapshot_load_ms": snapshot_load_ms,
        "snapshot_size_bytes": snapshot_path.stat().st_size,
        "model_cache_size_bytes": _directory_size(
            cache_dir / "models--qdrant--multilingual-e5-large-onnx"
        ),
        "rss_before_bytes": rss_before,
        "rss_after_model_bytes": rss_after_model,
        "rss_after_index_bytes": rss_after_index,
        "rss_model_delta_bytes": max(0, rss_after_model - rss_before),
        "rss_index_delta_bytes": max(0, rss_after_index - rss_after_model),
        "bm25_index_build_ms": bm25_build_ms,
        "component_latency_ms": {
            name: _distribution(values) for name, values in component_latency.items()
        },
        "persistent_snapshot_feasible": True,
        "runtime_reembedding_required": False,
    }
    safety = {
        "production_retrieval_changed": False,
        "production_embedding_changed": False,
        "generation_api_calls": 0,
        "external_embedding_api_calls": 0,
        "vision_api_calls": 0,
        "hospital_data_external_transfers": 0,
        "source_text_persisted": False,
        "question_text_persisted": False,
    }
    report = {
        "dataset": {
            "dataset_version": fixture["dataset_version"],
            "document_version": fixture["document_version"],
            "chunk_version": metadata["chunk_version"],
            "chunk_count": len(chunks),
            "case_count": len(fixture["cases"]),
            "approved_positive_count": audit["approved_case_count"],
            "human_review_count": audit["human_review_case_count"],
            "negative_count": audit["negative_case_count"],
            "candidate_limit": CANDIDATE_LIMIT,
            "cutoffs": fixture["cutoffs"],
        },
        "summaries": summaries,
        "scorecard": scorecard,
        "type_rows": type_rows,
        "runtime": runtime,
        "selective_policy": {
            "e5_case_count": sum(row["use_e5"] for row in selective_decisions),
            "total_case_count": len(selective_decisions),
            "e5_rate": selective_rate,
            "gold_or_question_id_inputs": False,
        },
        "decision": decision,
        "safety": safety,
    }

    forbidden_texts = {chunk.text for chunk in chunks}
    safe_results = {
        name: [_safe_result(row) for row in rows] for name, rows in results.items()
    }
    for payload in (
        report["dataset"],
        scorecard,
        type_rows,
        runtime,
        selective_decisions,
        decision,
        safety,
        safe_results,
    ):
        ensure_fairness_artifact_safe(payload, forbidden_texts)

    output_dir.mkdir(parents=True, exist_ok=False)
    for name, rows in safe_results.items():
        (output_dir / f"{name}_results.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    payloads = {
        "summary.json": report,
        "runtime.json": runtime,
        "selective_decisions.json": selective_decisions,
        "test_results.json": {
            "status": "pending final verification",
            "generation_api_calls": 0,
            "external_embedding_api_calls": 0,
            "vision_api_calls": 0,
            "hospital_data_external_transfers": 0,
        },
    }
    for name, payload in payloads.items():
        (output_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    _write_csv(output_dir / "comparison_metrics.csv", scorecard)
    _write_csv(output_dir / "metrics_by_question_type.csv", type_rows)
    with (output_dir / "common_results.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "retriever",
                "question_id",
                "question_type",
                "rank",
                "retrieved_chunk_id",
                "score",
                "is_gold",
                "latency_ms",
            ),
        )
        writer.writeheader()
        for name, rows in results.items():
            for row in rows:
                gold = set(row["reference_context_ids"])
                for rank, (identifier, score) in enumerate(
                    zip(row["retrieved_context_ids"], row["scores"], strict=True), 1
                ):
                    writer.writerow(
                        {
                            "retriever": name,
                            "question_id": row["question_id"],
                            "question_type": row["question_type"],
                            "rank": rank,
                            "retrieved_chunk_id": identifier,
                            "score": score,
                            "is_gold": identifier in gold,
                            "latency_ms": row["latency_ms"],
                        }
                    )
    (output_dir / "review.html").write_text(
        _review_html(report), encoding="utf-8"
    )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--prior-e5-audit", type=Path, default=DEFAULT_PRIOR_E5_AUDIT
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = evaluate_bm25_e5_strategy(
        catalog_path=args.catalog,
        fixture_path=args.fixture,
        cache_dir=args.cache_dir,
        snapshot_path=args.snapshot,
        output_dir=args.output_dir,
        prior_e5_audit_path=args.prior_e5_audit,
    )
    print(
        json.dumps(
            {
                "dataset": report["dataset"],
                "decision": report["decision"],
                "generation_api_calls": 0,
                "external_embedding_api_calls": 0,
                "vision_api_calls": 0,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
