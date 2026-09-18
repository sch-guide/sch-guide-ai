"""Context-fix regressions. Stored TRF-006 replay never searches or calls an API."""
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from mvp.ai import generate
from mvp.evidence import assess_evidence
from mvp.library import Chunk, Hit
from mvp.query import QueryPlan, plan_query
from mvp.settings import Settings


def hit(identifier, text, *, complete=True, parent=None, document='doc', context_only=False):
    chunk = Chunk(identifier, document, 'fixture.pdf', 1, '교육', '수혈', None,
                  text, 0, parent_id=parent or identifier)
    return Hit(chunk, .9, context_complete=complete, context_only=context_only)


def stored_trf006():
    path = Path(__file__).resolve().parents[1] / 'evaluation/results/bm25_answer_baseline_v1.json'
    row = next(r for r in json.loads(path.read_text(encoding='utf-8'))['results']
               if r['question_id'] == 'TRF-006')
    contents = {c['chunk_id']: c['content'] for c in row['retrieved_contexts']}
    hits = []
    for h in row['retrieval_trace']['final_hits']:
        c = Chunk(h['chunk_id'], h['document_id'], h['document_name'], h['page_number'],
                  '수혈간호', h['section_title'], None, contents[h['chunk_id']], h['index'],
                  location=h['location'], parent_id=h['parent_id'])
        hits.append(Hit(c, h['similarity'], bm25_score=h['bm25_score'],
                        fusion_score=h['fusion_score'], rerank_score=h['rerank_score'],
                        context_only=h['context_only'], context_complete=h['context_complete']))
    plan = dict(row['query_plan'])
    for key in ('focus', 'document_ids', 'entities', 'corrections'):
        plan[key] = tuple(plan[key])
    return QueryPlan(**plan), hits


def test_complete_core_survives_incomplete_supplement():
    core = hit('core', '수혈 활력징후는 15분마다 확인한다.')
    extra = hit('extra', '수혈 교육을 실시한다.', complete=False)
    result = assess_evidence(plan_query('수혈 활력징후 확인 주기는?'), [core, extra])
    assert result.sufficient
    assert result.hits == (core,)
    assert not extra.context_complete  # No flag rewriting.


def test_incomplete_core_remains_blocked():
    core = hit('core', '수혈 활력징후는 15분마다 확인한다.', complete=False)
    extra = hit('extra', '수혈 교육을 실시한다.')
    result = assess_evidence(plan_query('수혈 활력징후 확인 주기는?'), [core, extra])
    assert not result.sufficient
    assert result.reason == 'incomplete_semantic_block'


def test_incomplete_leading_evidence_cannot_be_replaced_by_lower_generic_source():
    hits = [hit('core', '수혈 활력징후는 15분마다 확인한다.', complete=False),
            hit('other', '수혈 활력징후 확인은 교육 후 시행한다.')]
    assert not assess_evidence(plan_query('수혈 활력징후는 언제 확인해?'), hits).sufficient


def test_when_question_requires_time_support_in_complete_subset():
    hits = [hit('generic', '수혈 활력징후와 부작용 관찰을 교육한다.'),
            hit('timing', '수혈 활력징후와 부작용은 15분마다 확인한다.', complete=False)]
    assert not assess_evidence(plan_query('수혈 활력징후와 부작용은 언제 확인해?'), hits).sufficient


def test_all_required_complete_parents_are_retained():
    hits = [hit('bp', '수혈 혈압은 15분마다 확인한다.'),
            hit('temp', '수혈 체온은 30분마다 확인한다.'),
            hit('extra', '수혈 교육을 실시한다.', complete=False)]
    result = assess_evidence(plan_query('수혈 혈압 및 체온 확인 주기는?'), hits)
    assert result.sufficient
    assert {h.chunk.id for h in result.hits} == {'bp', 'temp'}


