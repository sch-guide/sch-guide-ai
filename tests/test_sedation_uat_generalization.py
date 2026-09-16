import json

import httpx
import pytest

from mvp.ai import generate
from mvp.query import classify, plan_query, topic_words
from mvp.retrieval import _temporal_phases
from mvp.settings import ROOT
from tools.rag_facet_slot_evaluate import MockQuota, settings
from tools.rag_sedation_uat_generalization_evaluate import build_report, load_cases

FIXTURE = ROOT / 'tests' / 'fixtures' / 'sedation_uat_queries.json'
EXPECTED_CATEGORIES = {
    'procedure', 'purpose', 'preparation', 'cautions', 'summary_broad',
    'branch_specific', 'fact_specific', 'comparison', 'negative_out_of_scope',
}
EXPECTED_MODES = {
    'independent_answer': 39,
    'independent_abstain': 4,
    'context_follow_up': 2,
    'clarification': 0,
}


def test_sedation_uat_fixture_is_complete_unique_and_mode_aware():
    cases = load_cases(FIXTURE)

    assert len(cases) == 45
    assert {case['category'] for case in cases} == EXPECTED_CATEGORIES
    assert len({case['id'] for case in cases}) == len(cases)
    assert len({case['question'] for case in cases}) == len(cases)
    assert all(case['expected_behavior'] in {'answer', 'abstain', 'follow_up', 'clarify'}
               for case in cases)
    assert all(case['expected_intent'] for case in cases)
    assert all('expected_qualifier' in case for case in cases)
    assert {
        mode: sum(case['expected_mode'] == mode for case in cases)
        for mode in EXPECTED_MODES
    } == EXPECTED_MODES
    assert {case['id'] for case in cases if case['expected_mode'] == 'context_follow_up'} == {
        'FACT02', 'COMP02',
    }


def test_fixture_is_evaluation_only_and_has_no_production_mappings():
    payload = json.loads(FIXTURE.read_text(encoding='utf-8'))
    serialized = json.dumps(payload, ensure_ascii=False)

    assert 'chunk-' not in serialized
    assert 'source_unit' not in serialized
    assert 'gold' not in serialized.casefold()


@pytest.mark.parametrize(('question', 'expected'), (
    ('진정 왜 해?', 'purpose'),
    ('진정하는 이유 알려줘', 'purpose'),
    ('진정 목적은 뭐야?', 'purpose'),
    ('진정에 대해 알려줘', 'summary'),
    ('소아 진정 알려줘', 'summary'),
    ('진정간호 전체적으로 설명해줘', 'summary'),
    ('진정간호를 간단히 정리해줘', 'summary'),
    ('성인과 소아 진정 후 관찰 방법이 어떻게 달라?', 'comparison'),
    ('진정 시행 전에 준비할 것은?', 'preparation'),
    ('진정 후 이상 증상은 무엇을 봐야 해?', 'cautions'),
))
def test_general_clinical_intent_grammar(question, expected):
    assert classify(question) == expected


@pytest.mark.parametrize(('question', 'expected'), (
    ('진정 전에 뭐 확인해?', frozenset({'before'})),
    ('진정하기 전 확인사항은?', frozenset({'before'})),
    ('진정 시행 중 관찰은?', frozenset({'during'})),
    ('진정하는 동안 활력징후는?', frozenset({'during'})),
    ('진정하고 나서 관찰은?', frozenset({'after'})),
    ('진정 시행 후 회복은?', frozenset({'after'})),
))
def test_bounded_temporal_grammar(question, expected):
    assert _temporal_phases(question) == expected


@pytest.mark.parametrize('separator', ('/', '·', '‧'))
def test_branch_qualifiers_preserve_common_separators(separator):
    metadata = {'id': 'sedation', 'title': '진정간호', 'document_name': '진정간호.pdf'}
    plan = plan_query(f'성인{separator}소아 진정 비교해줘', documents=[metadata])

    assert plan.kind == 'comparison'
    assert plan.domain == 'hospital'
    assert {'성인', '소아'}.issubset(plan.expanded.split())


def test_purpose_verb_form_resolves_to_registered_topic():
    metadata = {'id': 'sedation', 'title': '진정간호', 'document_name': '진정간호.pdf'}
    plan = plan_query('진정하는 이유 알려줘', documents=[metadata])

    assert plan.domain == 'hospital'
    assert plan.kind == 'purpose'
    assert topic_words(plan) == ['진정간호']


@pytest.mark.parametrize(('question', 'expected_kind'), (
    ('성인은 몇 분마다 모니터링해?', 'cautions'),
    ('성인과 소아 모니터링 간격 비교해줘', 'comparison'),
))
def test_context_follow_up_requires_and_recovers_registered_topic(question, expected_kind):
    metadata = {'id': 'sedation', 'title': '진정간호', 'document_name': '진정간호.pdf'}
    standalone = plan_query(question, documents=[metadata])
    follow_up = plan_query(
        question,
        previous='진정간호 모니터링 기준을 알려줘',
        follow_up=True,
        documents=[metadata],
    )

    assert standalone.domain == 'unknown'
    assert follow_up.domain == 'hospital'
    assert follow_up.kind == expected_kind
    assert '진정간호' in topic_words(follow_up)


def test_all_45_reclassified_uat_cases_pass_offline():
    report = build_report(FIXTURE)

    assert report['question_count'] == 45
    assert report['actual_groq_calls'] == 0
    assert report['q006_zero_call']['pass']
    assert report['passed'] == 45
    assert report['failed'] == 0


def test_q006_remains_a_provider_zero_call():
    calls = []
    plan = plan_query('화성 우주선의 궤도 계산 공식은?')
    answer, used = generate(
        settings(),
        plan.query,
        [],
        'sedation-uat-q006-test',
        quota=MockQuota(),
        transport=httpx.MockTransport(lambda request: calls.append(request)),
        plan=plan,
    )

    assert plan.domain == 'out_of_scope'
    assert not answer.answerable
    assert used == []
    assert calls == []
