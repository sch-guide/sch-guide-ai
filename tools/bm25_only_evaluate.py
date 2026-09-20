"""Evaluation-only pure BM25 retrieval over the frozen SCHAT corpus.

This module stops at BM25-ranked chunk IDs. It does not call vector search,
fusion, reranking, context expansion, evidence admission, generation, or
clinical validators.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.library import Chunk, fingerprint
from src.query import plan_query
from src.repository import snapshot
from src.retrieval import BM25Index
from tools import chromadb_only_evaluate as common
from tools.retrieval_baseline_metrics import FINGERPRINT_ALGORITHM_ID

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = common.DEFAULT_CATALOG
DEFAULT_UAT = common.DEFAULT_UAT
DEFAULT_REVIEWED_GOLD = common.DEFAULT_REVIEWED_GOLD
DEFAULT_OPERATIONAL_GOLD = common.DEFAULT_OPERATIONAL_GOLD
DEFAULT_OUTPUT = ROOT / "workspace" / "검색_성능평가" / "BM25" / "2026-09-19_bm25-only-eval"
TOP_K = common.TOP_K
ApprovedCase = common.ApprovedCase


@dataclass(frozen=True)
class BM25RetrievedChunk:
    chunk_id: str
    document_id: str
    page: int | None
    section: str
    rank: int
    score: float


def retrieval_contract() -> dict[str, Any]:
    """Declare and expose the pure lexical evaluation boundary."""
    return {
        "engine": "bm25_only",
        "bm25_calls_per_query": 1,
        "minilm_calls": 0,
        "vector_retrieval_calls": 0,
        "chromadb_calls": 0,
        "faiss_calls": 0,
        "pgvector_calls": 0,
        "rrf_calls": 0,
        "reranker_calls": 0,
        "context_expansion": False,
        "evidence_gate": False,
        "source_unit": False,
        "llm_calls": 0,
        "validator_calls": 0,
    }


def rank_bm25_chunks(
    chunks: Sequence[Chunk],
    expanded_query: str,
    *,
    limit: int,
    index: Any | None = None,
) -> list[BM25RetrievedChunk]:
    """Return raw BM25 Top-k with deterministic source-order tie handling."""
    if not chunks or limit < 1 or not expanded_query.strip():
        raise ValueError("BM25 ranking requires chunks, query, and positive limit")
    bm25 = index or BM25Index(chunks)
    scores = bm25.scores(expanded_query)
    if len(scores) != len(chunks):
        raise RuntimeError("BM25 score/chunk alignment drift")
    numeric_scores = [float(value) for value in scores]
    if any(not math.isfinite(value) for value in numeric_scores):
        raise RuntimeError("BM25 returned a non-finite score")
    positions = sorted(
        range(len(chunks)),
        key=lambda position: (
            -numeric_scores[position],
            position,
            chunks[position].id,
        ),
    )[:limit]
    return [
        BM25RetrievedChunk(
            chunk_id=chunks[position].id,
            document_id=chunks[position].document_id,
            page=chunks[position].page,
            section=chunks[position].section,
            rank=rank,
            score=numeric_scores[position],
        )
        for rank, position in enumerate(positions, 1)
    ]


def build_case_result(
    case: ApprovedCase,
    retrieved: Sequence[BM25RetrievedChunk],
    *,
    latency_ms: float,
    corpus_chunk_ids: set[str],
) -> dict[str, Any]:
    """Build the same metric/failure contract used by the Chroma-only run."""
    retrieved_ids = [row.chunk_id for row in retrieved]
    metrics = common._metric_values(retrieved_ids, case.gold_ids)
    missing_gold = sorted(set(case.gold_ids) - corpus_chunk_ids)
    available_gold = set(case.gold_ids).intersection(corpus_chunk_ids)
    if not available_gold:
        failure = "gold_not_in_bm25_corpus"
    elif missing_gold:
        failure = "gold_partially_outside_bm25_corpus"
    elif metrics["hit_at_10"] == 0.0:
        failure = "no_gold_hit_top_10"
    elif metrics["hit_at_1"] == 0.0:
        failure = "gold_below_rank_1"
    else:
        failure = "none"
    gold_ids = set(case.gold_ids)
    return {
        "case_id": case.case_id,
        "question_type": case.question_type,
        "document_scope": case.document_scope,
        "expected_evidence_type": case.expected_evidence_type,
        "gold_ids": list(case.gold_ids),
        "gold_ids_in_bm25_corpus": sorted(available_gold),
        "gold_ids_outside_bm25_corpus": missing_gold,
        "ranked_results": [
            {
                "rank": row.rank,
                "chunk_id": row.chunk_id,
                "document_id": row.document_id,
                "page": row.page,
                "section": row.section,
                "score": round(row.score, 8),
                "gold_hit": row.chunk_id in gold_ids,
            }
            for row in retrieved
        ],
        "metrics": metrics,
        "latency_ms": {
            "query": round(float(latency_ms), 3),
            "total": round(float(latency_ms), 3),
        },
        "failure_category": failure,
    }


def _build_index_cache(
    chunks: Sequence[Chunk], scopes: Mapping[str, tuple[str, ...]]
) -> dict[tuple[str, ...], tuple[list[Chunk], BM25Index]]:
    cache: dict[tuple[str, ...], tuple[list[Chunk], BM25Index]] = {}
    for document_ids in scopes.values():
        key = tuple(sorted(document_ids))
        if key in cache:
            continue
        allowed = set(key)
        scoped_chunks = [chunk for chunk in chunks if chunk.document_id in allowed]
        if not scoped_chunks:
            raise ValueError("document scope has no chunks")
        cache[key] = (scoped_chunks, BM25Index(scoped_chunks))
    return cache


def _write_failure_analysis(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    failures = [row for row in rows if row["failure_category"] != "none"]
    lines = [
        "# BM25-only Retrieval 실패 분석",
        "",
        "- 질문 원문과 병원 근거 원문은 저장하지 않았다.",
        "- 아래 분류는 동결된 Gold ID와 BM25 Top-10 ID만 비교한 결과다.",
        "",
        "| Case ID | 질문 유형 | 근거 유형 | 실패 분류 | Top-10 Gold hit |",
        "|---|---|---|---|---:|",
    ]
    for row in failures:
        lines.append(
            "| {case_id} | {question_type} | {expected_evidence_type} | "
            "{failure_category} | {hit_at_10:.0f} |".format(
                **row, hit_at_10=float(row["metrics"]["hit_at_10"])
            )
        )
    if not failures:
        lines.append("| - | - | - | none | 1 |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def evaluate(
    *,
    catalog_path: Path = DEFAULT_CATALOG,
    uat_path: Path = DEFAULT_UAT,
    reviewed_gold_path: Path = DEFAULT_REVIEWED_GOLD,
    operational_gold_path: Path = DEFAULT_OPERATIONAL_GOLD,
    output_dir: Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    """Run BM25-only retrieval and persist raw-source-free comparison artifacts."""
    reviewed = common._read_json(reviewed_gold_path)
    uat = common._read_json(uat_path)
    operational_gold = common._read_json(operational_gold_path)
    approved_cases = common.load_approved_cases(reviewed, uat)
    approved_by_id = {case.case_id: case for case in approved_cases}

    revision = common._catalog_revision(catalog_path)
    library = snapshot(str(catalog_path.resolve()), revision)
    if not library.chunks or not library.docs:
        raise ValueError("frozen corpus is empty")
    corpus_chunk_ids = {chunk.id for chunk in library.chunks}
    if len(corpus_chunk_ids) != len(library.chunks):
        raise ValueError("duplicate catalog chunk ID")
    corpus_fingerprint = fingerprint(library.docs, [str(document["id"]) for document in library.docs])

    from tools.schat_v1_final_validate import _scope_document_ids

    scopes = _scope_document_ids(library.docs)
    index_cache = _build_index_cache(library.chunks, scopes)
    results: list[dict[str, Any]] = []
    previous: dict[str, dict[str, Any]] = {}
    for uat_case in uat["cases"]:
        scope = str(uat_case.get("document_scope", ""))
        case_id = str(uat_case.get("case_id", ""))
        if scope not in scopes:
            continue
        is_follow_up = str(uat_case.get("question_type")) == "follow_up"
        prior = previous.get(scope, {}) if is_follow_up else {}
        allowed_documents = scopes[scope]
        plan = plan_query(
            str(uat_case["question"]),
            previous=str(prior.get("question", "")),
            follow_up=is_follow_up,
            documents=library.docs,
            previous_sources=tuple(prior.get("document_ids", ())),
            context_document_ids=allowed_documents,
        )
        case = approved_by_id.get(case_id)
        if case is not None:
            if plan.clarification or plan.domain == "out_of_scope":
                raise ValueError(f"{case_id}: approved case was not queryable")
            effective_documents = set(allowed_documents)
            if plan.document_ids:
                effective_documents.intersection_update(plan.document_ids)
            if not effective_documents:
                raise ValueError(f"{case_id}: query plan removed document scope")
            key = tuple(sorted(effective_documents))
            if key not in index_cache:
                scoped_chunks = [
                    chunk for chunk in library.chunks if chunk.document_id in effective_documents
                ]
                index_cache[key] = (scoped_chunks, BM25Index(scoped_chunks))
            scoped_chunks, index = index_cache[key]
            started = time.perf_counter()
            ranked = rank_bm25_chunks(
                scoped_chunks,
                plan.expanded,
                limit=10,
                index=index,
            )
            latency_ms = (time.perf_counter() - started) * 1000
            if len(ranked) != min(10, len(scoped_chunks)):
                raise RuntimeError(f"{case_id}: incomplete BM25 Top-k")
            results.append(
                build_case_result(
                    case,
                    ranked,
                    latency_ms=latency_ms,
                    corpus_chunk_ids=corpus_chunk_ids,
                )
            )
        if str(uat_case.get("expected_behavior")) == "answer":
            previous[scope] = {
                "question": str(uat_case["question"]),
                "document_ids": allowed_documents,
            }

    if {row["case_id"] for row in results} != set(approved_by_id):
        raise ValueError("approved Gold evaluation coverage drift")

    summary = common.aggregate_results(results)
    contract = {
        "schema_version": 1,
        "evaluation_date": date.today().isoformat(),
        "evaluator_file": "tools/bm25_only_evaluate.py",
        "engine": "bm25_only",
        "retrieval_contract": retrieval_contract(),
        "corpus": {
            "catalog_revision": revision,
            "fingerprint_algorithm": FINGERPRINT_ALGORITHM_ID,
            "fingerprint": corpus_fingerprint,
            "document_count": len(library.docs),
            "chunk_count": len(library.chunks),
            "document_ids": sorted(str(document["id"]) for document in library.docs),
        },
        "gold": {
            "fixture": str(reviewed_gold_path.relative_to(ROOT)).replace("\\", "/"),
            "fixture_sha256": common._sha256_file(reviewed_gold_path),
            "dataset_version": reviewed.get("dataset_version", "unknown"),
            "approved_positive_case_count": len(approved_cases),
            "deferred_positive_case_count": len(reviewed["cases"]) - len(approved_cases),
            "approved_abstention_case_count": common._approved_abstention_count(operational_gold),
            "negative_handling": "separate_not_queried_by_positive_retrieval_evaluator",
        },
        "query_processing": {
            "planner": "src.query.plan_query",
            "query_text": "plan.expanded",
            "follow_up_context": True,
            "document_scope_filter": True,
            "context_expansion": False,
        },
        "bm25": {
            "implementation": "src.retrieval.BM25Index",
            "query_method": "BM25Index.scores(plan.expanded)",
            "ranking": {
                "primary_sort": "bm25_score_descending",
                "tie_break": "original_corpus_position_ascending",
                "final_fallback": "chunk_id_ascending",
            },
            "index_build_in_latency": False,
        },
        "top_k": list(TOP_K),
    }
    summary_payload = {
        "schema_version": 1,
        "engine": "bm25_only",
        "approved_positive_case_count": len(approved_cases),
        "deferred_positive_case_count": len(reviewed["cases"]) - len(approved_cases),
        "approved_abstention_case_count": common._approved_abstention_count(operational_gold),
        "metrics": summary,
        "production_changed": False,
        "gold_changed": False,
        "raw_source_text_persisted": False,
    }
    per_case_payload = {
        "schema_version": 1,
        "engine": "bm25_only",
        "cases": results,
    }
    forbidden_texts = {chunk.text for chunk in library.chunks}
    for payload in (contract, summary_payload, per_case_payload):
        common.ensure_artifact_safe(payload, forbidden_exact_texts=forbidden_texts)

    output_dir.mkdir(parents=True, exist_ok=True)
    common._write_json(output_dir / "evaluation_contract.json", contract)
    common._write_json(output_dir / "summary.json", summary_payload)
    common._write_json(output_dir / "per_case_results.json", per_case_payload)
    common._write_metrics_csv(output_dir / "metrics.csv", summary)
    _write_failure_analysis(output_dir / "failure_analysis.md", results)
    return {
        "contract": contract,
        "summary": summary_payload,
        "per_case": per_case_payload,
        "output_dir": str(output_dir),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the evaluation-only SCHAT pure BM25 retrieval path.")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--uat", type=Path, default=DEFAULT_UAT)
    parser.add_argument("--reviewed-gold", type=Path, default=DEFAULT_REVIEWED_GOLD)
    parser.add_argument("--operational-gold", type=Path, default=DEFAULT_OPERATIONAL_GOLD)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = evaluate(
        catalog_path=args.catalog,
        uat_path=args.uat,
        reviewed_gold_path=args.reviewed_gold,
        operational_gold_path=args.operational_gold,
        output_dir=args.output,
    )
    overall = report["summary"]["metrics"]["overall"]
    print(
        json.dumps(
            {
                "engine": "bm25_only",
                "case_count": overall["case_count"],
                "hit_at_10": overall["hit_at_10"],
                "mrr": overall["mrr"],
                "output_dir": report["output_dir"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
