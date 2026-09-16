import hashlib
import itertools
import json
import math
from collections import Counter
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import httpx
import pytest
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as JSONSchemaError

from mvp.ai import (
    AI_VERSION,
    GROQ_REQUEST_TOKEN_BUDGET,
    OUTPUT_LIMIT,
    PROMPT_EVIDENCE_SCHEMA_VERSION,
    RESPONSE_SELECTION_SCHEMA_VERSION,
    SOURCE_UNIT_SYSTEM,
    Answer,
    answer_text,
    build_facet_selection_contract,
    build_selection_contract,
    generate,
    groq_answer_json_schema,
    prompt_messages,
    validate_source_unit_selection,
)
from mvp.evidence import (
    answer_coverage,
    assess_evidence,
    build_source_unit_catalog,
    evidence_groups,
    is_broad_procedure,
    procedure_answer_requirement,
    procedure_coverage,
    source_sentences,
)
from mvp.library import NO_GUIDELINE, Chunk, Hit
from mvp.query import plan_query
from mvp.settings import GuideError, Settings
from tools.rag_phase2_evaluate import _load_q002
from tools.rag_source_unit_evaluate import _safe_group_selection_counts


class MockQuota:
    def __init__(self):
        self.reservations = []

    def reserve(self, settings, user_id, tokens):
        self.reservations.append(tokens)
        return 'source-unit-reservation'

    def settle(self, identifier, usage):
        pass

    def cancel(self, identifier):
        pass


def groq_settings():
    return Settings(
        llm_provider='groq_free', llm_key='fixture', llm_model='openai/gpt-oss-20b',
        llm_approved=True, groq_free_confirmed=True,
    )


@pytest.fixture(scope='module')
def q002():
    metadata, gold, hits = _load_q002()
    plan = plan_query(gold['question'], documents=[metadata])
    assessment = assess_evidence(plan, hits)
    catalog = build_source_unit_catalog(assessment.groups)
    by_key = {(unit.chunk_id, unit.source_order[1]): unit for unit in catalog}
    required_ids = []
    required_stages = [stage for stage in gold['stages'] if stage['importance'] == 'required']
    for stage in required_stages:
        alternative = stage['source_unit_requirements'][0]
        for item in alternative['all_of']:
            unit = by_key[(item['chunk_id'], item['source_unit_position'])]
            assert hashlib.sha256(unit.exact_text.encode()).hexdigest() == item['exact_text_sha256']
            if unit.source_unit_id not in required_ids:
                required_ids.append(unit.source_unit_id)
    contract = build_selection_contract(assessment.groups, catalog)
    return plan, hits, assessment, catalog, contract, required_stages, required_ids


def response(contract, ids, *, overrides=None, extra=None):
    selections = {slot.prompt_group_id: [] for slot in contract.slots}
    slot_by_id = {
        identifier: slot.prompt_group_id
        for slot in contract.slots
        for identifier in slot.selectable_source_unit_ids
    }
    for identifier in ids:
        prompt_group_id = slot_by_id.get(identifier, contract.slots[0].prompt_group_id)
        selections[prompt_group_id].append(identifier)
    if overrides:
        selections.update(overrides)
    value = {'group_selections': selections}
    if extra:
        value.update(extra)
    return json.dumps(value, ensure_ascii=False)


def facet_contract_for(plan, assessment, catalog):
    return build_facet_selection_contract(
        plan, assessment.procedure_coverage, catalog,
    )


