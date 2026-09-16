import json
from dataclasses import replace

import httpx
import pytest

from mvp.ai import (
    Quota,
    answer_text,
    generate,
    prompt_messages,
    serialize_evidence_groups,
    validate_answer,
)
from mvp.evidence import assess_evidence, evidence_groups, required_coverage_loss
from mvp.library import NO_GUIDELINE, Chunk, Hit
from mvp.query import plan_query
from mvp.settings import GuideError, Settings


def chunk(identifier, text, index, *, parent='', section='진정 절차'):
    return Chunk(identifier, 'doc', '진정간호.pdf', 3, '진정간호', section, None, text, index,
                 parent_id=parent)


def configured():
    return Settings(llm_provider='internal', llm_url='https://example.invalid/v1',
                    llm_model='fixture', llm_approved=True)


def response(statements):
    return json.dumps({'answerable': True, 'statements': statements}, ensure_ascii=False)


def statement(text, *sources):
    return {'text': text, 'evidence': [
        {'chunk_id': source.id, 'quote': source.text} for source in sources
    ]}


def test_procedure_groups_distinguish_required_seed_branch_and_optional_context():
    seed = Hit(chunk('seed', '1) 진정 전 상태를 확인한다.', 0, parent='common'), .8,
               bm25_score=.5)
    adult = Hit(chunk('adult', '[성인]\n진정 중 상태를 기록한다.', 1, parent='adult'), .7,
                context_only=True)
    optional = Hit(chunk('optional', '퇴실 안내문을 확인한다.', 2, parent='optional'), .6,
                   context_only=True)
    plan = plan_query('진정간호 절차는?')

    groups = evidence_groups(plan, [seed, adult, optional])

    assert [(group.required, group.requirement_reason) for group in groups] == [
        (True, 'seed'), (True, 'explicit_branch'), (False, 'optional_context')]


def test_procedure_action_noun_without_an_action_does_not_open_generation_gate():
    hit = Hit(chunk('noun', '진정 모니터링 관련 일반 용어 목록입니다.', 0), .99,
              bm25_score=.8)

    assessment = assess_evidence(plan_query('진정간호 절차는?'), [hit])

    assert not assessment.sufficient
    assert assessment.reason == 'no_topic_evidence'


def test_required_coverage_comparison_allows_only_optional_group_loss():
    seed = Hit(chunk('seed', '진정 전 상태를 확인한다.', 0), .8, bm25_score=.5)
    optional = Hit(chunk('optional', '관련 안내문을 확인한다.', 1), .7, context_only=True)
    plan = plan_query('진정간호 절차는?')
    before = assess_evidence(plan, [seed, optional])
    after = assess_evidence(plan, [seed])

    assert before.sufficient and after.sufficient
    assert before.procedure_coverage.optional_group_keys
    assert required_coverage_loss(before.procedure_coverage, after.procedure_coverage) == ''


def test_required_coverage_comparison_detects_missing_unit_inside_required_group():
    first = Hit(chunk('first', '진정 전 상태를 확인한다.', 0, parent='same'), .8,
                bm25_score=.5)
    second = Hit(chunk('second', '진정 중 상태를 기록한다.', 1, parent='same'), .7,
                 context_only=True)
    plan = plan_query('진정간호 절차는?')
    before = assess_evidence(plan, [first, second])
    after = assess_evidence(plan, [first])

    assert before.sufficient and after.sufficient
    assert required_coverage_loss(before.procedure_coverage, after.procedure_coverage) == 'missing_procedure_unit'


def test_prompt_budget_keeps_parent_group_atomic_and_records_optional_exclusion():
    required = Hit(chunk('required', '진정 절차를 확인한다.', 0, parent='required'), .8,
                   bm25_score=.5)
    optional = Hit(chunk('optional', '보완 절차를 기록한다. ' + '가' * 15000, 1,
                         parent='optional'), .7, context_only=True)
    plan = plan_query('진정간호 절차는?')
    assessment = assess_evidence(plan, [required, optional])
    trace = {}

    _, selected = prompt_messages(plan.query, assessment.hits, 14000, plan=plan,
                                  groups=assessment.groups, trace=trace)

    assert [hit.chunk.id for hit in selected] == ['required']
    assert trace['prompt_excluded_groups'] == [{
        'group_key': assessment.procedure_coverage.optional_group_keys[0],
        'required': False,
        'reason': 'not_required_by_facet_contract',
    }]


def test_missing_required_unit_after_budget_blocks_mock_transport(monkeypatch):
    first = Hit(chunk('first', '진정 전 상태를 확인한다.', 0, parent='same'), .8,
                bm25_score=.5)
    second = Hit(chunk('second', '진정 중 상태를 기록한다.', 1, parent='same'), .7,
                 context_only=True)
    plan = plan_query('진정간호 절차는?')
    monkeypatch.setattr('mvp.ai.prompt_messages', lambda *args, **kwargs: ([
        {'role': 'system', 'content': 'fixture'}, {'role': 'user', 'content': 'fixture'}
    ], [first]))

    answer, _ = generate(configured(), plan.query, [first, second], 'employee', plan=plan,
                         transport=httpx.MockTransport(lambda request: pytest.fail('LLM called')))

    assert answer_text(answer) == NO_GUIDELINE


