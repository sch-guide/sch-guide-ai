"""LLM 호출 전의 보수적 근거 검사. 유사도 점수는 정답 확률이 아닙니다."""

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from mvp.library import INTENT_TERMS, anchors, clean, compatible, has_substantive_body, term_matches

PROCEDURE_ACTION = (
    r'(?:확인|작성|시행|세척|교체|준비|연결|제거|소독|측정|주입|투여|기록|보고|표시|사용|'
    r'중단|평가|설명|동의|서명|이동|관찰|모니터링)(?:하|합|해|했|한|할|되|시)|'
    r'읽(?:고|습|는|어)|끄(?:고|는)|끕니다|누르|눌러'
)

# 같은 주제의 목적 문단만으로 용량·주기·해제 기준 등을 답하지 못하게 합니다.
ASPECTS = (
    (r'용량|투여량|세척량|몇\s*(?:mg|ml)|얼마나\s*(?:투여|주입|세척)',
     r'용량|투여량|세척량|\d+(?:\.\d+)?\s*(?:mg|mcg|μg|µg|ml|mL|cc|단위)'),
    (r'속도|몇\s*(?:방울|gtt)', r'속도|\d+(?:\.\d+)?\s*(?:ml/h|mL/h|gtt|방울)'),
    (r'주기|간격|몇\s*회|몇\s*번|얼마나\s*자주', r'주기|간격|매일|매주|\d+\s*(?:시간|분|일|회|번)'),
    (r'해제|종료\s*기준|중단\s*기준', r'해제|종료|중단|중지'),
    (r'준비물|준비할\s*물품', r'준비|물품'),
    (r'준비사항|체크\s*사항|뭘\s*준비|(?:사전|이전|전(?:에|에는|의|\s)).{0,20}(?:준비|확인)',
     r'준비|확인|평가|설명|동의|계획'),
    (r'주의사항|금기|주의할|조심|안전하게.{0,12}(?:봐야|확인|관찰)',
     r'주의|금기|금지|않|말아|해서는\s*안|관찰|모니터링|감시|증상|징후|합병증|응급'),
    (r'목적|정의|왜\s*(?:시행|수행|하|필요)', r'목적|정의|이란|란\s|예방|위해'),
    (r'방법|절차|순서|어떻게', PROCEDURE_ACTION),
)
GENERIC = INTENT_TERMS | {'교육', '안내', '지침서', '문서', '등록된', '병원', '환자', '직원',
                         '요약', '정리', '쉽게', '종합', '여러', '자료', '어떤', '뭐야', '무엇',
                         '전', '후', '교육은', '쓰는', '알려', '사용해', '확인해', '사용법',
                         '왜', '준비사항', '준비해야', '체크사항', '확인할', '주의할', '점은',
                         '조심해야', '안전하게', '봐야', '항목', '것은', '뭘'}

_ASPECT_SUPPORT = {
    'materials': r'준비|물품',
    'preparation': r'준비|확인|평가|설명|동의|계획',
    'cautions': r'주의|금기|금지|않|말아|해서는\s*안|관찰|모니터링|감시|증상|징후|합병증|응급',
    'purpose': r'목적|정의|이란|란\s|예방|위해',
    'release': r'해제|종료|중단|중지',
}


@dataclass(frozen=True)
class EvidenceGroup:
    key: str
    hits: tuple
    document_id: str
    parent_id: str
    branch: str
    source_start: int
    source_end: int
    complete: bool
    substantive: bool
    required: bool
    requirement_reason: str


@dataclass(frozen=True)
class PromptCoverage:
    required_group_keys: tuple[str, ...]
    optional_group_keys: tuple[str, ...]
    required_procedural_unit_keys: tuple[str, ...]
    optional_procedural_unit_keys: tuple[str, ...]
    required_branches: tuple[str, ...]
    source_ordered: bool
    complete: bool
    duplicate_count: int

    @property
    def input_required_unit_keys(self):
        return self.required_procedural_unit_keys

    @property
    def input_optional_unit_keys(self):
        return self.optional_procedural_unit_keys


# 기존 호출자와 trace 필드의 호환성을 유지하되 의미는 prompt 입력 coverage로 한정합니다.
ProcedureCoverage = PromptCoverage


@dataclass(frozen=True)
class SourceUnit:
    source_unit_id: str
    chunk_id: str
    source_order: tuple[int, int]
    branch: str
    exact_text: str
    group_key: str
    required: bool
    selectable: bool
    phase: str = 'unspecified'
    action_families: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProcedureAnswerRequirement:
    broad_procedure: bool
    required_group_keys: tuple[str, ...]
    required_branches: tuple[str, ...]
    required_phase_slots: tuple[tuple[str, str], ...]
    required_action_slots: tuple[tuple[str, str, str], ...]
    required_action_families: tuple[str, ...]
    minimum_action_diversity: int
    source_order_required: bool
    capacity_valid: bool
    capacity_witness_size: int


FacetKind = Literal[
    'group', 'branch', 'phase', 'phase_action', 'action_family', 'query_action'
]