def facet_response(contract, ids, *, preserve_order=True):
    target = tuple(ids)
    if preserve_order:
        @lru_cache(maxsize=None)
        def visit(slot_index, introduced):
            if slot_index == len(contract.slots):
                return () if introduced == len(target) else None
            allowed = set(contract.slots[slot_index].eligible_source_unit_ids)
            choices = []
            if introduced < len(target) and target[introduced] in allowed:
                choices.append(target[introduced])
            choices.extend(identifier for identifier in target[:introduced] if identifier in allowed)
            for identifier in choices:
                next_introduced = introduced + int(
                    introduced < len(target) and identifier == target[introduced]
                )
                tail = visit(slot_index + 1, next_introduced)
                if tail is not None:
                    return (identifier,) + tail
            return None

        chosen = visit(0, 0)
        assert chosen is not None, 'requested IDs cannot be introduced in source order'
    else:
        matched = {}

        def place(identifier, visited):
            for slot_index, slot in enumerate(contract.slots):
                if slot_index in visited or identifier not in slot.eligible_source_unit_ids:
                    continue
                visited.add(slot_index)
                if slot_index not in matched or place(matched[slot_index], visited):
                    matched[slot_index] = identifier
                    return True
            return False

        assert all(place(identifier, set()) for identifier in target)
        chosen = tuple(
            matched.get(index) or next(
                identifier for identifier in target
                if identifier in slot.eligible_source_unit_ids
            )
            for index, slot in enumerate(contract.slots)
        )
    return json.dumps({
        'facet_selections': {
            slot.prompt_facet_id: identifier
            for slot, identifier in zip(contract.slots, chosen)
        },
    }, ensure_ascii=False)


def run_mock(plan, hits, content, *, schema_valid=False):
    calls = []
    trace = {}
    quota = MockQuota()

    def handle(request):
        calls.append(request)
        payload = json.loads(request.content)
        assert payload['response_format']['type'] == 'json_schema'
        assert payload['response_format']['json_schema']['strict'] is True
        schema = payload['response_format']['json_schema']['schema']
        Draft202012Validator.check_schema(schema)
        if schema_valid:
            Draft202012Validator(schema).validate(json.loads(content))
        assert payload['max_completion_tokens'] == OUTPUT_LIMIT
        return httpx.Response(200, json={
            'model': 'openai/gpt-oss-20b',
            'choices': [{'finish_reason': 'stop', 'message': {'content': content, 'refusal': None}}],
            'usage': {'prompt_tokens': 1000, 'completion_tokens': 100, 'total_tokens': 1100},
        })

    answer, selected = generate(
        groq_settings(), plan.query, hits, 'fixture', quota=quota,
        transport=httpx.MockTransport(handle), plan=plan, trace=trace,
    )
    return answer, selected, calls, trace, quota


def test_wrapped_bullet_segmentation_is_general_and_keeps_new_boundaries(q002):
    _, hits, _, _, _, _, _ = q002
    by_suffix = {hit.chunk.id.rsplit('-', 1)[-1]: hit.chunk.text for hit in hits}

    assert sum(len(source_sentences(hit.chunk.text)) for hit in hits) == 40
    assert len(source_sentences(by_suffix['00025'])) == 2
    assert len(source_sentences(by_suffix['00026'])) == 5
    assert len(source_sentences(by_suffix['00027'])) == 3
    assert len(source_sentences(by_suffix['00020'])) == 5
    assert '해당과 의사의 판단 하에 5분 간격으로 평가하고 기록한다.' in source_sentences(
        by_suffix['00025']
    )[0]


def test_prompt_and_answer_coverage_are_separate_and_budget_is_safe(q002):
    plan, hits, assessment, catalog, _, _, required_ids = q002
    trace = {}

    messages, selected, prompt_catalog, contract = prompt_messages(
        plan.query, assessment.hits, 14000, token_budget=GROQ_REQUEST_TOKEN_BUDGET,
        plan=plan, groups=assessment.groups, trace=trace, return_catalog=True,
        return_contract=True,
    )

    assert len(hits) == len(selected) == 12
    assert [hit.chunk.id for hit in selected] == [hit.chunk.id for hit in hits]
    assert len(assessment.procedure_coverage.input_required_unit_keys) == 23
    assert len(catalog) == len(prompt_catalog) == 40
    assert len(required_ids) == 14
    assert len(contract.slots) == 41
    assert not [slot for slot in contract.slots if not slot.eligible_source_unit_ids]
    reservation = trace['estimated_request_tokens']
    assert reservation <= 5120
    assert trace['request_token_headroom'] >= max(256, math.ceil(reservation * .08))
    envelope = json.loads(messages[1]['content'].split('Evidence groups (JSON):\n', 1)[1])
    assert envelope['schema_version'] == 6
    assert envelope['selection_policy'] == {
        'goal': 'one_eligible_id_per_required_facet',
        'maximum_distinct_total': 16,
        'same_id_across_facets': 'allowed',
        'source_order': 'required',
    }
    assert len(envelope['source_units']) == 21
    assert all(set(unit) == {'id', 'text'} for unit in envelope['source_units'])
    serialized = json.dumps(envelope, ensure_ascii=False).lower()
    assert all(term not in serialized for term in ('q002', 'gold', 'stage', 'chunk_id'))
    system = ' '.join(SOURCE_UNIT_SYSTEM.lower().split())
    assert 'return only json facet_selections' in system
    assert 'every schema facet key exactly once' in system
    assert 'reuse the same id across facets' in system
    assert 'server decides answerability' in system
    assert 'output no text, quote, chunk id, metadata' in system
    assert trace['response_schema_serialized_tokens'] > 0


