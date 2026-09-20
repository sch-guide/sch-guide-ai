"""Evaluate the unchanged production Current Hybrid retrieval path.

This evaluation-only runner calls :meth:`src.library.LocalLibrary.search`
instead of reimplementing BM25, FAISS, RRF, reranking, or context expansion.
It stops at ranked chunk IDs and never invokes evidence, generation, or
clinical validation code.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.library import Embedder, bounded_embedding_question, fingerprint
from src.query import plan_query
from src.repository import snapshot
from src.settings import DIMENSIONS, MODEL
from tools import chromadb_only_evaluate as common
from tools.retrieval_baseline_metrics import FINGERPRINT_ALGORITHM_ID

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = common.DEFAULT_CATALOG
DEFAULT_UAT = common.DEFAULT_UAT
DEFAULT_REVIEWED_GOLD = common.DEFAULT_REVIEWED_GOLD
DEFAULT_OPERATIONAL_GOLD = common.DEFAULT_OPERATIONAL_GOLD
DEFAULT_OUTPUT = ROOT / "workspace" / "검색_성능평가" / "Hybrid" / "2026-09-19_current-hybrid-eval"
TOP_K = common.TOP_K
ApprovedCase = common.ApprovedCase


@dataclass(frozen=True)
class HybridRetrievedChunk:
    chunk_id: str
    document_id: str
    page: int | None
    section: str
    rank: int
    similarity: float
    bm25_score: float
    fusion_score: float
    rerank_score: float
    context_only: bool = False
    context_complete: bool = True


def retrieval_contract() -> dict[str, Any]:
    """Declare the existing production boundary exercised by this runner."""
    return {
        "engine": "current_hybrid",
        "production_search": "src.library.LocalLibrary.search",
        "minilm_calls_per_query": 1,
        "bm25_enabled": True,
        "faiss_enabled": True,
        "rrf_enabled": True,
        "reranker_enabled": True,
        "context_expansion": True,
        "evidence_gate": False,
        "source_unit": False,
        "llm_calls": 0,
        "validator_calls": 0,
    }


def build_case_result(
    case: ApprovedCase,
    retrieved: Sequence[HybridRetrievedChunk],
    *,
    latency_ms: float,
    embedding_latency_ms: float,
    retrieval_latency_ms: float,
    corpus_chunk_ids: set[str],
) -> dict[str, Any]:
    """Apply the frozen retrieval metric and failure contract."""
    retrieved_ids = [row.chunk_id for row in retrieved]
    metrics = common._metric_values(retrieved_ids, case.gold_ids)
    missing_gold = sorted(set(case.gold_ids) - corpus_chunk_ids)
    available_gold = set(case.gold_ids).intersection(corpus_chunk_ids)
    if not available_gold:
        failure = "gold_not_in_hybrid_corpus"
    elif missing_gold:
        failure = "gold_partially_outside_hybrid_corpus"
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
        "gold_ids_in_hybrid_corpus": sorted(available_gold),
        "gold_ids_outside_hybrid_corpus": missing_gold,
        "ranked_results": [
            {
                "rank": row.rank,
                "chunk_id": row.chunk_id,
                "document_id": row.document_id,
                "page": row.page,
                "section": row.section,
                "similarity": round(row.similarity, 8),
                "bm25_score": round(row.bm25_score, 8),
                "fusion_score": round(row.fusion_score, 8),
                "rerank_score": round(row.rerank_score, 8),
                "context_only": row.context_only,
                "context_complete": row.context_complete,
                "gold_hit": row.chunk_id in gold_ids,
            }
            for row in retrieved
        ],
        "metrics": metrics,
        "latency_ms": {
            "embedding": round(float(embedding_latency_ms), 3),
            "retrieval": round(float(retrieval_latency_ms), 3),
            "total": round(float(latency_ms), 3),
        },
        "failure_category": failure,
    }


def _write_failure_analysis(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    failures = [row for row in rows if row["failure_category"] != "none"]
    lines = [
        "# Current Hybrid Retrieval 실패 분석",
        "",
        "- 질문 원문과 병원 근거 원문은 저장하지 않는다.",
        "- 분류는 승인 Gold ID와 Production Hybrid Top-10 ID만 비교한 결과다.",
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
    """Run the current production Hybrid retriever without changing it."""
    reviewed = common._read_json(reviewed_gold_path)
    uat = common._read_json(uat_path)
    operational_gold = common._read_json(operational_gold_path)
    approved_cases = common.load_approved_cases(reviewed, uat)
    approved_by_id = {case.case_id: case for case in approved_cases}

    revision = common._catalog_revision(catalog_path)
    library = snapshot(str(catalog_path.resolve()), revision)
    if not library.chunks or not library.docs:
        raise ValueError("frozen corpus is empty")
    if any(str(document.get("model")) != MODEL for document in library.docs):
        raise ValueError("catalog embedding model drift")
    vector_details = common.validate_vectors(library.vectors, dimensions=DIMENSIONS)
    corpus_chunk_ids = {chunk.id for chunk in library.chunks}
    if len(corpus_chunk_ids) != len(library.chunks):
        raise ValueError("duplicate catalog chunk ID")
    corpus_fingerprint = fingerprint(
        library.docs, [str(document["id"]) for document in library.docs]
    )

    from tools.schat_v1_final_validate import _scope_document_ids

    scopes = _scope_document_ids(library.docs)
    embedder = Embedder()
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

            embedding_started = time.perf_counter()
            query_text = bounded_embedding_question(plan.expanded, embedder)
            query_vector = embedder.encode([query_text])[0]
            embedding_ms = (time.perf_counter() - embedding_started) * 1000
            retrieval_started = time.perf_counter()
            hits = library.search(
                plan.query,
                query_vector,
                tuple(sorted(effective_documents)),
                0.38,
                plan=plan,
                trace={},
            )
            retrieval_ms = (time.perf_counter() - retrieval_started) * 1000
            ranked = [
                HybridRetrievedChunk(
                    hit.chunk.id,
                    hit.chunk.document_id,
                    hit.chunk.page,
                    hit.chunk.section,
                    rank,
                    float(hit.similarity),
                    float(hit.bm25_score),
                    float(hit.fusion_score),
                    float(hit.rerank_score),
                    bool(hit.context_only),
                    bool(hit.context_complete),
                )
                for rank, hit in enumerate(hits[:10], 1)
            ]
            if len({row.chunk_id for row in ranked}) != len(ranked):
                raise RuntimeError(f"{case_id}: duplicate Hybrid chunk ID")
            results.append(
                build_case_result(
                    case,
                    ranked,
                    latency_ms=embedding_ms + retrieval_ms,
                    embedding_latency_ms=embedding_ms,
                    retrieval_latency_ms=retrieval_ms,
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
        "evaluator_file": "tools/current_hybrid_evaluate.py",
        "engine": "current_hybrid",
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
            "approved_abstention_case_count": common._approved_abstention_count(
                operational_gold
            ),
            "negative_handling": "separate_not_queried_by_positive_retrieval_evaluator",
        },
        "query_processing": {
            "planner": "src.query.plan_query",
            "embedding_projection": "src.library.bounded_embedding_question(plan.expanded)",
            "follow_up_context": True,
            "document_scope_filter": True,
            "context_expansion": True,
        },
        "embedding": {
            "model": MODEL,
            "runtime_library": "fastembed",
            "runtime_version": common._package_version("fastembed"),
            "dimension": DIMENSIONS,
            **vector_details,
        },
        "hybrid": {
            "implementation": "src.library.LocalLibrary.search",
            "components": [
                "BM25Index",
                "FAISS IndexFlatIP",
                "RRF",
                "reranker",
                "context expansion",
            ],
            "minimum_similarity": 0.38,
            "separate_reimplementation": False,
        },
        "top_k": list(TOP_K),
    }
    summary_payload = {
        "schema_version": 1,
        "engine": "current_hybrid",
        "approved_positive_case_count": len(approved_cases),
        "deferred_positive_case_count": len(reviewed["cases"]) - len(approved_cases),
        "approved_abstention_case_count": common._approved_abstention_count(operational_gold),
        "metrics": summary,
        "production_changed": False,
        "gold_changed": False,
        "raw_source_text_persisted": False,
    }
    per_case_payload = {"schema_version": 1, "engine": "current_hybrid", "cases": results}
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
    parser = argparse.ArgumentParser(description="Evaluate the unchanged SCHAT Current Hybrid path.")
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
                "engine": "current_hybrid",
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
