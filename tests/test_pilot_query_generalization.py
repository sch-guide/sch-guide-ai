import json

import httpx
import pytest

from src.ai import (
    GROQ_REQUEST_TOKEN_BUDGET,
    FacetSlotSelectionContract,
    generate,
    prompt_messages,
)
from src.evidence import assess_evidence, build_source_unit_catalog, evidence_groups
from src.library import Chunk, Hit
from src.query import plan_query, topic_words
from tools.rag_facet_slot_evaluate import (
    MockQuota,
    facet_response,
    q002_inputs,
    settings,
)
from tools.rag_procedure_live_evaluate import pilot_corpus, retrieve

REGISTERED_SEDATION_DOCUMENTS = ({
    'id': 'sedation-guideline',
    'document_name': '실무지침서_진정간호.pdf',
    'title': '진정간호',
},)
NEW_GENERALIZATION_CASES = (
    ('진정간호 절차는?', 'procedure'),
    ('진정 절차는?', 'procedure'),
    ('진정절차 알려줘', 'procedure'),
    ('진정절차에 대해 알려줘', 'procedure'),
    ('진정간호 방법 알려줘', 'procedure'),
    ('진정은 어떻게 진행해?', 'procedure'),
    ('진정 시행 순서는?', 'procedure'),
    ('진정목적 알려줘', 'purpose'),
    ('진정 목적은?', 'purpose'),
    ('진정은 왜 시행해?', 'purpose'),
    ('진정준비사항 알려줘', 'preparation'),
    ('진정 전 준비사항은?', 'preparation'),
    ('진정 전에 뭘 확인해?', 'preparation'),
    ('진정주의사항 알려줘', 'cautions'),
    ('진정 주의사항은?', 'cautions'),
    ('진정할 때 조심할 점은?', 'cautions'),
)


def chunk(identifier, text, index=0, *, parent='', section='', title='진정간호'):
    return Chunk(
        identifier,
        'doc',
        '실무지침서_진정간호.pdf',
        1,
        title,
        section,
        None,
        text,
        index,
        parent_id=parent,
    )


@pytest.mark.parametrize(
    ('question', 'kind', 'topic'),
    [
        ('진정간호 목적 알려줘', 'purpose', ('진정간호',)),
        ('진정 간호는 왜 시행하나요?', 'purpose', ('진정간호',)),
        ('진정의 목적이 뭐야?', 'purpose', ('진정',)),
        ('진정 전에 뭘 준비해야 해?', 'preparation', ('진정',)),
        ('진정 시행 전 확인할 것은?', 'preparation', ('진정',)),
        ('진정 전 체크사항 알려줘', 'preparation', ('진정',)),
        ('진정 시 주의할 점은?', 'cautions', ('진정',)),
        ('진정간호에서 조심해야 할 것은?', 'cautions', ('진정간호',)),
        ('진정 중 안전하게 봐야 할 항목은?', 'cautions', ('진정',)),
    ],
)
def test_pilot_paraphrases_share_general_intent_and_topic(question, kind, topic):
    plan = plan_query(question)

    assert plan.kind == kind
    assert tuple(topic_words(plan)) == topic
    assert plan.domain == 'hospital'


def test_spaced_nursing_topic_and_compound_topic_have_same_plan_subject():
    compact = plan_query('진정간호 목적은?')
    spaced = plan_query('진정 간호의 목적은 무엇인가요?')

    assert topic_words(compact) == topic_words(spaced) == ['진정간호']
    assert compact.kind == spaced.kind == 'purpose'
    assert compact.focus == spaced.focus


@pytest.mark.parametrize(('question', 'kind'), NEW_GENERALIZATION_CASES)
def test_registered_topic_and_aspect_variants_share_a_canonical_hospital_subject(question, kind):
    plan = plan_query(question, documents=REGISTERED_SEDATION_DOCUMENTS)

    assert plan.kind == kind
    assert plan.domain == 'hospital'
    assert topic_words(plan) == ['진정간호']
    assert all(noise not in topic_words(plan) for noise in (
        '알려줘', '알려주세요', '대해', '무엇인가요', '뭐야', '어떻게', '진행해',
    ))


def test_attached_topic_aspect_normalization_is_not_sedation_specific():
    documents = ({
        'id': 'catheter-guideline',
        'document_name': '간호실무지침서_도뇨관관리.pdf',
        'title': '도뇨관관리',
    },)

    plan = plan_query('도뇨관관리주의사항 알려줘', documents=documents)

    assert plan.kind == 'cautions'
    assert plan.domain == 'hospital'
    assert topic_words(plan) == ['도뇨관관리']


