"""Evaluation-only structured table evidence, citation, and local search.

Exact PDF cell text exists only in memory and local UI rendering.  Persisted
manifests created by this module contain hashes and structural metadata only.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from mvp.retrieval import lexical_tokens
from tools.chroma_baseline_evaluate import CatalogChunk


@dataclass(frozen=True)
class TableCellEvidence:
    row_index: int
    column_index: int
    text: str
    bbox: tuple[float, float, float, float] | None


@dataclass(frozen=True)
class TableRowEvidence:
    row_id: str
    row_index: int
    cells: tuple[TableCellEvidence, ...]
    source_chunk_ids: tuple[str, ...]


@dataclass(frozen=True)
class TableRecord:
    table_id: str
    document_id: str
    document_name: str
    page: int
    table_index: int
    bbox: tuple[float, float, float, float]
    fingerprint: str
    header: tuple[TableCellEvidence, ...]
    rows: tuple[TableRowEvidence, ...]
    source_chunk_ids: tuple[str, ...]
    extraction_mode: str = 'pdf_cells'


@dataclass(frozen=True)
class TableCitation:
    document_id: str
    document_name: str
    page: int
    table_id: str
    row_index: int


@dataclass(frozen=True)
class TableSearchHit:
    table_id: str
    row_id: str
    page: int
    row_index: int
    score: float
    citation: TableCitation


_CACHE: dict[tuple[Any, ...], tuple[TableRecord, ...]] = {}


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _stable_id(prefix: str, *parts: object) -> str:
    return f"{prefix}-{_sha256('|'.join(str(part) for part in parts))[:20]}"


def _clean_cell(value: Any) -> str:
    return ' '.join(str(value or '').split())


def _rounded_bbox(value: Any) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    return tuple(round(float(part), 2) for part in value)


def _table_tokens(text: str) -> tuple[str, ...]:
    numeric = re.findall(
        r'\d+(?:[.,]\d+)?\s*(?:%|mg|mcg|μg|µg|g|kg|ml|mL|L|cc|분|시간|초|일|회|번)?',
        text,
        re.I,
    )
    normalized_numeric = [re.sub(r'\s+', '', token).casefold() for token in numeric]
    return tuple((*lexical_tokens(text), *normalized_numeric))


def _linked_chunk_ids(
    text: str,
    chunks: Sequence[CatalogChunk],
    *,
    page: int,
) -> tuple[str, ...]:
    table_tokens = set(_table_tokens(text))
    candidates = [
        chunk for chunk in chunks
        if chunk.page == page and chunk.substantive_body
    ]
    scored = []
    for chunk in candidates:
        chunk_tokens = set(_table_tokens(chunk.text))
        overlap = len(table_tokens.intersection(chunk_tokens))
        if overlap:
            scored.append((overlap, chunk.position, chunk.chunk_id))
    if scored:
        best = max(score for score, _, _ in scored)
        return tuple(
            identifier for score, _, identifier in sorted(scored, key=lambda row: row[1])
            if score >= max(1, math.ceil(best * 0.25))
        )
    return tuple(chunk.chunk_id for chunk in candidates)


def _fallback_parent_group(
    page: Any,
    table: Any,
    chunks: Sequence[CatalogChunk],
    *,
    page_number: int,
) -> tuple[tuple[CatalogChunk, ...], tuple[float, float, float, float]]:
    """Link a header-only grid to exact catalog rows without inferring cell columns."""
    x0, top, x1, bottom = (float(value) for value in table.bbox)
    wide_image_tops = [
        float(image['top'])
        for image in page.images
        if float(image.get('top', 0)) > bottom + 10
        and float(image.get('x1', 0)) - float(image.get('x0', 0)) > page.width * 0.5
    ]
    extended_bottom = min(
        wide_image_tops,
        default=min(float(page.height) - 50, bottom + 300),
    )
    bbox = (x0, top, x1, extended_bottom)
    region_text = page.crop(bbox).extract_text() or ''
    region_tokens = set(_table_tokens(region_text))
    groups: defaultdict[str, list[CatalogChunk]] = defaultdict(list)
    for chunk in chunks:
        if chunk.page == page_number and chunk.substantive_body:
            groups[chunk.parent_id].append(chunk)
    ranked = []
    for parent_id, group in groups.items():
        group.sort(key=lambda chunk: chunk.position)
        group_tokens = set(_table_tokens(' '.join(chunk.text for chunk in group)))
        overlap = len(region_tokens.intersection(group_tokens)) / max(1, len(group_tokens))
        if len(group) >= 2 and overlap >= 0.5:
            ranked.append((overlap, -group[0].position, parent_id, tuple(group)))
    if not ranked:
        return (), tuple(round(value, 2) for value in bbox)
    return max(ranked, key=lambda row: (row[0], row[1]))[3], tuple(
        round(value, 2) for value in bbox
    )


def extract_table_records(
    pdf_path: Path,
    document_id: str,
    document_name: str,
    chunks: Sequence[CatalogChunk],
) -> tuple[TableRecord, ...]:
    """Extract deterministic exact table cells without persisting their text."""
    import pdfplumber

    resolved = pdf_path.resolve()
    stat = resolved.stat()
    cache_key = (
        str(resolved), stat.st_size, stat.st_mtime_ns, document_id,
        tuple((chunk.chunk_id, chunk.page, chunk.position, _sha256(chunk.text)) for chunk in chunks),
    )
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    records = []
    with pdfplumber.open(resolved) as pdf:
        for page_number, page in enumerate(pdf.pages, 1):
            for table_index, table in enumerate(page.find_tables()):
                extracted = table.extract() or []
                if not extracted:
                    continue
                width = max((len(row) for row in extracted), default=0)
                if width < 2:
                    continue
                cleaned = [
                    tuple(_clean_cell(row[column] if column < len(row) else '') for column in range(width))
                    for row in extracted
                ]
                if sum(bool(value) for value in cleaned[0]) < 1:
                    continue
                fallback_group: tuple[CatalogChunk, ...] = ()
                table_bbox = tuple(round(float(value), 2) for value in table.bbox)
                if len(extracted) == 1:
                    fallback_group, table_bbox = _fallback_parent_group(
                        page, table, chunks, page_number=page_number
                    )
                    if not fallback_group:
                        continue
                table_id = _stable_id(
                    'tbl', document_id, page_number, table_index, *table_bbox
                )
                row_objects = table.rows

                def cells_for(row_index: int, values: tuple[str, ...]):
                    bboxes = row_objects[row_index].cells if row_index < len(row_objects) else ()
                    return tuple(
                        TableCellEvidence(
                            row_index,
                            column,
                            value,
                            _rounded_bbox(bboxes[column] if column < len(bboxes) else None),
                        )
                        for column, value in enumerate(values)
                    )

                header = cells_for(0, cleaned[0])
                table_text = ' '.join(value for row in cleaned for value in row if value)
                record_chunk_ids = (
                    tuple(chunk.chunk_id for chunk in fallback_group)
                    if fallback_group
                    else _linked_chunk_ids(table_text, chunks, page=page_number)
                )
                rows = []
                for row_index, values in enumerate(cleaned[1:], 1):
                    if not any(values):
                        continue
                    row_text = ' '.join(value for value in values if value)
                    row_chunk_ids = _linked_chunk_ids(
                        row_text, chunks, page=page_number
                    ) or record_chunk_ids
                    rows.append(TableRowEvidence(
                        row_id=_stable_id('tr', table_id, row_index, _sha256(row_text)),
                        row_index=row_index,
                        cells=cells_for(row_index, values),
                        source_chunk_ids=row_chunk_ids,
                    ))
                if fallback_group:
                    for row_index, chunk in enumerate(fallback_group, 1):
                        rows.append(TableRowEvidence(
                            row_id=_stable_id('tr', table_id, row_index, chunk.chunk_id),
                            row_index=row_index,
                            cells=(TableCellEvidence(
                                row_index,
                                0,
                                chunk.text,
                                None,
                            ),),
                            source_chunk_ids=(chunk.chunk_id,),
                        ))
                if not rows or not record_chunk_ids:
                    continue
                fingerprint_source = '\n'.join('\t'.join(row) for row in cleaned)
                if fallback_group:
                    fingerprint_source += '\n' + '\n'.join(
                        _sha256(chunk.text) for chunk in fallback_group
                    )
                fingerprint = _sha256(fingerprint_source)
                records.append(TableRecord(
                    table_id=table_id,
                    document_id=document_id,
                    document_name=document_name,
                    page=page_number,
                    table_index=table_index,
                    bbox=table_bbox,
                    fingerprint=fingerprint,
                    header=header,
                    rows=tuple(rows),
                    source_chunk_ids=record_chunk_ids,
                    extraction_mode=(
                        'catalog_row_fallback' if fallback_group else 'pdf_cells'
                    ),
                ))
    result = tuple(sorted(records, key=lambda record: (
        record.page, record.table_index, record.table_id
    )))
    _CACHE[cache_key] = result
    return result


def safe_table_record_manifest(records: Sequence[TableRecord]) -> list[dict[str, Any]]:
    """Return a raw-text-free structural manifest suitable for artifacts."""
    return [
        {
            'table_id': record.table_id,
            'document_id': record.document_id,
            'page': record.page,
            'table_index': record.table_index,
            'bbox': list(record.bbox),
            'fingerprint': record.fingerprint,
            'header_sha256': _sha256('|'.join(cell.text for cell in record.header)),
            'row_sha256': [
                _sha256('|'.join(cell.text for cell in row.cells)) for row in record.rows
            ],
            'row_ids': [row.row_id for row in record.rows],
            'row_count': len(record.rows),
            'column_count': len(record.header),
            'source_chunk_ids': list(record.source_chunk_ids),
            'extraction_mode': record.extraction_mode,
        }
        for record in records
    ]


def table_citation(record: TableRecord, row_index: int) -> TableCitation:
    if row_index not in {row.row_index for row in record.rows}:
        raise ValueError('table row not found')
    return TableCitation(
        record.document_id,
        record.document_name,
        record.page,
        record.table_id,
        row_index,
    )


def _markdown_cell(value: str) -> str:
    return value.replace('|', '\\|').replace('\n', '<br>')


def render_table_markdown(
    record: TableRecord,
    *,
    row_indexes: Sequence[int] | None = None,
) -> str:
    """Render exact parsed cells with a separate table-row citation column."""
    selected = set(row_indexes) if row_indexes is not None else None
    rows = [row for row in record.rows if selected is None or row.row_index in selected]
    if not rows:
        raise ValueError('no table rows selected')
    headers = [_markdown_cell(cell.text) for cell in record.header]
    lines = [
        '| ' + ' | '.join((*headers, '근거')) + ' |',
        '| ' + ' | '.join(['---'] * (len(headers) + 1)) + ' |',
    ]
    for display_index, row in enumerate(rows, 1):
        values = [_markdown_cell(cell.text) for cell in row.cells]
        if len(values) < len(headers):
            values.extend([''] * (len(headers) - len(values)))
        lines.append('| ' + ' | '.join((*values, f'[T{display_index}]')) + ' |')
    return '\n'.join(lines)


def search_table_records(
    question: str,
    records: Sequence[TableRecord],
    *,
    limit: int = 10,
) -> tuple[TableSearchHit, ...]:
    """Run local lexical row lookup; it is not wired into production retrieval."""
    if limit < 1:
        raise ValueError('limit must be positive')
    query_tokens = Counter(_table_tokens(question))
    if not query_tokens:
        return ()
    documents = []
    document_frequency: Counter[str] = Counter()
    for record in records:
        header_text = ' '.join(cell.text for cell in record.header)
        for row in record.rows:
            row_text = ' '.join(cell.text for cell in row.cells)
            counts = Counter(_table_tokens(f'{header_text} {row_text}'))
            documents.append((record, row, counts))
            document_frequency.update(counts)
    total = max(1, len(documents))
    ranked = []
    for record, row, counts in documents:
        score = 0.0
        for token in query_tokens:
            if token not in counts:
                continue
            idf = math.log(1 + (total + 1) / (document_frequency[token] + 1))
            score += idf * (1 + math.log(counts[token]))
        if score <= 0:
            continue
        ranked.append(TableSearchHit(
            table_id=record.table_id,
            row_id=row.row_id,
            page=record.page,
            row_index=row.row_index,
            score=score,
            citation=table_citation(record, row.row_index),
        ))
    ranked.sort(key=lambda hit: (-hit.score, hit.page, hit.row_index, hit.row_id))
    return tuple(ranked[:limit])