@dataclass(frozen=True)
class RequiredFacet:
    kind: FacetKind
    key: tuple[str, ...]
    group_key: str = ''
    branch: str = ''
    phase: str = ''
    action_family: str = ''
    query_action: str = ''


@dataclass(frozen=True)
class AnswerCoverage:
    required_group_keys: tuple[str, ...]
    selected_group_keys: tuple[str, ...]
    required_branches: tuple[str, ...]
    selected_branches: tuple[str, ...]
    required_query_actions: tuple[str, ...]
    selected_query_actions: tuple[str, ...]
    required_phase_slots: tuple[tuple[str, str], ...]
    selected_phase_slots: tuple[tuple[str, str], ...]
    required_action_slots: tuple[tuple[str, str, str], ...]
    selected_action_slots: tuple[tuple[str, str, str], ...]
    required_action_families: tuple[str, ...]
    selected_action_families: tuple[str, ...]
    minimum_action_diversity: int
    source_ordered: bool
    complete: bool
    reason: str


@dataclass(frozen=True)
class EvidenceAssessment:
    sufficient: bool
    hits: tuple = ()
    reason: str = ''
    groups: tuple[EvidenceGroup, ...] = ()
    procedure_coverage: PromptCoverage | None = None


def _branch(text):
    markers = set(re.findall(r'\[(성인|소아)\]|^\s*(성인|소아)\s*(?:\||$)', text, re.MULTILINE))
    flattened = {marker for pair in markers for marker in pair if marker}
    if flattened == {'성인'}:
        return 'adult'
    if flattened == {'소아'}:
        return 'pediatric'
    return 'common' if not flattened else 'explicit_other'


def evidence_groups(plan, hits, branch_by_chunk=None):
    grouped, order = {}, []
    linked_to_seed = {
        identifier
        for hit in hits if not hit.context_only
        for identifier in (hit.chunk.previous_chunk_id, hit.chunk.next_chunk_id)
        if identifier
    }
    for hit in hits:
        identity = hit.chunk.parent_id or hit.chunk.id
        key = f'{hit.chunk.document_id}:{identity}'
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(hit)
    result = []
    for key in order:
        members = tuple(sorted(grouped[key], key=lambda hit: hit.chunk.index))
        text = '\n'.join(hit.chunk.text for hit in members)
        preserved = {
            branch_by_chunk.get(hit.chunk.id)
            for hit in members
            if branch_by_chunk and branch_by_chunk.get(hit.chunk.id)
        }
        branch = next(iter(preserved)) if len(preserved) == 1 else _branch(text)
        has_seed = any(not hit.context_only for hit in members)
        explicit_branch = plan.kind == 'procedure' and branch in {'adult', 'pediatric', 'explicit_other'}
        seed_neighbor = plan.kind == 'procedure' and any(
            hit.chunk.id in linked_to_seed for hit in members
        )
        required = plan.kind != 'procedure' or has_seed or explicit_branch or seed_neighbor
        reason = ('seed' if has_seed else 'explicit_branch' if explicit_branch
                  else 'seed_neighbor' if seed_neighbor else 'optional_context')
        result.append(EvidenceGroup(
            key=key, hits=members, document_id=members[0].chunk.document_id,
            parent_id=members[0].chunk.parent_id, branch=branch,
            source_start=members[0].chunk.index, source_end=members[-1].chunk.index,
            complete=all(hit.context_complete for hit in members),
            substantive=any(has_substantive_body(hit.chunk) for hit in members),
            required=required, requirement_reason=reason,
        ))
    return tuple(result)


def _procedure_unit_keys(group):
    return tuple(
        f'{hit.chunk.id}#{position}'
        for hit in group.hits
        # PromptCoverage의 기존 23-unit fingerprint는 segmentation 개선과 독립적으로 유지합니다.
        for position, sentence in enumerate(source_sentences(hit.chunk.text, join_wrapped_bullets=False), start=1)
        if re.search(PROCEDURE_ACTION, sentence)
    )


def procedure_coverage(groups):
    required = tuple(group for group in groups if group.required)
    optional = tuple(group for group in groups if not group.required)
    ordered = {}
    signatures = []
    for group in groups:
        for hit in group.hits:
            ordered.setdefault((hit.chunk.document_id, group.branch), []).append(hit.chunk.index)
            signatures.append((hit.chunk.document_id, group.branch, clean(hit.chunk.text).casefold()))
    return ProcedureCoverage(
        required_group_keys=tuple(group.key for group in required),
        optional_group_keys=tuple(group.key for group in optional),
        required_procedural_unit_keys=tuple(key for group in required for key in _procedure_unit_keys(group)),
        optional_procedural_unit_keys=tuple(key for group in optional for key in _procedure_unit_keys(group)),
        required_branches=tuple(dict.fromkeys(
            group.branch for group in required if group.branch != 'common'
        )),
        source_ordered=all(indexes == sorted(indexes) for indexes in ordered.values()),
        complete=all(group.complete for group in groups),
        duplicate_count=len(signatures) - len(set(signatures)),
    )