def test_catalog_keeps_context_units_but_marks_headings_and_orphans_non_selectable(q002):
    _, _, _, catalog, _, _, _ = q002
    by_key = {(unit.chunk_id.rsplit('-', 1)[-1], unit.source_order[1]): unit for unit in catalog}

    assert by_key[('00013', 1)].selectable is False
    assert by_key[('00022', 1)].selectable is False
    assert by_key[('00023', 1)].selectable is False
    assert by_key[('00025', 1)].selectable is True


def test_q002_required_gold_10_of_10_fits_fourteen_ids_and_reconstructs_exactly(q002):
    plan, hits, assessment, catalog, _, stages, required_ids = q002
    contract = facet_contract_for(plan, assessment, catalog)

    answer, selected, calls, trace, quota = run_mock(
        plan, hits, facet_response(contract, required_ids), schema_valid=True
    )

    selected_set = set(required_ids)
    by_key = {(unit.chunk_id, unit.source_order[1]): unit for unit in catalog}
    recalled = sum(
        any(all(by_key[(item['chunk_id'], item['source_unit_position'])].source_unit_id
                in selected_set for item in alternative['all_of'])
            for alternative in stage['source_unit_requirements'])
        for stage in stages
    )
    by_id = {unit.source_unit_id: unit for unit in catalog}
    expected = [by_id[item] for item in required_ids]
    assert recalled == 10
    assert len(calls) == 1
    assert len(selected) == 12
    assert answer.answerable
    assert len(answer.statements) == 14
    assert [statement.text for statement in answer.statements] == [unit.exact_text for unit in expected]
    assert [statement.evidence[0].quote for statement in answer.statements] == [
        unit.exact_text for unit in expected
    ]
    assert trace['response_parse_stage'] == 'complete'
    assert trace['answer_coverage']['complete'] is True
    assert trace['response_selection_schema_version'] == 5
    assert trace['selected_facet_count'] == 41
    assert trace['selected_source_unit_count'] == 14
    assert trace['citation_assessment'] == 'supported'
    assert trace['citation_branch_metadata'] == 'server_evidence_group'
    assert quota.reservations and quota.reservations[0] < 5120


def test_provider_schema_keeps_all_facet_slots_required_and_enum_scoped(q002):
    plan, _, assessment, catalog, _, _, _ = q002
    contract = facet_contract_for(plan, assessment, catalog)
    schema = groq_answer_json_schema(contract)
    facets = schema['properties']['facet_selections']
    slots = facets['properties']

    assert len(contract.slots) == 41
    assert set(slots) == {f'f{index:02d}' for index in range(1, 42)}
    assert set(facets['required']) == set(slots)
    assert facets['additionalProperties'] is False
    for slot in contract.slots:
        assert slots[slot.prompt_facet_id] == {
            'type': 'string', 'enum': list(slot.eligible_source_unit_ids),
        }
        assert slot.eligible_source_unit_ids
    assert set(schema) == {'type', 'properties', 'required', 'additionalProperties'}


def test_provider_schema_omits_unconfirmed_constraint_keywords(q002):
    plan, _, assessment, catalog, _, _, _ = q002
    contract = facet_contract_for(plan, assessment, catalog)
    schema = groq_answer_json_schema(contract)
    keys = set()

    def collect(value):
        if isinstance(value, dict):
            keys.update(value)
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(schema)

    assert 'enum' in keys
    assert not keys.intersection({
        'anyOf', 'minItems', 'maxItems', 'const', 'oneOf', 'allOf',
        'if', 'then', 'else', 'pattern', 'uniqueItems', 'dependentSchemas',
        'propertyNames', 'minProperties', 'maxProperties',
    })


