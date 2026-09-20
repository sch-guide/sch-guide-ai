"""Evaluation-only clinical action and obligation preservation checks.

The checker is intentionally independent from Production validators.  It may
only reject an evaluation candidate; it never makes an existing rejection
pass.  Returned metadata contains labels and request-local IDs, never source
or candidate text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

_ACTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("prohibit", re.compile(r"금지|해서는\s*안|하면\s*안|하지\s*말")),
    ("caution", re.compile(r"주의|유의")),
    ("stop", re.compile(r"중단|중지")),
    ("measure", re.compile(r"측정|계측")),
    ("verify", re.compile(r"확인|점검")),
    ("execute", re.compile(r"시행|수행|실시")),
    ("administer", re.compile(r"투여|투약|주입")),
    ("prepare", re.compile(r"준비|구비")),
    ("record", re.compile(r"기록|기재|작성")),
    ("collect", re.compile(r"채혈|채취")),
    ("connect", re.compile(r"연결|장착")),
    ("remove", re.compile(r"제거|발관")),
)

_IMMEDIATE = re.compile(r"즉시|지체\s*없이|바로")
_MANDATORY = re.compile(r"반드시|필수|해야\s*(?:한다|함)|하여야\s*(?:한다|함)")
_RECOMMENDED = re.compile(r"권고|권장|바람직")
_CONSIDER = re.compile(r"고려|여부\s*(?:를\s*)?검토|검토\s*(?:한다|함)")
_OPTIONAL = re.compile(r"할\s*수\s*있|가능|선택")
_NEGATED = re.compile(r"하지\s*(?:않|말)|해서는\s*안|하면\s*안|금지")
_CLAUSE_SPLIT = re.compile(r"[.!?。！？;\n]+")
_ACTION_PHRASE_BOUNDARY = re.compile(r"\S+(?:하며|하고|하여|해서)\s+|[,]\s*")
_TEMPORAL_NOUN_SUFFIX = re.compile(
    r"^\s*(?:을|를)?\s*(?:전|후|중|시)(?=\s|$|[,.)])"
)


@dataclass(frozen=True)
class ActionStrengthResult:
    passed: bool
    error_code: str | None
    related_source_unit_ids: tuple[str, ...]
    source_action_kinds: tuple[str, ...]
    candidate_action_kinds: tuple[str, ...]
    source_strengths: tuple[str, ...]
    candidate_strengths: tuple[str, ...]


@dataclass(frozen=True)
class _Signature:
    kind: str
    strength: str


def _strength(clause: str, kind: str) -> str:
    if kind == "prohibit" or _NEGATED.search(clause):
        return "prohibited"
    if _CONSIDER.search(clause):
        return "consider"
    if _RECOMMENDED.search(clause):
        return "recommended"
    if _OPTIONAL.search(clause):
        return "optional"
    if _IMMEDIATE.search(clause):
        return "immediate_required"
    if _MANDATORY.search(clause):
        return "required"
    if kind == "caution":
        return "caution"
    return "required"


def _action_phrase(clause: str, start: int, end: int) -> str:
    """Return only the coordinated phrase that contains an action match."""
    phrase_start = 0
    phrase_end = len(clause)
    for boundary in _ACTION_PHRASE_BOUNDARY.finditer(clause):
        if boundary.end() <= start:
            phrase_start = boundary.end()
            continue
        if boundary.start() >= end:
            phrase_end = boundary.start()
            break
    return clause[phrase_start:phrase_end]


def _signatures(text: str) -> tuple[_Signature, ...]:
    values: list[_Signature] = []
    for clause in (value.strip() for value in _CLAUSE_SPLIT.split(text)):
        if not clause:
            continue
        found: list[tuple[str, re.Match[str]]] = []
        for kind, pattern in _ACTION_PATTERNS:
            for match in pattern.finditer(clause):
                if not _TEMPORAL_NOUN_SUFFIX.match(clause[match.end() :]):
                    found.append((kind, match))
        # "투여를 중단한다" describes a stop action; the object noun must not
        # be misread as a second administration instruction.
        stop_phrases = {
            _action_phrase(clause, match.start(), match.end())
            for kind, match in found
            if kind == "stop"
        }
        for kind, match in found:
            phrase = _action_phrase(clause, match.start(), match.end())
            if kind == "administer" and phrase in stop_phrases:
                continue
            signature = _Signature(kind=kind, strength=_strength(phrase, kind))
            if signature not in values:
                values.append(signature)
    return tuple(values)


def _result(
    *,
    passed: bool,
    error_code: str | None,
    source_ids: tuple[str, ...],
    source: tuple[_Signature, ...],
    candidate: tuple[_Signature, ...],
) -> ActionStrengthResult:
    return ActionStrengthResult(
        passed=passed,
        error_code=error_code,
        related_source_unit_ids=source_ids,
        source_action_kinds=tuple(dict.fromkeys(value.kind for value in source)),
        candidate_action_kinds=tuple(dict.fromkeys(value.kind for value in candidate)),
        source_strengths=tuple(dict.fromkeys(value.strength for value in source)),
        candidate_strengths=tuple(dict.fromkeys(value.strength for value in candidate)),
    )


def validate_action_strength(
    *,
    statement_text: str,
    cited_sources: Mapping[str, str],
) -> ActionStrengthResult:
    """Compare candidate actions only with the request-local sources it cites."""
    source_ids = tuple(cited_sources)
    source = tuple(
        signature
        for source_text in cited_sources.values()
        for signature in _signatures(source_text)
    )
    candidate = _signatures(statement_text)

    # Absence remains the responsibility of AnswerCoverage.  This checker is
    # scoped to changed actions and changed obligation strength.
    if not candidate:
        return _result(
            passed=True,
            error_code=None,
            source_ids=source_ids,
            source=source,
            candidate=candidate,
        )

    source_kinds = {value.kind for value in source}
    if any(value.kind not in source_kinds for value in candidate):
        return _result(
            passed=False,
            error_code="evaluation_action_kind_changed",
            source_ids=source_ids,
            source=source,
            candidate=candidate,
        )

    for value in candidate:
        if not any(
            expected.kind == value.kind and expected.strength == value.strength
            for expected in source
        ):
            return _result(
                passed=False,
                error_code="evaluation_action_strength_changed",
                source_ids=source_ids,
                source=source,
                candidate=candidate,
            )

    return _result(
        passed=True,
        error_code=None,
        source_ids=source_ids,
        source=source,
        candidate=candidate,
    )