def required_coverage_loss(before, after):
    if before is None:
        return ''
    if after is None:
        return 'missing_evidence_group'
    if not set(before.required_group_keys).issubset(after.required_group_keys):
        return 'missing_evidence_group'
    if not set(before.required_procedural_unit_keys).issubset(after.required_procedural_unit_keys):
        return 'missing_procedure_unit'
    if not set(before.required_branches).issubset(after.required_branches):
        return 'missing_branch'
    if not after.complete:
        return 'incomplete_semantic_block'
    if not after.source_ordered:
        return 'source_order'
    return ''


def _requested_branches(question):
    """질문에 명시된 성인/소아 분기만 반환하며 누락된 분기를 추론하지 않습니다."""
    current = question.split(' / 추가 질문: ')[-1]
    branches = []
    if re.search(r'(?<![가-힣])성인(?:과|와|은|는|이|가|의|에게|[·‧/]|\s|[?!,.]|$)', current):
        branches.append('adult')
    if re.search(r'(?<![가-힣])소아(?:과|와|은|는|이|가|의|에게|[·‧/]|\s|[?!,.]|$)', current):
        branches.append('pediatric')
    return tuple(branches)


def _requested_body_support(plan):
    """구체 질문 qualifier가 실제 본문에 있어야 하는 bounded support 규칙."""
    current = plan.query.split(' / 추가 질문: ')[-1]
    patterns = []
    if re.search(r'몇\s*분|얼마나\s*자주|간격', current):
        patterns.append(r'(?:\d+(?:\.\d+)?\s*분\s*간격|간격)')
    if '동의서' in current:
        patterns.append(r'동의서')
    if re.search(r'산소\s*포화도|산소포화도|spo2', current, re.I):
        patterns.append(r'산소\s*포화도|산소포화도|SpO2|Oxymetry')
    if re.search(r'활력\s*징후|활력징후|v/s', current, re.I):
        patterns.append(r'활력\s*징후|활력징후|V/S|혈압|맥박|호흡수')
    if re.search(r'모니터링|관찰|확인|측정|평가', current):
        patterns.append(r'모니터링|관찰|확인|측정|평가')
    if re.search(r'투약|투여|약물', current):
        patterns.append(r'투약|투여|약물|약품명|용량|용법')
    if re.search(r'회복', current):
        patterns.append(r'회복')
    if re.search(r'입원실|병실|이동', current):
        patterns.extend((r'입원실|병실', r'이동|모니터링|관찰'))
    return tuple(dict.fromkeys(patterns))


def relevant_body(plan, hit):
    """제목이나 높은 벡터 점수만으로 근거를 인정하지 않습니다."""
    text = hit.chunk.text
    context = text + ' ' + hit.chunk.section
    if not compatible(plan.query, context) or not has_substantive_body(hit.chunk):
        return False
    if plan.entities:
        return bool(set(plan.entities) & set(anchors(context)))
    from mvp.query import topic_words

    words = topic_words(plan)
    if not words:
        return False
    topic_in_body = any(term_matches(word, text) for word in words)
    topic_context = ' '.join((hit.chunk.document_name, hit.chunk.title, hit.chunk.section))
    topic_in_context = any(term_matches(word, topic_context) for word in words)
    heading_context = explicit_heading_context(plan, hit.chunk)
    support = _ASPECT_SUPPORT.get(plan.kind)
    topic_supported = topic_in_body or topic_in_context or heading_context
    if support:
        # Metadata는 문서 topic만 연결한다. 요청 aspect와 구체 qualifier는 본문이 지지해야 한다.
        qualifiers = _requested_body_support(plan)
        return heading_context or (
            topic_supported
            and bool(re.search(support, text, re.I))
            and all(re.search(pattern, text, re.I) for pattern in qualifiers)
        )
    if plan.kind == 'summary':
        # 완전성은 parent context와 assess_evidence에서 별도로 검사한다.
        return topic_supported
    if plan.kind == 'comparison':
        qualifiers = _requested_body_support(plan)
        return topic_supported and all(re.search(pattern, text, re.I) for pattern in qualifiers)
    if plan.kind == 'fact':
        qualifiers = _requested_body_support(plan)
        if qualifiers:
            return topic_supported and all(re.search(pattern, text, re.I) for pattern in qualifiers)
    # Aspect가 없는 일반 fact는 제목 일치만으로 근거를 열지 않는다.
    return topic_in_body or heading_context


