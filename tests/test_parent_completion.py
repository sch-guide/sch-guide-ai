from dataclasses import replace
import pytest
from mvp.context import expand_context
from mvp.evidence import assess_evidence
from mvp.library import Chunk, Hit
from mvp.query import plan_query


def chunk(i, parent='core', text='수혈 혈압은 15분마다 확인한다.'):
    return Chunk(str(i), 'doc', 'fixture.pdf', 1, '', parent, None, text, i, parent_id=parent)


def test_fill_all_siblings_beyond_neighbor_radius():
    chunks = [chunk(i) for i in range(5)]
    hits = expand_context('수혈 혈압 확인 주기는?', [Hit(chunks[0], .9)], chunks, limit=5)
    assert {h.chunk.id for h in hits} == {c.id for c in chunks}
    assert all(h.context_complete for h in hits)


def test_supplement_cannot_displace_required_parent():
    chunks = [chunk(i) for i in range(4)]
    extra = chunk(5, 'extra', '교육실 예약표를 읽습니다.')
    hits = expand_context('수혈 혈압 확인 주기는?', [Hit(chunks[0], .9), Hit(extra, .8)], chunks+[extra], limit=4)
    assert {h.chunk.id for h in hits} == {c.id for c in chunks}


def test_both_relevant_parents_completed():
    chunks = [chunk(0),chunk(1),chunk(2,'second','수혈 체온은 30분마다 확인한다.'),chunk(3,'second','수혈 체온은 30분마다 확인한다.')]
    question='수혈 혈압 및 체온 확인 주기는?'
    hits=expand_context(question,[Hit(chunks[0],.9),Hit(chunks[2],.8)],chunks,limit=4)
    assert len(hits)==4 and all(h.context_complete for h in hits)
    assert assess_evidence(plan_query(question),hits).sufficient
    limited=expand_context(question,[Hit(chunks[0],.9),Hit(chunks[2],.8)],chunks,limit=3)
    assert not assess_evidence(plan_query(question),limited).sufficient


def test_oversized_required_parent_abstains():
    chunks=[chunk(i) for i in range(6)]
    hits=expand_context('수혈 혈압 주기는?',[Hit(chunks[0],.9)],chunks,limit=3)
    assert len(hits)<=3
    assert not assess_evidence(plan_query('수혈 혈압 주기는?'),hits).sufficient


def test_complete_parent_not_padded_with_unrelated_neighbors():
    core=chunk(0)
    extra=replace(chunk(1,'extra','교육실 예약표를 읽습니다.'),section=core.section)
    hits=expand_context('수혈 혈압 주기는?',[Hit(core,.9)],[core,extra],limit=5)
    assert [h.chunk.id for h in hits]==[core.id]


def test_saved_trf001_top_parent_completeness_improves():
    import json
    from pathlib import Path
    from dataclasses import fields
    data=json.loads((Path(__file__).parent/'fixtures/trf001_parent_completion.json').read_text(encoding='utf-8'))
    names={f.name for f in fields(Chunk)}
    chunks=[Chunk(**{k:v for k,v in c.items() if k in names}) for c in data['chunks']]
    old=data['seed']
    assert old['context_complete'] is False
    seed=Hit(next(c for c in chunks if c.id==old['chunk_id']),old['similarity'])
    hits=expand_context(data['question'],[seed],chunks,limit=data['limit'])
    assert len(hits)==len(chunks)<=data['limit']
    assert all(h.context_complete for h in hits)


def test_same_parent_id_in_other_document_not_included():
    a=chunk(0)
    b=replace(chunk(1),document_id='other')
    hits=expand_context('수혈 혈압 주기는?',[Hit(a,.9)],[a,b])
    assert [h.chunk.document_id for h in hits]==['doc']


def test_zero_budget_returns_no_context():
    a=chunk(0)
    assert expand_context('수혈 혈압 주기는?',[Hit(a,.9)],[a],limit=0)==[]
