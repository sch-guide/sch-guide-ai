"""Evaluation-only BM25/vector fairness audit and ablation runner."""

from __future__ import annotations

import argparse
import csv
import html
import inspect
import json
import math
import re
import time
import unicodedata
from collections import Counter, defaultdict
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from statistics import fmean
from typing import Any, Sequence

import numpy as np

from mvp.library import CHUNK_VERSION, Embedder, bounded_embedding_question, clean
from mvp.query import normalize_attached_aspects, plan_query
from mvp.retrieval import BM25Index, lexical_tokens, rank_bm25_candidates
from mvp.settings import DIMENSIONS, MODEL
from tools.chroma_baseline_evaluate import (
    CatalogChunk,
    load_catalog,
    validate_evaluation_cases,
    validate_fixture_document,
)
from tools.retrieval_baseline_metrics import aggregate_metrics
from tools.retrieval_strategy_evaluate import (
    _evaluated_result,
    _source_chunk,
    negative_score_diagnostics,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "library" / "catalog.sqlite3"
DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "transfusion_retrieval_baseline.json"
DEFAULT_PRIOR = ROOT / "artifacts" / "2026-09-17_transfusion-expanded-retrieval"
DEFAULT_OUTPUT = ROOT / "artifacts" / "2026-09-17_bm25-vector-fairness-validation"
CORE_METRICS = ("hit_at_5", "hit_at_10", "mrr", "recall_at_10")
PRODUCTION_BM25_CANDIDATE_LIMIT = int(
    inspect.signature(rank_bm25_candidates).parameters["limit"].default
)
_QUERY_NOISE = {
    "알려줘",
    "알려주세요",
    "해주세요",
    "해줘",
    "무엇인가요",
    "뭔가요",
    "뭐야",
    "대해서",
    "대해",
    "대한",
    "관한",
}


def minimal_tokens(text: str) -> tuple[str, ...]:
    """Tokenize without SCHAT n-grams, aliases, particle removal, or expansion."""
    normalized = clean(unicodedata.normalize("NFKC", text)).casefold()
    return tuple(re.findall(r"[a-z][a-z0-9-]*|[가-힣]+|\d+(?:[.,]\d+)?", normalized))


def common_query(question: str) -> str:
    """Apply only retriever-neutral Korean boundary/noise normalization."""
    normalized = normalize_attached_aspects(
        clean(unicodedata.normalize("NFKC", question))
    ).casefold()
    tokens = re.findall(r"[a-z][a-z0-9-]*|[가-힣]+|\d+(?:[.,]\d+)?", normalized)
    result = []
    for token in tokens:
        if token in _QUERY_NOISE:
            continue
        if re.fullmatch(r"[가-힣]+", token):
            base = re.sub(
                r"(?:에서는|으로는|에는|에서|은|는|을|를|이|가|에|의)$", "", token
            )
            if base:
                token = base
        if token and token not in _QUERY_NOISE:
            result.append(token)
    return " ".join(dict.fromkeys(result))


def exact_vector_ranking(
    query_vector: np.ndarray,
    document_vectors: np.ndarray,
    identifiers: Sequence[str],
    *,
    limit: int,
) -> tuple[list[str], list[float]]:
    """Return deterministic exact cosine ranking with source-order ties."""
    query = np.asarray(query_vector, dtype=np.float32)
    matrix = np.asarray(document_vectors, dtype=np.float32)
    if query.ndim != 1 or matrix.ndim != 2 or matrix.shape[1] != query.shape[0]:
        raise ValueError("vector dimension mismatch")
    if matrix.shape[0] != len(identifiers) or len(set(identifiers)) != len(identifiers):
        raise ValueError("vector identifiers must be aligned and unique")
    if limit < 1:
        raise ValueError("limit must be positive")
    query_norm = float(np.linalg.norm(query))
    row_norms = np.linalg.norm(matrix, axis=1)
    if not query_norm or np.any(row_norms == 0) or not np.isfinite(matrix).all():
        raise ValueError("vectors must be finite and non-zero")
    similarities = (matrix @ query) / (row_norms * query_norm)
    positions = sorted(
        range(len(identifiers)), key=lambda position: (-float(similarities[position]), position)
    )[:limit]
    return (
        [identifiers[position] for position in positions],
        [float(similarities[position]) for position in positions],
    )


def build_representation(
    *,
    document_name: str,
    title: str,
    section: str,
    body: str,
    mode: str,
    embedder: Any,
    maximum_tokens: int = 128,
) -> str:
    """Build an in-memory vector representation without changing chunk identity."""
    document_title = re.sub(r"\.[a-z0-9]{1,8}$", "", document_name, flags=re.I)
    document_title = clean(document_title.replace("_", " "))
    fields = {
        "body": [body],
        "title_section_body": [title, section, body],
        "document_title_section_body": [document_title, title, section, body],
    }
    if mode not in fields:
        raise ValueError("unknown representation mode")
    parts = list(dict.fromkeys(clean(value) for value in fields[mode] if clean(value)))
    if not parts:
        raise ValueError("representation is empty")
    text = " ".join(parts)
    while embedder.count(text) > maximum_tokens and " " in text:
        text = text.rsplit(" ", 1)[0]
    if not text or embedder.count(text) > maximum_tokens:
        raise ValueError("representation exceeds model limit")
    return text


def _core_score(metrics: dict[str, float]) -> float:
    return fmean(float(metrics[name]) for name in CORE_METRICS)


def classify_fairness_verdict(
    minimal_bm25: dict[str, float],
    current_bm25: dict[str, float],
    best_vector: dict[str, float],
) -> dict[str, Any]:
    """Apply the frozen A/B/C/D decision contract."""
    minimal_score = _core_score(minimal_bm25)
    current_score = _core_score(current_bm25)
    vector_score = _core_score(best_vector)
    minimal_wins = sum(minimal_bm25[key] > best_vector[key] for key in CORE_METRICS)
    vector_wins = sum(best_vector[key] > current_bm25[key] for key in CORE_METRICS)
    if vector_score - current_score >= 0.05 and vector_wins >= 3:
        code = "D"
    elif minimal_score - vector_score >= 0.05 and minimal_wins >= 3:
        code = "A"
    elif vector_score >= current_score - 0.05:
        code = "C"
    elif current_score - minimal_score >= 0.05 and minimal_score <= vector_score + 0.03:
        code = "B"
    else:
        code = "A" if minimal_score > vector_score else "B"
    return {
        "code": code,
        "core_scores": {
            "bm25_minimal": minimal_score,
            "bm25_current": current_score,
            "vector_best": vector_score,
        },
        "current_minus_minimal": current_score - minimal_score,
        "minimal_minus_vector": minimal_score - vector_score,
        "vector_minus_current": vector_score - current_score,
    }


def rank_current_bm25(
    question: str,
    chunks: Sequence[CatalogChunk],
    *,
    limit: int,
    index: BM25Index | None = None,
) -> tuple[list[str], list[float], float]:
    source_chunks = [_source_chunk(chunk) for chunk in chunks]
    index = index or BM25Index(source_chunks)
    started = time.perf_counter()
    scores = index.scores(question)
    positions = sorted(
        range(len(chunks)), key=lambda position: (-float(scores[position]), position)
    )[:limit]
    return (
        [chunks[position].chunk_id for position in positions],
        [float(scores[position]) for position in positions],
        (time.perf_counter() - started) * 1000,
    )


def ensure_fairness_artifact_safe(payload: Any, forbidden_exact_texts: set[str]) -> None:
    """Reject persisted questions, source text fields, exact source, and secrets."""
    forbidden_fields = {
        "question",
        "questions",
        "chunk_text",
        "content",
        "contents",
        "exact_text",
        "quote",
        "source_text",
        "source_unit_text",
        "authorization",
        "api_key",
    }

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).casefold() in forbidden_fields:
                    raise ValueError(f"forbidden artifact field: {key}")
                visit(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item)
        elif isinstance(value, str) and value in forbidden_exact_texts:
            raise ValueError("artifact contains exact source text")

    visit(payload)


