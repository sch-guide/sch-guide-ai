"""Evaluation-only multilingual-E5 comparison against frozen BM25/MiniLM baselines."""

from __future__ import annotations

import argparse
import csv
import html
import json
import time
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable, Sequence

import numpy as np

from src.library import clean, embedding_question
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
    load_catalog,
    validate_evaluation_cases,
    validate_fixture_document,
)
from tools.retrieval_strategy_evaluate import _evaluated_result

ROOT = Path(__file__).resolve().parents[1]
MODEL_NAME = "intfloat/multilingual-e5-large"
MODEL_DIMENSIONS = 1024
QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "
DEFAULT_CATALOG = ROOT / "data" / "library" / "catalog.sqlite3"
DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "transfusion_retrieval_baseline.json"
DEFAULT_BASELINE = ROOT / "workspace" / "과거작업" / "평가산출물" / "2026-09-17_bm25-vector-fairness-validation"
DEFAULT_CACHE = ROOT / "data" / "models"
DEFAULT_OUTPUT = ROOT / "workspace" / "과거작업" / "평가산출물" / "2026-09-17_multilingual-e5-large-fairness-validation"
BASELINE_CONFIGS = (
    "bm25_minimal_raw",
    "bm25_current_baseline",
    "vector_current_minilm",
)
ALTERNATE_CONFIG = "vector_multilingual_e5_large"


def _prefixed(text: str, prefix: str) -> str:
    value = clean(text)
    if not value:
        raise ValueError("embedding text must not be empty")
    if value.casefold().startswith(prefix.casefold()):
        return prefix + value[len(prefix) :].lstrip()
    return prefix + value


def e5_query_text(text: str) -> str:
    """Apply the multilingual-E5 query prefix exactly once."""
    return _prefixed(text, QUERY_PREFIX)


def e5_passage_text(text: str) -> str:
    """Apply the multilingual-E5 passage prefix exactly once."""
    return _prefixed(text, PASSAGE_PREFIX)


def _core_score(metrics: dict[str, float]) -> float:
    return fmean(float(metrics[name]) for name in CORE_METRICS)


def classify_alternate_verdict(
    minimal_bm25: dict[str, float],
    current_bm25: dict[str, float],
    alternate_vector: dict[str, float],
) -> dict[str, Any]:
    """Apply the frozen A/B/C alternate-model decision contract."""
    minimal_score = _core_score(minimal_bm25)
    current_score = _core_score(current_bm25)
    alternate_score = _core_score(alternate_vector)
    minimal_wins = sum(
        minimal_bm25[key] > alternate_vector[key] for key in CORE_METRICS
    )
    alternate_wins = sum(
        alternate_vector[key] > current_bm25[key] for key in CORE_METRICS
    )
    if alternate_score - current_score >= 0.05 and alternate_wins >= 3:
        code = "C"
    elif minimal_score - alternate_score >= 0.05 and minimal_wins >= 3:
        code = "A"
    else:
        code = "B"
    labels = {
        "A": "multilingual-e5-large도 minimal/current BM25보다 명확히 열세",
        "B": "multilingual-e5-large가 BM25에 근접하여 selective fallback 재검토 가치 있음",
        "C": "multilingual-e5-large가 current BM25를 능가하여 vector 전략 재평가 필요",
    }
    return {
        "code": code,
        "label": labels[code],
        "core_scores": {
            "bm25_minimal": minimal_score,
            "bm25_current": current_score,
            "multilingual_e5_large": alternate_score,
        },
        "minimal_minus_alternate": minimal_score - alternate_score,
        "current_minus_alternate": current_score - alternate_score,
        "alternate_minus_current": alternate_score - current_score,
        "production_changed": False,
    }


def _load_csv_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    parsed = []
    for row in rows:
        parsed.append(
            {
                key: (
                    value
                    if key in {"config", "group"}
                    else int(value)
                    if key == "case_count" and value
                    else float(value)
                    if value
                    else 0.0
                )
                for key, value in row.items()
            }
        )
    return parsed