def explicit_heading_context(plan, chunk):
    """목적/정의 항목의 실질 본문만 문서 주제에 연결합니다. 제목만으로 허용하지 않습니다."""
    if plan.entities or not set(plan.focus).intersection({'목적', '정의'}):
        return False
    from mvp.query import topic_words

    words = topic_words(plan)
    subject = ' '.join((chunk.document_name.rsplit('.', 1)[0], chunk.title, chunk.section))
    if not words or not any(term_matches(w, subject) for w in words):
        return False
    heading = r'\s*(?:\d+(?:\.\d+)*[.)]?\s*)?(목적|정의)\s*[:：]?\s*'
    from mvp.documents import PdfPage
    from mvp.structure import semantic_blocks

    # 저장 section이 없는 구형 DB도 원문에 명시된 항목 제목만 사용할 수 있습니다.
    for text, section, _ in semantic_blocks(PdfPage(chunk.page, chunk.text, section=chunk.section), chunk.document_id):
        match = re.fullmatch(heading, section.rsplit('>', 1)[-1])
        if not match or match[1] not in plan.focus:
            continue
        body = [line for line in source_sentences(text) if not re.fullmatch(heading, line)
                and line not in {clean(chunk.title), clean(chunk.document_name)}]
        if any(len(line) >= 8 and re.search(r'[가-힣a-zA-Z]', line) for line in body):
            return True
    return False


def citation_section(chunk, quotes):
    """구버전 DB가 항목 metadata를 버린 경우에도, 원문 안의 실제 제목 범위만 복원합니다.

    인용이 여러 항목에 걸치거나 같은 문장이 다른 항목에도 있으면 문맥을 추측하지 않습니다.
    """
    from mvp.documents import PdfPage
    from mvp.structure import semantic_blocks

    blocks = list(semantic_blocks(PdfPage(chunk.page, chunk.text, section=chunk.section), chunk.document_id))
    locations = [{section for text, section, _ in blocks if clean(quote) in clean(text)} for quote in quotes]
    if not locations or any(len(found) != 1 for found in locations):
        return ''
    sections = set.union(*locations)
    return next(iter(sections)) if len(sections) == 1 else ''


def _procedure_seed(plan, hit):
    if hit.context_only or not has_substantive_body(hit.chunk):
        return False
    context = hit.chunk.text + ' ' + hit.chunk.section
    if not compatible(plan.query, context) or not re.search(PROCEDURE_ACTION, hit.chunk.text):
        return False
    return relevant_body(plan, hit) or hit.lexical > 0 or hit.bm25_score > 0