@pytest.mark.parametrize(
    ('question', 'kind'),
    [
        ('진정시행 알려줘', 'procedure'),
        ('진정이유 알려줘', 'purpose'),
        ('진정확인 알려줘', 'preparation'),
        ('진정안전 알려줘', 'cautions'),
    ],
)
def test_all_declared_attached_aspect_families_are_classified(question, kind):
    plan = plan_query(question, documents=REGISTERED_SEDATION_DOCUMENTS)

    assert plan.kind == kind
    assert plan.domain == 'hospital'
    assert topic_words(plan) == ['진정간호']


def test_canonical_query_keeps_clinical_qualifiers_and_actions():
    documents = ({
        'id': 'line-guideline',
        'document_name': '간호실무지침서 PCN간호.pdf',
        'title': 'PCN간호',
    },)

    plan = plan_query('소아 PCN 사용 방법 알려줘', documents=documents)

    assert plan.kind == 'procedure'
    assert plan.domain == 'hospital'
    assert topic_words(plan) == ['pcn간호']
    assert '소아' in plan.expanded
    assert '사용' in plan.expanded


def test_registered_topic_matching_does_not_guess_an_ambiguous_base():
    documents = (
        {'id': 'nursing', 'document_name': '진정간호.pdf', 'title': '진정간호'},
        {'id': 'treatment', 'document_name': '진정치료.pdf', 'title': '진정치료'},
    )

    plan = plan_query('진정 절차는?', documents=documents)

    assert plan.canonical_topics == ()
    assert topic_words(plan) == ['진정']


def test_registered_topic_matching_does_not_merge_distinct_subjects():
    documents = (
        {'id': 'sedation', 'document_name': '진정간호.pdf', 'title': '진정간호'},
        {'id': 'pediatric', 'document_name': '소아간호.pdf', 'title': '소아간호'},
    )

    plan = plan_query('소아 진정 절차는?', documents=documents)

    assert plan.canonical_topics == ()


def test_out_of_scope_precedes_registered_topic_normalization():
    plan = plan_query(
        '화성 우주선의 궤도 계산 공식은?',
        documents=REGISTERED_SEDATION_DOCUMENTS,
    )

    assert plan.domain == 'out_of_scope'
    assert plan.canonical_topics == ()


def test_non_procedure_relevance_combines_document_topic_with_body_aspect():
    purpose = Hit(chunk(
        'purpose',
        '저혈압과 호흡 기능 억제 등 부작용을 예방하기 위해 상태를 평가한다.',
        section='1. 목적',
    ), .9)
    caution = Hit(chunk(
        'caution',
        '주의해야 할 주요 증상과 발생할 수 있는 합병증을 교육한다.',
        section='퇴실 시 교육',
    ), .9)

    assert assess_evidence(plan_query('진정 간호의 목적은 무엇인가요?'), [purpose]).sufficient
    assert assess_evidence(plan_query('진정간호 주의사항은?'), [caution]).sufficient


def test_context_with_same_blank_section_but_different_parent_is_not_evidence_scope():
    seed = Hit(chunk(
        'seed',
        '주의해야 할 주요 증상과 발생할 수 있는 합병증을 교육한다.',
        parent='caution',
    ), .9)
    unrelated = Hit(chunk(
        'unrelated',
        '진정 후 기록을 작성한다.',
        index=1,
        parent='other',
    ), .8, context_only=True, context_complete=False)

    assessment = assess_evidence(plan_query('진정간호 주의사항은?'), [seed, unrelated])

    assert assessment.sufficient
    assert [hit.chunk.id for hit in assessment.hits] == ['seed']


def test_independent_bullet_rows_are_selectable_but_heading_is_not():
    source = chunk(
        'rows',
        '퇴실 시 교육\nŸ 주의해야 할 주요증상 및 징후\nŸ 발생할 수 있는 합병증',
        parent='education',
    )
    groups = evidence_groups(plan_query('진정간호 주의사항은?'), [Hit(source, .9)])

    catalog = build_source_unit_catalog(groups)

    selectable = [unit.exact_text for unit in catalog if unit.selectable]
    assert selectable == ['Ÿ 주의해야 할 주요증상 및 징후', 'Ÿ 발생할 수 있는 합병증']


def test_q006_remains_out_of_scope():
    plan = plan_query('화성 우주선의 궤도 계산 공식은?')

    assert plan.domain == 'out_of_scope'
    assert not assess_evidence(plan, []).sufficient
    assert assess_evidence(plan, []).reason == 'domain_or_clarification'


@pytest.fixture(scope='module')
def pilot_runtime():
    return pilot_corpus()