def test_missing_required_facet_property_fails_closed(q002):
    plan, hits, assessment, catalog, _, _, required_ids = q002
    contract = facet_contract_for(plan, assessment, catalog)
    value = json.loads(facet_response(contract, required_ids))
    value['facet_selections'].pop(contract.slots[0].prompt_facet_id)

    answer, _, calls, trace, _ = run_mock(plan, hits, json.dumps(value))

    assert len(calls) == 1
    assert not answer.answerable
    assert trace['validation_reason'] == 'selection_schema'


def test_allowlist_outside_id_fails_closed(q002):
    plan, hits, assessment, catalog, _, _, required_ids = q002
    contract = facet_contract_for(plan, assessment, catalog)
    value = json.loads(facet_response(contract, required_ids))
    value['facet_selections'][contract.slots[0].prompt_facet_id] = 'su999'

    answer, _, calls, trace, _ = run_mock(plan, hits, json.dumps(value))

    assert len(calls) == 1
    assert not answer.answerable
    assert trace['validation_reason'] == 'selection_wrong_facet'


def test_all_twenty_one_selectable_units_are_blocked_without_truncation(q002):
    plan, hits, assessment, catalog, _, _, _ = q002
    contract = facet_contract_for(plan, assessment, catalog)
    all_selectable_ids = [unit.source_unit_id for unit in catalog if unit.selectable]

    assert len(all_selectable_ids) == 21
    answer, _, calls, trace, _ = run_mock(
        plan, hits, facet_response(contract, all_selectable_ids, preserve_order=False),
        schema_valid=True,
    )

    assert len(calls) == 1
    assert not answer.answerable
    assert trace['validation_reason'] == 'selection_limit'
    assert trace['selected_source_unit_count'] == 21


def test_minimum_ten_distinct_ids_cover_all_facets_and_canonicalize(q002):
    plan, _, assessment, catalog, _, _, _ = q002
    contract = facet_contract_for(plan, assessment, catalog)
    minimum_ids = (
        'su002', 'su003', 'su005', 'su012', 'su017',
        'su020', 'su021', 'su024', 'su029', 'su032',
    )
    raw = facet_response(contract, minimum_ids)
    trace = {}

    selection, units = validate_source_unit_selection(
        raw, catalog, contract, assessment.procedure_coverage, plan, trace=trace,
    )

    assert len(selection.facet_selections) == 41
    assert len(units) == 10
    assert [unit.source_unit_id for unit in units] == list(minimum_ids)
    assert trace['answer_coverage']['complete'] is True


def test_q002_eleven_source_units_keep_exact_identity_in_presentation_sidecar(q002):
    plan, hits, assessment, catalog, _, _, _ = q002
    contract = facet_contract_for(plan, assessment, catalog)
    eleven_ids = (
        'su002', 'su003', 'su005', 'su006', 'su012', 'su017',
        'su020', 'su021', 'su024', 'su029', 'su032',
    )
    by_id = {unit.source_unit_id: unit for unit in catalog}

    answer, _, calls, trace, _ = run_mock(
        plan, hits, facet_response(contract, eleven_ids), schema_valid=True,
    )

    assert len(calls) == 1
    assert trace['selected_source_unit_count'] == 11
    assert len(answer.statements) == 11
    assert [item.source_unit_id for item in answer.presentation.statements] == list(eleven_ids)
    assert [item.statement_index for item in answer.presentation.statements] == list(range(11))
    assert [statement.text for statement in answer.statements] == [
        by_id[identifier].exact_text for identifier in eleven_ids
    ]
    assert [statement.evidence[0].quote for statement in answer.statements] == [
        by_id[identifier].exact_text for identifier in eleven_ids
    ]
    assert [statement.evidence[0].chunk_id for statement in answer.statements] == [
        by_id[identifier].chunk_id for identifier in eleven_ids
    ]
    assert [item.source_order for item in answer.presentation.statements] == sorted(
        item.source_order for item in answer.presentation.statements
    )