def assess_evidence(plan, hits, branch_by_chunk=None):
    if plan.domain != 'hospital' or plan.clarification:
        return EvidenceAssessment(False, reason='domain_or_clarification')
    preserved_branches = dict(branch_by_chunk or {})
    for group in evidence_groups(plan, hits, branch_by_chunk=preserved_branches):
        if group.branch == 'common':
            continue
        for hit in group.hits:
            preserved_branches.setdefault(hit.chunk.id, group.branch)
    branch_by_chunk = preserved_branches
    if plan.kind == 'procedure':
        seeds = [hit for hit in hits if _procedure_seed(plan, hit)]
        documents = {hit.chunk.document_id for hit in seeds}
        relevant = [hit for hit in hits if hit in seeds or (
            hit.chunk.document_id in documents
            and has_substantive_body(hit.chunk)
            and compatible(plan.query, hit.chunk.text + ' ' + hit.chunk.section)
            and re.search(PROCEDURE_ACTION, hit.chunk.text)
        )]
    else:
        # 완전한 parent에서 함께 회수된 본문도 topic과 요청 aspect를 직접 지지하면
        # non-procedure evidence scope를 열 수 있다. 불완전 context는 seed가 될 수 없다.
        seeds = [h for h in hits if relevant_body(plan, h)
                 and (not h.context_only or h.context_complete)]
        def scope(hit):
            if hit.chunk.parent_id:
                return hit.chunk.document_id, 'parent', hit.chunk.parent_id
            if hit.chunk.section:
                return hit.chunk.document_id, 'section', hit.chunk.section
            return hit.chunk.document_id, 'chunk', hit.chunk.id

        scopes = {scope(h) for h in seeds}
        relevant = [h for h in hits if h in seeds or (
            h.context_only and scope(h) in scopes
            and compatible(plan.query, h.chunk.text + ' ' + h.chunk.section))]
        document_order = {
            document_id: position
            for position, document_id in enumerate(dict.fromkeys(
                hit.chunk.document_id for hit in relevant
            ))
        }
        relevant.sort(key=lambda hit: (
            document_order[hit.chunk.document_id], hit.chunk.index
        ))
        # 분기를 요청하지 않은 좁은 질문은 충분한 common 근거가 있을 때
        # 성인/소아 예시 block까지 자동 required로 승격하지 않는다.
        if (plan.kind not in {'summary', 'comparison'}
                and not re.search(r'성인|소아', plan.query)):
            preview_groups = evidence_groups(plan, relevant, branch_by_chunk=branch_by_chunk)
            common_keys = {group.key for group in preview_groups if group.branch == 'common'}
            if common_keys:
                relevant = [hit for hit in relevant if (
                    f'{hit.chunk.document_id}:{hit.chunk.parent_id or hit.chunk.id}' in common_keys
                )]
    if not seeds:
        return EvidenceAssessment(False, reason='no_topic_evidence')
    groups = evidence_groups(plan, relevant, branch_by_chunk=branch_by_chunk)
    requested_branches = set(_requested_branches(plan.query))
    if len(requested_branches) == 1:
        requested_branch = next(iter(requested_branches))
        groups = tuple(
            group for group in groups
            if group.branch in {'common', requested_branch}
        )
        relevant = [hit for group in groups for hit in group.hits]
        if requested_branch not in {group.branch for group in groups}:
            return EvidenceAssessment(
                False, tuple(relevant), 'missing_requested_branch', groups,
            )
    if plan.kind == 'comparison' and requested_branches == {'adult', 'pediatric'}:
        present = {group.branch for group in groups}
        if not requested_branches.issubset(present):
            return EvidenceAssessment(
                False, tuple(relevant), 'missing_comparison_branch', groups,
            )
    coverage = procedure_coverage(groups) if plan.kind == 'procedure' else None
    if any(not group.complete for group in groups):
        return EvidenceAssessment(False, tuple(relevant), 'incomplete_semantic_block', groups, coverage)
    if coverage is not None:
        if not coverage.required_procedural_unit_keys:
            return EvidenceAssessment(False, tuple(relevant), 'missing_procedure_unit', groups, coverage)
        if not coverage.source_ordered:
            return EvidenceAssessment(False, tuple(relevant), 'procedure_source_order', groups, coverage)
        if coverage.duplicate_count:
            return EvidenceAssessment(False, tuple(relevant), 'duplicate_evidence', groups, coverage)
    bodies = '\n'.join(h.chunk.text for h in relevant)
    current = plan.query.split(' / 추가 질문: ')[-1]
    # 검색어 확장에 사용한 동의어는 원문 존재 여부를 판단할 때 재사용하지 않습니다.
    for request, support in ASPECTS:
        # 목적/정의라는 항목명은 인용할 답변 문장에 반복될 필요가 없습니다.
        # 실제 저장된 해당 항목의 메타데이터가 있을 때만 문맥을 인정합니다.
        aspect_text = bodies
        if plan.kind == 'purpose' and re.search(request, current, re.I):
            aspect_text += '\n' + '\n'.join(h.chunk.section for h in relevant
                                            if explicit_heading_context(plan, h.chunk))
        if re.search(request, current, re.I) and not re.search(support, aspect_text, re.I):
            return EvidenceAssessment(False, tuple(relevant), 'missing_requested_aspect', groups, coverage)
    if plan.entities:
        present = set().union(*(anchors(h.chunk.text + ' ' + h.chunk.section) for h in relevant))
        if not set(plan.entities).issubset(present):
            return EvidenceAssessment(False, tuple(relevant), 'missing_entity', groups, coverage)
        # PCN이라는 이름만 있고 질문의 구체적 동작(예: 세척)이 없는 경우를 차단합니다.
        for action in ('세척', 'irrigation', '소독', '교체'):
            if term_matches(action, current):
                aliases = ('세척', 'irrigation') if action in {'세척', 'irrigation'} else (action,)
                if not any(term_matches(word, bodies) for word in aliases):
                    return EvidenceAssessment(False, tuple(relevant), 'missing_action', groups, coverage)
    docs = {h.chunk.document_id for h in relevant}
    if len(docs) < plan.min_documents or not set(plan.document_ids).issubset(docs):
        return EvidenceAssessment(False, tuple(relevant), 'missing_document', groups, coverage)
    return EvidenceAssessment(True, tuple(relevant), 'supported', groups, coverage)


def source_sentences(text, *, join_wrapped_bullets=True):
    """명확한 한국어 연결어 뒤의 PDF 줄바꿈은 문장 경계가 아닙니다.

    단어·조건·부정은 그대로 두고 공백만 정규화합니다. 빈 줄/표 행/목록/항목 경계는 유지합니다.
    """
    lines = []
    bullet = r'^\s*[-•●▪Ÿ*※]'
    boundary = r'^(?:[-•●▪Ÿ*※]|[①-⑳]|\d+(?:\.\d+)*[.)]\s|[가-하]\))|[|]|[:：]$'
    continuation = r'(?:[가-힣]{2,}(?:의|을|를|와|과|은|는|가|로)|[a-zA-Z%]+(?:의|을|를|로)|(?:경우|위해|하며|하여|하고|또는|및|후|전|때|없이|않고))$'
    terminal = r'[.!?。！？:：]$'
    for raw in text.splitlines():
        line = clean(raw)
        previous = lines[-1] if lines else ''
        heading_like = bool(re.search(
            r'(?:방법|절차|목적|기준|안내|목록)(?:\s*\[[^\]]+\])?$', previous
        ))
        wrapped_bullet = (
            join_wrapped_bullets
            and re.search(bullet, previous)
            and not re.search(terminal, previous)
            and not heading_like
        )
        if (line and previous and not re.search(boundary, line)
                and '|' not in previous and not re.search(terminal, previous)
                and (re.search(continuation, previous) or wrapped_bullet)):
            lines[-1] += ' ' + line
        else:
            lines.append(line)
    return [clean(s) for s in re.split(r'(?<=[.!?。！？])(?<!\d\.)\s+|\n+', '\n'.join(lines)) if clean(s)]


