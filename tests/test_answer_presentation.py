from mvp.ai import Answer, Evidence, Statement
from mvp.answer_ui import grouped_statement_markdown, uses_grouped_procedure
from mvp.evidence import SourceUnit
from mvp.presentation import (
    AnswerPresentation,
    StatementPresentation,
    build_answer_presentation,
    leading_marker,
    procedure_display_rows,
)


def source_unit(identifier, text, order, branch='common', phase='unspecified'):
    return SourceUnit(
        source_unit_id=identifier,
        chunk_id=f'chunk-{identifier}',
        source_order=order,
        branch=branch,
        exact_text=text,
        group_key=f'group-{branch}',
        required=True,
        selectable=True,
        phase=phase,
    )


def answer_for(units, *, format='steps'):
    return Answer(
        answerable=True,
        format=format,
        statements=[
            Statement(
                text=unit.exact_text,
                evidence=[Evidence(chunk_id=unit.chunk_id, quote=unit.exact_text)],
            )
            for unit in units
        ],
    )


def test_presentation_sidecar_contains_metadata_only_and_does_not_change_answer_json():
    units = (
        source_unit('su001', '1) 첫 번째 절차를 확인한다.', (1, 1), 'adult', 'before'),
        source_unit('su002', '15분 간격으로 관찰한다.', (1, 2), 'adult', 'during'),
    )
    answer = answer_for(units)
    original = answer.model_dump()

    presentation = build_answer_presentation(answer, units)
    answer.attach_presentation(presentation)

    assert answer.model_dump() == original
    assert 'presentation' not in answer.model_json_schema()['properties']
    assert answer.presentation == presentation
    assert tuple(item.statement_index for item in presentation.statements) == (0, 1)
    assert not hasattr(presentation.statements[0], 'text')
    assert not hasattr(presentation.statements[0], 'quote')
    assert not hasattr(presentation.statements[0], 'chunk_id')


def test_leading_marker_only_recognizes_structural_prefixes():
    expected = {
        '1) 첫 번째 절차': '1)',
        '2. 두 번째 절차': '2.',
        '① 진정 전 확인': '①',
        '④ 진정 후 확인': '④',
        '- 독립 목록 행': '-',
    }
    for text, marker in expected.items():
        assert leading_marker(text) == marker

    for clinical_number in ('15분 간격으로 관찰한다.', '10분 간격으로 관찰한다.',
                            '10 mg을 투여한다.', '95% 이상을 유지한다.'):
        assert leading_marker(clinical_number) == ''


def test_procedure_rows_add_branch_and_phase_headings_without_reordering_statements():
    units = (
        source_unit('su001', '공통 절차를 확인한다.', (1, 1), 'common', 'before'),
        source_unit('su002', '1) 성인 전 절차를 확인한다.', (2, 1), 'adult', 'before'),
        source_unit('su003', '성인 중 절차를 확인한다.', (2, 2), 'adult', 'during'),
        source_unit('su004', '성인 후 절차를 확인한다.', (2, 3), 'adult', 'after'),
        source_unit('su005', '① 소아 전 절차를 확인한다.', (3, 1), 'pediatric', 'before'),
        source_unit('su006', '소아 중 절차를 확인한다.', (3, 2), 'pediatric', 'during'),
        source_unit('su007', '소아 후 절차를 확인한다.', (3, 3), 'pediatric', 'after'),
    )
    answer = answer_for(units)
    presentation = build_answer_presentation(answer, units)

    rows = procedure_display_rows(presentation)

    assert [(row.kind, row.label) for row in rows if row.kind != 'statement'] == [
        ('branch', '공통'),
        ('branch', '성인'),
        ('phase', '시행 전'),
        ('phase', '시행 중'),
        ('phase', '시행 후'),
        ('branch', '소아'),
        ('phase', '시행 전'),
        ('phase', '시행 중'),
        ('phase', '시행 후'),
    ]
    assert [row.statement_index for row in rows if row.kind == 'statement'] == list(range(7))


def test_grouped_statement_markdown_has_no_outer_number_and_keeps_source_marker_once():
    statement = Statement(
        text='1) 원문 절차를 시행한다.',
        evidence=[Evidence(chunk_id='chunk-1', quote='1) 원문 절차를 시행한다.')],
    )
    item = StatementPresentation(0, 'su001', 'adult', 'before', (1, 1), '1)')

    rendered = grouped_statement_markdown(statement, item, '[[1]](#source)')

    assert not rendered.startswith('1. ')
    assert rendered.count('1\\)') == 1
    assert '원문 절차를 시행한다' in rendered
    assert '[[1]](#source)' in rendered


def test_only_steps_with_a_complete_sidecar_use_grouped_procedure_ui():
    item = StatementPresentation(0, 'su001', 'common', 'unspecified', (1, 1), '')
    sidecar = AnswerPresentation((item,))
    unit = source_unit('su001', '목적을 확인한다.', (1, 1))

    assert uses_grouped_procedure(answer_for((unit,)), sidecar)
    for format in ('paragraph', 'bullets', 'summary', 'comparison'):
        assert not uses_grouped_procedure(answer_for((unit,), format=format), sidecar)
    assert not uses_grouped_procedure(answer_for((unit,)), None)