def _baseline_rows(baseline_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    metric_rows = _load_csv_rows(baseline_dir / "metrics.csv")
    selected = [row for row in metric_rows if row["config"] in BASELINE_CONFIGS]
    if [row["config"] for row in selected] != list(BASELINE_CONFIGS):
        raise ValueError("frozen baseline config drift")
    type_rows = [
        row
        for row in _load_csv_rows(baseline_dir / "metrics_by_type.csv")
        if row["config"] in BASELINE_CONFIGS
        and str(row["group"]).startswith("question_type:")
    ]
    if not type_rows:
        raise ValueError("frozen baseline type metrics missing")
    return selected, type_rows


def _directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _load_local_engine(cache_dir: Path):
    from fastembed import TextEmbedding

    return TextEmbedding(
        model_name=MODEL_NAME,
        cache_dir=str(cache_dir),
        threads=2,
        providers=["CPUExecutionProvider"],
        local_files_only=True,
    )


def _embed(engine: Any, texts: Iterable[str], *, batch_size: int) -> np.ndarray:
    vectors = np.asarray(list(engine.embed(texts, batch_size=batch_size)), dtype=np.float32)
    if vectors.ndim != 2 or not vectors.size or not np.isfinite(vectors).all():
        raise ValueError("embedding output must be a finite matrix")
    if np.any(np.linalg.norm(vectors, axis=1) == 0):
        raise ValueError("embedding output contains a zero vector")
    return vectors


def _safe_results(results: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = (
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
    return [{key: row[key] for key in fields} for row in results]


def _review_html(report: dict[str, Any], rows: Sequence[dict[str, Any]]) -> str:
    body = []
    for row in rows:
        body.append(
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
                    f"{row['ragas_id_context_precision']:.4f}",
                    f"{row['ragas_id_context_recall']:.4f}",
                    f"{row['mean_latency_ms']:.3f}",
                    f"{row['p95_latency_ms']:.3f}",
                )
            )
            + "</tr>"
        )
    verdict = report["verdict"]
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'>
<title>Multilingual E5 Large Fairness Evaluation</title><style>
body{{font-family:system-ui,sans-serif;margin:32px;color:#17324d;background:#f7fafc}}
header,section{{background:#fff;border:1px solid #d7e1e8;border-radius:12px;padding:18px;margin:12px 0}}
table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{border:1px solid #d7e1e8;padding:7px}}th{{background:#eaf3f8}}
.verdict{{font-size:1.3rem;font-weight:700}}</style></head><body>
<header><h1>Multilingual E5 Large Fairness Evaluation</h1>
<p>수혈 105 chunks · 90문항 · 승인 positive 78 · 외부 API 0회</p>
<p class='verdict'>판정 {html.escape(verdict['code'])}: {html.escape(verdict['label'])}</p></header>
<section><h2>동일 조건 비교</h2><table><thead><tr><th>Config</th><th>Hit@1</th><th>Hit@3</th><th>Hit@5</th><th>Hit@10</th><th>MRR</th><th>Recall@10</th><th>Precision@10</th><th>ID Precision</th><th>ID Recall</th><th>Mean ms</th><th>P95 ms</th></tr></thead>
<tbody>{''.join(body)}</tbody></table></section></body></html>"""


def evaluate_multilingual_e5(
    *,
    catalog_path: Path,
    fixture_path: Path,
    baseline_dir: Path,
    output_dir: Path,
    engine: Any | None = None,
    expected_dimensions: int = MODEL_DIMENSIONS,
    model_cache_size_bytes: int | None = None,
    cache_dir: Path = DEFAULT_CACHE,
) -> dict[str, Any]:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    metadata, chunks = load_catalog(
        catalog_path, document_name=fixture["document"]["name"]
    )
    validate_fixture_document(fixture["document"], metadata, chunks)
    case_audit = validate_evaluation_cases(
        fixture["cases"], chunks, minimum_cases=80, maximum_cases=100
    )
    if fixture.get("dataset_version") != "transfusion-retrieval-v3":
        raise ValueError("dataset version drift")
    if tuple(fixture["cutoffs"]) != (1, 3, 5, 10) or fixture["document"]["chunk_version"] != 4:
        raise ValueError("retrieval contract drift")
    baseline_rows, baseline_type_rows = _baseline_rows(baseline_dir)

    engine = engine or _load_local_engine(cache_dir)
    passage_inputs = [e5_passage_text(chunk.text) for chunk in chunks]
    build_started = time.perf_counter()
    passage_vectors = _embed(engine, passage_inputs, batch_size=4)
    index_build_ms = (time.perf_counter() - build_started) * 1000
    if passage_vectors.shape != (len(chunks), expected_dimensions):
        raise ValueError("alternate embedding dimension/count drift")

    identifiers = tuple(chunk.chunk_id for chunk in chunks)
    results = []
    for case in fixture["cases"]:
        started = time.perf_counter()
        query_input = e5_query_text(embedding_question(case["question"]))
        query_vector = _embed(engine, [query_input], batch_size=1)[0]
        retrieved_ids, scores = exact_vector_ranking(
            query_vector, passage_vectors, identifiers, limit=10
        )
        latency_ms = (time.perf_counter() - started) * 1000
        results.append(
            _evaluated_result(
                case=case,
                retrieved_ids=retrieved_ids,
                scores=scores,
                latency_ms=latency_ms,
            )
        )

    summary = _metric_summary(results)
    summary["index_build_ms"] = index_build_ms
    e5_row = _summary_rows({ALTERNATE_CONFIG: summary})[0]
    comparison_rows = [*baseline_rows, e5_row]
    type_rows = [
        *baseline_type_rows,
        *[
            row
            for row in _group_metrics(
                fixture["cases"], {ALTERNATE_CONFIG: results}
            )
            if str(row["group"]).startswith("question_type:")
        ],
    ]
    baseline_by_name = {row["config"]: row for row in baseline_rows}
    verdict = classify_alternate_verdict(
        baseline_by_name["bm25_minimal_raw"],
        baseline_by_name["bm25_current_baseline"],
        summary["overall"],
    )

    cache_size = (
        model_cache_size_bytes
        if model_cache_size_bytes is not None
        else _directory_size(cache_dir / "models--qdrant--multilingual-e5-large-onnx")
    )
    norms = np.linalg.norm(passage_vectors, axis=1)
    model_audit = {
        "model": MODEL_NAME,
        "dimensions": expected_dimensions,
        "query_prefix": QUERY_PREFIX,
        "passage_prefix": PASSAGE_PREFIX,
        "input_contract": "same current-MiniLM query content; body-only passages",
        "maximum_model_tokens": 512,
        "chunk_vector_count": len(passage_vectors),
        "chunk_ids_unique": len(set(identifiers)) == len(identifiers),
        "vectors_finite": bool(np.isfinite(passage_vectors).all()),
        "norm_min": float(norms.min()),
        "norm_max": float(norms.max()),
        "index_build_ms": index_build_ms,
        "model_cache_size_bytes": cache_size,
        "local_inference": True,
        "external_embedding_api_calls": 0,
    }
    dataset = {
        "dataset_version": fixture["dataset_version"],
        "chunk_version": metadata["chunk_version"],
        "chunk_count": len(chunks),
        "case_count": len(fixture["cases"]),
        "evaluated_positive_count": case_audit["approved_case_count"],
        "human_review_count": case_audit["human_review_case_count"],
        "negative_count": case_audit["negative_case_count"],
    }
    safety = {
        "production_retrieval_changed": False,
        "production_embedding_changed": False,
        "generation_api_calls": 0,
        "external_embedding_api_calls": 0,
        "hospital_data_external_transfers": 0,
        "source_text_persisted": False,
        "question_text_persisted": False,
    }
    safe_results = _safe_results(results)
    report = {
        "dataset": dataset,
        "model_audit": model_audit,
        "summaries": {ALTERNATE_CONFIG: summary},
        "comparison_rows": comparison_rows,
        "type_rows": type_rows,
        "verdict": verdict,
        "safety": safety,
    }

    forbidden_texts = {chunk.text for chunk in chunks}
    persisted_payloads = (
        model_audit,
        comparison_rows,
        type_rows,
        safe_results,
        {"dataset": dataset, "summary": summary, "verdict": verdict, "safety": safety},
    )
    for payload in persisted_payloads:
        ensure_fairness_artifact_safe(payload, forbidden_texts)

    output_dir.mkdir(parents=True, exist_ok=False)
    json_payloads = {
        "model_audit.json": model_audit,
        "e5_results.json": safe_results,
        "e5_summary.json": {
            "dataset": dataset,
            "summary": summary,
            "verdict": verdict,
            "safety": safety,
        },
        "test_results.json": {
            "status": "pending final verification",
            "generation_api_calls": 0,
            "external_embedding_api_calls": 0,
            "hospital_data_external_transfers": 0,
        },
    }
    for name, payload in json_payloads.items():
        (output_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    _write_csv(output_dir / "comparison_metrics.csv", comparison_rows)
    _write_csv(output_dir / "metrics_by_question_type.csv", type_rows)
    (output_dir / "review.html").write_text(
        _review_html(report, comparison_rows), encoding="utf-8"
    )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--baseline-dir", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = evaluate_multilingual_e5(
        catalog_path=args.catalog,
        fixture_path=args.fixture,
        baseline_dir=args.baseline_dir,
        output_dir=args.output_dir,
        cache_dir=args.cache_dir,
    )
    print(
        json.dumps(
            {
                "case_count": report["dataset"]["case_count"],
                "chunk_count": report["dataset"]["chunk_count"],
                "verdict": report["verdict"]["code"],
                "generation_api_calls": 0,
                "external_embedding_api_calls": 0,
                "hospital_data_external_transfers": 0,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
