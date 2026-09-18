"""Local-only SCHAT v1 final stabilization and reviewed-Gold evaluation.

The module treats human review as immutable input. Retrieval output may be
compared with Gold, but it can never approve or rewrite Gold.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import sqlite3
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from statistics import fmean
from typing import Any

import numpy as np

from tools.retrieval_baseline_metrics import (
    id_based_context_scores,
    ranked_retrieval_metrics,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data/library/catalog.sqlite3"
DEFAULT_UAT = ROOT / "tests/fixtures/schat_v1_operational_uat.json"
DEFAULT_OPERATIONAL_GOLD = ROOT / "tests/fixtures/schat_v1_operational_gold.json"
DEFAULT_REVIEWED_GOLD = (
    ROOT / "tests/fixtures/schat_v1_operational_gold_reviewed.json"
)
DEFAULT_TF027 = ROOT / "tests/fixtures/tf027_image_human_review_checklist.json"
DEFAULT_E5_SNAPSHOT = ROOT / "data/evaluation/e5-operational-reviewed-v1.npz"
DEFAULT_MODEL_CACHE = ROOT / "data/models"
DEFAULT_OUTPUT = ROOT / "artifacts/2026-09-18_schat-v1-final-stabilization"
RETRIEVER_NAMES = (
    "bm25_current",
    "production_hybrid",
    "e5_large_eval",
    "bm25_e5_rrf_eval",
)


def _unique_strings(values: object, *, field: str) -> list[str]:
    if not isinstance(values, list):
        raise ValueError(f"{field} must be a list")
    normalized = [str(value).strip() for value in values]
    if any(not value for value in normalized):
        raise ValueError(f"{field} contains an empty value")
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"duplicate evidence in {field}")
    return normalized


def build_gold_manifest(
    reviewed: Mapping[str, Any],
    operational_gold: Mapping[str, Any],
    *,
    valid_evidence_ids: set[str],
) -> dict[str, Any]:
    """Validate reviewed Gold and return the immutable aggregate boundary."""
    review_cases = reviewed.get("cases")
    if not isinstance(review_cases, list):
        raise ValueError("reviewed Gold cases must be a list")
    review_ids = [str(case.get("case_id", "")).strip() for case in review_cases]
    if not all(review_ids) or len(review_ids) != len(set(review_ids)):
        raise ValueError("reviewed Gold case IDs must be present and unique")

    approved_positive: list[str] = []
    deferred_positive: list[str] = []
    approved_rows: list[dict[str, Any]] = []
    evidence_owners: dict[str, list[str]] = {}
    for case in review_cases:
        case_id = str(case["case_id"])
        primary = _unique_strings(case.get("primary_gold_ids"), field="primary_gold_ids")
        acceptable = _unique_strings(
            case.get("acceptable_gold_ids"), field="acceptable_gold_ids"
        )
        if set(primary).intersection(acceptable):
            raise ValueError("Primary and Acceptable evidence overlap")
        for evidence_id in (*primary, *acceptable):
            evidence_owners.setdefault(evidence_id, []).append(case_id)

        final_approved = case.get("final_gold_approved") is True
        if final_approved:
            if case.get("review_status") != "approved":
                raise ValueError("final Gold must have approved review_status")
            critical_facts = _unique_strings(
                case.get("critical_facts"), field="critical_facts"
            )
            if not critical_facts:
                raise ValueError("approved Gold requires a critical fact")
            if not primary:
                raise ValueError("approved Gold requires Primary evidence")
            unknown = (set(primary) | set(acceptable)) - valid_evidence_ids
            if unknown:
                raise ValueError(f"unknown Gold evidence IDs: {sorted(unknown)}")
            approved_positive.append(case_id)
            approved_rows.append(
                {
                    "case_id": case_id,
                    "expected_document": str(case.get("expected_document", "")),
                    "expected_evidence_type": str(
                        case.get("expected_evidence_type", "")
                    ),
                    "primary_gold_ids": primary,
                    "acceptable_gold_ids": acceptable,
                    "critical_facts_count": len(critical_facts),
                    "aggregate_eligible": True,
                }
            )
        else:
            deferred_positive.append(case_id)

    negative_cases = operational_gold.get("cases")
    if not isinstance(negative_cases, list):
        raise ValueError("operational Gold cases must be a list")
    approved_abstention = sorted(
        str(case["case_id"])
        for case in negative_cases
        if case.get("label_status") == "approved"
        and case.get("expected_abstain") is True
    )
    return {
        "approved_positive_count": len(approved_positive),
        "deferred_positive_count": len(deferred_positive),
        "approved_abstention_count": len(approved_abstention),
        "approved_positive_case_ids": approved_positive,
        "deferred_positive_case_ids": deferred_positive,
        "approved_abstention_case_ids": approved_abstention,
        "aggregate_case_ids": [*approved_positive, *approved_abstention],
        "approved_positive_cases": approved_rows,
        "evidence_id_case_count": {
            evidence_id: len(case_ids)
            for evidence_id, case_ids in sorted(evidence_owners.items())
        },
        "automatic_gold_approval": False,
    }


def score_ranked_ids(
    retrieved_ids: Sequence[str],
    *,
    primary_gold_ids: Sequence[str],
    acceptable_gold_ids: Sequence[str],
) -> dict[str, float]:
    """Score one ranked list against the frozen Primary+Acceptable union."""
    gold = list(dict.fromkeys([*primary_gold_ids, *acceptable_gold_ids]))
    if not retrieved_ids:
        zeros = {
            f"{prefix}_at_{cutoff}": 0.0
            for prefix in ("hit", "recall", "precision")
            for cutoff in (1, 3, 5, 10)
        }
        return {
            **zeros,
            "mrr": 0.0,
            "ragas_id_context_precision": 0.0,
            "ragas_id_context_recall": 0.0,
        }
    metrics = ranked_retrieval_metrics(retrieved_ids, gold)
    ragas = id_based_context_scores(retrieved_ids[:10], gold)
    return {
        **metrics,
        "ragas_id_context_precision": ragas["context_precision"],
        "ragas_id_context_recall": ragas["context_recall"],
    }


_SAFE_ARTIFACT_FIELDS = frozenset(
    {
        "case_id",
        "question_type",
        "expected_document",
        "expected_evidence_type",
        "aggregate_eligible",
        "retrieved_context_ids",
        "scores",
        "latency_ms",
        "metrics",
        "failure_category",
        "chunk_candidate_count",
        "table_candidate_count",
        "guideline_evidence_status",
        "improvement_layer",
        "automatic_gold_approval",
        "requires_human_review",
        "question_sha256",
        "review_note_sha256",
    }
)


def safe_artifact_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Project an evaluation row to identifiers, hashes, metrics, and reasons."""
    return {
        key: value
        for key, value in row.items()
        if key in _SAFE_ARTIFACT_FIELDS
    }


