"""Evaluation-only ChromaDB retrieval over the frozen SCHAT corpus.

This module deliberately stops at ranked chunk IDs. It does not import or call
the production lexical retriever, fusion, reranking, context expansion,
evidence admission, SourceUnit, generation, or clinical validators.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from statistics import fmean
from typing import Any, Mapping, Sequence

import numpy as np

from src.library import Chunk, Embedder, bounded_embedding_question, fingerprint
from src.query import plan_query
from src.repository import snapshot
from src.settings import DIMENSIONS, MODEL
from tools.retrieval_baseline_metrics import (
    FINGERPRINT_ALGORITHM_ID,
    id_based_context_scores,
    ranked_retrieval_metrics,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "library" / "catalog.sqlite3"
DEFAULT_UAT = ROOT / "tests" / "fixtures" / "schat_v1_operational_uat.json"
DEFAULT_REVIEWED_GOLD = ROOT / "tests" / "fixtures" / "schat_v1_operational_gold_reviewed.json"
DEFAULT_OPERATIONAL_GOLD = ROOT / "tests" / "fixtures" / "schat_v1_operational_gold.json"
DEFAULT_OUTPUT = ROOT / "workspace" / "검색_성능평가" / "ChromaDB" / "2026-09-19_chromadb-only-eval"
COLLECTION_NAME = "schat_chromadb_eval_v1"
TOP_K = (1, 3, 5, 10)
FORBIDDEN_ARTIFACT_FIELDS = frozenset(
    {
        "answer",
        "chunk_text",
        "content",
        "documents",
        "evidence_text",
        "full_prompt",
        "prompt",
        "question",
        "raw_request",
        "raw_response",
        "raw_text",
        "source_text",
        "source_unit_text",
        "text",
    }
)
SAFE_EXACT_METADATA_FIELDS = frozenset({"section"})


@dataclass(frozen=True)
class ApprovedCase:
    case_id: str
    question: str
    question_type: str
    document_scope: str
    expected_evidence_type: str
    primary_gold_ids: tuple[str, ...]
    acceptable_gold_ids: tuple[str, ...]

    @property
    def gold_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.primary_gold_ids, *self.acceptable_gold_ids)))


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    document_id: str
    page: int | None
    section: str
    rank: int
    similarity: float
    distance: float


def retrieval_contract() -> dict[str, Any]:
    """Declare the intentionally narrow engine boundary for audit/tests."""
    return {
        "engine": "chromadb_only",
        "bm25_calls": 0,
        "rrf_calls": 0,
        "reranker_calls": 0,
        "context_expansion": False,
        "evidence_gate": False,
        "source_unit": False,
        "llm_calls": 0,
        "validator_calls": 0,
    }


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "not_installed"


def _catalog_revision(path: Path) -> int:
    resolved = path.resolve()
    if not resolved.is_file():
        raise ValueError(f"catalog not found: {resolved}")
    with sqlite3.connect(resolved.as_uri() + "?mode=ro&immutable=1", uri=True) as connection:
        row = connection.execute("select version from corpus").fetchone()
    if row is None:
        raise ValueError("catalog revision missing")
    return int(row[0])


def _unique_ids(value: object, *, field: str, case_id: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{case_id}: {field} must be a list")
    result = tuple(str(item).strip() for item in value)
    if any(not item for item in result) or len(result) != len(set(result)):
        raise ValueError(f"{case_id}: invalid {field}")
    return result


def load_approved_cases(reviewed: Mapping[str, Any], uat: Mapping[str, Any]) -> list[ApprovedCase]:
    """Join human-reviewed positive Gold to operational questions without mutation."""
    review_rows = reviewed.get("cases")
    uat_rows = uat.get("cases")
    if not isinstance(review_rows, list) or not isinstance(uat_rows, list):
        raise ValueError("reviewed Gold and UAT must contain case lists")
    reviewed_by_id = {str(row.get("case_id", "")): row for row in review_rows}
    if "" in reviewed_by_id or len(reviewed_by_id) != len(review_rows):
        raise ValueError("reviewed Gold case IDs must be unique")

    cases: list[ApprovedCase] = []
    for uat_case in uat_rows:
        case_id = str(uat_case.get("case_id", ""))
        review = reviewed_by_id.get(case_id)
        if not review or review.get("final_gold_approved") is not True:
            continue
        if review.get("review_status") != "approved":
            raise ValueError(f"{case_id}: final Gold is not approved")
        if str(uat_case.get("document_scope")) == "out_of_scope":
            raise ValueError(f"{case_id}: positive Gold cannot be out of scope")
        primary = _unique_ids(review.get("primary_gold_ids"), field="primary_gold_ids", case_id=case_id)
        acceptable = _unique_ids(
            review.get("acceptable_gold_ids"),
            field="acceptable_gold_ids",
            case_id=case_id,
        )
        if not primary or set(primary).intersection(acceptable):
            raise ValueError(f"{case_id}: invalid approved evidence IDs")
        expected_document = str(review.get("expected_document", ""))
        document_scope = str(uat_case.get("document_scope", ""))
        if expected_document != document_scope:
            raise ValueError(f"{case_id}: reviewed/UAT document scope drift")
        cases.append(
            ApprovedCase(
                case_id=case_id,
                question=str(uat_case.get("question", "")),
                question_type=str(uat_case.get("question_type", "unknown")),
                document_scope=document_scope,
                expected_evidence_type=str(review.get("expected_evidence_type", "text")),
                primary_gold_ids=primary,
                acceptable_gold_ids=acceptable,
            )
        )
    if not cases:
        raise ValueError("no approved positive Gold cases")
    return cases


def validate_vectors(vectors: np.ndarray, *, dimensions: int) -> dict[str, Any]:
    """Verify the frozen production vectors are finite L2 unit vectors."""
    if vectors.ndim != 2 or vectors.shape[1] != dimensions or not len(vectors):
        raise ValueError("embedding matrix shape drift")
    if not np.isfinite(vectors).all():
        raise ValueError("embedding matrix contains non-finite values")
    norms = np.linalg.norm(vectors, axis=1)
    if not np.allclose(norms, 1.0, rtol=0.0, atol=1e-4):
        raise ValueError("production embeddings are not L2-normalized")
    return {
        "normalization": "l2_unit",
        "minimum_norm": round(float(norms.min()), 6),
        "maximum_norm": round(float(norms.max()), 6),
    }


def rebuild_collection(
    client: Any,
    chunks: Sequence[Chunk],
    vectors: np.ndarray,
    *,
    collection_name: str = COLLECTION_NAME,
    batch_size: int = 64,
) -> Any:
    """Cleanly replace one evaluation collection without persisting source text."""
    if len(chunks) != len(vectors) or not chunks or batch_size < 1:
        raise ValueError("chunk/vector collection input mismatch")
    existing = {str(item if isinstance(item, str) else item.name) for item in client.list_collections()}
    if collection_name in existing:
        client.delete_collection(collection_name)
    collection = client.create_collection(
        name=collection_name,
        embedding_function=None,
        metadata={"hnsw:space": "cosine"},
    )
    for start in range(0, len(chunks), batch_size):
        batch_chunks = chunks[start : start + batch_size]
        batch_vectors = vectors[start : start + batch_size]
        collection.add(
            ids=[chunk.id for chunk in batch_chunks],
            embeddings=[vector.tolist() for vector in batch_vectors],
            metadatas=[
                {
                    "document_id": chunk.document_id,
                    "page": chunk.page if chunk.page is not None else -1,
                    "section": chunk.section,
                    "position": chunk.index,
                    "parent_id": chunk.parent_id,
                }
                for chunk in batch_chunks
            ],
        )
    if collection.count() != len(chunks):
        raise RuntimeError("ChromaDB collection count mismatch")
    return collection


def query_collection(
    collection: Any,
    query_vector: np.ndarray,
    *,
    document_ids: Sequence[str],
    limit: int,
    candidate_limit: int | None = None,
) -> list[RetrievedChunk]:
    """Return a document-scoped cosine ranking with deterministic tie handling."""
    allowed = list(dict.fromkeys(str(value) for value in document_ids if str(value)))
    if not allowed or limit < 1:
        raise ValueError("query requires document scope and positive limit")
    query_limit = candidate_limit if candidate_limit is not None else limit
    if query_limit < limit:
        raise ValueError("candidate limit must cover requested Top-k")
    vector = np.asarray(query_vector, dtype=np.float32)
    if vector.ndim != 1 or not np.isfinite(vector).all():
        raise ValueError("invalid query vector")
    result = collection.query(
        query_embeddings=[vector.tolist()],
        n_results=query_limit,
        where={"document_id": {"$in": allowed}},
        include=["distances", "metadatas"],
    )
    ids = list(result["ids"][0])
    distances = [float(value) for value in result["distances"][0]]
    metadatas = list(result["metadatas"][0])
    if len(ids) != len(set(ids)) or not (len(ids) == len(distances) == len(metadatas)):
        raise RuntimeError("invalid ChromaDB ranked result")
    candidates = list(zip(ids, distances, metadatas, strict=True))
    candidates.sort(
        key=lambda row: (
            row[1],
            int(row[2].get("position", -1)),
            str(row[0]),
        )
    )
    rows = []
    for rank, (chunk_id, distance, metadata) in enumerate(candidates[:limit], 1):
        document_id = str(metadata.get("document_id", ""))
        if document_id not in allowed:
            raise RuntimeError("ChromaDB result escaped document scope")
        page_value = int(metadata.get("page", -1))
        rows.append(
            RetrievedChunk(
                chunk_id=str(chunk_id),
                document_id=document_id,
                page=None if page_value < 0 else page_value,
                section=str(metadata.get("section", "")),
                rank=rank,
                similarity=1.0 - distance,
                distance=distance,
            )
        )
    return rows


def _metric_values(retrieved_ids: list[str], gold_ids: tuple[str, ...]) -> dict[str, float]:
    metrics = ranked_retrieval_metrics(retrieved_ids, list(gold_ids), cutoffs=TOP_K)
    ragas = id_based_context_scores(retrieved_ids[:10], list(gold_ids))
    return {
        **{key: float(value) for key, value in metrics.items()},
        "ragas_id_context_precision": float(ragas["context_precision"]),
        "ragas_id_context_recall": float(ragas["context_recall"]),
    }


def build_case_result(
    case: ApprovedCase,
    retrieved: Sequence[RetrievedChunk],
    *,
    latency_ms: float,
    corpus_chunk_ids: set[str],
    embedding_latency_ms: float | None = None,
    query_latency_ms: float | None = None,
) -> dict[str, Any]:
    retrieved_ids = [row.chunk_id for row in retrieved]
    metrics = _metric_values(retrieved_ids, case.gold_ids)
    missing_gold = sorted(set(case.gold_ids) - corpus_chunk_ids)
    available_gold = set(case.gold_ids).intersection(corpus_chunk_ids)
    if not available_gold:
        failure = "gold_not_in_chromadb_corpus"
    elif missing_gold:
        failure = "gold_partially_outside_chromadb_corpus"
    elif metrics["hit_at_10"] == 0.0:
        failure = "no_gold_hit_top_10"
    elif metrics["hit_at_1"] == 0.0:
        failure = "gold_below_rank_1"
    else:
        failure = "none"
    return {
        "case_id": case.case_id,
        "question_type": case.question_type,
        "document_scope": case.document_scope,
        "expected_evidence_type": case.expected_evidence_type,
        "gold_ids": list(case.gold_ids),
        "gold_ids_in_chromadb_corpus": sorted(available_gold),
        "gold_ids_outside_chromadb_corpus": missing_gold,
        "ranked_results": [
            {
                "rank": row.rank,
                "chunk_id": row.chunk_id,
                "document_id": row.document_id,
                "page": row.page,
                "section": row.section,
                "similarity": round(row.similarity, 8),
                "distance": round(row.distance, 8),
                "gold_hit": row.chunk_id in set(case.gold_ids),
            }
            for row in retrieved
        ],
        "metrics": metrics,
        "latency_ms": {
            "embedding": round(float(embedding_latency_ms or 0.0), 3),
            "query": round(float(query_latency_ms or 0.0), 3),
            "total": round(float(latency_ms), 3),
        },
        "failure_category": failure,
    }


def _aggregate(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"case_count": 0}
    metric_names = tuple(rows[0]["metrics"])
    totals = [float(row["latency_ms"]["total"]) for row in rows]
    return {
        "case_count": len(rows),
        **{name: round(fmean(float(row["metrics"][name]) for row in rows), 12) for name in metric_names},
        "latency_mean_ms": round(fmean(totals), 3),
    }


def aggregate_results(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, dict[str, Any]] = {}
    for question_type in sorted({str(row["question_type"]) for row in rows}):
        by_type[question_type] = _aggregate([row for row in rows if row["question_type"] == question_type])
    return {
        "overall": _aggregate(rows),
        "by_question_type": by_type,
        "failure_categories": dict(sorted(Counter(str(row["failure_category"]) for row in rows).items())),
        "failure_case_ids": [str(row["case_id"]) for row in rows if str(row["failure_category"]) != "none"],
    }


def ensure_artifact_safe(payload: Any, *, forbidden_exact_texts: set[str]) -> None:
    """Reject raw-source fields and verbatim registered chunk text."""

    def visit(value: Any, *, field: str = "") -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                normalized_key = str(key).casefold()
                if normalized_key in FORBIDDEN_ARTIFACT_FIELDS:
                    raise ValueError(f"forbidden artifact field: {key}")
                visit(item, field=normalized_key)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item, field=field)
        elif (
            isinstance(value, str)
            and value in forbidden_exact_texts
            and field not in SAFE_EXACT_METADATA_FIELDS
        ):
            raise ValueError("artifact contains exact source text")

    visit(payload)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_metrics_csv(path: Path, summary: Mapping[str, Any]) -> None:
    rows = [{"scope": "overall", "question_type": "all", **summary["overall"]}]
    rows.extend(
        {"scope": "question_type", "question_type": question_type, **metrics}
        for question_type, metrics in summary["by_question_type"].items()
    )
    fieldnames = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_failure_analysis(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    failures = [row for row in rows if row["failure_category"] != "none"]
    lines = [
        "# ChromaDB-only Retrieval 실패 분석",
        "",
        "- 질문 원문과 병원 근거 원문은 저장하지 않았다.",
        "- 아래 분류는 동결된 Gold ID와 ChromaDB Top-10 ID만 비교한 결과다.",
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


def _approved_abstention_count(operational_gold: Mapping[str, Any]) -> int:
    cases = operational_gold.get("cases")
    if not isinstance(cases, list):
        raise ValueError("operational Gold cases missing")
    return sum(row.get("label_status") == "approved" and row.get("expected_abstain") is True for row in cases)


def evaluate(
    *,
    catalog_path: Path = DEFAULT_CATALOG,
    uat_path: Path = DEFAULT_UAT,
    reviewed_gold_path: Path = DEFAULT_REVIEWED_GOLD,
    operational_gold_path: Path = DEFAULT_OPERATIONAL_GOLD,
    output_dir: Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    """Run the local ChromaDB-only path and persist raw-source-free metrics."""
    import chromadb
    from chromadb.config import Settings

    reviewed = _read_json(reviewed_gold_path)
    uat = _read_json(uat_path)
    operational_gold = _read_json(operational_gold_path)
    approved_cases = load_approved_cases(reviewed, uat)
    approved_by_id = {case.case_id: case for case in approved_cases}

    revision = _catalog_revision(catalog_path)
    library = snapshot(str(catalog_path.resolve()), revision)
    if not library.chunks or not library.docs:
        raise ValueError("frozen corpus is empty")
    if any(str(document.get("model")) != MODEL for document in library.docs):
        raise ValueError("catalog embedding model drift")
    vector_details = validate_vectors(library.vectors, dimensions=DIMENSIONS)
    corpus_chunk_ids = {chunk.id for chunk in library.chunks}
    if len(corpus_chunk_ids) != len(library.chunks):
        raise ValueError("duplicate catalog chunk ID")
    corpus_fingerprint = fingerprint(library.docs, [str(document["id"]) for document in library.docs])

    from tools.schat_v1_final_validate import _scope_document_ids

    scopes = _scope_document_ids(library.docs)
    index_dir = output_dir / "chroma_index"
    index_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(index_dir), settings=Settings(anonymized_telemetry=False))
    collection = rebuild_collection(client, library.chunks, library.vectors)
    stored = collection.get(limit=len(library.chunks), include=["documents"])
    if any(value is not None for value in (stored.get("documents") or [])):
        raise RuntimeError("ChromaDB unexpectedly persisted source documents")

    embedder = Embedder()
    results: list[dict[str, Any]] = []
    previous: dict[str, dict[str, Any]] = {}
    for uat_case in uat["cases"]:
        scope = str(uat_case.get("document_scope", ""))
        case_id = str(uat_case.get("case_id", ""))
        if scope in scopes:
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
                query_started = time.perf_counter()
                ranked = query_collection(
                    collection,
                    query_vector,
                    document_ids=tuple(sorted(effective_documents)),
                    limit=10,
                    candidate_limit=sum(chunk.document_id in effective_documents for chunk in library.chunks),
                )
                query_ms = (time.perf_counter() - query_started) * 1000
                if len(ranked) != min(
                    10, sum(chunk.document_id in effective_documents for chunk in library.chunks)
                ):
                    raise RuntimeError(f"{case_id}: incomplete ChromaDB Top-k")
                results.append(
                    build_case_result(
                        case,
                        ranked,
                        latency_ms=embedding_ms + query_ms,
                        embedding_latency_ms=embedding_ms,
                        query_latency_ms=query_ms,
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

    summary = aggregate_results(results)
    contract = {
        "schema_version": 1,
        "evaluation_date": date.today().isoformat(),
        "evaluator_file": "tools/chromadb_only_evaluate.py",
        "engine": "chromadb_only",
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
            "fixture_sha256": _sha256_file(reviewed_gold_path),
            "dataset_version": reviewed.get("dataset_version", "unknown"),
            "approved_positive_case_count": len(approved_cases),
            "deferred_positive_case_count": len(reviewed["cases"]) - len(approved_cases),
            "approved_abstention_case_count": _approved_abstention_count(operational_gold),
            "negative_handling": "separate_not_queried_by_positive_retrieval_evaluator",
        },
        "query_processing": {
            "planner": "src.query.plan_query",
            "embedding_projection": "src.library.bounded_embedding_question(plan.expanded)",
            "follow_up_context": True,
            "document_scope_filter": True,
            "context_expansion": False,
            "equal_distance_tie_breaker": "corpus_position_then_chunk_id",
        },
        "embedding": {
            "model": MODEL,
            "runtime_library": "fastembed",
            "runtime_version": _package_version("fastembed"),
            "dimension": DIMENSIONS,
            **vector_details,
        },
        "chromadb": {
            "version": _package_version("chromadb"),
            "collection_name": COLLECTION_NAME,
            "similarity_metric": "cosine",
            "metric_reason": (
                "Production MiniLM vectors are L2-normalized; cosine ranking is "
                "equivalent to the current FAISS inner-product dense ranking."
            ),
            "default_embedding_function_used": False,
            "documents_stored": False,
            "anonymized_telemetry": False,
            "clean_rebuild": True,
        },
        "top_k": list(TOP_K),
    }
    summary_payload = {
        "schema_version": 1,
        "engine": "chromadb_only",
        "approved_positive_case_count": len(approved_cases),
        "deferred_positive_case_count": len(reviewed["cases"]) - len(approved_cases),
        "approved_abstention_case_count": _approved_abstention_count(operational_gold),
        "metrics": summary,
        "production_changed": False,
        "gold_changed": False,
        "raw_source_text_persisted": False,
    }
    per_case_payload = {
        "schema_version": 1,
        "engine": "chromadb_only",
        "cases": results,
    }
    forbidden_texts = {chunk.text for chunk in library.chunks}
    for payload in (contract, summary_payload, per_case_payload):
        ensure_artifact_safe(payload, forbidden_exact_texts=forbidden_texts)

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "evaluation_contract.json", contract)
    _write_json(output_dir / "summary.json", summary_payload)
    _write_json(output_dir / "per_case_results.json", per_case_payload)
    _write_metrics_csv(output_dir / "metrics.csv", summary)
    _write_failure_analysis(output_dir / "failure_analysis.md", results)
    return {
        "contract": contract,
        "summary": summary_payload,
        "per_case": per_case_payload,
        "output_dir": str(output_dir),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the evaluation-only SCHAT ChromaDB retrieval path.")
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
                "engine": "chromadb_only",
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