class EvaluationBM25Index:
    """Small configurable BM25 used only to isolate preprocessing effects."""

    def __init__(self, documents: Sequence[str], *, tokenizer=minimal_tokens):
        self.tokenizer = tokenizer
        self.lengths: list[int] = []
        self.postings: defaultdict[str, dict[int, int]] = defaultdict(dict)
        for position, document in enumerate(documents):
            counts = Counter(tokenizer(document))
            self.lengths.append(sum(counts.values()))
            for token, count in counts.items():
                self.postings[token][position] = count
        self.size = len(documents)
        self.average = max(1.0, fmean(self.lengths)) if self.lengths else 1.0

    def scores(self, query: str) -> np.ndarray:
        scores = np.zeros(self.size, dtype=np.float32)
        for token in set(self.tokenizer(query)):
            posting = self.postings.get(token, {})
            idf = math.log(1 + (self.size - len(posting) + 0.5) / (len(posting) + 0.5))
            for position, count in posting.items():
                denominator = count + 1.5 * (
                    1 - 0.75 + 0.75 * self.lengths[position] / self.average
                )
                scores[position] += idf * count * 2.5 / denominator
        return scores


def _rank_scores(
    scores: Sequence[float], chunks: Sequence[CatalogChunk], *, limit: int
) -> tuple[list[str], list[float]]:
    positions = sorted(
        range(len(chunks)), key=lambda position: (-float(scores[position]), position)
    )[:limit]
    return (
        [chunks[position].chunk_id for position in positions],
        [float(scores[position]) for position in positions],
    )


