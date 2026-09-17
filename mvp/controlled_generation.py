"""Provider-neutral controlled paraphrasing contract.

This module is deliberately disconnected from the live generation path.  It
validates request-scoped evidence IDs and conservative clinical invariants so a
future provider evaluation cannot bypass the existing extractive Answer path.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, ValidationError

if TYPE_CHECKING:
    from mvp.evidence import SourceUnit


MAX_STATEMENTS = 16
MAX_EVIDENCE_PER_STATEMENT = 4

_NUMBER_UNIT = re.compile(
    r'(?<![\w.])\d+(?:[.,]\d+)?\s*'
    r'(?:%|mg|mcg|μg|µg|g|kg|ml|mL|L|cc|단위|분|시간|초|일|회|번|℃|°C)?',
    re.I,
)
_NEGATION = re.compile(r'않|아니|없|금지|말(?:고|아야|라)|해서는\s*안|하지\s*말')
_CONDITION = re.compile(
    r'경우|(?:으|이|하)면|다면|할\s*때|필요\s*시|'
    r'\d+(?:[.,]\d+)?\s*(?:%|mg|mcg|μg|µg|g|kg|ml|mL|L|cc|단위|분|시간|초|일|회|번)?\s*'
    r'(?:이상|이하|초과|미만)',
    re.I,
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


@dataclass(frozen=True)
class ValidatedControlledParaphrase:
    statements: tuple[ValidatedControlledStatement, ...]
    covered_source_unit_ids: tuple[str, ...]
    semantic_support_pending: bool = True


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
    units: tuple[SourceUnit, ...], *, intent: str
) -> str:
    """Build an in-memory prompt for later Mock/provider evaluation only."""
    evidence = '\n'.join(
        f'{unit.source_unit_id}: {unit.exact_text}' for unit in units
    )
    return (
        'Rewrite only the verified evidence into concise Korean statements. '
        f'Intent: {intent}. Return text and supporting_source_unit_ids only. '
        'Every statement must cite request IDs. Preserve every number, unit, time, '
        'condition, negation, branch, and phase. Never mix adult and pediatric evidence '
        'or evidence from different phases in one statement. Add no clinical fact.\n'
        f'VERIFIED EVIDENCE:\n{evidence}'
    )


def _normalized_numeric_tokens(text: str) -> set[str]:
    return {
        re.sub(r'\s+', '', match.group(0)).lower().replace(',', '')
        for match in _NUMBER_UNIT.finditer(text)
        if match.group(0).strip()
    }


def _marker_state(pattern: re.Pattern[str], text: str) -> bool:
    return bool(pattern.search(text))


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

        citations = tuple(
            ControlledCitation(unit.source_unit_id, unit.chunk_id, unit.exact_text)
            for unit in cited
        )
        validated.append(ValidatedControlledStatement(candidate.text, citations))
        covered.extend(identifiers)

    covered_ids = tuple(dict.fromkeys(covered))
    if require_all_evidence and set(covered_ids) != set(by_id):
        raise ControlledGenerationError('evidence_coverage')
    return ValidatedControlledParaphrase(tuple(validated), covered_ids)
