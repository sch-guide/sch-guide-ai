import json
import socket

import pytest

from mvp.evidence import SourceUnit
from tools.provider_controlled_generation_evaluate import (
    audit_persisted_artifacts,
    build_provider_blueprints,
    evaluate_mock_providers,
    normalize_mock_response,
    run_offline_mock_evaluation,
    safe_comparison_report,
)


def unit(identifier, text, *, order=1):
    return SourceUnit(
        source_unit_id=identifier,
        chunk_id=f'private-chunk-{identifier}',
        source_order=(order, 1),
        branch='common',
        exact_text=text,
        group_key=f'group-{identifier}',
        required=True,
        selectable=True,
        phase='unspecified',
    )


def candidate():
    return {
        'statements': [
            {
                'text': '상태는 15분 간격으로 확인한다.',
                'supporting_source_unit_ids': ['su001'],
            },
            {
                'text': '이상 반응이 있으면 즉시 중단한다.',
                'supporting_source_unit_ids': ['su002'],
            },
        ]
    }


def schema_keywords(value):
    if isinstance(value, dict):
        return set(value) | {
            key for child in value.values() for key in schema_keywords(child)
        }
    if isinstance(value, list):
        return {key for child in value for key in schema_keywords(child)}
    return set()


def test_blueprints_share_one_evidence_contract_without_private_metadata():
    units = (
        unit('su001', '15분 간격으로 상태를 확인한다.'),
        unit('su002', '이상 반응이 있으면 즉시 중단한다.', order=2),
    )

    blueprints = build_provider_blueprints(
        case_id='LIVE-PREP-001',
        intent='summary',
        units=units,
        groq_model='openai/gpt-oss-20b',
        gemini_model='gemini-2.5-flash',
    )

    assert tuple(row.provider for row in blueprints) == ('groq', 'gemini')
    assert len({row.evidence_fingerprint for row in blueprints}) == 1
    assert len({row.schema_fingerprint for row in blueprints}) == 1
    assert len({row.prompt_fingerprint for row in blueprints}) == 1
    assert all(row.evidence_count == 2 for row in blueprints)

    groq, gemini = blueprints
    assert groq.payload['response_format']['type'] == 'json_schema'
    assert groq.payload['response_format']['json_schema']['strict'] is True
    assert gemini.payload['generationConfig']['responseFormat']['text'][
        'mimeType'
    ] == 'application/json'
    groq_schema = groq.payload['response_format']['json_schema']['schema']
    gemini_schema = gemini.payload['generationConfig']['responseFormat']['text'][
        'schema'
    ]
    assert groq_schema == gemini_schema
    assert {'minItems', 'maxItems'} & schema_keywords(groq_schema) == set()

    encoded = json.dumps([row.payload for row in blueprints], ensure_ascii=False)
    assert units[0].exact_text in encoded
    assert 'private-chunk-su001' not in encoded
    assert 'document_name' not in encoded
    assert 'page' not in encoded
    assert 'api_key' not in encoded.lower()
    assert 'authorization' not in encoded.lower()
    assert 'tools' not in encoded.lower()


def test_required_coverage_strengthens_instruction_without_changing_wire_schema():
    units = (unit('su001', '수혈 시작 전 동의서를 확인한다.'),)
    baseline = build_provider_blueprints(
        case_id='LIVE-COVERAGE-BASELINE',
        intent='preparation',
        units=units,
        groq_model='openai/gpt-oss-20b',
        gemini_model='gemini-2.5-flash',
        requested_phase='before',
        preserve_source_order=True,
    )[0]
    strengthened = build_provider_blueprints(
        case_id='LIVE-COVERAGE-REQUIRED',
        intent='preparation',
        units=units,
        groq_model='openai/gpt-oss-20b',
        gemini_model='gemini-2.5-flash',
        requested_phase='before',
        preserve_source_order=True,
        required_coverage=(
            {
                'slot_id': 'required_qualifier_001',
                'category': 'qualifier',
                'supporting_source_unit_ids': ['su001'],
            },
        ),
    )[0]

    prompt = strengthened.payload['messages'][0]['content']
    assert 'required_qualifier_001 [qualifier] -> su001' in prompt
    assert 'Each required coverage slot' in prompt
    assert strengthened.schema_fingerprint == baseline.schema_fingerprint


def test_provider_envelopes_normalize_to_the_same_candidate():
    content = json.dumps(candidate(), ensure_ascii=False)
    groq = {
        'model': 'openai/gpt-oss-20b',
        'choices': [
            {
                'finish_reason': 'stop',
                'message': {'content': content},
            }
        ],
        'usage': {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30},
    }
    gemini = {
        'modelVersion': 'gemini-2.5-flash',
        'candidates': [
            {
                'finishReason': 'STOP',
                'content': {'parts': [{'text': content}]},
            }
        ],
        'usageMetadata': {
            'promptTokenCount': 10,
            'candidatesTokenCount': 20,
            'totalTokenCount': 30,
        },
    }

    groq_normalized = normalize_mock_response('groq', groq)
    gemini_normalized = normalize_mock_response('gemini', gemini)

    assert groq_normalized.content == gemini_normalized.content == content
    assert groq_normalized.finish_reason == gemini_normalized.finish_reason == 'stop'
    assert groq_normalized.usage == gemini_normalized.usage == {
        'prompt_tokens': 10,
        'completion_tokens': 20,
        'total_tokens': 30,
    }