def test_seventeen_distinct_ids_fail_selection_limit(q002):
    plan, hits, assessment, catalog, _, _, required_ids = q002
    contract = facet_contract_for(plan, assessment, catalog)
    by_id = {unit.source_unit_id: unit for unit in catalog}
    extras = [
        unit.source_unit_id for unit in catalog
        if unit.selectable and unit.source_unit_id not in required_ids
    ][:3]
    seventeen = sorted(required_ids + extras, key=lambda identifier: by_id[identifier].source_order)
    answer, _, calls, trace, _ = run_mock(
        plan, hits, facet_response(contract, seventeen, preserve_order=False),
        schema_valid=True,
    )

    assert len(set(seventeen)) == 17
    assert len(calls) == 1
    assert not answer.answerable
    assert trace['validation_reason'] == 'selection_limit'
    assert trace['selected_source_unit_count'] == 17


def test_live_group_count_observer_keeps_counts_without_source_unit_ids(q002):
    _, _, _, _, contract, _, required_ids = q002
    raw = response(contract, required_ids)

    rows = _safe_group_selection_counts(raw, contract)

    assert [row['selected_count'] for row in rows] == [1, 1, 1, 6, 5]
    assert all(set(row) == {
        'prompt_group_id', 'branch', 'required', 'selected_count'
    } for row in rows)
    serialized_rows = json.dumps(rows, ensure_ascii=False)
    assert all(identifier not in serialized_rows for identifier in required_ids)


@pytest.mark.parametrize('mutation,reason', [
    ('unknown', 'selection_unknown_id'),
    ('duplicate', 'selection_duplicate_id'),
    ('non_selectable', 'selection_non_selectable'),
    ('too_many', 'selection_limit'),
    ('wrong_group', 'selection_wrong_group'),
    ('group_reversed', 'selection_source_order'),
    ('provider_answerable', 'selection_schema'),
    ('extra_text', 'selection_schema'),
])
def test_invalid_group_selections_fail_closed(q002, mutation, reason):
    plan, hits, assessment, catalog, contract, _, required_ids = q002
    ids = list(required_ids)
    overrides = {}
    extra = None
    if mutation == 'unknown':
        overrides[contract.slots[0].prompt_group_id] = ['su999']
    elif mutation == 'duplicate':
        slot = next(item for item in contract.slots if ids[0] in item.selectable_source_unit_ids)
        overrides[slot.prompt_group_id] = [ids[0], ids[0]]
    elif mutation == 'non_selectable':
        unit = next(unit for unit in catalog if not unit.selectable)
        slot = next(item for item in contract.slots if item.group_key == unit.group_key)
        overrides[slot.prompt_group_id] = [unit.source_unit_id]
    elif mutation == 'too_many':
        ids = [unit.source_unit_id for unit in catalog if unit.selectable][:17]
    elif mutation == 'wrong_group':
        source_slot = next(
            slot for slot in contract.slots
            if len([item for item in ids if item in slot.selectable_source_unit_ids]) >= 2
        )
        source_values = [item for item in ids if item in source_slot.selectable_source_unit_ids]
        moved = source_values.pop()
        target_slot = next(slot for slot in contract.slots if slot != source_slot)
        target_values = [item for item in ids if item in target_slot.selectable_source_unit_ids]
        overrides[source_slot.prompt_group_id] = source_values
        overrides[target_slot.prompt_group_id] = target_values + [moved]
    elif mutation == 'group_reversed':
        slot = next(item for item in contract.slots if len(item.selectable_source_unit_ids) >= 2)
        chosen = [item for item in ids if item in slot.selectable_source_unit_ids]
        if len(chosen) < 2:
            chosen = list(slot.selectable_source_unit_ids[:2])
        overrides[slot.prompt_group_id] = list(reversed(chosen))
    elif mutation == 'provider_answerable':
        extra = {'answerable': False}
    elif mutation == 'extra_text':
        extra = {'statement': {'text': 'forbidden'}}

    trace = {}
    with pytest.raises(GuideError, match='AI_EVIDENCE'):
        validate_source_unit_selection(
            response(contract, ids, overrides=overrides, extra=extra),
            catalog, contract, assessment.procedure_coverage, plan, trace=trace,
        )
    assert trace['validation_reason'] == reason


