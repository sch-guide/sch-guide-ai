import json
from pathlib import Path

from tools.chroma_baseline_evaluate import load_catalog
from tools.structured_table_evidence import (
    TableCellEvidence,
    TableRecord,
    TableRowEvidence,
    extract_table_records,
    render_table_markdown,
    safe_table_record_manifest,
    search_table_records,
    table_citation,
)

ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / 'data' / '실무지침서_수혈간호.pdf'
CATALOG = ROOT / 'data' / 'library' / 'catalog.sqlite3'
DOCUMENT_NAME = '실무지침서_수혈간호.pdf'


def synthetic_record():
    header = (
        TableCellEvidence(0, 0, '항목', (0.0, 0.0, 10.0, 5.0)),
        TableCellEvidence(0, 1, '값', (10.0, 0.0, 20.0, 5.0)),
    )
    rows = (
        TableRowEvidence(
            'row-1',
            1,
            (
                TableCellEvidence(1, 0, 'A', (0.0, 5.0, 10.0, 10.0)),
                TableCellEvidence(1, 1, '10분', (10.0, 5.0, 20.0, 10.0)),
            ),
            ('chunk-1',),
        ),
        TableRowEvidence(
            'row-2',
            2,
            (
                TableCellEvidence(2, 0, 'B', (0.0, 10.0, 10.0, 15.0)),
                TableCellEvidence(2, 1, '15분', (10.0, 10.0, 20.0, 15.0)),
            ),
            ('chunk-2',),
        ),
    )
    return TableRecord(
        table_id='table-1',
        document_id='doc-1',
        document_name='synthetic.pdf',
        page=2,
        table_index=0,
        bbox=(0.0, 0.0, 20.0, 15.0),
        fingerprint='f' * 64,
        header=header,
        rows=rows,
        source_chunk_ids=('chunk-1', 'chunk-2'),
    )


def test_table_records_extract_exact_structure_deterministically():
    metadata, chunks = load_catalog(CATALOG, document_name=DOCUMENT_NAME)

    first = extract_table_records(
        PDF, metadata['id'], DOCUMENT_NAME, chunks
    )
    second = extract_table_records(
        PDF, metadata['id'], DOCUMENT_NAME, chunks
    )

    assert first == second
    assert first
    assert len({record.table_id for record in first}) == len(first)
    assert {record.page for record in first} >= {2, 9, 15, 17}
    assert all(record.header and record.rows for record in first)
    assert all(cell.text is not None for record in first for cell in record.header)
    assert all(row.row_index >= 1 for record in first for row in record.rows)
    assert all(record.source_chunk_ids for record in first)


def test_safe_manifest_has_only_hashes_ids_coordinates_and_links():
    metadata, chunks = load_catalog(CATALOG, document_name=DOCUMENT_NAME)
    records = extract_table_records(
        PDF, metadata['id'], DOCUMENT_NAME, chunks
    )

    manifest = safe_table_record_manifest(records)
    encoded = json.dumps(manifest, ensure_ascii=False)

    assert manifest
    assert all('header_sha256' in row and 'row_sha256' in row for row in manifest)
    assert all('header' not in row and 'cells' not in row and 'text' not in row for row in manifest)
    assert all(chunk.text not in encoded for chunk in chunks)


def test_table_citation_and_markdown_preserve_exact_cells():
    record = synthetic_record()

    citation = table_citation(record, 2)
    markdown = render_table_markdown(record, row_indexes=(1, 2))

    assert citation.document_id == 'doc-1'
    assert citation.page == 2
    assert citation.table_id == 'table-1'
    assert citation.row_index == 2
    assert '| 항목 | 값 | 근거 |' in markdown
    assert '| A | 10분 | [T1] |' in markdown
    assert '| B | 15분 | [T2] |' in markdown
    assert '10분' in markdown and '15분' in markdown


def test_table_search_is_evaluation_only_and_returns_distinct_rows():
    record = synthetic_record()

    hits = search_table_records('15분', (record,), limit=10)

    assert hits[0].row_id == 'row-2'
    assert hits[0].table_id == 'table-1'
    assert hits[0].score > 0
    assert len({hit.row_id for hit in hits}) == len(hits)
