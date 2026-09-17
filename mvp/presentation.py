"""검증된 답변의 임상 원문과 분리된 표시 전용 metadata입니다."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from mvp.ai import Answer
    from mvp.evidence import SourceUnit


_LEADING_MARKER = re.compile(
    r'^\s*(?P<marker>(?:\d+(?:\.\d+)*[.)]|[①-⑳]|[가-힣][.)]|[-•●▪◦Ÿ]))'
    r'(?=\s|[가-힣A-Za-z]|$)'
)
_BRANCH_LABELS = {
    'common': '공통',
    'adult': '성인',
    'pediatric': '소아',
    'explicit_other': '기타',
}
_PHASE_LABELS = {
    'before': '시행 전',
    'during': '시행 중',
    'after': '시행 후',
}


@dataclass(frozen=True)
class StatementPresentation:
    statement_index: int
    source_unit_id: str
    branch: str
    phase: str
    source_order: tuple[int, int]
    leading_marker: str


@dataclass(frozen=True)
class AnswerPresentation:
    statements: tuple[StatementPresentation, ...] = ()
    intent: str = ''
    answer_format: str = ''


@dataclass(frozen=True)
class ProcedureDisplayRow:
    kind: Literal['section', 'branch', 'phase', 'statement']
    label: str = ''
    statement_index: int = -1
    leading_marker: str = ''


def leading_marker(text: str) -> str:
    match = _LEADING_MARKER.match(text)
    return match.group('marker') if match else ''


def build_answer_presentation(
    answer: Answer,
    units: tuple[SourceUnit, ...],
    *,
    intent: str = '',
) -> AnswerPresentation:
    """Build a metadata-only sidecar after the reconstructed Answer was validated."""
    if not answer.answerable:
        return AnswerPresentation(intent=intent, answer_format=answer.format)
    if len(answer.statements) != len(units):
        raise ValueError('answer/source-unit presentation count mismatch')
    if any(left.source_order > right.source_order for left, right in zip(units, units[1:])):
        raise ValueError('answer/source-unit presentation order mismatch')

    items = []
    for index, (statement, unit) in enumerate(zip(answer.statements, units, strict=True)):
        if (
            statement.text != unit.exact_text
            or len(statement.evidence) != 1
            or statement.evidence[0].chunk_id != unit.chunk_id
            or statement.evidence[0].quote != unit.exact_text
        ):
            raise ValueError('answer/source-unit presentation identity mismatch')
        items.append(StatementPresentation(
            statement_index=index,
            source_unit_id=unit.source_unit_id,
            branch=unit.branch,
            phase=unit.phase,
            source_order=unit.source_order,
            leading_marker=leading_marker(unit.exact_text),
        ))
    return AnswerPresentation(tuple(items), intent=intent, answer_format=answer.format)


def procedure_display_rows(presentation: AnswerPresentation) -> tuple[ProcedureDisplayRow, ...]:
    """Insert headings while preserving the validated statement sequence exactly."""
    rows = []
    current_branch = None
    current_phase = None
    for item in presentation.statements:
        if item.branch != current_branch:
            current_branch = item.branch
            current_phase = None
            rows.append(ProcedureDisplayRow(
                'branch', _BRANCH_LABELS.get(item.branch, '기타')
            ))
        if (
            item.branch != 'common'
            and item.phase != 'unspecified'
            and item.phase != current_phase
        ):
            current_phase = item.phase
            rows.append(ProcedureDisplayRow(
                'phase', _PHASE_LABELS.get(item.phase, item.phase)
            ))
        rows.append(ProcedureDisplayRow(
            'statement', statement_index=item.statement_index,
            leading_marker=item.leading_marker,
        ))
    return tuple(rows)


def answer_display_rows(presentation: AnswerPresentation) -> tuple[ProcedureDisplayRow, ...]:
    """Build display-only headings without changing the validated statement order."""
    if presentation.answer_format == 'steps':
        return procedure_display_rows(presentation)

    rows = []
    if presentation.answer_format == 'summary' or presentation.intent in {'summary', 'synthesis'}:
        rows.append(ProcedureDisplayRow('section', '핵심 요약'))

    has_explicit_branch = any(
        item.branch not in {'common', 'unspecified', ''}
        for item in presentation.statements
    )
    current_branch = None
    current_phase = None
    for item in presentation.statements:
        if has_explicit_branch and item.branch != current_branch:
            current_branch = item.branch
            current_phase = None
            rows.append(ProcedureDisplayRow(
                'branch', _BRANCH_LABELS.get(item.branch, '기타')
            ))
        if (
            item.phase not in {'unspecified', ''}
            and item.phase != current_phase
        ):
            current_phase = item.phase
            rows.append(ProcedureDisplayRow(
                'phase', _PHASE_LABELS.get(item.phase, item.phase)
            ))
        rows.append(ProcedureDisplayRow(
            'statement', statement_index=item.statement_index,
            leading_marker=item.leading_marker,
        ))
    return tuple(rows)