def aggregate_retrieval_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    eligible = [row for row in rows if row.get("aggregate_eligible") is True]
    if not eligible:
        return {"evaluated_case_count": 0}
    metric_names = tuple(eligible[0]["metrics"])
    return {
        "evaluated_case_count": len(eligible),
        **{
            name: fmean(float(row["metrics"][name]) for row in eligible)
            for name in metric_names
        },
    }


def evaluate_fixed_rankings(
    gold_cases: Sequence[Mapping[str, Any]],
    rankings: Mapping[str, Mapping[str, Sequence[str]]],
) -> dict[str, Any]:
    """Apply one frozen Gold contract to multiple already-ranked strategies."""
    expected_ids = {str(case["case_id"]) for case in gold_cases}
    result_rows: dict[str, list[dict[str, Any]]] = {}
    summaries: dict[str, dict[str, Any]] = {}
    for strategy, by_case in rankings.items():
        if set(by_case) != expected_ids:
            raise ValueError(f"{strategy} case coverage drift")
        rows = []
        for case in gold_cases:
            case_id = str(case["case_id"])
            retrieved = list(by_case[case_id])[:10]
            if not retrieved or len(retrieved) != len(set(retrieved)):
                raise ValueError(f"{strategy} returned invalid ranked IDs for {case_id}")
            rows.append(
                {
                    "case_id": case_id,
                    "question_type": str(case.get("question_type", "unknown")),
                    "expected_evidence_type": str(
                        case.get("expected_evidence_type", "text")
                    ),
                    "aggregate_eligible": bool(
                        case.get("aggregate_eligible", True)
                    ),
                    "retrieved_context_ids": retrieved,
                    "metrics": score_ranked_ids(
                        retrieved,
                        primary_gold_ids=case["primary_gold_ids"],
                        acceptable_gold_ids=case["acceptable_gold_ids"],
                    ),
                }
            )
        result_rows[strategy] = rows
        summaries[strategy] = aggregate_retrieval_rows(rows)

    table_ids = [
        str(case["case_id"])
        for case in gold_cases
        if str(case.get("expected_evidence_type", "")) in {"table", "mixed"}
    ]
    table_set = set(table_ids)
    table_summaries = {
        strategy: aggregate_retrieval_rows(
            [row for row in rows if row["case_id"] in table_set]
        )
        for strategy, rows in result_rows.items()
    }
    return {
        "rows": result_rows,
        "summaries": summaries,
        "table_subset": {
            "case_ids": table_ids,
            "summaries": table_summaries,
        },
    }


