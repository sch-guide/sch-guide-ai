import pytest

from mvp.query import plan_query
from mvp.retrieval import _temporal_phases
from tools.rag_monitoring_qualifier_evaluate import build_report, load_cases


@pytest.mark.parametrize(('question', 'expected'), (
    ('진정 전', frozenset({'before'})),
    ('진정 전에', frozenset({'before'})),
    ('진정하기 전', frozenset({'before'})),
    ('진정 중', frozenset({'during'})),
    ('진정 중에', frozenset({'during'})),
    ('진정 시', frozenset({'during'})),
    ('진정할 때', frozenset({'during'})),
    ('진정하는 동안', frozenset({'during'})),
    ('진정 후', frozenset({'after'})),
    ('진정 후에', frozenset({'after'})),
    ('진정하고 나서', frozenset({'after'})),
    ('필요시', frozenset()),
    ('회복 시', frozenset()),
    ('동의 시', frozenset()),
))
def test_event_bound_temporal_grammar(question, expected):
    assert _temporal_phases(question) == expected


@pytest.mark.parametrize('case', load_cases(), ids=lambda case: case['id'])
def test_monitoring_qualifier_is_structured(case):
    plan = plan_query(case['question'])

    assert plan.kind == 'fact'
    assert plan.monitoring_branches == (case['expected_branch'],)
    assert plan.monitoring_phase == case['expected_phase']
    assert plan.monitoring_item == case['expected_item']
    assert plan.monitoring_action == case['expected_action']


def test_monitoring_generalization_offline_funnel():
    report = build_report()

    assert report['actual_groq_calls'] == 0
    assert report['question_count'] == 7
    assert report['passed'] == 7
    assert report['failed'] == 0