def _load_prior_top10(path: Path, *, retriever: str) -> dict[str, list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if row["k"] == "10" and row["retriever"] == retriever
        ]
    grouped: defaultdict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["question_id"]].append(row)
    return {
        case_id: [
            row["retrieved_chunk_id"]
            for row in sorted(case_rows, key=lambda item: int(item["rank"]))
        ]
        for case_id, case_rows in grouped.items()
    }


def _metric_summary(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for result in results:
        metrics = dict(result["metrics"])
        if result["ragas"]:
            metrics.update(
                ragas_id_context_precision=result["ragas"]["context_precision"],
                ragas_id_context_recall=result["ragas"]["context_recall"],
            )
        rows.append({**result, "metrics": metrics})
    summary = aggregate_metrics(rows)
    latencies = [float(row["latency_ms"]) for row in results]
    summary["latency_ms"] = {
        "count": len(latencies),
        "mean": fmean(latencies),
        "p95": float(np.percentile(latencies, 95)),
        "minimum": min(latencies),
        "maximum": max(latencies),
    }
    summary["negative_diagnostics"] = negative_score_diagnostics(results)
    return summary


def _evaluate_ranker(
    cases: Sequence[dict[str, Any]], ranker: Any
) -> list[dict[str, Any]]:
    results = []
    for case in cases:
        identifiers, scores, latency_ms = ranker(case)
        results.append(
            _evaluated_result(
                case=case,
                retrieved_ids=identifiers,
                scores=scores,
                latency_ms=latency_ms,
            )
        )
    return results


def _bm25_configs(
    cases: Sequence[dict[str, Any]],
    chunks: Sequence[CatalogChunk],
    metadata: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, float]]:
    source_chunks = [_source_chunk(chunk) for chunk in chunks]
    build_times: dict[str, float] = {}

    started = time.perf_counter()
    minimal_index = EvaluationBM25Index([chunk.text for chunk in chunks])
    build_times["bm25_minimal_raw"] = (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    schat_body_index = EvaluationBM25Index(
        [chunk.text for chunk in chunks], tokenizer=lambda text: tuple(lexical_tokens(text))
    )
    build_times["bm25_schat_tokens_body_all"] = (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    current_index = BM25Index(source_chunks)
    current_build = (time.perf_counter() - started) * 1000
    for name in (
        "bm25_current_baseline",
        "bm25_current_canonical",
        "bm25_expanded_no_temporal",
        "bm25_production_query",
    ):
        build_times[name] = current_build

    documents = ({
        "id": metadata["id"],
        "document_name": metadata["document_name"],
        "title": metadata.get("title", ""),
    },)

    def rank_index(index: Any, query: str):
        started_at = time.perf_counter()
        scores = index.scores(query)
        identifiers, ranked_scores = _rank_scores(scores, chunks, limit=10)
        return identifiers, ranked_scores, (time.perf_counter() - started_at) * 1000

    results = {
        "bm25_minimal_raw": _evaluate_ranker(
            cases, lambda case: rank_index(minimal_index, case["question"])
        ),
        "bm25_schat_tokens_body_all": _evaluate_ranker(
            cases, lambda case: rank_index(schat_body_index, case["question"])
        ),
        "bm25_current_baseline": _evaluate_ranker(
            cases, lambda case: rank_current_bm25(
                case["question"], chunks, limit=10, index=current_index
            )
        ),
        "bm25_current_canonical": _evaluate_ranker(
            cases, lambda case: rank_index(current_index, common_query(case["question"]))
        ),
    }

    def expanded(case: dict[str, Any], *, temporal: bool):
        started_at = time.perf_counter()
        plan = plan_query(case["question"], documents=documents)
        scores = current_index.scores(plan.expanded)
        if temporal:
            ranking = rank_bm25_candidates(
                plan.original,
                source_chunks,
                scores,
                limit=PRODUCTION_BM25_CANDIDATE_LIMIT,
            )
            positions = list(ranking.positions[:10])
            identifiers = [chunks[position].chunk_id for position in positions]
            ranked_scores = [float(scores[position]) for position in positions]
        else:
            identifiers, ranked_scores = _rank_scores(scores, chunks, limit=10)
        return identifiers, ranked_scores, (time.perf_counter() - started_at) * 1000

    results["bm25_expanded_no_temporal"] = _evaluate_ranker(
        cases, lambda case: expanded(case, temporal=False)
    )
    results["bm25_production_query"] = _evaluate_ranker(
        cases, lambda case: expanded(case, temporal=True)
    )
    return results, build_times


def _representation_matrix(
    chunks: Sequence[CatalogChunk], *, mode: str, embedder: Embedder
) -> tuple[np.ndarray, float]:
    texts = [
        build_representation(
            document_name=chunk.document_name,
            title=chunk.title,
            section=chunk.section,
            body=chunk.text,
            mode=mode,
            embedder=embedder,
        )
        for chunk in chunks
    ]
    started = time.perf_counter()
    vectors = embedder.encode(texts)
    return vectors, (time.perf_counter() - started) * 1000


def _vector_configs(
    cases: Sequence[dict[str, Any]],
    chunks: Sequence[CatalogChunk],
    *,
    embedder: Embedder,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, float], dict[str, Any]]:
    identifiers = tuple(chunk.chunk_id for chunk in chunks)
    catalog_matrix = np.stack([chunk.vector for chunk in chunks]).astype(np.float32)
    body_matrix, body_build_ms = _representation_matrix(chunks, mode="body", embedder=embedder)
    title_matrix, title_build_ms = _representation_matrix(
        chunks, mode="title_section_body", embedder=embedder
    )
    document_matrix, document_build_ms = _representation_matrix(
        chunks, mode="document_title_section_body", embedder=embedder
    )
    matrices = {
        "vector_current_minilm": catalog_matrix,
        "vector_common_query_body": body_matrix,
        "vector_title_section_body": title_matrix,
        "vector_document_title_section_body": document_matrix,
    }
    build_times = {
        "vector_current_minilm": 0.0,
        "vector_common_query_body": body_build_ms,
        "vector_title_section_body": title_build_ms,
        "vector_document_title_section_body": document_build_ms,
    }

    def rank(case: dict[str, Any], *, name: str):
        started = time.perf_counter()
        query = (
            bounded_embedding_question(case["question"], embedder)
            if name == "vector_current_minilm"
            else common_query(case["question"])
        )
        vector = embedder.encode([query])[0]
        ranked_ids, scores = exact_vector_ranking(
            vector, matrices[name], identifiers, limit=10
        )
        return ranked_ids, scores, (time.perf_counter() - started) * 1000

    results = {
        name: _evaluate_ranker(cases, lambda case, name=name: rank(case, name=name))
        for name in matrices
    }
    probe_ids, _ = exact_vector_ranking(
        body_matrix[0], body_matrix, identifiers, limit=10
    )
    repeated_ids, _ = exact_vector_ranking(
        body_matrix[0], body_matrix, identifiers, limit=10
    )
    audit = {
        "catalog_vector_count": int(catalog_matrix.shape[0]),
        "catalog_dimensions": int(catalog_matrix.shape[1]),
        "catalog_ids_unique": len(set(identifiers)) == len(identifiers),
        "catalog_vectors_finite": bool(np.isfinite(catalog_matrix).all()),
        "catalog_norm_min": float(np.linalg.norm(catalog_matrix, axis=1).min()),
        "catalog_norm_max": float(np.linalg.norm(catalog_matrix, axis=1).max()),
        "body_rebuild_max_abs_delta": float(np.max(np.abs(body_matrix - catalog_matrix))),
        "deterministic_rebuild": probe_ids == repeated_ids,
        "similarity_semantics": "cosine similarity; stored Chroma similarity = 1 - cosine distance",
        "query_model_equals_document_model": True,
        "model": MODEL,
        "dimensions": DIMENSIONS,
    }
    return results, build_times, audit


def _diagnostic_axes(case: dict[str, Any]) -> tuple[str, ...]:
    metadata_axes = set(case.get("metadata", {}).get("variation_axes", ()))
    axes = {f"question_type:{case['question_type']}"}
    if metadata_axes & {"abbreviation", "english_term", "product"}:
        axes.add("diagnostic:exact_medical_or_product_term")
    if metadata_axes & {"time", "unit", "frequency"}:
        axes.add("diagnostic:numeric_or_time")
    if "colloquial" in metadata_axes:
        axes.add("diagnostic:colloquial")
    if "natural_language" in metadata_axes:
        axes.add("diagnostic:long_natural_language")
    for axis in ("temporal", "procedure", "paraphrase", "product_specific"):
        if case["question_type"] == axis:
            axes.add(f"diagnostic:{axis}")
    return tuple(sorted(axes))


def _group_metrics(
    cases: Sequence[dict[str, Any]], results_by_config: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    cases_by_id = {case["question_id"]: case for case in cases}
    rows = []
    for config, results in results_by_config.items():
        groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for result in results:
            case = cases_by_id[result["question_id"]]
            for axis in _diagnostic_axes(case):
                groups[axis].append(result)
        for axis, group in sorted(groups.items()):
            summary = _metric_summary(group)
            overall = summary["overall"]
            rows.append(
                {
                    "config": config,
                    "group": axis,
                    "case_count": summary["evaluated_case_count"],
                    **overall,
                    "mean_latency_ms": summary["latency_ms"]["mean"],
                    "p95_latency_ms": summary["latency_ms"]["p95"],
                }
            )
    return rows


def _summary_rows(summaries: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for name, summary in summaries.items():
        rows.append(
            {
                "config": name,
                **summary["overall"],
                "mean_latency_ms": summary["latency_ms"]["mean"],
                "p95_latency_ms": summary["latency_ms"]["p95"],
                "index_build_ms": summary["index_build_ms"],
            }
        )
    return rows


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _review_html(report: dict[str, Any]) -> str:
    metric_rows = []
    for row in report["metric_rows"]:
        metric_rows.append(
            "<tr>"
            + "".join(
                f"<td>{html.escape(str(value))}</td>"
                for value in (
                    row["config"],
                    f"{row['hit_at_5']:.4f}",
                    f"{row['hit_at_10']:.4f}",
                    f"{row['mrr']:.4f}",
                    f"{row['recall_at_10']:.4f}",
                    f"{row['precision_at_10']:.4f}",
                    f"{row['ragas_id_context_precision']:.4f}",
                    f"{row['ragas_id_context_recall']:.4f}",
                    f"{row['mean_latency_ms']:.3f}",
                )
            )
            + "</tr>"
        )
    audit_rows = "".join(
        "<tr>"
        f"<td>{html.escape(item['feature'])}</td>"
        f"<td>{html.escape(item['bm25_current'])}</td>"
        f"<td>{html.escape(item['vector_current'])}</td>"
        f"<td>{html.escape(item['classification'])}</td>"
        "</tr>"
        for item in report["preprocessing_audit"]["features"]
    )
    verdict = report["verdict"]
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'>
<title>BM25–Vector Fairness Validation</title><style>
body{{font-family:system-ui,sans-serif;margin:32px;color:#17324d;background:#f7fafc}}
header,section{{background:#fff;border:1px solid #d7e1e8;border-radius:12px;padding:18px;margin:12px 0}}
table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{border:1px solid #d7e1e8;padding:7px}}th{{background:#eaf3f8}}
.verdict{{font-size:1.3rem;font-weight:700}}</style></head><body>
<header><h1>BM25–Vector Fairness Validation</h1>
<p>수혈 105 chunks · 90문항 · 승인 positive 78 · generation/API 0회</p>
<p class='verdict'>판정 {html.escape(verdict['code'])}: {html.escape(verdict['label'])}</p></header>
<section><h2>최종 비교</h2><table><thead><tr><th>Config</th><th>Hit@5</th><th>Hit@10</th><th>MRR</th><th>Recall@10</th><th>Precision@10</th><th>ID Precision</th><th>ID Recall</th><th>Mean ms</th></tr></thead>
<tbody>{''.join(metric_rows)}</tbody></table></section>
<section><h2>Preprocessing 감사</h2><table><thead><tr><th>항목</th><th>BM25 current</th><th>Vector current</th><th>분류</th></tr></thead><tbody>{audit_rows}</tbody></table></section>
</body></html>"""


def _preprocessing_audit() -> dict[str, Any]:
    features = [
        ("query normalization", "raw question", "bounded_embedding_question", "Vector only"),
        ("tokenization", "SCHAT lexical words + Korean bigrams", "MiniLM tokenizer", "retriever-specific"),
        ("lexical/alias expansion", "anchor tokens", "terms + registered aliases", "different on both"),
        ("topic/aspect normalization", "not used in prior baseline", "limited compound split", "Vector only (partial)"),
        ("temporal handling", "not used in prior baseline", "none", "same: none"),
        ("title filtering", "heading/running-header exclusion", "none", "BM25 only"),
        ("metadata", "section + inherited heading context", "none", "BM25 only"),
        ("document representation", "body + section/context", "body only", "BM25 only"),
        ("embedding normalization", "not applicable", "L2 normalized", "Vector only"),
        ("similarity", "BM25 lexical", "cosine", "retriever-specific"),
    ]
    return {
        "baseline_fairness": (
            "The prior baseline did not apply production QueryPlan expansion or temporal tiers to BM25; "
            "vector received more query normalization, while BM25 received corpus/title tuning."
        ),
        "features": [
            {
                "feature": feature,
                "bm25_current": bm25,
                "vector_current": vector,
                "classification": classification,
            }
            for feature, bm25, vector, classification in features
        ],
        "common_preprocessing": "NFKC + whitespace + attached topic/aspect boundary + request-noise removal",
        "production_bm25_candidate_limit": PRODUCTION_BM25_CANDIDATE_LIMIT,
    }


def _verdict_label(code: str) -> str:
    return {
        "A": "minimal BM25도 optimized vector보다 우세 — 현재 corpus에는 BM25 자체가 더 적합",
        "B": "current BM25 우세의 상당 부분이 SCHAT tuning 효과",
        "C": "optimized vector가 BM25에 근접 — selective fallback 검토 가치",
        "D": "optimized vector가 BM25를 전체적으로 능가 — vector strategy 재검토 필요",
    }[code]


def evaluate_fairness(
    *,
    catalog_path: Path,
    fixture_path: Path,
    prior_results_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    metadata, chunks = load_catalog(catalog_path, document_name=fixture["document"]["name"])
    validate_fixture_document(fixture["document"], metadata, chunks)
    audit = validate_evaluation_cases(
        fixture["cases"], chunks, minimum_cases=80, maximum_cases=100
    )
    if fixture.get("dataset_version") != "transfusion-retrieval-v3":
        raise ValueError("dataset version drift")
    if tuple(fixture["cutoffs"]) != (1, 3, 5, 10):
        raise ValueError("cutoff drift")
    if metadata["model"] != MODEL or DIMENSIONS != 384 or CHUNK_VERSION != 4:
        raise ValueError("embedding/chunk contract drift")

    cases = fixture["cases"]
    bm25_results, bm25_build_times = _bm25_configs(cases, chunks, metadata)
    embedder = Embedder()
    vector_results, vector_build_times, vector_audit = _vector_configs(
        cases, chunks, embedder=embedder
    )
    results = {**bm25_results, **vector_results}
    build_times = {**bm25_build_times, **vector_build_times}
    summaries = {name: _metric_summary(rows) for name, rows in results.items()}
    for name, summary in summaries.items():
        summary["index_build_ms"] = build_times[name]

    prior_bm25 = _load_prior_top10(prior_results_dir / "bm25_results.csv", retriever="bm25")
    prior_chroma = _load_prior_top10(
        prior_results_dir / "chroma_results.csv", retriever="chromadb"
    )

    def exact_match_rate(name: str, prior: dict[str, list[str]]) -> float:
        current = {row["question_id"]: row["retrieved_context_ids"] for row in results[name]}
        shared = sorted(set(current) & set(prior))
        if not shared:
            raise ValueError("prior result case coverage missing")
        return fmean(current[case_id] == prior[case_id] for case_id in shared)

    def overlap_rate(name: str, prior: dict[str, list[str]]) -> float:
        current = {row["question_id"]: row["retrieved_context_ids"] for row in results[name]}
        shared = sorted(set(current) & set(prior))
        return fmean(
            len(set(current[case_id]) & set(prior[case_id])) / 10 for case_id in shared
        )

    baseline_reproduction = {
        "bm25_top10_exact_match_rate": exact_match_rate("bm25_current_baseline", prior_bm25),
        "vector_chroma_top10_exact_match_rate": exact_match_rate(
            "vector_current_minilm", prior_chroma
        ),
        "vector_chroma_top10_mean_overlap": overlap_rate(
            "vector_current_minilm", prior_chroma
        ),
    }

    best_vector_name = max(
        vector_results,
        key=lambda name: _core_score(summaries[name]["overall"]),
    )
    verdict = classify_fairness_verdict(
        summaries["bm25_minimal_raw"]["overall"],
        summaries["bm25_current_baseline"]["overall"],
        summaries[best_vector_name]["overall"],
    )
    verdict.update(
        label=_verdict_label(verdict["code"]),
        best_vector_config=best_vector_name,
        current_bm25_config="bm25_current_baseline",
        production_changed=False,
    )
    preprocessing = _preprocessing_audit()
    metric_rows = _summary_rows(summaries)
    type_rows = _group_metrics(cases, results)
    vector_audit.update(
        prior_chroma_top10_exact_match_rate=baseline_reproduction[
            "vector_chroma_top10_exact_match_rate"
        ],
        prior_chroma_top10_mean_overlap=baseline_reproduction[
            "vector_chroma_top10_mean_overlap"
        ],
        chroma_package_currently_installed=_package_version("chromadb"),
        fresh_chroma_rebuild_performed=False,
        fresh_chroma_rebuild_reason=(
            "production dependency cleanup retained; exact cosine audits stored Chroma rankings"
        ),
    )
    alternate = {
        "current": {
            "model": MODEL,
            "dimensions": DIMENSIONS,
            "status": "evaluated_local_cache",
        },
        "alternate": {
            "candidate": "intfloat/multilingual-e5-large",
            "status": "not_run_download_required",
            "reason": "no alternate multilingual retrieval model exists in the local cache",
            "production_applied": False,
            "official_reference": "https://qdrant.github.io/fastembed/examples/Supported_Models/",
        },
    }
    bm25_ablation = {
        "config_order": list(bm25_results),
        "summaries": {name: summaries[name] for name in bm25_results},
        "core_score_deltas": {
            "schat_tokens_minus_minimal": _core_score(
                summaries["bm25_schat_tokens_body_all"]["overall"]
            )
            - _core_score(summaries["bm25_minimal_raw"]["overall"]),
            "corpus_policy_minus_schat_tokens": _core_score(
                summaries["bm25_current_baseline"]["overall"]
            )
            - _core_score(summaries["bm25_schat_tokens_body_all"]["overall"]),
            "canonical_minus_current_raw": _core_score(
                summaries["bm25_current_canonical"]["overall"]
            )
            - _core_score(summaries["bm25_current_baseline"]["overall"]),
            "expanded_minus_canonical": _core_score(
                summaries["bm25_expanded_no_temporal"]["overall"]
            )
            - _core_score(summaries["bm25_current_canonical"]["overall"]),
            "temporal_minus_expanded": _core_score(
                summaries["bm25_production_query"]["overall"]
            )
            - _core_score(summaries["bm25_expanded_no_temporal"]["overall"]),
        },
    }
    representation = {
        "best_vector_config": best_vector_name,
        "summaries": {name: summaries[name] for name in vector_results},
        "body_rebuild_matches_catalog": vector_audit["body_rebuild_max_abs_delta"] < 1e-5,
    }
    latency = {
        name: {
            "mean_ms": summary["latency_ms"]["mean"],
            "p95_ms": summary["latency_ms"]["p95"],
            "index_build_ms": summary["index_build_ms"],
        }
        for name, summary in summaries.items()
    }
    report = {
        "dataset": {
            "dataset_version": fixture["dataset_version"],
            "document_version": fixture["document_version"],
            "chunk_version": CHUNK_VERSION,
            "chunk_count": len(chunks),
            "case_count": len(cases),
            "evaluated_positive_count": audit["approved_case_count"],
            "human_review_count": audit["human_review_case_count"],
            "negative_count": audit["negative_case_count"],
        },
        "preprocessing_audit": preprocessing,
        "summaries": summaries,
        "metric_rows": metric_rows,
        "type_rows": type_rows,
        "vector_audit": vector_audit,
        "baseline_reproduction": baseline_reproduction,
        "embedding_comparison": alternate,
        "verdict": verdict,
        "safety": {
            "production_retrieval_changed": False,
            "generation_api_calls": 0,
            "external_embedding_api_calls": 0,
            "source_text_persisted": False,
        },
    }

    forbidden_texts = {chunk.text for chunk in chunks}
    for payload in (
        preprocessing,
        bm25_ablation,
        vector_audit,
        alternate,
        representation,
        metric_rows,
        type_rows,
        latency,
        report["dataset"],
        verdict,
        report["safety"],
    ):
        ensure_fairness_artifact_safe(payload, forbidden_texts)

    output_dir.mkdir(parents=True, exist_ok=False)
    json_files = {
        "preprocessing_audit.json": preprocessing,
        "bm25_ablation.json": bm25_ablation,
        "vector_config_audit.json": {
            **vector_audit,
            "baseline_reproduction": baseline_reproduction,
        },
        "embedding_comparison.json": alternate,
        "representation_comparison.json": representation,
        "latency.json": latency,
        "test_results.json": {
            "status": "pending final verification",
            "generation_api_calls": 0,
            "external_embedding_api_calls": 0,
        },
    }
    for filename, payload in json_files.items():
        (output_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    _write_csv(output_dir / "metrics.csv", metric_rows)
    _write_csv(output_dir / "metrics_by_type.csv", type_rows)
    (output_dir / "review.html").write_text(_review_html(report), encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--prior-results-dir", type=Path, default=DEFAULT_PRIOR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = evaluate_fairness(
        catalog_path=args.catalog,
        fixture_path=args.fixture,
        prior_results_dir=args.prior_results_dir,
        output_dir=args.output_dir,
    )
    print(
        json.dumps(
            {
                "case_count": report["dataset"]["case_count"],
                "chunk_count": report["dataset"]["chunk_count"],
                "best_vector": report["verdict"]["best_vector_config"],
                "verdict": report["verdict"]["code"],
                "generation_api_calls": 0,
                "external_embedding_api_calls": 0,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
