"""Offline-only Groq/Gemini controlled-generation comparison harness.

This module deliberately has no HTTP client, provider SDK, API-key handling, or
live execution flag. It builds request blueprints in memory and validates only
caller-supplied Mock response envelopes through the existing fail-closed
controlled-generation boundary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass
from html import escape
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from src.controlled_generation import (
    ControlledGenerationDecision,
    build_controlled_generation_prompt,
    build_controlled_generation_schema,
    decide_controlled_generation,
)
from src.evidence import SourceUnit
from src.prompt_config import load_evaluation_prompt

ProviderName = Literal['groq', 'gemini']
MAX_TRANSMITTED_SOURCE_UNITS = 16
HARNESS_SCHEMA_VERSION = 1


class ProviderEnvelopeError(ValueError):
    """Safe reason for a malformed offline provider response envelope."""


@dataclass(frozen=True)
class ProviderBlueprint:
    provider: ProviderName
    model: str
    case_id: str
    intent: str
    payload: dict[str, Any]
    evidence_count: int
    evidence_character_count: int
    evidence_fingerprint: str
    schema_fingerprint: str
    prompt_fingerprint: str
    request_body_bytes: int
    prompt_version: str
    config_sha256: str


@dataclass(frozen=True)
class NormalizedProviderResponse:
    provider: ProviderName
    model: str
    finish_reason: str
    content: str
    usage: dict[str, int]


@dataclass(frozen=True)
class MockProviderResult:
    provider: ProviderName
    model: str
    parse_stage: str
    finish_reason: str | None
    usage: dict[str, int]
    decision: ControlledGenerationDecision
    extractive_sha256: str
    candidate_sha256: str
    actual_external_calls: int = 0


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _fingerprint_projection(value: Any) -> str:
    """Hash an in-memory value without persisting its potentially raw content."""
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    elif hasattr(value, 'model_dump') and callable(value.model_dump):
        value = value.model_dump(mode='json')
    try:
        serialized = _canonical_json(value)
    except (TypeError, ValueError):
        serialized = _canonical_json({
            'type': f'{type(value).__module__}.{type(value).__qualname__}'
        })
    return _sha256_text(serialized)


def _fingerprint_candidate(content: str) -> str:
    try:
        value = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        value = content
    return _fingerprint_projection(value)


def _request_bytes(payload: Mapping[str, Any]) -> int:
    return len(_canonical_json(payload).encode('utf-8'))


def _provider_compatible_schema(value: Any) -> Any:
    """Remove unconfirmed wire keywords while retaining server-side limits."""
    if isinstance(value, dict):
        return {
            key: _provider_compatible_schema(child)
            for key, child in value.items()
            if key not in {'minItems', 'maxItems'}
        }
    if isinstance(value, list):
        return [_provider_compatible_schema(child) for child in value]
    return deepcopy(value)


def _common_contract(
    units: tuple[SourceUnit, ...],
    *,
    config,
    intent: str,
    requested_phase: str = '',
    preserve_source_order: bool = False,
    required_coverage: Sequence[Mapping[str, Any]] = (),
) -> tuple[str, dict[str, Any], str, str, str, int]:
    if not units or len(units) > MAX_TRANSMITTED_SOURCE_UNITS:
        raise ValueError('source_unit_count')
    identifiers = [unit.source_unit_id for unit in units]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError('duplicate_source_unit_id')
    prompt = build_controlled_generation_prompt(
        units,
        config=config,
        intent=intent,
        requested_phase=requested_phase,
        preserve_source_order=preserve_source_order,
        required_coverage=required_coverage,
    )
    schema = _provider_compatible_schema(build_controlled_generation_schema(units))
    evidence_contract = [
        {'id': unit.source_unit_id, 'text': unit.exact_text} for unit in units
    ]
    evidence_fingerprint = _sha256_text(_canonical_json(evidence_contract))
    schema_fingerprint = _sha256_text(_canonical_json(schema))
    prompt_fingerprint = _sha256_text(prompt)
    character_count = sum(len(unit.exact_text) for unit in units)
    return (
        prompt,
        schema,
        evidence_fingerprint,
        schema_fingerprint,
        prompt_fingerprint,
        character_count,
    )


def build_provider_blueprints(
    *,
    case_id: str,
    intent: str,
    units: tuple[SourceUnit, ...],
    groq_model: str,
    gemini_model: str,
    requested_phase: str = '',
    preserve_source_order: bool = False,
    required_coverage: Sequence[Mapping[str, Any]] = (),
    prompt_version: str | None = None,
) -> tuple[ProviderBlueprint, ProviderBlueprint]:
    """Build equivalent in-memory provider payloads without sending them."""
    config = load_evaluation_prompt(prompt_version)
    (
        prompt,
        schema,
        evidence_fingerprint,
        schema_fingerprint,
        prompt_fingerprint,
        character_count,
    ) = _common_contract(
        units,
        config=config,
        intent=intent,
        requested_phase=requested_phase,
        preserve_source_order=preserve_source_order,
        required_coverage=required_coverage,
    )

    groq_payload = {
        'model': groq_model,
        'messages': [{'role': 'user', 'content': prompt}],
        'temperature': 0,
        'max_completion_tokens': 2048,
        'store': False,
        'response_format': {
            'type': 'json_schema',
            'json_schema': {
                'name': 'schat_controlled_generation',
                'strict': True,
                'schema': schema,
            },
        },
    }
    gemini_payload = {
        'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
        'generationConfig': {
            'temperature': 0,
            'responseFormat': {
                'text': {
                    'mimeType': 'application/json',
                    'schema': schema,
                }
            },
        },
    }

    common = {
        'case_id': case_id,
        'intent': intent,
        'evidence_count': len(units),
        'evidence_character_count': character_count,
        'evidence_fingerprint': evidence_fingerprint,
        'schema_fingerprint': schema_fingerprint,
        'prompt_fingerprint': prompt_fingerprint,
        'prompt_version': config.prompt_version,
        'config_sha256': config.config_sha256,
    }
    return (
        ProviderBlueprint(
            provider='groq',
            model=groq_model,
            payload=groq_payload,
            request_body_bytes=_request_bytes(groq_payload),
            **common,
        ),
        ProviderBlueprint(
            provider='gemini',
            model=gemini_model,
            payload=gemini_payload,
            request_body_bytes=_request_bytes(gemini_payload),
            **common,
        ),
    )


def _usage_value(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def normalize_mock_response(
    provider: ProviderName, envelope: Mapping[str, Any]
) -> NormalizedProviderResponse:
    """Normalize an already supplied Mock envelope; never fetch a response."""
    if not isinstance(envelope, Mapping):
        raise ProviderEnvelopeError('top_level')
    if provider == 'groq':
        choices = envelope.get('choices')
        if not isinstance(choices, list) or len(choices) != 1:
            raise ProviderEnvelopeError('choices')
        choice = choices[0]
        if not isinstance(choice, Mapping):
            raise ProviderEnvelopeError('choice')
        finish_reason = choice.get('finish_reason')
        if finish_reason != 'stop':
            raise ProviderEnvelopeError('finish_reason')
        message = choice.get('message')
        if not isinstance(message, Mapping) or not isinstance(message.get('content'), str):
            raise ProviderEnvelopeError('content')
        usage = envelope.get('usage')
        usage = usage if isinstance(usage, Mapping) else {}
        return NormalizedProviderResponse(
            provider='groq',
            model=str(envelope.get('model') or ''),
            finish_reason='stop',
            content=message['content'],
            usage={
                'prompt_tokens': _usage_value(usage.get('prompt_tokens')),
                'completion_tokens': _usage_value(usage.get('completion_tokens')),
                'total_tokens': _usage_value(usage.get('total_tokens')),
            },
        )
    if provider == 'gemini':
        candidates = envelope.get('candidates')
        if not isinstance(candidates, list) or len(candidates) != 1:
            raise ProviderEnvelopeError('candidates')
        candidate = candidates[0]
        if not isinstance(candidate, Mapping) or candidate.get('finishReason') != 'STOP':
            raise ProviderEnvelopeError('finish_reason')
        content = candidate.get('content')
        parts = content.get('parts') if isinstance(content, Mapping) else None
        if (
            not isinstance(parts, list)
            or len(parts) != 1
            or not isinstance(parts[0], Mapping)
            or not isinstance(parts[0].get('text'), str)
        ):
            raise ProviderEnvelopeError('content')
        usage = envelope.get('usageMetadata')
        usage = usage if isinstance(usage, Mapping) else {}
        return NormalizedProviderResponse(
            provider='gemini',
            model=str(envelope.get('modelVersion') or ''),
            finish_reason='stop',
            content=parts[0]['text'],
            usage={
                'prompt_tokens': _usage_value(usage.get('promptTokenCount')),
                'completion_tokens': _usage_value(usage.get('candidatesTokenCount')),
                'total_tokens': _usage_value(usage.get('totalTokenCount')),
            },
        )
    raise ProviderEnvelopeError('provider')


def build_deterministic_mock_responses(
    candidate: Mapping[str, Any],
    blueprints: Sequence[ProviderBlueprint],
) -> dict[ProviderName, dict[str, Any]]:
    """Wrap one caller-supplied candidate as offline envelopes without I/O."""
    content = _canonical_json(candidate)
    responses: dict[ProviderName, dict[str, Any]] = {}
    for blueprint in blueprints:
        if blueprint.provider in responses:
            raise ValueError('duplicate_provider')
        if blueprint.provider == 'groq':
            responses['groq'] = {
                'model': blueprint.model,
                'choices': [{
                    'finish_reason': 'stop',
                    'message': {'content': content},
                }],
                'usage': {
                    'prompt_tokens': 0,
                    'completion_tokens': 0,
                    'total_tokens': 0,
                },
            }
        elif blueprint.provider == 'gemini':
            responses['gemini'] = {
                'modelVersion': blueprint.model,
                'candidates': [{
                    'finishReason': 'STOP',
                    'content': {'parts': [{'text': content}]},
                }],
                'usageMetadata': {
                    'promptTokenCount': 0,
                    'candidatesTokenCount': 0,
                    'totalTokenCount': 0,
                },
            }
        else:
            raise ValueError('provider')
    return responses


def evaluate_mock_providers(
    blueprints: tuple[ProviderBlueprint, ...],
    responses: Mapping[ProviderName, Mapping[str, Any]],
    *,
    units: tuple[SourceUnit, ...],
    intent: str,
    extractive_answer: Any,
) -> tuple[MockProviderResult, ...]:
    """Validate equivalent Mock outputs without retry or publication."""
    evidence_fingerprints = {row.evidence_fingerprint for row in blueprints}
    schema_fingerprints = {row.schema_fingerprint for row in blueprints}
    prompt_fingerprints = {row.prompt_fingerprint for row in blueprints}
    prompt_versions = {row.prompt_version for row in blueprints}
    config_fingerprints = {row.config_sha256 for row in blueprints}
    if not all(len(values) == 1 for values in (
        evidence_fingerprints,
        schema_fingerprints,
        prompt_fingerprints,
        prompt_versions,
        config_fingerprints,
    )):
        raise ValueError('provider_contract_mismatch')

    results = []
    extractive_sha256 = _fingerprint_projection(extractive_answer)
    for blueprint in blueprints:
        try:
            normalized = normalize_mock_response(
                blueprint.provider, responses[blueprint.provider]
            )
        except (KeyError, ProviderEnvelopeError) as exc:
            reason = str(exc) or 'provider_response'
            decision = ControlledGenerationDecision(
                output=extractive_answer,
                publish_controlled=False,
                fallback_to_extractive=True,
                reason=f'provider_{reason}',
                retry_count=0,
            )
            results.append(MockProviderResult(
                provider=blueprint.provider,
                model=blueprint.model,
                parse_stage='response',
                finish_reason=None,
                usage={'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0},
                decision=decision,
                extractive_sha256=extractive_sha256,
                candidate_sha256=_sha256_text(f'provider_{reason}'),
            ))
            continue
        decision = decide_controlled_generation(
            normalized.content,
            units,
            intent=intent,
            extractive_answer=extractive_answer,
        )
        results.append(MockProviderResult(
            provider=blueprint.provider,
            model=normalized.model or blueprint.model,
            parse_stage='complete',
            finish_reason=normalized.finish_reason,
            usage=normalized.usage,
            decision=decision,
            extractive_sha256=extractive_sha256,
            candidate_sha256=_fingerprint_candidate(normalized.content),
        ))
    return tuple(results)


def safe_comparison_report(
    blueprints: tuple[ProviderBlueprint, ...],
    results: tuple[MockProviderResult, ...],
) -> dict[str, Any]:
    """Return persistence-safe metadata without prompt, payload, or generated text."""
    if not blueprints:
        raise ValueError('blueprints_required')
    result_by_provider = {row.provider: row for row in results}
    extractive_hashes = {row.extractive_sha256 for row in results}
    candidate_hashes = {row.candidate_sha256 for row in results}
    if len(extractive_hashes) != 1:
        raise ValueError('extractive_contract_mismatch')
    providers = []
    for blueprint in blueprints:
        result = result_by_provider[blueprint.provider]
        validated = result.decision.validated_candidate
        providers.append({
            'provider': blueprint.provider,
            'model': blueprint.model,
            'evidence_count': blueprint.evidence_count,
            'evidence_character_count': blueprint.evidence_character_count,
            'evidence_sha256': blueprint.evidence_fingerprint,
            'schema_sha256': blueprint.schema_fingerprint,
            'instruction_sha256': blueprint.prompt_fingerprint,
            'request_body_bytes': blueprint.request_body_bytes,
            'parse_stage': result.parse_stage,
            'finish_reason': result.finish_reason,
            'usage': result.usage,
            'validation_reason': result.decision.reason,
            'statement_count': len(validated.statements) if validated else 0,
            'covered_id_count': len(validated.covered_source_unit_ids) if validated else 0,
            'publish_controlled': result.decision.publish_controlled,
            'fallback_to_extractive': result.decision.fallback_to_extractive,
            'retry_count': result.decision.retry_count,
        })
    return {
        'harness_schema_version': HARNESS_SCHEMA_VERSION,
        'case_id': blueprints[0].case_id,
        'intent': blueprints[0].intent,
        'prompt_version': blueprints[0].prompt_version,
        'config_sha256': blueprints[0].config_sha256,
        'offline_only': True,
        'production_connected': False,
        'actual_external_calls': sum(row.actual_external_calls for row in results),
        'comparison': {
            'extractive_sha256': next(iter(extractive_hashes)),
            'candidate_sha256': (
                next(iter(candidate_hashes)) if len(candidate_hashes) == 1 else None
            ),
            'candidate_variant_count': len(candidate_hashes),
            'validated_candidate_available': all(
                row.decision.validated_candidate is not None for row in results
            ),
        },
        'providers': providers,
    }


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def audit_persisted_artifacts(
    output_dir: Path,
    *,
    forbidden_exact_texts: tuple[str, ...],
) -> dict[str, Any]:
    """Scan persisted evaluation artifacts and fail closed on sensitive content."""
    artifact_paths = sorted(
        path
        for path in output_dir.iterdir()
        if path.name != 'security_audit.json' and path.suffix in {'.json', '.html'}
    )
    exact_matches = 0
    raw_envelopes = 0
    full_instructions = 0
    secret_markers = 0
    secret_pattern = re.compile(
        r'(?i)(authorization\s*[:=]|bearer\s+[a-z0-9._-]{12,}|'
        r'\b(?:gsk|sk)-[a-z0-9_-]{12,}|api[_-]?key\s*[:=])'
    )
    for path in artifact_paths:
        content = path.read_text(encoding='utf-8')
        exact_matches += sum(
            1 for text in forbidden_exact_texts if text and text in content
        )
        raw_envelopes += content.count('"raw_response"')
        full_instructions += content.count('"messages"') + content.count('"contents"')
        secret_markers += len(secret_pattern.findall(content))
    audit = {
        'status': 'passed',
        'artifact_files_checked': len(artifact_paths),
        'raw_clinical_text_matches': exact_matches,
        'raw_provider_envelopes': raw_envelopes,
        'full_instruction_bodies': full_instructions,
        'secret_markers': secret_markers,
        'actual_external_calls': 0,
    }
    if any((exact_matches, raw_envelopes, full_instructions, secret_markers)):
        audit['status'] = 'failed'
        raise ValueError('artifact_security_audit')
    return audit


def _review_html(report: Mapping[str, Any]) -> str:
    rows = ''.join(
        '<tr>'
        f'<td>{escape(str(row["provider"]))}</td>'
        f'<td>{escape(str(row["model"]))}</td>'
        f'<td>{row["evidence_count"]}</td>'
        f'<td>{escape(str(row["parse_stage"]))}</td>'
        f'<td>{escape(str(row["validation_reason"]))}</td>'
        f'<td>{row["retry_count"]}</td>'
        '</tr>'
        for row in report['providers']
    )
    return f'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><title>SCHAT v1.0 provider 준비 검수</title>
<style>body{{font-family:system-ui,sans-serif;max-width:980px;margin:auto;padding:28px;color:#17324d}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccd8e0;padding:8px}}th{{background:#eaf3f8}}</style></head>
<body><h1>Provider controlled-generation offline 검수</h1>
<p>실제 외부 호출: <strong>{report['actual_external_calls']}회</strong> · production 연결: 없음</p>
<table><thead><tr><th>Provider</th><th>Model label</th><th>Evidence units</th><th>Parse</th><th>Decision</th><th>Retry</th></tr></thead><tbody>{rows}</tbody></table>
<p>병원 원문, 전체 instruction, raw response와 인증정보는 이 검수본에 저장하지 않았습니다.</p></body></html>'''


def run_offline_mock_evaluation(output_dir: Path) -> dict[str, Any]:
    """Generate deterministic synthetic artifacts with zero network capability."""
    output_dir.mkdir(parents=True, exist_ok=True)
    units = (
        SourceUnit(
            source_unit_id='su001',
            chunk_id='local-only-chunk-1',
            source_order=(1, 1),
            branch='common',
            exact_text='검증용 상태 확인 문장이다.',
            group_key='synthetic-group-1',
            required=True,
            selectable=True,
            phase='unspecified',
        ),
    )
    blueprints = build_provider_blueprints(
        case_id='OFFLINE-MOCK-001',
        intent='fact_specific',
        units=units,
        groq_model='openai/gpt-oss-20b',
        gemini_model='gemini-2.5-flash',
    )
    candidate = {
        'statements': [{
            'text': '검증용 상태 확인 문장이다.',
            'supporting_source_unit_ids': ['su001'],
        }]
    }
    responses = build_deterministic_mock_responses(candidate, blueprints)
    results = evaluate_mock_providers(
        blueprints,
        responses,
        units=units,
        intent='fact_specific',
        extractive_answer={'kind': 'synthetic-extractive-placeholder'},
    )
    report = safe_comparison_report(blueprints, results)
    manifest = {
        'harness_schema_version': HARNESS_SCHEMA_VERSION,
        'offline_only': True,
        'actual_external_calls': 0,
        'prompt_version': blueprints[0].prompt_version,
        'config_sha256': blueprints[0].config_sha256,
        'provider_labels': [row.provider for row in blueprints],
        'same_evidence_sha256': len({row.evidence_fingerprint for row in blueprints}) == 1,
        'same_schema_sha256': len({row.schema_fingerprint for row in blueprints}) == 1,
        'source_unit_limit': MAX_TRANSMITTED_SOURCE_UNITS,
        'persisted_raw_clinical_text': False,
    }
    _write_json(output_dir / 'comparison_manifest.json', manifest)
    _write_json(output_dir / 'mock_results.json', report)
    (output_dir / 'review.html').write_text(_review_html(report), encoding='utf-8')
    audit = audit_persisted_artifacts(
        output_dir,
        forbidden_exact_texts=tuple(unit.exact_text for unit in units),
    )
    _write_json(output_dir / 'security_audit.json', audit)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--output',
        type=Path,
        default=Path('workspace/RAG_실험/2026-09-18_schat-v1-provider-preparation'),
    )
    args = parser.parse_args()
    report = run_offline_mock_evaluation(args.output)
    print(json.dumps({
        'output': str(args.output),
        'providers': len(report['providers']),
        'actual_external_calls': report['actual_external_calls'],
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