def test_optional_group_budget_loss_allows_exactly_one_mock_call(monkeypatch, tmp_path):
    required = Hit(chunk('required', '진정 절차를 확인한다.', 0), .8, bm25_score=.5)
    optional = Hit(chunk('optional', '관련 안내문을 확인한다.', 1), .7, context_only=True)
    plan = plan_query('진정간호 절차는?')
    monkeypatch.setattr('mvp.ai.prompt_messages', lambda *args, **kwargs: ([
        {'role': 'system', 'content': 'fixture'}, {'role': 'user', 'content': 'fixture'}
    ], [required]))
    calls = []

    def handle(request):
        calls.append(request)
        payload = {'choices': [{'message': {'content': response([
            statement(required.chunk.text, required.chunk)
        ])}}]}
        return httpx.Response(200, json=payload)

    answer, _ = generate(configured(), plan.query, [required, optional], 'employee',
                         Quota(tmp_path / 'quota.db'), httpx.MockTransport(handle), plan=plan)

    assert answer.answerable
    assert len(calls) == 1


def test_procedure_citation_order_rejects_reversed_statements_but_keeps_multi_evidence():
    first = chunk('first', '진정 전 상태를 확인한다.', 0, parent='same')
    second = chunk('second', '진정 중 상태를 기록한다.', 1, parent='same')
    plan = plan_query('진정간호 절차는?')
    reversed_raw = response([statement(second.text, second), statement(first.text, first)])

    with pytest.raises(GuideError, match='AI_EVIDENCE'):
        validate_answer(reversed_raw, [Hit(first, .8), Hit(second, .7)], plan=plan)

    shared_first = chunk('shared-first', '진정 절차를 확인한다.', 2, parent='shared')
    shared_second = chunk('shared-second', '진정 절차를 확인한다.', 3, parent='shared')
    combined = statement(shared_first.text, shared_first, shared_second)
    answer = validate_answer(response([combined]), [Hit(shared_first, .8), Hit(shared_second, .7)],
                             plan=plan)
    assert len(answer.statements[0].evidence) == 2


def test_compact_evidence_serialization_keeps_text_and_moves_shared_metadata_to_group():
    first = replace(
        chunk('first', '진정 전 상태를 확인한다.', 0, parent='same'),
        page=7,
        location='7쪽',
    )
    second = replace(
        chunk('second', '진정 중 상태를 기록한다.', 1, parent='same'),
        page=7,
        location='7쪽',
    )
    plan = plan_query('진정간호 절차는?')
    groups = evidence_groups(plan, [Hit(first, .8, bm25_score=.5), Hit(second, .7)])

    payload = json.loads(serialize_evidence_groups(groups))

    assert payload['schema_version'] == 6
    assert len(payload['groups']) == 1
    group = payload['groups'][0]
    assert group['document'] == first.document_name
    assert group['page'] == 7
    assert group['section'] == first.section
    assert group['location'] == '7쪽'
    assert [source['chunk_id'] for source in group['sources']] == ['first', 'second']
    assert [[unit['text'] for unit in source['units']] for source in group['sources']] == [
        [first.text], [second.text]
    ]
    assert all('document' not in source and 'page' not in source
               and 'section' not in source and 'location' not in source
               for source in group['sources'])
    assert serialize_evidence_groups(groups).count(first.document_name) == 1


def test_compact_evidence_serialization_keeps_source_overrides_for_different_metadata():
    first = replace(
        chunk('first', '진정 전 상태를 확인한다.', 0, parent='same'),
        page=7,
        section='성인 진정',
        location='7쪽',
    )
    second = replace(
        chunk('second', '진정 중 상태를 기록한다.', 1, parent='same'),
        page=8,
        section='소아 진정',
        location='8쪽',
    )
    plan = plan_query('진정간호 절차는?')
    groups = evidence_groups(plan, [Hit(first, .8, bm25_score=.5), Hit(second, .7)])

    group = json.loads(serialize_evidence_groups(groups))['groups'][0]

    assert 'page' not in group and 'section' not in group and 'location' not in group
    assert [(source['page'], source['section'], source['location'])
            for source in group['sources']] == [
        (7, '성인 진정', '7쪽'),
        (8, '소아 진정', '8쪽'),
    ]


def test_compact_prompt_uses_chunk_ids_but_citation_validation_uses_server_chunk(monkeypatch, tmp_path):
    source = replace(
        chunk('source', '진정 전 상태를 확인한다.', 0),
        page=9,
        section='서버측 실제 section',
        location='9쪽',
    )
    hit = Hit(source, .8, bm25_score=.5)
    plan = plan_query('진정간호 절차는?')
    requests = []

    def handle(request):
        requests.append(request)
        content = json.loads(request.content)['messages'][1]['content']
        payload = json.loads(content.split('Evidence groups (JSON):\n')[1])
        assert payload['groups'][0]['sources'][0]['chunk_id'] == source.id
        assert payload['groups'][0]['sources'][0]['units'][0]['text'] == source.text
        return httpx.Response(200, json={'choices': [{'message': {'content': response([
            statement(source.text, source)
        ])}}]})

    answer, selected = generate(
        configured(), plan.query, [hit], 'employee', Quota(tmp_path / 'quota.db'),
        httpx.MockTransport(handle), plan=plan,
    )

    assert answer.answerable
    assert len(requests) == 1
    assert selected[0].chunk.page == 9
    assert selected[0].chunk.section == '서버측 실제 section'
    assert selected[0].chunk.location == '9쪽'