def collect_valid_evidence_ids(catalog_path: Path) -> set[str]:
    """Collect registered chunk and locally extracted table-row IDs."""
    resolved = catalog_path.resolve()
    with sqlite3.connect(
        resolved.as_uri() + "?mode=ro&immutable=1", uri=True
    ) as connection:
        chunk_ids = {str(row[0]) for row in connection.execute("select id from chunks")}
        documents = []
        for row in connection.execute(
            "select id,metadata from documents where status='ready'"
        ):
            metadata = json.loads(row[1])
            documents.append((str(row[0]), metadata))

    table_ids: set[str] = set()
    from tools.chroma_baseline_evaluate import load_catalog
    from tools.structured_table_evidence import extract_table_records

    for document_id, metadata in documents:
        document_name = str(metadata["document_name"])
        pdf_path = resolved.parent.parent / document_name
        if not pdf_path.is_file():
            continue
        _, chunks = load_catalog(resolved, document_name=document_name)
        records = extract_table_records(pdf_path, document_id, document_name, chunks)
        table_ids.update(
            f"{record.table_id}:{row.row_id}"
            for record in records
            for row in record.rows
        )
    return chunk_ids | table_ids


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _write_json(path: Path, value: Mapping[str, Any] | Sequence[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _mean_p95(values: Sequence[float]) -> dict[str, float]:
    if not values:
        return {"mean_ms": 0.0, "p95_ms": 0.0}
    return {
        "mean_ms": round(fmean(values), 3),
        "p95_ms": round(float(np.percentile(values, 95)), 3),
    }


def _catalog_revision(path: Path) -> int:
    with sqlite3.connect(path) as connection:
        row = connection.execute("select version from corpus").fetchone()
    if row is None:
        raise ValueError("catalog revision missing")
    return int(row[0])


def _approved_cases(
    reviewed: Mapping[str, Any], uat: Mapping[str, Any]
) -> list[dict[str, Any]]:
    review_by_id = {str(case["case_id"]): case for case in reviewed["cases"]}
    cases = []
    for uat_case in uat["cases"]:
        case_id = str(uat_case["case_id"])
        review = review_by_id.get(case_id)
        if not review or review.get("final_gold_approved") is not True:
            continue
        cases.append(
            {
                "case_id": case_id,
                "question": str(uat_case["question"]),
                "question_type": str(uat_case["question_type"]),
                "expected_document": str(review["expected_document"]),
                "expected_evidence_type": str(review["expected_evidence_type"]),
                "primary_gold_ids": list(review["primary_gold_ids"]),
                "acceptable_gold_ids": list(review["acceptable_gold_ids"]),
                "aggregate_eligible": True,
            }
        )
    return cases


def _load_or_build_e5_passages(
    *,
    chunks: Sequence[Any],
    snapshot_path: Path,
    model_cache: Path,
) -> tuple[np.ndarray, Any, dict[str, Any]]:
    from tools.multilingual_e5_fairness_evaluate import (
        MODEL_DIMENSIONS,
        MODEL_NAME,
        _embed,
        _load_local_engine,
        e5_passage_text,
    )

    chunk_ids = np.asarray([str(chunk.id) for chunk in chunks])
    snapshot_reused = snapshot_path.is_file()
    snapshot_started = time.perf_counter()
    if snapshot_reused:
        with np.load(snapshot_path, allow_pickle=False) as payload:
            saved_ids = payload["chunk_ids"]
            vectors = payload["vectors"].astype(np.float32, copy=False)
        if not np.array_equal(saved_ids, chunk_ids):
            raise ValueError("E5 snapshot chunk identity drift")
        if vectors.shape != (len(chunks), MODEL_DIMENSIONS):
            raise ValueError("E5 snapshot vector shape drift")
        passage_build_ms = 0.0
        snapshot_load_ms = (time.perf_counter() - snapshot_started) * 1000
    else:
        engine_for_passages = _load_local_engine(model_cache)
        passage_started = time.perf_counter()
        vectors = _embed(
            engine_for_passages,
            (e5_passage_text(chunk.text) for chunk in chunks),
            batch_size=4,
        )
        passage_build_ms = (time.perf_counter() - passage_started) * 1000
        if vectors.shape != (len(chunks), MODEL_DIMENSIONS):
            raise ValueError("E5 passage vector shape drift")
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(snapshot_path, chunk_ids=chunk_ids, vectors=vectors)
        snapshot_load_ms = 0.0

    model_started = time.perf_counter()
    engine = _load_local_engine(model_cache)
    model_load_ms = (time.perf_counter() - model_started) * 1000
    if not np.isfinite(vectors).all() or np.any(np.linalg.norm(vectors, axis=1) == 0):
        raise ValueError("E5 passage vectors are invalid")
    return vectors, engine, {
        "model": MODEL_NAME,
        "dimensions": MODEL_DIMENSIONS,
        "snapshot_reused": snapshot_reused,
        "snapshot_load_ms": round(snapshot_load_ms, 3),
        "passage_build_ms": round(passage_build_ms, 3),
        "model_load_ms": round(model_load_ms, 3),
        "snapshot_size_bytes": snapshot_path.stat().st_size,
        "local_inference": True,
        "external_embedding_api_calls": 0,
    }


def _cosine_ranking(
    query_vector: np.ndarray,
    passage_vectors: np.ndarray,
    allowed_indices: Sequence[int],
    chunks: Sequence[Any],
    *,
    limit: int,
) -> tuple[list[str], list[float]]:
    query_norm = float(np.linalg.norm(query_vector))
    if not query_norm:
        raise ValueError("E5 query vector is zero")
    ranked = []
    for index in allowed_indices:
        vector = passage_vectors[index]
        score = float(np.dot(query_vector, vector) / (query_norm * np.linalg.norm(vector)))
        ranked.append((score, index, str(chunks[index].id)))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    selected = ranked[:limit]
    return [item[2] for item in selected], [item[0] for item in selected]


def evaluate_local_retrievers(
    *,
    catalog_path: Path,
    uat: Mapping[str, Any],
    gold_cases: Sequence[Mapping[str, Any]],
    snapshot_path: Path,
    model_cache: Path,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Run four local retrieval strategies with one planned-query contract."""
    from mvp.library import Embedder, bounded_embedding_question, embedding_question
    from mvp.query import plan_query
    from mvp.repository import snapshot
    from mvp.retrieval import BM25Index
    from tools.bm25_e5_strategy_evaluate import fuse_rrf
    from tools.multilingual_e5_fairness_evaluate import _embed, e5_query_text
    from tools.schat_v1_final_validate import _scope_document_ids

    library = snapshot(str(catalog_path.resolve()), _catalog_revision(catalog_path))
    scopes = _scope_document_ids(library.docs)
    approved_by_id = {str(case["case_id"]): case for case in gold_cases}
    vectors, e5_engine, e5_runtime = _load_or_build_e5_passages(
        chunks=library.chunks,
        snapshot_path=snapshot_path,
        model_cache=model_cache,
    )
    minilm = Embedder()
    indices_by_scope = {
        scope: [
            index
            for index, chunk in enumerate(library.chunks)
            if chunk.document_id in set(document_ids)
        ]
        for scope, document_ids in scopes.items()
    }
    bm25_by_scope = {
        scope: BM25Index([library.chunks[index] for index in indices])
        for scope, indices in indices_by_scope.items()
    }
    rankings: dict[str, dict[str, list[str]]] = {
        name: {} for name in RETRIEVER_NAMES
    }
    safe_rows: dict[str, list[dict[str, Any]]] = {
        name: [] for name in RETRIEVER_NAMES
    }
    latencies: dict[str, list[float]] = {name: [] for name in RETRIEVER_NAMES}
    previous: dict[str, dict[str, Any]] = {}

    for uat_case in uat["cases"]:
        scope = str(uat_case["document_scope"])
        if scope not in scopes:
            continue
        is_follow_up = str(uat_case["question_type"]) == "follow_up"
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
        case_id = str(uat_case["case_id"])
        if case_id in approved_by_id:
            allowed_indices = indices_by_scope[scope]
            scoped_chunks = [library.chunks[index] for index in allowed_indices]

            started = time.perf_counter()
            bm25_scores = bm25_by_scope[scope].scores(plan.expanded)
            bm25_positions = sorted(
                range(len(scoped_chunks)),
                key=lambda position: (-float(bm25_scores[position]), position),
            )[:40]
            bm25_ids = [scoped_chunks[position].id for position in bm25_positions]
            bm25_values = [float(bm25_scores[position]) for position in bm25_positions]
            bm25_ms = (time.perf_counter() - started) * 1000

            started = time.perf_counter()
            minilm_query = bounded_embedding_question(plan.expanded, minilm)
            minilm_vector = minilm.encode([minilm_query])[0]
            hybrid_hits = library.search(
                plan.query,
                minilm_vector,
                list(allowed_documents),
                0.38,
                plan=plan,
                trace={},
            )
            hybrid_ids = list(dict.fromkeys(hit.chunk.id for hit in hybrid_hits))[:10]
            hybrid_values = [
                float(hit.rerank_score or hit.fusion_score or hit.similarity)
                for hit in hybrid_hits[: len(hybrid_ids)]
            ]
            hybrid_ms = (time.perf_counter() - started) * 1000

            started = time.perf_counter()
            query_text = e5_query_text(embedding_question(plan.expanded))
            query_vector = _embed(e5_engine, [query_text], batch_size=1)[0]
            e5_ids, e5_values = _cosine_ranking(
                query_vector,
                vectors,
                allowed_indices,
                library.chunks,
                limit=40,
            )
            e5_ms = (time.perf_counter() - started) * 1000

            started = time.perf_counter()
            rrf_ids, rrf_values = fuse_rrf(
                [bm25_ids, e5_ids],
                source_order={chunk.id: index for index, chunk in enumerate(library.chunks)},
                limit=10,
            )
            rrf_fusion_ms = (time.perf_counter() - started) * 1000

            per_strategy = {
                "bm25_current": (bm25_ids[:10], bm25_values[:10], bm25_ms),
                "production_hybrid": (hybrid_ids, hybrid_values, hybrid_ms),
                "e5_large_eval": (e5_ids[:10], e5_values[:10], e5_ms),
                "bm25_e5_rrf_eval": (
                    rrf_ids,
                    rrf_values,
                    bm25_ms + e5_ms + rrf_fusion_ms,
                ),
            }
            gold_case = approved_by_id[case_id]
            for name, (ids, scores, latency_ms) in per_strategy.items():
                rankings[name][case_id] = list(ids)
                latencies[name].append(float(latency_ms))
                safe_rows[name].append(
                    {
                        "case_id": case_id,
                        "question_type": str(gold_case["question_type"]),
                        "expected_document": str(gold_case["expected_document"]),
                        "expected_evidence_type": str(
                            gold_case["expected_evidence_type"]
                        ),
                        "aggregate_eligible": True,
                        "retrieved_context_ids": list(ids),
                        "scores": [round(float(score), 8) for score in scores],
                        "latency_ms": round(float(latency_ms), 3),
                        "metrics": score_ranked_ids(
                            ids,
                            primary_gold_ids=gold_case["primary_gold_ids"],
                            acceptable_gold_ids=gold_case["acceptable_gold_ids"],
                        ),
                    }
                )

        if str(uat_case.get("expected_behavior")) == "answer":
            previous[scope] = {
                "question": str(uat_case["question"]),
                "document_ids": allowed_documents,
            }

    comparison = evaluate_fixed_rankings(gold_cases, rankings)
    comparison["latency"] = {
        name: _mean_p95(values) for name, values in latencies.items()
    }
    comparison["runtime"] = e5_runtime
    comparison["rows"] = safe_rows
    return comparison, e5_runtime, [chunk.text for chunk in library.chunks]


def classify_deferred_case(
    review_case: Mapping[str, Any],
    uat_case: Mapping[str, Any],
    *,
    chunk_candidate_count: int,
    table_candidate_count: int,
) -> dict[str, Any]:
    """Classify a deferred case from explicit review/evidence signals only."""
    note = str(review_case.get("note", "")).casefold()
    evidence_type = str(uat_case.get("expected_evidence_type", ""))
    if review_case.get("image_required") is True or evidence_type.startswith("image"):
        category = "image_human_review_required"
    elif "모호" in note or "ambiguous" in note:
        category = "ambiguous_question"
    elif review_case.get("table_required") is True or evidence_type == "table":
        category = "table_structure_or_mapping"
    elif not chunk_candidate_count:
        category = "retrieval_miss"
    elif not review_case.get("primary_gold_ids"):
        category = "insufficient_candidate_evidence"
    else:
        category = "other"

    evidence_exists = bool(
        review_case.get("primary_gold_ids")
        or review_case.get("acceptable_gold_ids")
        or chunk_candidate_count
        or table_candidate_count
    )
    layer_by_category = {
        "retrieval_miss": "retrieval_or_query_planning",
        "table_structure_or_mapping": "table_row_mapping_or_human_confirmation",
        "ambiguous_question": "human_question_clarification",
        "insufficient_candidate_evidence": "candidate_generation_or_human_confirmation",
        "image_human_review_required": "image_human_review",
        "other": "human_final_decision",
    }
    return {
        "case_id": str(uat_case.get("case_id", "")),
        "failure_category": category,
        "chunk_candidate_count": int(chunk_candidate_count),
        "table_candidate_count": int(table_candidate_count),
        "guideline_evidence_status": (
            "candidate_present_not_final_gold"
            if evidence_exists
            else "not_found_in_current_candidates"
        ),
        "improvement_layer": layer_by_category[category],
        "automatic_gold_approval": False,
        "requires_human_review": True,
    }


def final_readiness(
    *,
    core_regression_passed: bool,
    tf027_production_gold_approved: bool,
    external_transfer_approved: bool,
) -> dict[str, Any]:
    if not core_regression_passed:
        status = "NOT_READY"
    elif not tf027_production_gold_approved and not external_transfer_approved:
        status = "READY_EXCEPT_IMAGE_AND_LIVE_RAGAS"
    elif not tf027_production_gold_approved:
        status = "READY_EXCEPT_IMAGE_HUMAN_REVIEW"
    elif not external_transfer_approved:
        status = "READY_EXCEPT_LIVE_RAGAS"
    else:
        status = "READY"
    return {
        "status": status,
        "provider_live_status": (
            "READY_FOR_APPROVED_LIVE_SUBSET"
            if external_transfer_approved
            else "BLOCKED_BY_EXTERNAL_TRANSFER_APPROVAL"
        ),
        "provider_calls": 0,
        "llm_judge_calls": 0,
        "tf027_needs_human_review": not tf027_production_gold_approved,
        "production_image_gold_approved": tf027_production_gold_approved,
    }


def evaluate_structured_table_subset(
    *,
    catalog_path: Path,
    gold_cases: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Evaluate the existing local table-row route for approved table/mixed cases."""
    from tools.schat_gold_human_review import retrieve_local_table_candidates

    rows = []
    for case in gold_cases:
        if str(case["expected_evidence_type"]) not in {"table", "mixed"}:
            continue
        candidates = retrieve_local_table_candidates(
            catalog_path=catalog_path,
            question=str(case["question"]),
            document_scope=str(case["expected_document"]),
            limit=10,
        )
        identifiers = [str(candidate["evidence_id"]) for candidate in candidates]
        rows.append(
            {
                "case_id": str(case["case_id"]),
                "question_type": str(case["question_type"]),
                "expected_document": str(case["expected_document"]),
                "expected_evidence_type": str(case["expected_evidence_type"]),
                "aggregate_eligible": True,
                "retrieved_context_ids": identifiers,
                "scores": [round(float(candidate["score"]), 8) for candidate in candidates],
                "metrics": score_ranked_ids(
                    identifiers,
                    primary_gold_ids=case["primary_gold_ids"],
                    acceptable_gold_ids=case["acceptable_gold_ids"],
                ),
            }
        )
    return {
        "case_ids": [row["case_id"] for row in rows],
        "summary": aggregate_retrieval_rows(rows),
        "rows": [safe_artifact_row(row) for row in rows],
        "raw_table_text_persisted": False,
    }


def analyze_deferred_cases(
    *,
    catalog_path: Path,
    reviewed: Mapping[str, Any],
    uat: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Diagnose deferred cases while preserving every human decision."""
    from tools.schat_gold_human_review import (
        retrieve_local_chunk_candidates,
        retrieve_local_table_candidates,
    )

    review_by_id = {str(case["case_id"]): case for case in reviewed["cases"]}
    rows = []
    for uat_case in uat["cases"]:
        case_id = str(uat_case["case_id"])
        review = review_by_id.get(case_id)
        if not review or review.get("final_gold_approved") is True:
            continue
        scope = str(uat_case["document_scope"])
        chunk_candidates = retrieve_local_chunk_candidates(
            catalog_path=catalog_path,
            question=str(uat_case["question"]),
            document_scope=scope,
            limit=16,
        )
        table_candidates = []
        if str(uat_case["expected_evidence_type"]) in {"table", "mixed"}:
            table_candidates = retrieve_local_table_candidates(
                catalog_path=catalog_path,
                question=str(uat_case["question"]),
                document_scope=scope,
                limit=10,
            )
        diagnosis = classify_deferred_case(
            review,
            uat_case,
            chunk_candidate_count=len(chunk_candidates),
            table_candidate_count=len(table_candidates),
        )
        rows.append(
            {
                **diagnosis,
                "question": str(uat_case["question"]),
                "question_type": str(uat_case["question_type"]),
                "expected_document": scope,
                "expected_evidence_type": str(uat_case["expected_evidence_type"]),
                "review_note": str(review.get("note", "")),
                "existing_primary_count": len(review.get("primary_gold_ids", [])),
                "existing_acceptable_count": len(
                    review.get("acceptable_gold_ids", [])
                ),
                "automatic_improvement_applied": False,
                "post_evaluation_status": "human_review_queue",
            }
        )
    return rows


def _safe_deferred_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    safe = []
    for row in rows:
        safe.append(
            safe_artifact_row(
                {
                    **row,
                    "question_sha256": hashlib.sha256(
                        str(row.get("question", "")).encode("utf-8")
                    ).hexdigest(),
                    "review_note_sha256": hashlib.sha256(
                        str(row.get("review_note", "")).encode("utf-8")
                    ).hexdigest(),
                }
            )
        )
    return safe


def _tf027_queue(checklist: Mapping[str, Any]) -> dict[str, Any]:
    source = checklist["source_reference"]
    checks = list(checklist["checks"])
    return {
        "case_id": "TF027",
        "needs_human_review": checklist.get("needs_human_review") is True,
        "production_gold_approved": checklist.get("production_gold_approved") is True,
        "included_in_aggregate": checklist.get("included_in_aggregate") is True,
        "page": int(source["page"]),
        "figure_candidates": list(source["figure_candidates"]),
        "nearby_chunk_ids": list(source["nearby_chunk_ids"]),
        "check_ids": [str(check["check_id"]) for check in checks],
        "reviewed_check_count": sum(
            str(check.get("status")) != "unreviewed" for check in checks
        ),
        "remaining_check_count": sum(
            str(check.get("status")) == "unreviewed" for check in checks
        ),
        "vision_api_calls": 0,
        "automatic_image_interpretation": False,
    }


def _review_html(report: Mapping[str, Any]) -> str:
    rows = []
    for name in RETRIEVER_NAMES:
        metrics = report["retrieval"]["summaries"][name]
        latency = report["retrieval"]["latency"][name]
        rows.append(
            "<tr>"
            f"<td>{html.escape(name)}</td>"
            f"<td>{float(metrics['hit_at_1']):.4f}</td>"
            f"<td>{float(metrics['hit_at_3']):.4f}</td>"
            f"<td>{float(metrics['hit_at_5']):.4f}</td>"
            f"<td>{float(metrics['hit_at_10']):.4f}</td>"
            f"<td>{float(metrics['mrr']):.4f}</td>"
            f"<td>{float(metrics['recall_at_10']):.4f}</td>"
            f"<td>{float(metrics['precision_at_10']):.4f}</td>"
            f"<td>{float(latency['mean_ms']):.1f}</td>"
            "</tr>"
        )
    categories = report["deferred"]["category_counts"]
    return f"""<!doctype html><html lang="ko"><meta charset="utf-8">
<title>SCHAT v1 최종 안정화</title><style>
body{{font-family:system-ui;margin:36px;max-width:1180px;line-height:1.55}}
table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccd;padding:8px}}
.ok{{color:#087830}}.warn{{color:#9a5b00}}</style><body>
<h1>SCHAT v1 최종 안정화</h1>
<p>Readiness: <strong>{html.escape(str(report['readiness']['status']))}</strong></p>
<h2>Gold</h2><p>승인 positive {report['gold']['approved_positive_count']} · 보류
{report['gold']['deferred_positive_count']} · 승인 abstention {report['gold']['approved_abstention_count']}</p>
<h2>Retrieval</h2><table><thead><tr><th>전략</th><th>Hit@1</th><th>Hit@3</th>
<th>Hit@5</th><th>Hit@10</th><th>MRR</th><th>Recall@10</th><th>Precision@10</th>
<th>평균 ms</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<h2>Deferred</h2><p>{html.escape(json.dumps(categories, ensure_ascii=False))}</p>
<h2>UAT</h2><p>{report['uat']['pass_count']}/{report['uat']['case_count']} PASS</p>
<h2>Table</h2><p>구조화 table subset Hit@10:
{float(report['table']['summary'].get('hit_at_10', 0)):.4f}</p>
<h2>Image</h2><p class="warn">TF027 사람 검수 잔여
{report['image']['remaining_check_count']}개 · production Gold 미승인</p>
<h2>Provider/RAGAS</h2><p class="warn">{html.escape(report['readiness']['provider_live_status'])}
· 실제 호출 0회</p></body></html>"""


def run_stabilization(
    *,
    catalog_path: Path = DEFAULT_CATALOG,
    uat_path: Path = DEFAULT_UAT,
    operational_gold_path: Path = DEFAULT_OPERATIONAL_GOLD,
    reviewed_gold_path: Path = DEFAULT_REVIEWED_GOLD,
    tf027_path: Path = DEFAULT_TF027,
    snapshot_path: Path = DEFAULT_E5_SNAPSHOT,
    model_cache: Path = DEFAULT_MODEL_CACHE,
    output_dir: Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    """Execute all local-safe stabilization phases and write raw-free artifacts."""
    from tools.schat_gold_human_review import validate_review_fixture
    from tools.schat_multimodal_mvp_evaluate import (
        DEFAULT_FIXTURE as TABLE_RETRIEVAL_FIXTURE,
    )
    from tools.schat_multimodal_mvp_evaluate import (
        DEFAULT_MULTIMODAL as TABLE_GOLD_FIXTURE,
    )
    from tools.schat_multimodal_mvp_evaluate import (
        DEFAULT_PDF as TRANSFUSION_PDF,
    )
    from tools.schat_multimodal_mvp_evaluate import (
        evaluate_multimodal_mvp,
    )
    from tools.schat_v1_final_validate import (
        audit_artifacts,
        evaluate_operational_uat,
    )

    uat = _read_json(uat_path)
    operational_gold = _read_json(operational_gold_path)
    reviewed = _read_json(reviewed_gold_path)
    validate_review_fixture(reviewed)
    valid_ids = collect_valid_evidence_ids(catalog_path)
    gold = build_gold_manifest(
        reviewed,
        operational_gold,
        valid_evidence_ids=valid_ids,
    )
    if gold["approved_positive_count"] != 21 or gold["deferred_positive_count"] != 11:
        raise ValueError("reviewed Gold count drift")
    if gold["approved_abstention_count"] != 4:
        raise ValueError("approved abstention Gold count drift")

    gold_cases = _approved_cases(reviewed, uat)
    retrieval, e5_runtime, source_texts = evaluate_local_retrievers(
        catalog_path=catalog_path,
        uat=uat,
        gold_cases=gold_cases,
        snapshot_path=snapshot_path,
        model_cache=model_cache,
    )
    table = evaluate_structured_table_subset(
        catalog_path=catalog_path,
        gold_cases=gold_cases,
    )
    table_regression, _, _ = evaluate_multimodal_mvp(
        catalog_path=catalog_path,
        pdf_path=TRANSFUSION_PDF,
        fixture_path=TABLE_RETRIEVAL_FIXTURE,
        multimodal_fixture_path=TABLE_GOLD_FIXTURE,
    )
    table["approved_five_case_regression"] = table_regression["table"]
    deferred = analyze_deferred_cases(
        catalog_path=catalog_path,
        reviewed=reviewed,
        uat=uat,
    )
    category_counts = dict(
        sorted(Counter(row["failure_category"] for row in deferred).items())
    )
    tf027 = _tf027_queue(_read_json(tf027_path))
    uat_summary, uat_rows, performance = evaluate_operational_uat(
        catalog_path=catalog_path,
        fixture_path=uat_path,
    )
    core_passed = (
        uat_summary["pass_count"] == uat_summary["case_count"]
        and uat_summary["actual_provider_calls"] == 0
        and uat_summary["actual_vision_calls"] == 0
    )
    readiness = final_readiness(
        core_regression_passed=core_passed,
        tf027_production_gold_approved=bool(tf027["production_gold_approved"]),
        external_transfer_approved=False,
    )
    ragas = {
        name: {
            "id_context_precision": retrieval["summaries"][name][
                "ragas_id_context_precision"
            ],
            "id_context_recall": retrieval["summaries"][name][
                "ragas_id_context_recall"
            ],
            "faithfulness": "pending_external_llm_judge_approval",
            "answer_relevancy": "pending_external_llm_judge_approval",
        }
        for name in RETRIEVER_NAMES
    }
    provider = {
        "status": "BLOCKED_BY_EXTERNAL_TRANSFER_APPROVAL",
        "required_approvals": [
            "hospital_evidence_external_transfer",
            "provider_and_model",
            "region",
            "retention",
            "case_scope",
            "credential",
        ],
        "original_user_question_sent": False,
        "whole_document_sent": False,
        "actual_provider_calls": 0,
        "actual_llm_judge_calls": 0,
        "synthetic_wire_mock_available": True,
        "real_case_minimal_subset_ready_after_approval": True,
        "raw_payload_artifact_saved": False,
        "raw_response_artifact_saved": False,
    }
    report = {
        "schema_version": 1,
        "stable_version_before": "v0.9",
        "gold": gold,
        "retrieval": retrieval,
        "ragas": ragas,
        "table": table,
        "deferred": {
            "case_count": len(deferred),
            "category_counts": category_counts,
            "automatically_improved_count": 0,
            "remaining_human_review_count": len(deferred),
            "rows": _safe_deferred_rows(deferred),
        },
        "image": tf027,
        "provider": provider,
        "uat": uat_summary,
        "performance": {**performance, "e5": e5_runtime},
        "readiness": readiness,
        "production_retrieval_changed": False,
        "production_validator_changed": False,
        "actual_external_calls": 0,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "gold_manifest.json", gold)
    _write_json(
        output_dir / "retrieval_summary.json",
        {
            "summaries": retrieval["summaries"],
            "table_subset": retrieval["table_subset"],
            "latency": retrieval["latency"],
            "runtime": retrieval["runtime"],
        },
    )
    _write_json(output_dir / "retrieval_results.json", retrieval["rows"])
    _write_json(output_dir / "ragas_results.json", ragas)
    _write_json(output_dir / "table_summary.json", table)
    _write_json(
        output_dir / "deferred_analysis.json",
        {
            "case_count": len(deferred),
            "category_counts": category_counts,
            "automatically_improved_count": 0,
            "remaining_human_review_count": len(deferred),
            "rows": _safe_deferred_rows(deferred),
        },
    )
    _write_json(output_dir / "image_review_queue.json", tf027)
    _write_json(output_dir / "provider_readiness.json", provider)
    _write_json(output_dir / "uat_summary.json", uat_summary)
    _write_json(output_dir / "uat_results.json", uat_rows)
    _write_json(output_dir / "performance.json", report["performance"])
    _write_json(output_dir / "readiness.json", readiness)
    (output_dir / "review.html").write_text(_review_html(report), encoding="utf-8")
    security = audit_artifacts(output_dir, source_texts=source_texts)
    _write_json(output_dir / "security_audit.json", security)
    report["security"] = security
    if not security["pass"]:
        raise ValueError("artifact security audit failed")
    return {**report, "deferred_full": deferred}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--uat", type=Path, default=DEFAULT_UAT)
    parser.add_argument("--operational-gold", type=Path, default=DEFAULT_OPERATIONAL_GOLD)
    parser.add_argument("--reviewed-gold", type=Path, default=DEFAULT_REVIEWED_GOLD)
    parser.add_argument("--tf027", type=Path, default=DEFAULT_TF027)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_E5_SNAPSHOT)
    parser.add_argument("--model-cache", type=Path, default=DEFAULT_MODEL_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = run_stabilization(
        catalog_path=args.catalog,
        uat_path=args.uat,
        operational_gold_path=args.operational_gold,
        reviewed_gold_path=args.reviewed_gold,
        tf027_path=args.tf027,
        snapshot_path=args.snapshot,
        model_cache=args.model_cache,
        output_dir=args.output,
    )
    print(
        json.dumps(
            {
                "approved_positive": report["gold"]["approved_positive_count"],
                "deferred_positive": report["gold"]["deferred_positive_count"],
                "uat": f"{report['uat']['pass_count']}/{report['uat']['case_count']}",
                "readiness": report["readiness"]["status"],
                "provider_calls": report["actual_external_calls"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
