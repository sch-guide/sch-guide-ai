from tools.structured_table_evidence import (
    TableCellEvidence,
    TableRecord,
    TableRowEvidence,
    TableSearchHit,
    table_citation,
)
from tools.table_evidence_app import build_hit_view, local_safety_contract


def record():
    header = (
        TableCellEvidence(0, 0, '항목', None),
        TableCellEvidence(0, 1, '값', None),
    )
    row = TableRowEvidence(
        'row-1',
        1,
        (
            TableCellEvidence(1, 0, 'A', None),
            TableCellEvidence(1, 1, '10분', None),
        ),
        ('chunk-1',),
    )
    return TableRecord(
        'table-1', 'doc-1', 'synthetic.pdf', 2, 0, (0.0, 0.0, 1.0, 1.0),
        'f' * 64, header, (row,), ('chunk-1',)
    )


def test_local_table_hit_view_uses_exact_cells_and_traceable_citation():
    item = record()
    hit = TableSearchHit('table-1', 'row-1', 2, 1, 1.25, table_citation(item, 1))

    view = build_hit_view(hit, (item,))

    assert view['document'] == 'synthetic.pdf'
    assert view['page'] == 2
    assert view['table_id'] == 'table-1'
    assert view['row_index'] == 1
    assert view['cells'] == ('A', '10분')
    assert view['source_chunk_ids'] == ('chunk-1',)


def test_table_evidence_app_is_local_evaluation_only():
    assert local_safety_contract() == {
        'scope': 'local_evaluation_only',
        'generation_api_calls': 0,
        'artifact_source_text_writes': False,
        'production_retrieval_changed': False,
        'image_interpretation_enabled': False,
    }