def test_global_source_order_is_checked_without_server_reordering(q002):
    plan, _, assessment, catalog, _, _, required_ids = q002
    contract = facet_contract_for(plan, assessment, catalog)
    value = json.loads(facet_response(contract, required_ids))
    second = contract.slots[1]
    third = contract.slots[2]
    later = second.eligible_source_unit_ids[-1]
    earlier = value['facet_selections'][third.prompt_facet_id]
    by_id = {unit.source_unit_id: unit for unit in catalog}
    assert by_id[later].source_order > by_id[earlier].source_order
    value['facet_selections'][second.prompt_facet_id] = later
    trace = {}

    with pytest.raises(GuideError, match='AI_EVIDENCE'):
        validate_source_unit_selection(
            json.dumps(value), catalog, contract,
            assessment.procedure_coverage, plan, trace=trace,
        )

    assert trace['validation_reason'] == 'selection_source_order'


def test_prompt_coverage_and_selection_contract_drift_fails_closed(q002):
    plan, _, assessment, catalog, _, _, required_ids = q002
    contract = facet_contract_for(plan, assessment, catalog)
    drifted = replace(
        assessment.procedure_coverage,
        required_group_keys=assessment.procedure_coverage.required_group_keys[:-1],
    )
    trace = {}

    with pytest.raises(GuideError, match='AI_EVIDENCE'):
        validate_source_unit_selection(
            facet_response(contract, required_ids), catalog, contract, drifted, plan, trace=trace,
        )

    assert trace['validation_reason'] == 'selection_contract_drift'


def test_empty_facet_selection_object_fails_closed(q002):
    plan, hits, _, _, _, _, _ = q002

    answer, _, calls, trace, _ = run_mock(
        plan, hits, json.dumps({'facet_selections': {}}),
    )

    assert len(calls) == 1
    assert not answer.answerable
    assert trace['validation_reason'] == 'selection_schema'


def test_reconstruction_still_fails_closed_on_bad_citation(q002, monkeypatch):
    import mvp.ai as ai_module

    plan, hits, assessment, catalog, _, _, required_ids = q002
    contract = facet_contract_for(plan, assessment, catalog)
    original = ai_module.reconstruct_source_unit_answer

    def bad_reconstruction(selection, units, current_plan, conflicts):
        answer = original(selection, units, current_plan, conflicts)
        statements = list(answer.statements)
        first = statements[0]
        statements[0] = first.model_copy(update={
            'evidence': [first.evidence[0].model_copy(update={'chunk_id': 'unknown-chunk'})]
        })
        return answer.model_copy(update={'statements': statements})

    monkeypatch.setattr(ai_module, 'reconstruct_source_unit_answer', bad_reconstruction)
    answer, _, calls, trace, _ = run_mock(plan, hits, facet_response(contract, required_ids))

    assert len(calls) == 1
    assert not answer.answerable
    assert trace['llm_error_code'] == 'AI_EVIDENCE'
    assert trace['validation_reason'] == 'citation'


def test_selection_schema_forbids_model_generated_answer_fields_and_versions_are_current(q002):
    plan, _, assessment, catalog, _, _, required_ids = q002
    contract = facet_contract_for(plan, assessment, catalog)
    schema = groq_answer_json_schema(contract)
    valid = json.loads(facet_response(contract, required_ids))

    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    validator.validate(valid)
    required_slot = contract.slots[0]
    missing_required = json.loads(json.dumps(valid))
    missing_required['facet_selections'].pop(required_slot.prompt_facet_id)
    with pytest.raises(JSONSchemaError):
        validator.validate(missing_required)
    wrong_slot = json.loads(json.dumps(valid))
    wrong_slot['facet_selections'][required_slot.prompt_facet_id] = 'su999'
    with pytest.raises(JSONSchemaError):
        validator.validate(wrong_slot)
    for forbidden in ('text', 'quote', 'chunk_id', 'branch', 'phase', 'action',
                      'source_order', 'label'):
        invalid = dict(valid)
        invalid[forbidden] = 'forbidden'
        with pytest.raises(JSONSchemaError):
            validator.validate(invalid)
    extra_facet = json.loads(json.dumps(valid))
    extra_facet['facet_selections']['f99'] = required_ids[0]
    with pytest.raises(JSONSchemaError):
        validator.validate(extra_facet)
    assert Answer.model_json_schema()['properties']['statements']['maxItems'] == 16
    assert AI_VERSION == 19
    assert PROMPT_EVIDENCE_SCHEMA_VERSION == 6
    assert RESPONSE_SELECTION_SCHEMA_VERSION == 5