def _offline_assessment(question, pilot_runtime):
    model, metadata, chunks, vectors = pilot_runtime
    plan, hits, selected, before, trace = retrieve(
        question, model, metadata, chunks, vectors,
    )
    after = assess_evidence(plan, selected) if selected else None
    return {
        'plan': plan,
        'hits': hits,
        'selected': selected,
        'before': before,
        'after': after,
        'trace': trace,
        'selected_ids': tuple(hit.chunk.id for hit in selected),
        'group_keys': tuple(group.key for group in before.groups),
    }


def test_q001_and_q004_have_identical_evidence_assessment(pilot_runtime):
    compact = _offline_assessment('진정간호 목적은?', pilot_runtime)
    spaced = _offline_assessment('진정 간호의 목적은 무엇인가요?', pilot_runtime)

    def snapshot(result):
        return (
            result['before'].sufficient,
            result['before'].reason,
            result['selected_ids'],
            result['group_keys'],
            result['after'].reason if result['after'] else None,
        )

    assert snapshot(compact) == snapshot(spaced), {
        'Q001': snapshot(compact),
        'Q004': snapshot(spaced),
    }
    assert compact['before'].sufficient
    assert compact['after'] and compact['after'].sufficient


def test_q003_is_supported_by_pre_sedation_evidence(pilot_runtime):
    result = _offline_assessment('진정 전 준비사항은?', pilot_runtime)
    selected_suffixes = {identifier.rsplit('-', 1)[-1] for identifier in result['selected_ids']}

    assert result['plan'].kind == 'preparation'
    assert result['before'].sufficient, result['before'].reason
    assert result['after'] and result['after'].sufficient, (
        result['after'].reason if result['after'] else 'not_evaluated'
    )
    assert selected_suffixes.intersection({'00015', '00016', '00020', '00023'}), (
        'pre-sedation assessment/consent evidence was not selected', selected_suffixes,
    )


def test_preparation_paraphrases_share_a_supported_evidence_core(pilot_runtime):
    questions = (
        '진정 전 준비사항은?',
        '진정 전에 뭘 준비해야 해?',
        '진정 시행 전 확인할 것은?',
        '진정 전 체크사항 알려줘',
    )
    results = [_offline_assessment(question, pilot_runtime) for question in questions]
    failures = {
        question: {
            'kind': result['plan'].kind,
            'pre': result['before'].reason,
            'post': result['after'].reason if result['after'] else 'not_evaluated',
        }
        for question, result in zip(questions, results, strict=True)
        if (result['plan'].kind != 'preparation'
            or not result['before'].sufficient
            or result['after'] is None
            or not result['after'].sufficient)
    }
    assert not failures, failures
    selected_sets = [set(result['selected_ids']) for result in results]
    shared = set.intersection(*selected_sets)
    assert shared, {'selected_evidence': [sorted(values) for values in selected_sets]}


def test_caution_paraphrases_share_safety_evidence(pilot_runtime):
    questions = (
        '진정간호 주의사항은?',
        '진정 시 주의할 점은?',
        '진정간호에서 조심해야 할 것은?',
        '진정 중 안전하게 봐야 할 항목은?',
    )
    results = [_offline_assessment(question, pilot_runtime) for question in questions]
    failures = {
        question: {
            'kind': result['plan'].kind,
            'pre': result['before'].reason,
            'post': result['after'].reason if result['after'] else 'not_evaluated',
        }
        for question, result in zip(questions, results, strict=True)
        if (result['plan'].kind != 'cautions'
            or not result['before'].sufficient
            or result['after'] is None
            or not result['after'].sufficient)
    }
    assert not failures, failures
    selected_sets = [set(result['selected_ids']) for result in results]
    shared = set.intersection(*selected_sets)
    assert any(identifier.endswith('chunk-00028') for identifier in shared), {
        'reason': 'the shared safety/caution evidence row was not retained',
        'shared': sorted(shared),
    }


@pytest.mark.parametrize(
    'question',
    [
        '진정간호 목적은?',
        '진정간호 절차는?',
        '진정 전 준비사항은?',
        '진정 간호의 목적은 무엇인가요?',
        '진정간호 주의사항은?',
        '진정간호 목적 알려줘',
        '진정 간호는 왜 시행하나요?',
        '진정의 목적이 뭐야?',
        '진정 전에 뭘 준비해야 해?',
        '진정 시행 전 확인할 것은?',
        '진정 전 체크사항 알려줘',
        '진정 시 주의할 점은?',
        '진정간호에서 조심해야 할 것은?',
        '진정 중 안전하게 봐야 할 항목은?',
    ],
)
def test_answerable_pilot_and_paraphrases_pass_pre_and_post_budget(question, pilot_runtime):
    model, metadata, chunks, vectors = pilot_runtime

    plan, hits, selected, before, trace = retrieve(
        question, model, metadata, chunks, vectors,
    )

    assert before.sufficient, before.reason
    assert selected
    assert assess_evidence(plan, selected).sufficient
    assert trace['estimated_request_tokens'] <= GROQ_REQUEST_TOKEN_BUDGET
    assert trace['request_token_headroom'] >= trace['minimum_request_token_headroom']


