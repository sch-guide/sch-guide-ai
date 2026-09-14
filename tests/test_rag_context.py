import json
from dataclasses import replace
from pathlib import Path

from mvp.context import expand_context
from mvp.library import Chunk, Hit
from tools.rag_phase1_evaluate import stage_recall


def chunk(identifier, text, index, *, parent='', section='', page=1, document='doc'):
    return Chunk(
        identifier, document, 'guide.pdf', page, 'Guide', section, None, text, index,
        previous_chunk_id=None, next_chunk_id=None, parent_id=parent,
    )


def linked(chunks):
    return [replace(
        item,
        previous_chunk_id=chunks[index - 1].id if index else None,
        next_chunk_id=chunks[index + 1].id if index + 1 < len(chunks) else None,
    ) for index, item in enumerate(chunks)]


def test_procedure_expands_multiple_parent_groups_and_returns_source_order():
    chunks = linked([
        chunk('common', '1) 처방을 확인한다.', 0, parent='common'),
        chunk('adult-a', '작성 방법 [성인]\n① 시행 전 상태를 기록한다.', 1, parent='adult'),
        chunk('adult-b', '② 시행 중 상태를 기록한다.', 2, parent='adult'),
        chunk('child-a', '작성 방법 [소아]\n① 시행 전 상태를 기록한다.', 3, parent='child'),
        chunk('child-b', '② 시행 중 상태를 기록한다.', 4, parent='child'),
    ])
    seeds = [Hit(chunks[3], .9), Hit(chunks[0], .8)]

    hits = expand_context('진정간호 절차는?', seeds, chunks)

    assert [hit.chunk.id for hit in hits] == ['common', 'adult-a', 'adult-b', 'child-a', 'child-b']
    assert [hit.chunk.index for hit in hits] == sorted(hit.chunk.index for hit in hits)
    assert {hit.chunk.id for hit in hits if not hit.context_only} == {'common', 'child-a'}
    assert all(hit.context_complete for hit in hits)


def test_procedure_removes_exact_duplicates_but_keeps_distinct_branches():
    chunks = linked([
        chunk('adult', '작성 방법 [성인]\n① 상태를 확인한다.', 0, parent='adult'),
        chunk('duplicate', '작성 방법 [성인]\n① 상태를 확인한다.', 1, parent='duplicate', page=2),
        chunk('child', '작성 방법 [소아]\n① 상태를 확인한다.', 2, parent='child', page=2),
    ])

    hits = expand_context('진정 절차', [Hit(chunks[1], .9), Hit(chunks[2], .8)], chunks)

    assert [hit.chunk.id for hit in hits] == ['duplicate', 'child']
    assert '성인' in hits[0].chunk.text and '소아' in hits[1].chunk.text


def test_procedure_limit_marks_an_incomplete_parent_group():
    chunks = linked([
        chunk(str(index), f'{index + 1}) 절차 내용을 확인한다.', index, parent='procedure')
        for index in range(4)
    ])

    hits = expand_context('교육 방법은?', [Hit(chunks[0], .9)], chunks, limit=3)

    assert [hit.chunk.id for hit in hits] == ['0', '1', '2']
    assert not any(hit.context_complete for hit in hits)


def test_non_procedure_keeps_seed_first_round_robin_order():
    chunks = linked([
        chunk('a', '진정 목적을 설명하는 충분한 본문입니다.', 0, section='목적'),
        chunk('b', '진정 목적의 다음 문맥을 설명합니다.', 1, section='목적'),
        chunk('c', '주의사항을 설명하는 충분한 본문입니다.', 2, section='주의'),
    ])

    hits = expand_context('진정 목적은?', [Hit(chunks[2], .9), Hit(chunks[0], .8)], chunks)

    assert [hit.chunk.id for hit in hits] == ['c', 'a', 'b']


def test_non_procedure_duplicate_parent_part_still_counts_as_incomplete_when_truncated():
    chunks = linked([
        chunk('a', '진정 목적을 설명하는 충분한 본문입니다.', 0, parent='purpose'),
        chunk('b', '진정 목적을 설명하는 충분한 본문입니다.', 1, parent='purpose'),
    ])

    hits = expand_context('진정 목적은?', [Hit(chunks[0], .9)], chunks, limit=1)

    assert [hit.chunk.id for hit in hits] == ['a']
    assert not hits[0].context_complete


def test_expand_context_rejects_non_positive_limit():
    seed = chunk('a', '절차 내용을 충분히 설명합니다.', 0)

    try:
        expand_context('절차는?', [Hit(seed, .9)], [seed], limit=0)
    except ValueError as error:
        assert str(error) == 'limit은 1 이상이어야 합니다.'
    else:
        raise AssertionError('ValueError를 예상했습니다.')


def test_q002_gold_fixture_has_stable_order_branches_and_current_chunk_ids():
    path = Path(__file__).parent / 'fixtures' / 'q002_gold_stages.json'
    gold = json.loads(path.read_text(encoding='utf-8'))
    stages = gold['stages']

    assert gold['query_id'] == 'Q002'
    assert [stage['source_order'] for stage in stages] == list(range(1, len(stages) + 1))
    assert len({stage['stage_id'] for stage in stages}) == len(stages)
    assert {'common', 'adult', 'pediatric'} == {stage['branch'] for stage in stages}
    assert {'required', 'supplemental'} == {stage['importance'] for stage in stages}
    assert all(stage['allowed_chunk_ids'] for stage in stages)
    assert all(identifier.startswith(gold['document_id'] + '-chunk-')
               for stage in stages for identifier in stage['allowed_chunk_ids'])


def test_stage_recall_counts_units_instead_of_counting_duplicate_chunk_ids():
    gold = {'stages': [
        {'stage_id': 'a', 'importance': 'required', 'allowed_chunk_ids': ['c1', 'c2']},
        {'stage_id': 'b', 'importance': 'required', 'allowed_chunk_ids': ['c2']},
        {'stage_id': 'c', 'importance': 'supplemental', 'allowed_chunk_ids': ['c3']},
    ]}

    result = stage_recall(gold, ['c2', 'c2'])

    assert result['required'] == {'recalled': 2, 'total': 2, 'recall': 1.0}
    assert result['supplemental'] == {'recalled': 0, 'total': 1, 'recall': 0.0}
