"""Evaluation-only ChromaDB and RAGAS ID baseline for registered chunks."""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import html
import json
import os
import platform
import sqlite3
import sys
import time
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from statistics import fmean
from typing import Any

import numpy as np

from mvp.library import CHUNK_VERSION, Chunk, Embedder, bounded_embedding_question, has_substantive_body
from mvp.settings import DIMENSIONS, MODEL
from tools.retrieval_baseline_metrics import (
    aggregate_metrics,
    id_based_context_scores,
    ranked_retrieval_metrics,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "library" / "catalog.sqlite3"
DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "transfusion_retrieval_baseline.json"
DEFAULT_OUTPUT = ROOT / "artifacts" / "2026-09-16_transfusion-chromadb-ragas-baseline"
FORBIDDEN_TEXT_FIELDS = {
    "chunk_text",
    "content",
    "contents",
    "exact_text",
    "quote",
    "source_text",
    "source_unit_text",
}


@dataclass(frozen=True)
class CatalogChunk:
    chunk_id: str
    document_id: str
    position: int
    page: int | None
    section: str
    parent_id: str
    text: str
    vector: np.ndarray
    substantive_body: bool
    document_name: str = ""
    title: str = ""
    source_type: str = "pdf"
    location: str = ""


def _readonly_connection(path: Path) -> sqlite3.Connection:
    resolved = path.resolve()
    if not resolved.is_file():
        raise ValueError(f"catalog does not exist: {resolved}")
    connection = sqlite3.connect(resolved.as_uri() + "?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def load_catalog(path: Path, *, document_name: str) -> tuple[dict[str, Any], list[CatalogChunk]]:
    """Read one ready document and its registered vectors without mutating SQLite."""
    with _readonly_connection(path) as db:
        documents = []
        for row in db.execute("select id,status,metadata from documents where status='ready'"):
            metadata = json.loads(row["metadata"])
            if metadata.get("document_name") == document_name:
                documents.append((row["id"], metadata))
        if len(documents) != 1:
            raise ValueError(f"expected one ready document named {document_name!r}, found {len(documents)}")
        document_id, metadata = documents[0]
        rows = db.execute(
            "select id,document_id,position,payload,vector from chunks where document_id=? order by position",
            (document_id,),
        ).fetchall()

    if metadata.get("model") != MODEL:
        raise ValueError("catalog embedding model drift")
    if metadata.get("chunk_version") != CHUNK_VERSION:
        raise ValueError("catalog chunk version drift")
    if metadata.get("chunk_count") != len(rows):
        raise ValueError("catalog chunk count drift")
    if not rows:
        raise ValueError("catalog has no chunks")

    chunks: list[CatalogChunk] = []
    seen: set[str] = set()
    for expected_position, row in enumerate(rows):
        payload = json.loads(row["payload"])
        chunk = Chunk.from_row(payload)
        vector = np.frombuffer(row["vector"], dtype="<f4").copy()
        if row["id"] in seen:
            raise ValueError("duplicate catalog chunk id")
        if row["id"] != chunk.id or row["document_id"] != chunk.document_id:
            raise ValueError("catalog payload identity drift")
        if (
            row["document_id"] != document_id
            or row["position"] != expected_position
            or chunk.index != expected_position
        ):
            raise ValueError("catalog source order drift")
        if vector.shape != (DIMENSIONS,) or not np.isfinite(vector).all() or not np.linalg.norm(vector):
            raise ValueError("catalog vector integrity failure")
        if not chunk.parent_id:
            raise ValueError("catalog parent metadata missing")
        seen.add(row["id"])
        chunks.append(
            CatalogChunk(
                chunk_id=row["id"],
                document_id=document_id,
                position=expected_position,
                page=chunk.page,
                section=chunk.section,
                parent_id=chunk.parent_id,
                text=chunk.text,
                vector=vector,
                substantive_body=has_substantive_body(chunk),
                document_name=chunk.document_name,
                title=chunk.title,
                source_type=chunk.source_type,
                location=chunk.location,
            )
        )
    return {**metadata, "id": document_id}, chunks


def validate_fixture_document(
    fixture_document: dict[str, Any], metadata: dict[str, Any], chunks: list[CatalogChunk]
) -> None:
    expected = {
        "id": metadata["id"],
        "name": metadata["document_name"],
        "page_count": metadata["page_count"],
        "chunk_count": len(chunks),
        "chunk_version": metadata["chunk_version"],
        "model": metadata["model"],
        "dimensions": DIMENSIONS,
    }
    if fixture_document != expected:
        raise ValueError("fixture document metadata drift")


def validate_evaluation_cases(
    cases: list[dict[str, Any]],
    chunks: list[CatalogChunk],
    *,
    minimum_cases: int = 30,
    maximum_cases: int = 50,
) -> dict[str, Any]:
    if not minimum_cases <= len(cases) <= maximum_cases:
        raise ValueError(f"evaluation set must contain {minimum_cases} to {maximum_cases} cases")
    chunk_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    case_ids: set[str] = set()
    questions: set[str] = set()
    approved = 0
    review = 0
    positive = 0
    negative = 0
    allowed_types = {
        "preparation",
        "procedure",
        "monitoring",
        "adverse_reaction",
        "product_specific",
        "fact_specific",
        "temporal",
        "paraphrase",
        "negative_out_of_scope",
    }
    for case in cases:
        case_id = case.get("question_id")
        question = case.get("question")
        if not isinstance(case_id, str) or not case_id or case_id in case_ids:
            raise ValueError("case ids must be unique non-empty strings")
        if not isinstance(question, str) or not question.strip() or question in questions:
            raise ValueError("questions must be unique non-empty strings")
        if case.get("question_type") not in allowed_types:
            raise ValueError(f"{case_id}: question_type missing")
        if not isinstance(case.get("expected_answerable"), bool):
            raise ValueError(f"{case_id}: expected_answerable missing")
        references = case.get("reference_contexts")
        if not isinstance(references, list):
            raise ValueError(f"{case_id}: reference contexts missing")
        reference_ids = [reference.get("chunk_id") for reference in references]
        if case.get("reference_context_ids") != reference_ids:
            raise ValueError(f"{case_id}: reference context id drift")
        parent_ids = list(dict.fromkeys(reference.get("parent_id") for reference in references))
        if case.get("reference_parent_ids") != parent_ids:
            raise ValueError(f"{case_id}: reference parent id drift")
        if len(reference_ids) != len(set(reference_ids)):
            raise ValueError(f"{case_id}: duplicate reference chunk id")
        needs_review = case.get("needs_human_review") is True
        reasons = case.get("review_reasons")
        if not isinstance(reasons, list) or (needs_review and not reasons) or (not needs_review and reasons):
            raise ValueError(f"{case_id}: review flags inconsistent")
        if not case["expected_answerable"]:
            if case["question_type"] != "negative_out_of_scope" or references or needs_review:
                raise ValueError(f"{case_id}: negative case contract invalid")
            case_ids.add(case_id)
            questions.add(question)
            negative += 1
            continue
        if not references or case["question_type"] == "negative_out_of_scope":
            raise ValueError(f"{case_id}: positive reference contexts missing")
        positive += 1
        for reference in references:
            chunk = chunk_by_id.get(reference.get("chunk_id"))
            if chunk is None:
                raise ValueError(f"{case_id}: unknown reference chunk id")
            fingerprint = hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
            if reference.get("text_sha256") != fingerprint:
                raise ValueError(f"{case_id}: fingerprint drift")
            if reference.get("page") != chunk.page or reference.get("parent_id") != chunk.parent_id:
                raise ValueError(f"{case_id}: structural metadata drift")
            if reference.get("substantive_body") is not chunk.substantive_body:
                raise ValueError(f"{case_id}: substantive-body drift")
            source_shape = reference.get("source_shape")
            if source_shape not in {"body", "table", "image_plus_text"}:
                raise ValueError(f"{case_id}: unsupported source shape")
            if source_shape in {"table", "image_plus_text"} and not needs_review:
                review_status = case.get("review_status")
                review_audit = case.get("review_audit")
                allowed_status = {
                    "table": "approved_structural_table",
                    "image_plus_text": "approved_nearby_text",
                }[source_shape]
                if (
                    review_status != allowed_status
                    or not isinstance(review_audit, dict)
                    or review_audit.get("status") != review_status
                    or not review_audit.get("relationship")
                    or review_audit.get("raw_source_text_stored") is not False
                ):
                    raise ValueError(f"{case_id}: table/image reference requires human review")
            if not chunk.substantive_body and not needs_review:
                raise ValueError(f"{case_id}: approved gold is not substantive body")
        case_ids.add(case_id)
        questions.add(question)
        if needs_review:
            review += 1
        else:
            approved += 1
    return {
        "case_count": len(cases),
        "positive_case_count": positive,
        "approved_case_count": approved,
        "human_review_case_count": review,
        "negative_case_count": negative,
    }


def ensure_raw_text_free(payload: Any, *, forbidden_exact_texts: set[str]) -> None:
    """Reject source-text fields and verbatim registered chunk text in persisted artifacts."""

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).lower() in FORBIDDEN_TEXT_FIELDS:
                    raise ValueError(f"forbidden text field: {key}")
                visit(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item)
        elif isinstance(value, str) and value in forbidden_exact_texts:
            raise ValueError("artifact contains exact source text")

    visit(payload)


def build_comparison_schema(*, cutoffs: tuple[int, ...]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "join_keys": ["dataset_id", "case_id", "chunk_id"],
        "cutoffs": list(cutoffs),
        "case_fields": [
            "dataset_id",
            "case_id",
            "question_type",
            "question",
            "needs_human_review",
            "reference_context_ids",
            "retrieved_context_ids",
            "retrieval_latency_ms",
        ],
        "metric_fields": [
            *[f"hit_at_{cutoff}" for cutoff in cutoffs],
            "mrr",
            *[f"recall_at_{cutoff}" for cutoff in cutoffs],
            *[f"precision_at_{cutoff}" for cutoff in cutoffs],
            "ragas_id_context_precision",
            "ragas_id_context_recall",
        ],
        "ranked_result_fields": ["dataset_id", "case_id", "chunk_id", "rank", "distance_or_score"],
    }


def build_safe_case_result(
    *,
    case: dict[str, Any],
    retrieved_ids: list[str],
    distances: list[float],
    metrics: dict[str, float],
    ragas: dict[str, float],
    embedding_latency_ms: float,
    query_latency_ms: float,
) -> dict[str, Any]:
    return {
        "case_id": case["question_id"],
        "question_type": case["question_type"],
        "question": case["question"],
        "expected_answerable": case["expected_answerable"],
        "needs_human_review": bool(case["needs_human_review"]),
        "review_reasons": list(case["review_reasons"]),
        "reference_context_ids": list(case["reference_context_ids"]),
        "retrieved_context_ids": list(retrieved_ids),
        "distances": [float(value) for value in distances],
        "metrics": {key: float(value) for key, value in metrics.items()},
        "ragas": {key: float(value) for key, value in ragas.items()},
        "latency_ms": {
            "embedding": float(embedding_latency_ms),
            "query": float(query_latency_ms),
            "total": float(embedding_latency_ms + query_latency_ms),
        },
    }


def build_chroma_collection(
    index_dir: Path, chunks: list[CatalogChunk], *, name: str = "transfusion_baseline"
):
    import chromadb
    from chromadb.config import Settings

    index_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(
        path=str(index_dir),
        settings=Settings(anonymized_telemetry=False),
    )
    collection = client.create_collection(
        name=name,
        embedding_function=None,
        metadata={"hnsw:space": "cosine"},
    )
    batch_size = 64
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        collection.add(
            ids=[chunk.chunk_id for chunk in batch],
            embeddings=[chunk.vector.tolist() for chunk in batch],
            metadatas=[
                {
                    "document_id": chunk.document_id,
                    "position": chunk.position,
                    "page": chunk.page if chunk.page is not None else -1,
                    "parent_id": chunk.parent_id,
                }
                for chunk in batch
            ],
        )
    if collection.count() != len(chunks):
        raise RuntimeError("Chroma collection count mismatch")
    return client, collection


def query_chroma(collection, vector: np.ndarray, *, limit: int) -> tuple[list[str], list[float]]:
    result = collection.query(query_embeddings=[vector.tolist()], n_results=limit, include=["distances"])
    ids = list(result["ids"][0])
    distances = [float(value) for value in result["distances"][0]]
    if len(ids) != len(set(ids)) or len(ids) != len(distances):
        raise RuntimeError("invalid Chroma query result")
    return ids, distances


def _ragas_id_scores(retrieved_ids: list[str], reference_ids: list[str]) -> dict[str, float]:
    """Execute RAGAS non-LLM ID metrics and return stable scalar values."""
    from ragas import SingleTurnSample

    # RAGAS 0.4.3's public re-export emits an inaccurate collections-module
    # deprecation hint; the ID metrics still live in these version-pinned modules.
    from ragas.metrics._context_precision import IDBasedContextPrecision
    from ragas.metrics._context_recall import IDBasedContextRecall

    sample = SingleTurnSample(
        retrieved_context_ids=retrieved_ids,
        reference_context_ids=reference_ids,
    )

    async def score() -> tuple[float, float]:
        precision = await IDBasedContextPrecision().single_turn_ascore(sample)
        recall = await IDBasedContextRecall().single_turn_ascore(sample)
        return float(precision), float(recall)

    precision, recall = asyncio.run(score())
    expected = id_based_context_scores(retrieved_ids, reference_ids)
    if not np.isclose(precision, expected["context_precision"]) or not np.isclose(
        recall, expected["context_recall"]
    ):
        raise RuntimeError("RAGAS ID metric mismatch")
    return {"context_precision": precision, "context_recall": recall}


def _package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _review_html(report: dict[str, Any]) -> str:
    summary = report["summary"]
    rows = []
    for case in report["cases"]:
        if not case.get("expected_answerable", True):
            status = "negative 진단"
        else:
            status = "검토 필요" if case["needs_human_review"] else "평가 포함"
        rows.append(
            "<tr>"
            + "".join(
                f"<td>{html.escape(str(value))}</td>"
                for value in (
                    case["case_id"],
                    case["question_type"],
                    case["question"],
                    status,
                    case["metrics"].get("hit_at_1"),
                    case["metrics"].get("hit_at_5"),
                    round(case["ragas"].get("context_precision", 0.0), 4),
                    round(case["ragas"].get("context_recall", 0.0), 4),
                )
            )
            + "</tr>"
        )
    metric_cards = "".join(
        f"<div class='card'><b>{html.escape(key)}</b><span>{value:.4f}</span></div>"
        for key, value in summary["overall"].items()
    )
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'>
<title>수혈 ChromaDB + RAGAS baseline</title><style>
body{{font-family:system-ui,sans-serif;margin:32px;color:#17324d}}h1{{margin-bottom:4px}}
.meta{{color:#5b7185}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:24px 0}}
.card{{padding:14px;border:1px solid #cdd9e3;border-radius:10px;background:#f7fafc;display:flex;flex-direction:column;gap:8px}}
.card span{{font-size:1.3rem}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{border:1px solid #d7e0e7;padding:8px;text-align:left}}th{{background:#eaf2f8}}tr:nth-child(even){{background:#f8fafb}}
</style></head><body><h1>수혈 ChromaDB + RAGAS Retrieval Baseline</h1>
<p class='meta'>원문 비저장 · 승인 gold {summary["evaluated_case_count"]}건 · 사람 검토 {summary["human_review_case_count"]}건</p>
<div class='cards'>{metric_cards}</div><table><thead><tr><th>ID</th><th>유형</th><th>질문</th><th>상태</th><th>Hit@1</th><th>Hit@5</th><th>ID Precision</th><th>ID Recall</th></tr></thead><tbody>{"".join(rows)}</tbody></table>
</body></html>"""


def evaluate(*, catalog_path: Path, fixture_path: Path, output_dir: Path) -> dict[str, Any]:
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
    os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    metadata, chunks = load_catalog(catalog_path, document_name=fixture["document"]["name"])
    validate_fixture_document(fixture["document"], metadata, chunks)
    audit = validate_evaluation_cases(
        fixture["cases"], chunks, minimum_cases=80, maximum_cases=100
    )
    cutoffs = tuple(int(value) for value in fixture["cutoffs"])
    if cutoffs != (1, 3, 5, 10):
        raise ValueError("baseline cutoffs must be 1,3,5,10")

    output_dir.mkdir(parents=True, exist_ok=False)
    index_dir = output_dir / "chroma_index"
    started = time.perf_counter()
    _, collection = build_chroma_collection(index_dir, chunks)
    index_build_ms = (time.perf_counter() - started) * 1000
    embedder = Embedder()
    results: list[dict[str, Any]] = []
    for case in fixture["cases"]:
        embedding_started = time.perf_counter()
        query_text = bounded_embedding_question(case["question"], embedder)
        query_vector = embedder.encode([query_text])[0]
        embedding_ms = (time.perf_counter() - embedding_started) * 1000
        query_started = time.perf_counter()
        retrieved_ids, distances = query_chroma(collection, query_vector, limit=max(cutoffs))
        query_ms = (time.perf_counter() - query_started) * 1000
        reference_ids = [item["chunk_id"] for item in case["reference_contexts"]]
        metrics = (
            ranked_retrieval_metrics(retrieved_ids, reference_ids, cutoffs=cutoffs)
            if case["expected_answerable"]
            else {}
        )
        ragas = _ragas_id_scores(retrieved_ids, reference_ids) if reference_ids else {}
        results.append(
            build_safe_case_result(
                case=case,
                retrieved_ids=retrieved_ids,
                distances=distances,
                metrics=metrics,
                ragas=ragas,
                embedding_latency_ms=embedding_ms,
                query_latency_ms=query_ms,
            )
        )

    aggregate_rows = [
        {
            **case,
            "metrics": {
                **case["metrics"],
                **(
                    {
                        "ragas_id_context_precision": case["ragas"]["context_precision"],
                        "ragas_id_context_recall": case["ragas"]["context_recall"],
                    }
                    if case["ragas"]
                    else {}
                ),
            },
        }
        for case in results
    ]
    summary = aggregate_metrics(aggregate_rows)
    latencies = [case["latency_ms"]["total"] for case in results]
    summary["latency_ms"] = {
        "mean": fmean(latencies),
        "minimum": min(latencies),
        "maximum": max(latencies),
        "p50": float(np.percentile(latencies, 50)),
        "p95": float(np.percentile(latencies, 95)),
    }
    summary["index_build_ms"] = index_build_ms
    summary["storage_bytes"] = _directory_size(index_dir)
    summary["catalog_audit"] = audit

    forbidden_texts = {chunk.text for chunk in chunks}
    manifest = {
        "schema_version": fixture["schema_version"],
        "dataset_id": fixture["dataset_id"],
        "document": fixture["document"],
        "case_count": len(fixture["cases"]),
        "approved_case_count": audit["approved_case_count"],
        "human_review_case_count": audit["human_review_case_count"],
        "question_type_counts": {
            kind: sum(case["question_type"] == kind for case in fixture["cases"])
            for kind in sorted({case["question_type"] for case in fixture["cases"]})
        },
        "cutoffs": list(cutoffs),
        "gold_policy": fixture["gold_policy"],
        "reference_fingerprint_algorithm": "sha256",
    }
    report = {"dataset_id": fixture["dataset_id"], "summary": summary, "cases": results}
    environment = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "chromadb": _package_version("chromadb"),
        "ragas": _package_version("ragas"),
        "numpy": _package_version("numpy"),
        "fastembed": _package_version("fastembed"),
        "embedding_model": MODEL,
        "embedding_dimensions": DIMENSIONS,
        "chroma_space": "cosine",
        "generation_api_calls": 0,
        "production_requirements_changed": False,
    }
    ragas_payload = {
        "metric_mode": "ID-based/non-LLM",
        "llm_judge_metrics": "deferred",
        "generation_api_calls": 0,
        "cases": [
            {
                "case_id": case["case_id"],
                "expected_answerable": case["expected_answerable"],
                "needs_human_review": case["needs_human_review"],
                **case["ragas"],
            }
            for case in results
        ],
        "aggregate": {key: value for key, value in summary["overall"].items() if key.startswith("ragas_id_")},
    }
    comparison_schema = build_comparison_schema(cutoffs=cutoffs)
    for payload in (manifest, report, summary, environment, ragas_payload, comparison_schema):
        ensure_raw_text_free(payload, forbidden_exact_texts=forbidden_texts)

    (output_dir / "dataset_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "chroma_results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "chroma_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "ragas_results.json").write_text(
        json.dumps(ragas_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "environment.json").write_text(
        json.dumps(environment, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "comparison_schema.json").write_text(
        json.dumps(comparison_schema, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "review.html").write_text(_review_html(report), encoding="utf-8")

    flat_rows: list[dict[str, Any]] = []
    for case in results:
        flat_rows.append(
            {
                "dataset_id": fixture["dataset_id"],
                "case_id": case["case_id"],
                "question_type": case["question_type"],
                "question": case["question"],
                "needs_human_review": case["needs_human_review"],
                "reference_context_ids": "|".join(case["reference_context_ids"]),
                "retrieved_context_ids": "|".join(case["retrieved_context_ids"]),
                **case["metrics"],
                "ragas_id_context_precision": case["ragas"].get("context_precision", ""),
                "ragas_id_context_recall": case["ragas"].get("context_recall", ""),
                "retrieval_latency_ms": case["latency_ms"]["total"],
            }
        )
    _write_csv(
        output_dir / "chroma_results.csv",
        flat_rows,
        comparison_schema["case_fields"] + comparison_schema["metric_fields"],
    )
    by_type_rows = [
        {"question_type": kind, **metrics} for kind, metrics in summary["by_question_type"].items()
    ]
    by_type_fields = ["question_type", "case_count", *comparison_schema["metric_fields"]]
    _write_csv(output_dir / "metrics_by_question_type.csv", by_type_rows, by_type_fields)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = evaluate(catalog_path=args.catalog, fixture_path=args.fixture, output_dir=args.output_dir)
    print(
        json.dumps(
            {
                "dataset_id": report["dataset_id"],
                "evaluated_case_count": report["summary"]["evaluated_case_count"],
                "human_review_case_count": report["summary"]["human_review_case_count"],
                "generation_api_calls": 0,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