def test_q006_builds_no_catalog_and_never_calls_transport():
    calls = []

    def reject(request):
        calls.append(request)
        pytest.fail('Q006 must not call Groq')

    answer, selected = generate(
        groq_settings(), '화성 우주선의 궤도 계산 공식은?', [], 'fixture',
        transport=httpx.MockTransport(reject),
    )

    assert not calls
    assert selected == []
    assert not answer.answerable
    assert answer_text(answer) == NO_GUIDELINE


def test_q002_general_facets_accept_gold_without_production_gold_ids(q002):
    plan, _, assessment, catalog, _, _, required_ids = q002
    requirement = procedure_answer_requirement(
        plan, assessment.procedure_coverage, catalog
    )
    selected = tuple(
        next(unit for unit in catalog if unit.source_unit_id == identifier)
        for identifier in required_ids
    )
    coverage = answer_coverage(plan, assessment.procedure_coverage, catalog, selected)

    assert requirement.broad_procedure
    assert requirement.capacity_valid
    assert requirement.capacity_witness_size == 11
    assert requirement.capacity_witness_size < 16 < len([
        unit for unit in catalog if unit.selectable
    ])
    assert requirement.required_phase_slots == (
        ('common', 'before'),
        ('adult', 'before'), ('adult', 'during'), ('adult', 'after'),
        ('pediatric', 'before'), ('pediatric', 'during'), ('pediatric', 'after'),
    )
    assert requirement.required_action_families == (
        'assessment', 'execution', 'documentation',
        'communication', 'monitoring', 'transfer',
    )
    assert coverage.complete and coverage.reason == ''
    assert set(requirement.required_phase_slots) <= set(coverage.selected_phase_slots)
    assert set(requirement.required_action_slots) <= set(coverage.selected_action_slots)
    assert set(requirement.required_action_families) == set(coverage.selected_action_families)

    production = (Path(__file__).parents[1] / 'mvp' / 'evidence.py').read_text(encoding='utf-8')
    assert 'Q002' not in production
    assert 'chunk-000' not in production
    assert 'common-order' not in production


@pytest.mark.parametrize('question,expected', [
    ('진정간호 절차는?', True),
    ('PCN irrigation 방법은?', True),
    ('진정간호 전 절차는?', False),
    ('진정간호 전·중·후 절차는?', True),
    ('진정간호 용량과 방법은?', False),
    ('진정간호 준비물은?', False),
])
def test_broad_procedure_detection_uses_query_plan_not_fixture(question, expected):
    assert is_broad_procedure(plan_query(question)) is expected


def test_every_q002_one_per_group_selection_is_blocked(q002):
    plan, _, assessment, catalog, contract, _, _ = q002
    selectable = {
        slot.group_key: tuple(
            unit for unit in catalog
            if unit.group_key == slot.group_key and unit.selectable
        )
        for slot in contract.slots
    }
    combinations = tuple(itertools.product(*(
        selectable[slot.group_key] for slot in contract.slots
    )))
    reasons = Counter()

    for units in combinations:
        trace = {}
        with pytest.raises(GuideError, match='AI_EVIDENCE'):
            validate_source_unit_selection(
                response(contract, [unit.source_unit_id for unit in units]),
                catalog, contract, assessment.procedure_coverage, plan, trace=trace,
            )
        reasons[trace['validation_reason']] += 1

    assert len(combinations) == 72
    assert reasons == {'selection_missing_phase': 72}