@pytest.mark.parametrize('question', [question for question, _ in NEW_GENERALIZATION_CASES])
def test_new_topic_aspect_variants_pass_offline_evidence_gates(question, pilot_runtime):
    result = _offline_assessment(question, pilot_runtime)

    assert result['plan'].domain == 'hospital'
    assert result['before'].sufficient, result['before'].reason
    assert result['before'].reason == 'supported'
    assert result['selected']
    assert result['after'] is not None and result['after'].sufficient
    assert result['after'].reason == 'supported'


def test_uat_compound_procedure_recovers_the_same_core_evidence_as_q002(pilot_runtime):
    baseline = _offline_assessment('진정간호 절차는?', pilot_runtime)
    uat = _offline_assessment('진정절차에 대해 알려줘', pilot_runtime)

    assert uat['plan'].kind == baseline['plan'].kind == 'procedure'
    assert topic_words(uat['plan']) == topic_words(baseline['plan']) == ['진정간호']
    assert uat['before'].reason == baseline['before'].reason == 'supported'
    assert uat['selected_ids'] == baseline['selected_ids']
    assert uat['group_keys'] == baseline['group_keys']


def _group_response(contract):
    return json.dumps({
        'group_selections': {
            slot.prompt_group_id: (
                [slot.selectable_source_unit_ids[0]] if slot.required else []
            )
            for slot in contract.slots
        },
    }, ensure_ascii=False)


@pytest.mark.parametrize(
    'question',
    [
        '진정간호 목적은?',
        '진정 전 준비사항은?',
        '진정 간호의 목적은 무엇인가요?',
        '진정간호 주의사항은?',
    ],
)
def test_non_procedure_pilot_mock_reconstructs_a_cited_answer(question, pilot_runtime):
    model, metadata, chunks, vectors = pilot_runtime
    plan, hits, _, before, _ = retrieve(question, model, metadata, chunks, vectors)
    _, _, _, contract = prompt_messages(
        plan.query,
        before.hits,
        14000,
        token_budget=GROQ_REQUEST_TOKEN_BUDGET,
        plan=plan,
        groups=before.groups,
        return_catalog=True,
        return_contract=True,
    )
    assert not isinstance(contract, FacetSlotSelectionContract)
    content = _group_response(contract)
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={
            'model': 'openai/gpt-oss-20b',
            'choices': [{
                'finish_reason': 'stop',
                'message': {'content': content, 'refusal': None},
            }],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120},
        })

    trace = {}
    answer, _ = generate(
        settings(),
        question,
        hits,
        'pilot-generalization',
        quota=MockQuota(),
        transport=httpx.MockTransport(handle),
        plan=plan,
        trace=trace,
    )

    assert len(calls) == 1
    assert answer.answerable
    assert 1 <= len(answer.statements) <= 16
    assert all(statement.evidence for statement in answer.statements)
    assert trace['citation_assessment'] == 'supported'
    assert trace['response_parse_stage'] == 'complete'


def test_q002_and_q006_mock_boundaries_remain_unchanged():
    _, hits, plan, _, _, after, _, contract, _, gold_ids, _ = q002_inputs()
    content = facet_response(contract, gold_ids)
    q002_calls = []

    def q002_handle(request):
        q002_calls.append(request)
        return httpx.Response(200, json={
            'model': 'openai/gpt-oss-20b',
            'choices': [{
                'finish_reason': 'stop',
                'message': {'content': content, 'refusal': None},
            }],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120},
        })

    answer, _ = generate(
        settings(), plan.query, hits, 'pilot-q002', quota=MockQuota(),
        transport=httpx.MockTransport(q002_handle), plan=plan,
    )
    assert len(q002_calls) == 1
    assert answer.answerable and len(answer.statements) == 14
    assert after.sufficient

    q006_calls = []
    q006_plan = plan_query('화성 우주선의 궤도 계산 공식은?')
    answer, _ = generate(
        settings(), q006_plan.query, [], 'pilot-q006', quota=MockQuota(),
        transport=httpx.MockTransport(lambda request: q006_calls.append(request)),
        plan=q006_plan,
    )
    assert not answer.answerable
    assert q006_calls == []