def _source_unit_selectable(chunk, text, position, previous_chunk=None):
    """구조 표지는 문맥에 남기고 완전한 문장·독립 목록/표 행만 선택하게 합니다."""
    value = clean(text)
    if not value:
        return False
    normalized = value.casefold().strip(' .:：')
    structural_names = {
        clean(candidate).casefold().strip(' .:：')
        for candidate in (
            chunk.document_name.rsplit('.', 1)[0], chunk.title,
            chunk.section.rsplit('>', 1)[-1] if chunk.section else '',
        )
        if clean(candidate)
    }
    if normalized in structural_names:
        return False
    if previous_chunk is not None and position == 1:
        previous_units = source_sentences(previous_chunk.text)
        if any(clean(previous).endswith(value) for previous in previous_units):
            return False
    terminal = bool(re.search(r'[.!?。！？]$', value))
    list_row = bool(
        re.search(r'^\s*[-•●▪Ÿ*※]\s*\S.{3,}', value)
        and not re.search(r'[→⇒]', value)
    )
    if not terminal and not list_row:
        return False
    if re.fullmatch(r'\s*(?:[①-⑳]|\d+(?:\.\d+)*[.)]|[가-하]\))?\s*[^.!?。！？]{0,40}'
                    r'(?:방법|절차|목적|기준|투여|(?<![가-힣])(?:전|중|후))\s*', value):
        return False
    return True


_PHASE_WORDS = {
    'before': ('사전', '이전'),
    'during': ('동안',),
    'after': ('이후',),
}
_SHORT_PHASES = {'전': 'before', '중': 'during', '후': 'after'}
_PHASE_BOUNDARY = r'(?=$|[\s·‧/(),.\[\]]|으로|인|의)'
_EVENT_PHASE = re.compile(
    r'[가-힣a-zA-Z0-9()/-]{2,}(?:\s+[가-힣a-zA-Z0-9()/-]{2,}){0,3}\s+(전|중|후)'
    + _PHASE_BOUNDARY
)
_CONTINUED_PHASE = re.compile(r'[·‧/,]\s*(전|중|후)' + _PHASE_BOUNDARY)
_ACTION_FAMILY_ROOTS = (
    ('assessment', ('확인', '평가', '측정')),
    ('execution', ('시행', '수행', '세척', '교체', '준비', '연결', '제거', '소독',
                   '주입', '투여', '투약', '사용', '중단')),
    ('documentation', ('작성', '기록', '표시', '서명')),
    ('communication', ('보고', '설명', '동의')),
    ('monitoring', ('관찰', '모니터링')),
    ('transfer', ('이동',)),
)
_BROAD_PROCEDURE_TRIGGER = re.compile(r'절차|순서|방법|어떻게|과정|단계', re.I)
_NARROW_PROCEDURE_ASPECT = re.compile(
    r'용량|투여량|세척량|속도|주기|간격|몇\s*(?:회|번|mg|ml)|'
    r'해제|종료\s*기준|중단\s*기준|준비물|주의사항|금기|목적|정의', re.I
)


def _source_unit_phases(text):
    normalized = clean(unicodedata.normalize('NFKC', text)).casefold()
    phases = set()
    for phase, words in _PHASE_WORDS.items():
        if any(re.search(rf'(?<![가-힣a-z0-9]){word}(?![가-힣a-z0-9])', normalized)
               for word in words):
            phases.add(phase)
    phases.update(_SHORT_PHASES[marker] for marker in _EVENT_PHASE.findall(normalized))
    phases.update(_SHORT_PHASES[marker] for marker in _CONTINUED_PHASE.findall(normalized))
    return frozenset(phases)


def _action_families(text):
    return tuple(
        family for family, roots in _ACTION_FAMILY_ROOTS
        if any(root in text for root in roots)
    )


def _phase_context_marker(text, selectable):
    return (
        not selectable
        or bool(re.match(r'^\s*(?:[①-⑳]|\d+(?:\.\d+)*[.)])', text))
    )


def _phase_context_reset(text, selectable):
    return not selectable and bool(re.fullmatch(r'\s*(?:공통사항|주의사항|기타)\s*', text))


def is_broad_procedure(plan):
    current = plan.query.split(' / 추가 질문: ')[-1]
    return (
        plan.kind == 'procedure'
        and bool(_BROAD_PROCEDURE_TRIGGER.search(current))
        and not _NARROW_PROCEDURE_ASPECT.search(current)
        and len(_source_unit_phases(current)) != 1
    )


def build_source_unit_catalog(groups):
    units = []
    ordinal = 1
    for group in groups:
        previous_chunk = None
        current_phase = 'unspecified'
        for hit in group.hits:
            sentences = source_sentences(hit.chunk.text)
            for position, sentence in enumerate(sentences, start=1):
                selectable = _source_unit_selectable(
                    hit.chunk, sentence, position, previous_chunk=previous_chunk
                )
                explicit_phases = _source_unit_phases(sentence)
                if len(explicit_phases) == 1:
                    phase = next(iter(explicit_phases))
                    if _phase_context_marker(sentence, selectable):
                        current_phase = phase
                elif len(explicit_phases) > 1:
                    phase = 'unspecified'
                else:
                    if _phase_context_reset(sentence, selectable):
                        current_phase = 'unspecified'
                    phase = current_phase
                units.append(SourceUnit(
                    source_unit_id=f'su{ordinal:03d}',
                    chunk_id=hit.chunk.id,
                    source_order=(hit.chunk.index, position),
                    branch=group.branch,
                    exact_text=sentence,
                    group_key=group.key,
                    required=group.required,
                    selectable=selectable,
                    phase=phase,
                    action_families=_action_families(sentence),
                ))
                ordinal += 1
            previous_chunk = hit.chunk
    return tuple(units)