def test_same_mock_candidate_uses_existing_fail_closed_validator():
    units = (
        unit('su001', '15분 간격으로 상태를 확인한다.'),
        unit('su002', '이상 반응이 있으면 즉시 중단한다.', order=2),
    )
    blueprints = build_provider_blueprints(
        case_id='LIVE-PREP-001',
        intent='summary',
        units=units,
        groq_model='openai/gpt-oss-20b',
        gemini_model='gemini-2.5-flash',
    )
    content = json.dumps(candidate(), ensure_ascii=False)
    responses = {
        'groq': {
            'model': 'openai/gpt-oss-20b',
            'choices': [{'finish_reason': 'stop', 'message': {'content': content}}],
            'usage': {},
        },
        'gemini': {
            'modelVersion': 'gemini-2.5-flash',
            'candidates': [
                {'finishReason': 'STOP', 'content': {'parts': [{'text': content}]}}
            ],
            'usageMetadata': {},
        },
    }
    extractive = object()

    results = evaluate_mock_providers(
        blueprints,
        responses,
        units=units,
        intent='summary',
        extractive_answer=extractive,
    )

    assert len(results) == 2
    assert all(row.parse_stage == 'complete' for row in results)
    assert all(row.decision.output is extractive for row in results)
    assert all(row.decision.publish_controlled is False for row in results)
    assert all(row.decision.fallback_to_extractive is True for row in results)
    assert all(row.decision.reason == 'semantic_support_pending' for row in results)
    assert all(row.actual_external_calls == 0 for row in results)


def test_server_keeps_statement_limit_when_wire_schema_omits_cardinality_keywords():
    units = (unit('su001', '상태를 확인한다.'),)
    blueprints = build_provider_blueprints(
        case_id='LIVE-PREP-LIMIT',
        intent='fact',
        units=units,
        groq_model='openai/gpt-oss-20b',
        gemini_model='gemini-2.5-flash',
    )
    content = json.dumps(
        {
            'statements': [
                {
                    'text': '상태를 확인한다.',
                    'supporting_source_unit_ids': ['su001'],
                }
                for _ in range(17)
            ]
        },
        ensure_ascii=False,
    )
    responses = {
        'groq': {
            'choices': [{'finish_reason': 'stop', 'message': {'content': content}}]
        },
        'gemini': {
            'candidates': [
                {'finishReason': 'STOP', 'content': {'parts': [{'text': content}]}}
            ]
        },
    }

    results = evaluate_mock_providers(
        blueprints,
        responses,
        units=units,
        intent='fact',
        extractive_answer=object(),
    )

    assert all(row.decision.reason == 'controlled_schema' for row in results)
    assert all(row.decision.publish_controlled is False for row in results)
    assert all(row.decision.retry_count == 0 for row in results)


def test_safe_report_contains_hashes_and_counts_but_no_raw_data():
    units = (unit('su001', '15분 간격으로 상태를 확인한다.'),)
    blueprints = build_provider_blueprints(
        case_id='LIVE-PREP-001',
        intent='fact',
        units=units,
        groq_model='openai/gpt-oss-20b',
        gemini_model='gemini-2.5-flash',
    )
    content = json.dumps(
        {
            'statements': [
                {
                    'text': '상태는 15분 간격으로 확인한다.',
                    'supporting_source_unit_ids': ['su001'],
                }
            ]
        },
        ensure_ascii=False,
    )
    responses = {
        'groq': {
            'model': 'openai/gpt-oss-20b',
            'choices': [{'finish_reason': 'stop', 'message': {'content': content}}],
            'usage': {},
        },
        'gemini': {
            'modelVersion': 'gemini-2.5-flash',
            'candidates': [
                {'finishReason': 'STOP', 'content': {'parts': [{'text': content}]}}
            ],
            'usageMetadata': {},
        },
    }
    results = evaluate_mock_providers(
        blueprints,
        responses,
        units=units,
        intent='fact',
        extractive_answer=object(),
    )

    report = safe_comparison_report(blueprints, results)
    encoded = json.dumps(report, ensure_ascii=False)

    assert report['actual_external_calls'] == 0
    assert 'payload' not in encoded
    assert 'raw_response' not in encoded
    assert 'messages' not in encoded
    assert 'contents' not in encoded
    assert 'generationConfig' not in encoded
    assert '15분 간격' not in encoded
    assert 'private-chunk' not in encoded


def test_offline_evaluator_writes_raw_free_artifacts_without_network(tmp_path, monkeypatch):
    def reject_network(*_args, **_kwargs):
        raise AssertionError('offline evaluator attempted network access')

    monkeypatch.setattr(socket, 'create_connection', reject_network)

    report = run_offline_mock_evaluation(tmp_path)

    assert report['actual_external_calls'] == 0
    expected = {
        'comparison_manifest.json',
        'mock_results.json',
        'security_audit.json',
        'review.html',
    }
    assert {path.name for path in tmp_path.iterdir()} == expected
    combined = '\n'.join(
        path.read_text(encoding='utf-8')
        for path in tmp_path.iterdir()
        if path.suffix in {'.json', '.html'}
    )
    assert 'synthetic evidence' not in combined
    assert 'raw_response' not in combined
    assert 'api_key' not in combined.lower()


def test_artifact_audit_rejects_exact_source_text(tmp_path):
    source_text = '외부 저장이 금지된 정확한 SourceUnit 원문'
    (tmp_path / 'unsafe.json').write_text(
        json.dumps({'value': source_text}, ensure_ascii=False),
        encoding='utf-8',
    )

    with pytest.raises(ValueError, match='artifact_security_audit'):
        audit_persisted_artifacts(
            tmp_path,
            forbidden_exact_texts=(source_text,),
        )
