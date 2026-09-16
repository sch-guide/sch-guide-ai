import json

import httpx

from mvp.ai import generate
from mvp.query import plan_query
from mvp.settings import ROOT
from tools.rag_facet_slot_evaluate import MockQuota, settings
from tools.rag_sedation_uat_generalization_evaluate import load_cases

FIXTURE = ROOT / 'tests' / 'fixtures' / 'sedation_uat_queries.json'
EXPECTED_CATEGORIES = {
    'procedure', 'purpose', 'preparation', 'cautions', 'summary_broad',
    'branch_specific', 'fact_specific', 'comparison', 'negative_out_of_scope',
}


def test_sedation_uat_fixture_is_complete_and_unique():
    cases = load_cases(FIXTURE)

    assert 30 <= len(cases) <= 50
    assert {case['category'] for case in cases} == EXPECTED_CATEGORIES
    assert len({case['id'] for case in cases}) == len(cases)
    assert len({case['question'] for case in cases}) == len(cases)
    assert all(case['expected_behavior'] in {'answer', 'abstain'} for case in cases)
    assert all(case['expected_intent'] for case in cases)
    assert all('expected_qualifier' in case for case in cases)


def test_fixture_is_evaluation_only_and_has_no_production_mappings():
    payload = json.loads(FIXTURE.read_text(encoding='utf-8'))
    serialized = json.dumps(payload, ensure_ascii=False)

    assert 'chunk-' not in serialized
    assert 'source_unit' not in serialized
    assert 'gold' not in serialized.casefold()


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