def procedure_answer_requirement(plan, prompt_coverage, catalog, maximum_selected_units=16):
    required_groups = tuple(prompt_coverage.required_group_keys) if prompt_coverage else ()
    required_branches = tuple(prompt_coverage.required_branches) if prompt_coverage else ()
    broad = bool(prompt_coverage) and is_broad_procedure(plan)
    if not broad:
        return ProcedureAnswerRequirement(
            False, required_groups, required_branches, (), (), (), 0, True, True, 0
        )

    required_set = set(required_groups)
    candidates = tuple(
        unit for unit in catalog
        if unit.required and unit.selectable and unit.group_key in required_set
    )
    branch_order = tuple(dict.fromkeys(unit.branch for unit in candidates))
    phase_slots = tuple(
        (branch, phase)
        for branch in branch_order
        for phase in ('before', 'during', 'after')
        if any(unit.branch == branch and unit.phase == phase for unit in candidates)
    )
    action_slots = []
    for branch, phase in phase_slots:
        anchor = next(
            unit for unit in candidates
            if unit.branch == branch and unit.phase == phase
        )
        action_slots.extend(
            (branch, phase, family) for family in anchor.action_families
        )
    action_families = tuple(
        family for family, _ in _ACTION_FAMILY_ROOTS
        if any(family in unit.action_families for unit in candidates)
    )

    witness = []

    def add_witness(unit):
        if unit and unit.source_unit_id not in {item.source_unit_id for item in witness}:
            witness.append(unit)

    for group_key in required_groups:
        add_witness(next((unit for unit in candidates if unit.group_key == group_key), None))
    for slot in phase_slots:
        add_witness(next((unit for unit in candidates
                          if (unit.branch, unit.phase) == slot), None))
    for branch, phase, family in action_slots:
        add_witness(next((unit for unit in candidates
                          if (unit.branch, unit.phase) == (branch, phase)
                          and family in unit.action_families), None))
    for family in action_families:
        add_witness(next((unit for unit in candidates
                          if family in unit.action_families), None))

    capacity_complete = (
        all(any(unit.group_key == key for unit in candidates) for key in required_groups)
        and all(any((unit.branch, unit.phase) == slot for unit in candidates)
                for slot in phase_slots)
        and all(any((unit.branch, unit.phase) == (branch, phase)
                    and family in unit.action_families for unit in candidates)
                for branch, phase, family in action_slots)
        and all(any(family in unit.action_families for unit in candidates)
                for family in action_families)
    )
    return ProcedureAnswerRequirement(
        broad_procedure=True,
        required_group_keys=required_groups,
        required_branches=required_branches,
        required_phase_slots=phase_slots,
        required_action_slots=tuple(action_slots),
        required_action_families=action_families,
        minimum_action_diversity=len(action_families),
        source_order_required=True,
        capacity_valid=capacity_complete and len(witness) <= maximum_selected_units,
        capacity_witness_size=len(witness),
    )


_QUERY_ACTION_ROOTS = (
    '확인', '작성', '시행', '세척', '교체', '준비', '연결', '제거', '소독', '측정',
    '주입', '투여', '기록', '보고', '표시', '사용', '중단', '평가', '설명', '동의',
    '서명', '이동', '관찰', '모니터링',
)


def required_facets(plan, requirement):
    """Return the provider-independent facets required for a broad procedure answer."""
    if not requirement.broad_procedure:
        return ()
    facets = []
    for group_key in requirement.required_group_keys:
        facets.append(RequiredFacet('group', ('group', group_key), group_key=group_key))
    for branch in requirement.required_branches:
        facets.append(RequiredFacet('branch', ('branch', branch), branch=branch))
    for branch, phase in requirement.required_phase_slots:
        facets.append(RequiredFacet(
            'phase', ('phase', branch, phase), branch=branch, phase=phase,
        ))
    for branch, phase, family in requirement.required_action_slots:
        facets.append(RequiredFacet(
            'phase_action', ('phase_action', branch, phase, family),
            branch=branch, phase=phase, action_family=family,
        ))
    for family in requirement.required_action_families:
        facets.append(RequiredFacet(
            'action_family', ('action_family', family), action_family=family,
        ))
    for action in _QUERY_ACTION_ROOTS:
        if action in plan.query:
            facets.append(RequiredFacet(
                'query_action', ('query_action', action), query_action=action,
            ))
    return tuple(facets)


