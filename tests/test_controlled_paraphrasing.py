import json

import pytest

from mvp.controlled_generation import (
    ControlledGenerationError,
    build_controlled_generation_prompt,
    build_controlled_generation_schema,
    validate_controlled_paraphrase,
)
from mvp.evidence import SourceUnit


def unit(identifier, text, *, branch='common', phase='unspecified', order=1):
    return SourceUnit(
        source_unit_id=identifier,
        chunk_id=f'chunk-{identifier}',
        source_order=(order, 1),
        branch=branch,
        exact_text=text,
        group_key=f'group-{identifier}',
        required=True,
        selectable=True,
        phase=phase,
    )


def payload(*statements):
    return json.dumps({'statements': list(statements)}, ensure_ascii=False)


def statement(text, *identifiers):
    return {'text': text, 'supporting_source_unit_ids': list(identifiers)}


def test_schema_is_closed_and_source_ids_are_request_scoped_enums():
    units = (
        unit('su001', '15분 간격으로 상태를 확인한다.'),
        unit('su002', '이상 반응이 있으면 즉시 중단한다.', order=2),
    )

    schema = build_controlled_generation_schema(units)

    assert schema['type'] == 'object'
    assert schema['additionalProperties'] is False
    item = schema['properties']['statements']['items']
    assert item['required'] == ['text', 'supporting_source_unit_ids']
    assert item['additionalProperties'] is False
    assert item['properties']['supporting_source_unit_ids']['items']['enum'] == [
        'su001', 'su002'
    ]
    encoded = json.dumps(schema, ensure_ascii=False)
    assert '15분' not in encoded
    assert 'chunk-su001' not in encoded


def test_validated_paraphrase_derives_exact_citations_without_changing_sources():
    units = (
        unit('su001', '15분 간격으로 상태를 확인한다.'),
        unit('su002', '이상 반응이 있으면 즉시 중단한다.', order=2),
    )

    result = validate_controlled_paraphrase(
        payload(
            statement('상태는 15분 간격으로 확인한다.', 'su001'),
            statement('이상 반응이 있으면 즉시 중단한다.', 'su002'),
        ),
        units,
        intent='summary',
    )

    assert [row.text for row in result.statements] == [
        '상태는 15분 간격으로 확인한다.',
        '이상 반응이 있으면 즉시 중단한다.',
    ]
    assert result.statements[0].citations[0].chunk_id == 'chunk-su001'
    assert result.statements[0].citations[0].quote == units[0].exact_text
    assert result.covered_source_unit_ids == ('su001', 'su002')


@pytest.mark.parametrize(
    ('candidate', 'reason'),
    [
        (payload(statement('상태를 확인한다.', 'su999')), 'unknown_evidence_id'),
        (payload(statement('20분 간격으로 상태를 확인한다.', 'su001')), 'unsupported_number_or_unit'),
        (payload(statement('상태를 확인하지 않는다.', 'su001')), 'negation_changed'),
        (payload(statement('상태를 확인한다.', 'su001', 'su001')), 'duplicate_evidence_id'),
    ],
)
def test_validator_fails_closed_for_unsupported_or_invalid_output(candidate, reason):
    units = (unit('su001', '15분 간격으로 상태를 확인한다.'),)
    with pytest.raises(ControlledGenerationError, match=reason):
        validate_controlled_paraphrase(candidate, units, intent='fact')


def test_validator_rejects_evidence_coverage_branch_and_phase_mixing():
    adult = unit('su001', '성인 상태를 확인한다.', branch='adult', phase='during')
    pediatric = unit(
        'su002', '소아 상태를 확인한다.', branch='pediatric', phase='during', order=2
    )
    after = unit('su003', '회복 후 상태를 확인한다.', branch='adult', phase='after', order=3)

    with pytest.raises(ControlledGenerationError, match='branch_mixing'):
        validate_controlled_paraphrase(
            payload(statement('성인과 소아 상태를 확인한다.', 'su001', 'su002')),
            (adult, pediatric),
            intent='summary',
        )
    with pytest.raises(ControlledGenerationError, match='phase_mixing'):
        validate_controlled_paraphrase(
            payload(statement('시행 중과 회복 후 상태를 확인한다.', 'su001', 'su003')),
            (adult, after),
            intent='procedure',
        )
    with pytest.raises(ControlledGenerationError, match='evidence_coverage'):
        validate_controlled_paraphrase(
            payload(statement('성인 상태를 확인한다.', 'su001')),
            (adult, pediatric),
            intent='summary',
        )


def test_prompt_assigns_selection_not_answerability_and_has_safety_contract():
    units = (unit('su001', '15분 간격으로 상태를 확인한다.'),)

    prompt = build_controlled_generation_prompt(units, intent='fact')

    assert 'supporting_source_unit_ids' in prompt
    assert 'number' in prompt.lower()
    assert 'negation' in prompt.lower()
    assert 'branch' in prompt.lower()
    assert 'answerable' not in prompt.lower()
    assert units[0].exact_text in prompt