def test_required_second_parent_cannot_be_discarded():
    hits = [hit('amount', 'PCN 세척량은 가상값 5 mL이다.'),
            hit('interval', 'PCN 세척 간격은 가상값 3시간이다.', complete=False)]
    assert not assess_evidence(plan_query('PCN 세척량과 간격은?'), hits).sufficient


def test_required_second_subject_cannot_be_discarded():
    hits = [hit('bp', '수혈 혈압은 15분마다 확인한다.'),
            hit('temp', '수혈 체온은 30분마다 확인한다.', complete=False)]
    assert not assess_evidence(plan_query('수혈 혈압 및 체온 확인 주기는?'), hits).sufficient


def test_required_document_cannot_be_discarded():
    plan = replace(plan_query('수혈 확인 주기는?'), document_ids=('a', 'b'), min_documents=2)
    hits = [hit('a', '수혈은 15분마다 확인한다.', document='a'),
            hit('b', '수혈은 30분마다 확인한다.', document='b', complete=False)]
    assert not assess_evidence(plan, hits).sufficient


@pytest.mark.parametrize('question', ['수혈 절차를 순서대로 알려줘', '수혈 전체 내용을 알려줘',
                                     '수혈 예외 조건은?', '수혈 내용을 요약해줘'])
def test_open_ended_or_exception_request_keeps_incomplete_guard(question):
    hits = [hit('core', '수혈 시 환자를 확인하고 기록한다.'),
            hit('extra', '수혈 시 주의하여 확인한다.', complete=False)]
    assert not assess_evidence(plan_query(question), hits).sufficient


def test_conflicting_incomplete_source_is_not_silently_discarded():
    hits = [hit('a', '수혈 활력징후 확인 간격은 15분이다.', document='a'),
            hit('b', '수혈 활력징후 확인 간격은 30분이다.', document='b', complete=False)]
    assert not assess_evidence(plan_query('수혈 활력징후 확인 간격은?'), hits).sufficient


def test_equally_specific_incomplete_parent_is_not_assumed_supplementary():
    # Same document: the existing cross-document conflict detector cannot decide this.
    hits = [hit('a', '수혈 활력징후 확인 간격은 15분이다.'),
            hit('b', '수혈 활력징후 확인 간격은 30분이다.', complete=False)]
    assert not assess_evidence(plan_query('수혈 활력징후 확인 간격은?'), hits).sufficient


def test_parent_with_mixed_flags_cannot_be_partially_used():
    hits = [hit('a', '수혈 활력징후는 15분마다 확인한다.', parent='same'),
            hit('b', '수혈 주의사항을 확인한다.', parent='same', complete=False)]
    assert not assess_evidence(plan_query('수혈 활력징후 확인 주기는?'), hits).sufficient


def test_trf006_saved_hits_pass_with_only_complete_parents():
    plan, hits = stored_trf006()
    before = list(hits)
    result = assess_evidence(plan, hits)
    assert result.sufficient, result.reason
    assert result.reason != 'incomplete_semantic_block'
    assert hits == before
    assert all(h.context_complete for h in result.hits)
    assert any('30분마다 활력징후와 부작용' in h.chunk.text for h in result.hits)
    assert not {h.chunk.parent_id for h in result.hits} & {
        h.chunk.parent_id for h in hits if not h.context_complete}


def test_trf006_generation_only_passes_complete_context_to_prompt(monkeypatch):
    plan, hits = stored_trf006()
    # Stop at prompt construction: test service integration without any API or quota.
    class PromptReached(Exception):
        pass
    def prompt(question, selected, *args, **kwargs):
        assert all(h.context_complete for h in selected)
        assert any('30분마다 활력징후와 부작용' in h.chunk.text for h in selected)
        raise PromptReached
    monkeypatch.setattr('mvp.ai.prompt_messages', prompt)
    quota = Mock()
    with pytest.raises(PromptReached):
        generate(Settings(), plan.query, hits, 'fixture', quota=quota, plan=plan)
    quota.reserve.assert_not_called()
