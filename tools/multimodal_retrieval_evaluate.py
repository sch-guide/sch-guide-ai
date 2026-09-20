"""Evaluation-only table/figure sidecars and retrieval comparison.

Clinical source text is used only in memory. Persisted manifests contain stable
IDs, hashes, coordinates, source links, counts, and metrics.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from src.library import Chunk
from src.retrieval import BM25Index, lexical_tokens
from tools.chroma_baseline_evaluate import CatalogChunk, ensure_raw_text_free, load_catalog
from tools.retrieval_strategy_evaluate import _engine_summary, _evaluated_result
from tools.schat_mvp_stabilize import validate_review_audit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PDF = ROOT / "data" / "실무지침서_수혈간호.pdf"
DEFAULT_CATALOG = ROOT / "data" / "library" / "catalog.sqlite3"
DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "transfusion_retrieval_baseline.json"
DEFAULT_TEXT_RESULTS = (
    ROOT / "workspace" / "과거작업" / "평가산출물" / "2026-09-17_transfusion-expanded-retrieval" / "bm25_results.json"
)
DEFAULT_OUTPUT = ROOT / "workspace" / "과거작업" / "평가산출물" / "2026-09-17_schat-mvp-stabilization"
_TABLE_UNIT_CACHE: dict[tuple[Any, ...], tuple["TableUnit", ...]] = {}


@dataclass(frozen=True)
class TableUnit:
    unit_id: str
    table_id: str
    parent_table_id: str
    page: int
    table_index: int
    unit_kind: str
    row_index: int | None
    column_count: int
    source_chunk_ids: tuple[str, ...]
    bbox: tuple[float, float, float, float]
    caption_sha256: str
    header_sha256: str
    cell_sha256: str
    search_text: str


@dataclass(frozen=True)
class FigureUnit:
    figure_id: str
    page: int
    bbox: tuple[float, float, float, float]
    caption_sha256: str
    nearby_chunk_ids: tuple[str, ...]
    confidence: str
    vision_description: None = None


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_id(prefix: str, *parts: object) -> str:
    payload = "|".join(str(part) for part in parts)
    return f"{prefix}-{_sha256(payload)[:20]}"


def _clean_cells(row: Sequence[Any]) -> tuple[str, ...]:
    return tuple(" ".join(str(value or "").split()) for value in row)


def _source_chunk(chunk: CatalogChunk, *, text: str | None = None) -> Chunk:
    return Chunk(
        id=chunk.chunk_id,
        document_id=chunk.document_id,
        document_name=chunk.document_name,
        page=chunk.page,
        title=chunk.title,
        section=chunk.section,
        updated_date=None,
        text=chunk.text if text is None else text,
        index=chunk.position,
        source_type=chunk.source_type,
        location=chunk.location,
        parent_id=chunk.parent_id,
    )


def _table_region(page: Any, table: Any, rows: Sequence[Sequence[Any]]) -> tuple[float, float, float, float]:
    x0, top, x1, bottom = (float(value) for value in table.bbox)
    if len(rows) == 1:
        wide_image_tops = [
            float(image["top"])
            for image in page.images
            if float(image.get("top", 0)) > bottom + 10
            and float(image.get("x1", 0)) - float(image.get("x0", 0)) > page.width * 0.5
        ]
        bottom = min(wide_image_tops, default=min(float(page.height) - 50, bottom + 300))
    return x0, top, x1, bottom


def _caption_text(page: Any, bbox: tuple[float, float, float, float]) -> str:
    x0, top, x1, _ = bbox
    if top <= 1:
        return ""
    return " ".join(
        (page.crop((x0, max(0.0, top - 80.0), x1, top)).extract_text() or "").split()
    )


def build_table_units(
    pdf_path: Path, document_id: str, chunks: Sequence[CatalogChunk]
) -> tuple[TableUnit, ...]:
    import pdfplumber

    resolved_pdf = pdf_path.resolve()
    stat = resolved_pdf.stat()
    cache_key = (
        str(resolved_pdf),
        stat.st_size,
        stat.st_mtime_ns,
        document_id,
        tuple(
            (
                chunk.chunk_id,
                chunk.parent_id,
                chunk.page,
                chunk.position,
                _sha256(chunk.text),
                chunk.substantive_body,
            )
            for chunk in chunks
        ),
    )
    cached = _TABLE_UNIT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    groups: defaultdict[tuple[int, str], list[CatalogChunk]] = defaultdict(list)
    for chunk in chunks:
        if chunk.page is not None and chunk.substantive_body:
            groups[(chunk.page, chunk.parent_id)].append(chunk)
    for group in groups.values():
        group.sort(key=lambda chunk: chunk.position)

    units: list[TableUnit] = []
    linked_parents: set[tuple[int, int, str]] = set()
    with pdfplumber.open(resolved_pdf) as pdf:
        for page_number, page in enumerate(pdf.pages, 1):
            for table_index, table in enumerate(page.find_tables()):
                rows = table.extract() or []
                column_count = max((len(row) for row in rows), default=0)
                if column_count < 3:
                    continue
                bbox = _table_region(page, table, rows)
                region_text = page.crop(bbox).extract_text() or ""
                region_tokens = set(lexical_tokens(region_text))
                if not region_tokens:
                    continue
                headers = _clean_cells(rows[0]) if rows else ()
                header_text = " ".join(value for value in headers if value)
                caption = _caption_text(page, bbox)
                for (chunk_page, parent_id), group in sorted(groups.items()):
                    if chunk_page != page_number or len(group) < 2:
                        continue
                    group_text = " ".join(chunk.text for chunk in group)
                    group_tokens = set(lexical_tokens(group_text))
                    overlap = len(region_tokens.intersection(group_tokens)) / max(1, len(group_tokens))
                    if overlap < 0.5:
                        continue
                    link_key = (page_number, table_index, parent_id)
                    if link_key in linked_parents:
                        continue
                    linked_parents.add(link_key)
                    table_id = _stable_id(
                        "tbl", document_id, page_number, table_index, parent_id, *(round(x, 2) for x in bbox)
                    )
                    common = {
                        "table_id": table_id,
                        "parent_table_id": table_id,
                        "page": page_number,
                        "table_index": table_index,
                        "column_count": column_count,
                        "bbox": tuple(round(value, 2) for value in bbox),
                        "caption_sha256": _sha256(caption),
                        "header_sha256": _sha256(header_text),
                    }
                    units.append(
                        TableUnit(
                            unit_id=_stable_id("tu", table_id, "whole_table"),
                            unit_kind="whole_table",
                            row_index=None,
                            source_chunk_ids=tuple(chunk.chunk_id for chunk in group),
                            cell_sha256=_sha256(group_text),
                            search_text=" ".join(value for value in (caption, header_text, group_text) if value),
                            **common,
                        )
                    )
                    for row_index, chunk in enumerate(group, 1):
                        units.append(
                            TableUnit(
                                unit_id=_stable_id("tu", table_id, "row", row_index, chunk.chunk_id),
                                unit_kind="row",
                                row_index=row_index,
                                source_chunk_ids=(chunk.chunk_id,),
                                cell_sha256=_sha256(chunk.text),
                                search_text=chunk.text,
                                **common,
                            )
                        )
                        units.append(
                            TableUnit(
                                unit_id=_stable_id("tu", table_id, "header_row", row_index, chunk.chunk_id),
                                unit_kind="header_row",
                                row_index=row_index,
                                source_chunk_ids=(chunk.chunk_id,),
                                cell_sha256=_sha256(chunk.text),
                                search_text=" ".join(value for value in (caption, header_text, chunk.text) if value),
                                **common,
                            )
                        )
    result = tuple(
        sorted(
            units,
            key=lambda unit: (
                unit.page,
                unit.table_index,
                unit.table_id,
                unit.unit_kind,
                unit.row_index or 0,
            ),
        )
    )
    _TABLE_UNIT_CACHE[cache_key] = result
    return result


def safe_table_manifest(units: Sequence[TableUnit]) -> list[dict[str, Any]]:
    return [
        {
            "unit_id": unit.unit_id,
            "table_id": unit.table_id,
            "parent_table_id": unit.parent_table_id,
            "page": unit.page,
            "table_index": unit.table_index,
            "unit_kind": unit.unit_kind,
            "row_index": unit.row_index,
            "column_count": unit.column_count,
            "source_chunk_ids": list(unit.source_chunk_ids),
            "bbox": list(unit.bbox),
            "caption_sha256": unit.caption_sha256,
            "header_sha256": unit.header_sha256,
            "cell_sha256": unit.cell_sha256,
        }
        for unit in units
    ]


def table_aware_bm25_ranking(
    question: str,
    chunks: Sequence[CatalogChunk],
    table_units: Sequence[TableUnit],
    *,
    limit: int,
    index: BM25Index | None = None,
) -> dict[str, Any]:
    header_rows = {
        unit.source_chunk_ids[0]: unit.search_text
        for unit in table_units
        if unit.unit_kind == "header_row" and len(unit.source_chunk_ids) == 1
    }
    documents = [
        _source_chunk(chunk, text=header_rows.get(chunk.chunk_id, chunk.text)) for chunk in chunks
    ]
    index = index or BM25Index(documents)
    started = time.perf_counter()
    scores = index.scores(question)
    positions = sorted(
        range(len(chunks)), key=lambda position: (-float(scores[position]), chunks[position].position)
    )[:limit]
    return {
        "retrieved_context_ids": [chunks[position].chunk_id for position in positions],
        "scores": [float(scores[position]) for position in positions],
        "latency_ms": (time.perf_counter() - started) * 1000,
    }


def build_figure_inventory(
    pdf_path: Path, document_id: str, chunks: Sequence[CatalogChunk]
) -> tuple[FigureUnit, ...]:
    import pdfplumber

    chunks_by_page: defaultdict[int, list[CatalogChunk]] = defaultdict(list)
    for chunk in chunks:
        if chunk.page is not None and chunk.substantive_body:
            chunks_by_page[chunk.page].append(chunk)

    with pdfplumber.open(pdf_path) as pdf:
        signatures = Counter(
            (
                round(float(image.get("x0", 0)), 1),
                round(float(image.get("top", 0)), 1),
                round(float(image.get("x1", 0)), 1),
                round(float(image.get("bottom", 0)), 1),
            )
            for page in pdf.pages
            for image in page.images
        )
        figures: list[FigureUnit] = []
        repeat_threshold = max(3, len(pdf.pages) // 3)
        for page_number, page in enumerate(pdf.pages, 1):
            for image_index, image in enumerate(page.images):
                raw_bbox = (
                    float(image.get("x0", 0)),
                    float(image.get("top", 0)),
                    float(image.get("x1", 0)),
                    float(image.get("bottom", 0)),
                )
                signature = tuple(round(value, 1) for value in raw_bbox)
                x0, top, x1, bottom = raw_bbox
                width = x1 - x0
                height = bottom - top
                if signatures[signature] >= repeat_threshold or width < 24 or height < 16:
                    continue
                if width * height < float(page.width) * float(page.height) * 0.005:
                    continue
                clipped = (
                    max(0.0, x0),
                    max(0.0, top),
                    min(float(page.width), x1),
                    min(float(page.height), bottom),
                )
                if clipped[0] >= clipped[2] or clipped[1] >= clipped[3]:
                    continue
                above = ""
                if clipped[1] > 0:
                    above = page.crop(
                        (clipped[0], max(0.0, clipped[1] - 36.0), clipped[2], clipped[1])
                    ).extract_text() or ""
                below = ""
                if clipped[3] < float(page.height):
                    below = page.crop(
                        (
                            clipped[0],
                            clipped[3],
                            clipped[2],
                            min(float(page.height), clipped[3] + 36.0),
                        )
                    ).extract_text() or ""
                caption = " ".join(f"{above} {below}".split())
                nearby = tuple(chunk.chunk_id for chunk in chunks_by_page.get(page_number, ()))
                figures.append(
                    FigureUnit(
                        figure_id=_stable_id(
                            "fig", document_id, page_number, image_index, *signature
                        ),
                        page=page_number,
                        bbox=tuple(round(value, 2) for value in clipped),
                        caption_sha256=_sha256(caption),
                        nearby_chunk_ids=nearby,
                        confidence="metadata_only",
                    )
                )
    return tuple(sorted(figures, key=lambda figure: (figure.page, figure.bbox, figure.figure_id)))


def safe_figure_manifest(figures: Sequence[FigureUnit]) -> list[dict[str, Any]]:
    return [
        {
            "figure_id": figure.figure_id,
            "page": figure.page,
            "bbox": list(figure.bbox),
            "caption_sha256": figure.caption_sha256,
            "nearby_chunk_ids": list(figure.nearby_chunk_ids),
            "confidence": figure.confidence,
        }
        for figure in figures
    ]


def _load_text_results(path: Path, cases: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload["cases"]
    if [row["question_id"] for row in rows] != [case["question_id"] for case in cases]:
        raise ValueError("text-only result case drift")
    return rows


def evaluate_multimodal_tracks(
    *,
    pdf_path: Path,
    catalog_path: Path,
    fixture_path: Path,
    text_results_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    _, chunks = load_catalog(catalog_path, document_name=fixture["document"]["name"])
    review_audit = validate_review_audit(fixture["cases"], chunks)
    table_units = build_table_units(pdf_path, fixture["document"]["id"], chunks)
    figures = build_figure_inventory(pdf_path, fixture["document"]["id"], chunks)
    text_results = _load_text_results(text_results_path, fixture["cases"])

    documents = [
        _source_chunk(
            chunk,
            text=next(
                (
                    unit.search_text
                    for unit in table_units
                    if unit.unit_kind == "header_row"
                    and unit.source_chunk_ids == (chunk.chunk_id,)
                ),
                chunk.text,
            ),
        )
        for chunk in chunks
    ]
    started = time.perf_counter()
    table_index = BM25Index(documents)
    index_build_ms = (time.perf_counter() - started) * 1000
    table_results = []
    for case in fixture["cases"]:
        ranked = table_aware_bm25_ranking(
            case["question"], chunks, table_units, limit=10, index=table_index
        )
        table_results.append(
            _evaluated_result(
                case=case,
                retrieved_ids=ranked["retrieved_context_ids"],
                scores=ranked["scores"],
                latency_ms=ranked["latency_ms"],
            )
        )

    table_case_ids = {
        case["question_id"]
        for case in fixture["cases"]
        if case["expected_answerable"]
        and not case["needs_human_review"]
        and any(ref["source_shape"] == "table" for ref in case["reference_contexts"])
    }
    text_table_results = [row for row in text_results if row["question_id"] in table_case_ids]
    aware_table_results = [row for row in table_results if row["question_id"] in table_case_ids]
    report = {
        "schema_version": 1,
        "dataset_version": fixture["dataset_version"],
        "document_version": fixture["document_version"],
        "tracks": {
            "text_only_bm25": _engine_summary(text_results),
            "table_aware_bm25": _engine_summary(table_results),
        },
        "approved_table_case_count": len(table_case_ids),
        "approved_table_subset": {
            "text_only_bm25": _engine_summary(text_table_results),
            "table_aware_bm25": _engine_summary(aware_table_results),
        },
        "table_index_build_ms": index_build_ms,
        "table_unit_count": len(table_units),
        "table_count": len({unit.table_id for unit in table_units}),
        "figure_candidate_count": len(figures),
        "multimodal_aware": {
            "status": "not_evaluated_no_approved_image_dependent_gold",
            "approved_image_dependent_case_count": 0,
            "pending_case_ids": ["TF027"],
            "vision_descriptions_generated": 0,
        },
        "review_audit": review_audit,
        "generation_api_calls": 0,
        "production_changed": False,
    }
    forbidden = {chunk.text for chunk in chunks}
    table_manifest = safe_table_manifest(table_units)
    figure_manifest = safe_figure_manifest(figures)
    for payload in (report, table_manifest, figure_manifest):
        ensure_raw_text_free(payload, forbidden_exact_texts=forbidden)

    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "table_manifest.json").write_text(
        json.dumps(table_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "figure_manifest.json").write_text(
        json.dumps(figure_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "multimodal_results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    fields = [
        "question_id",
        "question_type",
        "expected_answerable",
        "needs_human_review",
        "latency_ms",
        "hit_at_1",
        "hit_at_3",
        "hit_at_5",
        "hit_at_10",
        "mrr",
        "recall_at_10",
    ]
    with (output_dir / "table_aware_results.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in table_results:
            writer.writerow(
                {
                    "question_id": row["question_id"],
                    "question_type": row["question_type"],
                    "expected_answerable": row["expected_answerable"],
                    "needs_human_review": row["needs_human_review"],
                    "latency_ms": row["latency_ms"],
                    **{key: row["metrics"].get(key, "") for key in fields if key in row["metrics"]},
                }
            )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--text-results", type=Path, default=DEFAULT_TEXT_RESULTS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = evaluate_multimodal_tracks(
        pdf_path=args.pdf,
        catalog_path=args.catalog,
        fixture_path=args.fixture,
        text_results_path=args.text_results,
        output_dir=args.output_dir,
    )
    print(
        json.dumps(
            {
                "table_unit_count": report["table_unit_count"],
                "table_count": report["table_count"],
                "approved_table_case_count": report["approved_table_case_count"],
                "figure_candidate_count": report["figure_candidate_count"],
                "generation_api_calls": 0,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