def test_phase_action_and_action_diversity_have_distinct_reasons(q002):
    plan, _, assessment, catalog, contract, _, required_ids = q002
    by_id = {unit.source_unit_id: unit for unit in catalog}
    gold = [by_id[identifier] for identifier in required_ids]

    adult_before = next(
        unit for unit in gold
        if unit.branch == 'adult' and unit.phase == 'before'
        and 'assessment' in unit.action_families
    )
    alternate = next(
        unit for unit in catalog
        if unit.branch == 'adult' and unit.phase == 'before' and unit.selectable
        and 'assessment' not in unit.action_families
    )
    assert alternate in gold
    phase_action_ids = [
        unit.source_unit_id for unit in gold if unit != adult_before
    ]
    trace = {}
    with pytest.raises(GuideError, match='AI_EVIDENCE'):
        validate_source_unit_selection(
            response(contract, phase_action_ids), catalog, contract,
            assessment.procedure_coverage, plan, trace=trace,
        )
    assert trace['validation_reason'] == 'selection_missing_phase_action'

    transfer = next(unit for unit in gold if 'transfer' in unit.action_families)
    diversity_ids = [unit.source_unit_id for unit in gold if unit != transfer]
    trace = {}
    with pytest.raises(GuideError, match='AI_EVIDENCE'):
        validate_source_unit_selection(
            response(contract, diversity_ids), catalog, contract,
            assessment.procedure_coverage, plan, trace=trace,
        )
    assert trace['validation_reason'] == 'selection_insufficient_action_diversity'


def _duplicate_chunk(identifier, document_id, text, index, parent):
    return Chunk(
        identifier, document_id, f'{document_id}.pdf', 1, '진정 지침', '진정 절차',
        None, text, index, parent_id=parent,
    )


def test_duplicate_evidence_signature_contract_is_exact_document_and_branch():
    plan = plan_query('진정간호 절차는?')
    exact = '진정 전 상태를 확인한다.'
    first = Hit(_duplicate_chunk('a', 'doc', exact, 1, 'p1'), .9, bm25_score=1)
    same = Hit(_duplicate_chunk('b', 'doc', exact, 2, 'p2'), .8, bm25_score=1)
    other_doc = Hit(_duplicate_chunk('c', 'other', exact, 3, 'p3'), .8, bm25_score=1)
    semantic = Hit(_duplicate_chunk('d', 'doc', '진정 전에 상태를 평가한다.', 4, 'p4'), .8,
                   bm25_score=1)

    assert procedure_coverage(evidence_groups(plan, [first, same])).duplicate_count == 1
    assert procedure_coverage(evidence_groups(plan, [first, other_doc])).duplicate_count == 0
    assert procedure_coverage(evidence_groups(plan, [first, semantic])).duplicate_count == 0
    assert procedure_coverage(evidence_groups(
        plan, [first, same], branch_by_chunk={'a': 'adult', 'b': 'pediatric'}
    )).duplicate_count == 0


def test_server_branch_metadata_prevents_heading_loss_false_duplicate():
    plan = plan_query('진정간호 절차는?')
    quote = '진정 전 상태를 확인한다.'
    adult = Hit(_duplicate_chunk('adult', 'doc', f'작성 방법 [성인]\n{quote}', 1, 'pa'),
                .9, bm25_score=1)
    pediatric = Hit(_duplicate_chunk('pediatric', 'doc', f'작성 방법 [소아]\n{quote}', 2, 'pp'),
                    .8, bm25_score=1)
    original_groups = evidence_groups(plan, [adult, pediatric])
    branch_by_chunk = {
        hit.chunk.id: group.branch for group in original_groups for hit in group.hits
    }
    cited = [
        Hit(replace(hit.chunk, text=quote, section=''), hit.similarity,
            bm25_score=hit.bm25_score)
        for hit in (adult, pediatric)
    ]

    assert procedure_coverage(evidence_groups(plan, cited)).duplicate_count == 1
    preserved = evidence_groups(plan, cited, branch_by_chunk=branch_by_chunk)
    assert [group.branch for group in preserved] == ['adult', 'pediatric']
    assert procedure_coverage(preserved).duplicate_count == 0
