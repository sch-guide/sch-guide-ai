"""Provider-neutral controlled paraphrasing contract.

This module is deliberately disconnected from the live generation path.  It
validates request-scoped evidence IDs and conservative clinical invariants so a
future provider evaluation cannot bypass the existing extractive Answer path.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

if TYPE_CHECKING:
    from mvp.evidence import SourceUnit


MAX_STATEMENTS = 16
MAX_EVIDENCE_PER_STATEMENT = 4

_NUMBER_UNIT = re.compile(
    r'(?<![\w.])\d+(?:[.,]\d+)?\s*'
    r'(?:(?:%|mcg|μg|µg|mg|kg|gage|gauge|g|ml|l|cc|단위|분|시간|초|일|회|번|℃|°c)'
    r'(?:\s*/\s*(?:hours?|hrs?|hr|minutes?|mins?|min|시간|분))?)?',
    re.I,
)
_PRESENTATION_ORDER_PREFIX = re.compile(
    r'''^
        \s*(?:[-*+]\s*)?
        (?:
            (?P<range>
                \d{1,3}\s*[~\-–—]\s*\d{1,3}\s*(?:단계|steps?)\s*[:：]
            )
            |
            \(\s*(?P<parenthesized>\d{1,3})\s*\)
            |
            (?:
                (?:step|단계)\s*(?P<label_first>\d{1,3})
                |
                (?P<label_last>\d{1,3})\s*(?:단계|steps?)
            )\s*[:：]
            |
            (?P<plain>\d{1,3})[.)](?!\d)
        )
        \s*
    ''',
    re.I | re.X,
)
_NEGATION = re.compile(
    r'않|아니|아닌|없|금지|말(?:고|아야|라)|해서는\s*안|하지\s*말'
)
_CONDITION = re.compile(
    r'경우|[가-힣]+(?:으|이|하|되)?면|다면|할\s*때|[가-힣]+\s*시|'
    r'\d+(?:[.,]\d+)?\s*(?:%|mg|mcg|μg|µg|g|kg|ml|mL|L|cc|단위|분|시간|초|일|회|번)?\s*'
    r'(?:이상|이하|초과|미만)',
    re.I,
)
_EXCEPTION = re.compile(r'제외|예외|다만|단\s*,')
_CASE_TARGET = re.compile(
    r'(?P<target>[가-힣A-Za-z0-9_-]+)(?:인|이\s*아닌)\s*경우'
)


class ControlledGenerationError(ValueError):
    """A fixed fail-closed reason for an unsafe generated candidate."""


class ControlledStatementPayload(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    text: str = Field(min_length=1, max_length=700)
    supporting_source_unit_ids: list[str] = Field(
        min_length=1, max_length=MAX_EVIDENCE_PER_STATEMENT
    )


class ControlledResponsePayload(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    statements: list[ControlledStatementPayload] = Field(
        min_length=1, max_length=MAX_STATEMENTS
    )


@dataclass(frozen=True)
class ControlledCitation:
    source_unit_id: str
    chunk_id: str
    quote: str


@dataclass(frozen=True)
class ValidatedControlledStatement:
    text: str
    citations: tuple[ControlledCitation, ...]
    step_order: int | None = None


@dataclass(frozen=True)
class ClinicalStatementProjection:
    """Separate presentation order from text subject to clinical invariants."""

    text: str
    step_order: int | None


@dataclass(frozen=True)
class ValidatedControlledParaphrase:
    statements: tuple[ValidatedControlledStatement, ...]
    covered_source_unit_ids: tuple[str, ...]
    semantic_support_pending: bool = True


@dataclass(frozen=True)
class ControlledGenerationDecision:
    """Publication decision that never repairs or retries clinical text."""

    output: Any
    publish_controlled: bool
    fallback_to_extractive: bool
    reason: str
    retry_count: int
    validated_candidate: ValidatedControlledParaphrase | None = None


def build_controlled_generation_schema(units: tuple[SourceUnit, ...]) -> dict:
    """Return a request-scoped closed schema without source text or chunk IDs."""
    identifiers = [unit.source_unit_id for unit in units]
    if not identifiers or len(identifiers) != len(set(identifiers)):
        raise ControlledGenerationError('invalid_evidence_catalog')
    return {
        'type': 'object',
        'properties': {
            'statements': {
                'type': 'array',
                'minItems': 1,
                'maxItems': MAX_STATEMENTS,
                'items': {
                    'type': 'object',
                    'properties': {
                        'text': {'type': 'string'},
                        'supporting_source_unit_ids': {
                            'type': 'array',
                            'minItems': 1,
                            'maxItems': MAX_EVIDENCE_PER_STATEMENT,
                            'items': {'type': 'string', 'enum': identifiers},
                        },
                    },
                    'required': ['text', 'supporting_source_unit_ids'],
                    'additionalProperties': False,
                },
            }
        },
        'required': ['statements'],
        'additionalProperties': False,
    }


def build_controlled_generation_prompt(
    units: tuple[SourceUnit, ...],
    *,
    intent: str,
    requested_phase: str = '',
    preserve_source_order: bool = False,
) -> str:
    """Build an in-memory prompt for later Mock/provider evaluation only."""
    evidence = '\n'.join(
        f'{unit.source_unit_id}: {unit.exact_text}' for unit in units
    )
    phase_contract = ''
    if requested_phase and requested_phase != 'all':
        phase_contract = (
            f'Requested workflow phase: {requested_phase}. Use only that phase. '
        )
        if requested_phase == 'before':
            phase_contract += (
                'For before/preparation requests, do not add during or after actions. '
            )
    order_contract = ''
    if preserve_source_order:
        order_contract = (
            'Keep action statements in ascending SourceUnit order. Do not reorder, '
            'merge, or move workflow steps across SourceUnits. '
        )
    return (
        'Rewrite only the verified evidence into concise Korean statements. '
        f'Intent: {intent}. Return text and supporting_source_unit_ids only. '
        f'{phase_contract}{order_contract}'
        'Do not write list numbers or step labels inside statement text; '
        'statement order is carried separately by SourceUnit order. '
        'Every statement must cite request IDs. Preserve every number, unit, time, '
        'condition, negation, branch, and phase. Never mix adult and pediatric evidence '
        'or evidence from different phases in one statement. Add no clinical fact.\n'
        f'VERIFIED EVIDENCE:\n{evidence}'
    )


def _project_clinical_statement(text: str) -> ClinicalStatementProjection:
    """Project a leading presentation marker away from clinical statement text."""
    match = _PRESENTATION_ORDER_PREFIX.match(text)
    if match is None:
        return ClinicalStatementProjection(text=text, step_order=None)
    order_value = next(
        (
            value
            for value in (
                match.group('parenthesized'),
                match.group('label_first'),
                match.group('label_last'),
                match.group('plain'),
            )
            if value is not None
        ),
        None,
    )
    return ClinicalStatementProjection(
        text=text[match.end():],
        step_order=int(order_value) if order_value is not None else None,
    )


def _clinical_numeric_text(text: str) -> str:
    return _project_clinical_statement(text).text


def _normalize_numeric_token(token: str) -> str:
    normalized = re.sub(r'\s+', '', token).casefold().replace(',', '')
    normalized = re.sub(r'(?:gage|gauge)$', 'g', normalized)
    normalized = re.sub(r'/(?:hours?|hrs?|hr|시간)$', '/hr', normalized)
    normalized = re.sub(r'/(?:minutes?|mins?|min|분)$', '/min', normalized)
    return normalized


def _normalized_numeric_tokens(text: str) -> set[str]:
    return {
        _normalize_numeric_token(match.group(0))
        for match in _NUMBER_UNIT.finditer(_clinical_numeric_text(text))
        if match.group(0).strip()
    }


def _marker_state(pattern: re.Pattern[str], text: str) -> bool:
    return bool(pattern.search(text))


def _condition_case_targets(text: str) -> set[str]:
    return {
        match.group('target').casefold()
        for match in _CASE_TARGET.finditer(text)
    }


def validate_controlled_paraphrase(
    content: str | dict,
    units: tuple[SourceUnit, ...],
    *,
    intent: str,
    require_all_evidence: bool = True,
) -> ValidatedControlledParaphrase:
    """Validate IDs and clinical invariants without altering candidate text."""
    by_id = {unit.source_unit_id: unit for unit in units}
    if not by_id or len(by_id) != len(units):
        raise ControlledGenerationError('invalid_evidence_catalog')
    try:
        raw = json.loads(content) if isinstance(content, str) else content
        parsed = ControlledResponsePayload.model_validate(raw)
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise ControlledGenerationError('controlled_schema') from exc

    covered = []
    validated = []
    for candidate in parsed.statements:
        identifiers = candidate.supporting_source_unit_ids
        if len(identifiers) != len(set(identifiers)):
            raise ControlledGenerationError('duplicate_evidence_id')
        unknown = [identifier for identifier in identifiers if identifier not in by_id]
        if unknown:
            raise ControlledGenerationError('unknown_evidence_id')
        cited = tuple(by_id[identifier] for identifier in identifiers)
        branches = {unit.branch for unit in cited if unit.branch in {'adult', 'pediatric'}}
        if len(branches) > 1:
            raise ControlledGenerationError('branch_mixing')
        phases = {
            unit.phase for unit in cited if unit.phase not in {'', 'unspecified'}
        }
        if len(phases) > 1:
            raise ControlledGenerationError('phase_mixing')

        source_text = ' '.join(unit.exact_text for unit in cited)
        if not _normalized_numeric_tokens(candidate.text).issubset(
            _normalized_numeric_tokens(source_text)
        ):
            raise ControlledGenerationError('unsupported_number_or_unit')
        if _marker_state(_NEGATION, candidate.text) != _marker_state(_NEGATION, source_text):
            raise ControlledGenerationError('negation_changed')
        if _marker_state(_CONDITION, candidate.text) != _marker_state(_CONDITION, source_text):
            raise ControlledGenerationError('condition_changed')
        if _marker_state(_EXCEPTION, candidate.text) != _marker_state(_EXCEPTION, source_text):
            raise ControlledGenerationError('condition_changed')
        source_targets = _condition_case_targets(source_text)
        candidate_targets = _condition_case_targets(candidate.text)
        if source_targets or candidate_targets:
            if source_targets != candidate_targets:
                raise ControlledGenerationError('condition_changed')

        citations = tuple(
            ControlledCitation(unit.source_unit_id, unit.chunk_id, unit.exact_text)
            for unit in cited
        )
        projection = _project_clinical_statement(candidate.text)
        validated.append(
            ValidatedControlledStatement(
                projection.text,
                citations,
                step_order=projection.step_order,
            )
        )
        covered.extend(identifiers)

    covered_ids = tuple(dict.fromkeys(covered))
    if require_all_evidence and set(covered_ids) != set(by_id):
        raise ControlledGenerationError('evidence_coverage')
    return ValidatedControlledParaphrase(tuple(validated), covered_ids)


def decide_controlled_generation(
    content: str | dict,
    units: tuple[SourceUnit, ...],
    *,
    intent: str,
    extractive_answer: Any,
) -> ControlledGenerationDecision:
    """Publish only fully verified output; otherwise retain the exact Answer.

    This function performs no provider call, retry, repair, reordering, or text
    mutation.  There is deliberately no caller-supplied bypass: publication
    remains disabled until an approved semantic validator owns that decision.
    """
    try:
        validated = validate_controlled_paraphrase(content, units, intent=intent)
    except ControlledGenerationError as exc:
        return ControlledGenerationDecision(
            output=extractive_answer,
            publish_controlled=False,
            fallback_to_extractive=True,
            reason=str(exc),
            retry_count=0,
        )
    return ControlledGenerationDecision(
        output=extractive_answer,
        publish_controlled=False,
        fallback_to_extractive=True,
        reason='semantic_support_pending',
        retry_count=0,
        validated_candidate=validated,
    )