def facet_eligible_source_unit_ids(facet, requirement, catalog):
    """Return selectable SourceUnit IDs that can satisfy one required facet."""
    required_groups = set(requirement.required_group_keys)
    candidates = (
        unit for unit in catalog
        if unit.required and unit.selectable and unit.group_key in required_groups
    )

    def matches(unit):
        if facet.kind == 'group':
            return unit.group_key == facet.group_key
        if facet.kind == 'branch':
            return unit.branch == facet.branch
        if facet.kind == 'phase':
            return (unit.branch, unit.phase) == (facet.branch, facet.phase)
        if facet.kind == 'phase_action':
            return (
                (unit.branch, unit.phase) == (facet.branch, facet.phase)
                and facet.action_family in unit.action_families
            )
        if facet.kind == 'action_family':
            return facet.action_family in unit.action_families
        if facet.kind == 'query_action':
            return facet.query_action in unit.exact_text
        return False

    return tuple(unit.source_unit_id for unit in candidates if matches(unit))


def answer_coverage(plan, prompt_coverage, catalog, selected_units):
    requirement = procedure_answer_requirement(plan, prompt_coverage, catalog)
    selected_groups = tuple(dict.fromkeys(unit.group_key for unit in selected_units))
    selected_branches = tuple(dict.fromkeys(
        unit.branch for unit in selected_units if unit.branch != 'common'
    ))
    required_actions = tuple(action for action in _QUERY_ACTION_ROOTS if action in plan.query)
    selected_actions = tuple(action for action in required_actions
                             if any(action in unit.exact_text for unit in selected_units))
    selected_phase_slots = tuple(dict.fromkeys(
        (unit.branch, unit.phase) for unit in selected_units
        if unit.phase != 'unspecified'
    ))
    selected_action_slots = tuple(dict.fromkeys(
        (unit.branch, unit.phase, family)
        for unit in selected_units if unit.phase != 'unspecified'
        for family in unit.action_families
    ))
    selected_action_families = tuple(
        family for family, _ in _ACTION_FAMILY_ROOTS
        if any(family in unit.action_families for unit in selected_units)
    )
    ordered = all(
        left.source_order <= right.source_order
        for left, right in zip(selected_units, selected_units[1:])
    )
    reason = ''
    if not set(prompt_coverage.required_branches).issubset(selected_branches):
        reason = 'selection_branch'
    elif not set(prompt_coverage.required_group_keys).issubset(selected_groups):
        reason = 'selection_missing_group'
    elif not requirement.capacity_valid:
        reason = 'selection_requirement_capacity'
    elif not set(requirement.required_phase_slots).issubset(selected_phase_slots):
        reason = 'selection_missing_phase'
    elif not set(requirement.required_action_slots).issubset(selected_action_slots):
        reason = 'selection_missing_phase_action'
    elif len(selected_action_families) < requirement.minimum_action_diversity:
        reason = 'selection_insufficient_action_diversity'
    elif not set(required_actions).issubset(selected_actions):
        reason = 'selection_missing_action'
    elif not ordered:
        reason = 'selection_source_order'
    return AnswerCoverage(
        required_group_keys=prompt_coverage.required_group_keys,
        selected_group_keys=selected_groups,
        required_branches=prompt_coverage.required_branches,
        selected_branches=selected_branches,
        required_query_actions=required_actions,
        selected_query_actions=selected_actions,
        required_phase_slots=requirement.required_phase_slots,
        selected_phase_slots=selected_phase_slots,
        required_action_slots=requirement.required_action_slots,
        selected_action_slots=selected_action_slots,
        required_action_families=requirement.required_action_families,
        selected_action_families=selected_action_families,
        minimum_action_diversity=requirement.minimum_action_diversity,
        source_ordered=ordered,
        complete=not reason,
        reason=reason,
    )


def sentence_evidence(text, evidence, sources):
    """문장 전체가 원문과 일치해야 합니다. 조건/부정을 삭제한 부분 인용은 실패합니다."""
    result = []
    for sentence in source_sentences(text):
        matching = [e for e in evidence if sentence in source_sentences(sources[e.chunk_id].text)
                    and sentence in clean(e.quote)]
        if not matching:
            return []
        result.append((sentence, matching))
    return result


def procedure_citations_ordered(plan, statements, sources):
    if plan is None or plan.kind != 'procedure':
        return True
    parent_branches = {}
    for chunk in sources.values():
        key = (chunk.document_id, chunk.parent_id or chunk.id)
        parent_branches.setdefault(key, []).append(chunk.text)
    branch_by_group = {key: _branch('\n'.join(texts)) for key, texts in parent_branches.items()}
    last = {}
    for statement in statements:
        current = {}
        for evidence in statement.evidence:
            chunk = sources[evidence.chunk_id]
            group = (chunk.document_id, chunk.parent_id or chunk.id)
            key = (chunk.document_id, branch_by_group[group])
            current.setdefault(key, []).append(chunk.index)
        for key, indexes in current.items():
            first, final = min(indexes), max(indexes)
            if first < last.get(key, first):
                return False
            last[key] = final
    return True
