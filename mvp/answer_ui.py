"""검증이 끝난 답변과 출처를 NotebookLM 방식으로 표시합니다."""

import hashlib
import re
from datetime import date

import streamlit as st


def escape(text):
    return re.sub(r'([\\`*{}\[\]()#+.!|<>_~-])', r'\\\1', text).replace('\n', ' ')


def fallback_display(text, headings=()):
    """Display-only blocks; never join separate evidence or alter lexical content."""
    normalize = lambda value: re.sub(r'\s+', ' ', value).strip()
    titles = {normalize(value) for value in headings if value}
    blocks, current = [], []
    def flush():
        if current:
            blocks.append(' '.join(current))
            current.clear()
    for raw in text.splitlines():
        line = normalize(raw)
        if not line or line == '↓':
            flush()
            continue
        # Only remove leading PDF bullet artifacts; preserve clinical ↓ symbols.
        bullet = bool(re.match(r'^(?:Ÿ\s*|[•●▪]\s*|[-*]\s+)', line))
        line = re.sub(r'^(?:Ÿ\s*|[•●▪]\s*|[-*]\s+)', '', line).strip()
        if not line:
            flush()
            continue
        if line in titles:
            flush()
            # Only adjacent, exact metadata headings; never deduplicate instructions.
            if not blocks or blocks[-1] != line:
                blocks.append(line)
            continue
        if bullet:
            flush()
        current.append(line)
        if re.search(r'[.!?。！？]$', line):
            flush()
    flush()
    return blocks


def source_anchor(index, group_key):
    return 'source-' + hashlib.sha256(f'{index}:{group_key}'.encode()).hexdigest()[:20]


def fallback_outline(statements, chunks, procedure):
    """Extract labels, never summarize clinical sentences or change source order."""
    norm=lambda text: re.sub(r'\s+', ' ', text).strip()
    entries=[]
    for statement in statements:
        headings={norm(v) for e in statement.evidence
                  for v in (chunks[e.chunk_id].section,chunks[e.chunk_id].title) if v}
        labels=[]
        if procedure:
            # PDF flowchart labels precede bullet bodies, at chunk start or after ↓.
            # Require a body in this chunk; dangling labels are not claimed as steps.
            for segment in re.split(r'(?m)^\s*↓\s*$',statement.text):
                lines=[norm(line) for line in segment.splitlines() if norm(line)]
                prefix=[]
                for line in lines:
                    if re.match(r'^(?:Ÿ|[•●▪]|[-*]\s)',line):
                        label=' '.join(prefix)
                        if label and len(label)<=120 and not re.search(r'[.!?。！？]',label):
                            labels.append(label)
                        break
                    if line not in headings:
                        prefix.append(line)
            # Distinct metadata subheadings are usable when they name an action.
            if not labels:
                for e in statement.evidence:
                    section=norm(chunks[e.chunk_id].section)
                    if re.search(r'(?:작성|시행|수령|확인|관찰|기록|시작|중지|반납)$',section):
                        labels.append(section)
        else:
            labels=[b for b in fallback_display(statement.text,headings) if b not in headings]
        entries.extend((label,statement) for label in labels)
    # Exact whitespace-normalized duplicate only; keep differing facts/conditions.
    seen=set(); result=[]
    for label,statement in entries:
        if label not in seen:
            seen.add(label);result.append((label,statement))
    return result


def _location(chunk):
    return f'p.{chunk.page}' if chunk.source_type == 'pdf' else (chunk.location or '위치 미입력')


def _group_key(chunk):
    return chunk.document_id, chunk.page, chunk.location


def _is_stale(updated_date):
    try:
        return (date.today() - date.fromisoformat(updated_date)).days > 730
    except (TypeError, ValueError):
        return False


def render_answer(answer, hits, index, source_view, *, on_review=None, checklists=(), on_checklist=None,
                  question_kind=None, _detail=False):
    """문장별 인용은 유지하면서 같은 문서·위치의 chunk는 하나의 출처로 묶습니다."""
    fallback = getattr(answer, 'answer_kind', None) == 'evidence_only'
    if fallback and not _detail:
        st.markdown('### 근거 기반 안내')
        st.caption('AI가 생성한 답변이 아니라 등록 지침의 원문 근거를 표시합니다. 출처와 전체 문맥을 함께 확인하세요.')
    chunks = {hit.chunk.id: hit.chunk for hit in hits}
    groups, chunk_to_group = {}, {}
    for statement in answer.statements:
        for evidence in statement.evidence:
            chunk = chunks[evidence.chunk_id]
            key = _group_key(chunk)
            chunk_to_group[evidence.chunk_id] = key
            group = groups.setdefault(key, {'chunk': chunk, 'quotes': [], 'chunk_ids': []})
            if evidence.quote not in group['quotes']:
                group['quotes'].append(evidence.quote)
            if evidence.chunk_id not in group['chunk_ids']:
                group['chunk_ids'].append(evidence.chunk_id)
    numbers = {key: number for number, key in enumerate(groups, 1)}
    anchors = {key: source_anchor(index, key) for key in groups}

    def references(statement):
        keys = dict.fromkeys(chunk_to_group[e.chunk_id] for e in statement.evidence)
        return ' '.join(f'[[{numbers[key]}]](#{anchors[key]})' for key in keys)

    if fallback and not _detail:
        st.markdown('### 핵심 안내')
        procedure=question_kind=='procedure'
        outline=fallback_outline(answer.statements,chunks,procedure)
        if not outline:
            st.caption('원문에서 명확한 단계명을 확인하기 어려워 전체 근거를 아래에 제공합니다.')
        for number,(label,statement) in enumerate(outline if procedure else outline[:8],1):
            prefix=f'{number}. ' if procedure else '- '
            st.markdown(prefix+escape(label)+' '+references(statement))
        if procedure:
            st.caption('원문의 단계명 목록입니다. 각 단계의 조건·주의사항과 세부 내용은 전체 근거를 확인하세요.')
        elif len(outline)>8:
            st.caption(f'전체 {len(outline)}개 근거 중 앞의 8개를 표시합니다. 나머지 내용은 아래에서 확인하세요.')
        with st.expander('근거 원문 자세히 보기',expanded=False):
            render_answer(answer,hits,index,source_view,on_review=on_review,checklists=checklists,
                          on_checklist=on_checklist,question_kind=question_kind,_detail=True)
        return

    source_docs = {group['chunk'].document_id for group in groups.values()}
    revisions = [group['chunk'].updated_date for group in groups.values() if group['chunk'].updated_date]
    latest = max(revisions, default='미입력')
    stale = any(_is_stale(value) for value in revisions)
    with st.container(key=f'answer_meta_{index}'):
        st.html('<div class="answer-meta">'
                f'<span>근거 문서 <b>{len(source_docs)}</b>개</span>'
                f'<span>최신 개정일 <b>{escape(latest)}</b></span>'
                + ('<span class="stale-badge">오래된 지침 포함</span>' if stale else '') + '</div>')

    if answer.conflict:
        st.warning('지침 내용이 서로 다릅니다. 아래 양쪽 근거를 확인하고 담당 부서에 적용 기준을 확인하세요.')
    if answer.format == 'comparison':
        rows = ['| 항목 | 지침에 근거한 내용 | 출처 |', '| --- | --- | --- |']
        for statement in answer.statements:
            names = ' · '.join(dict.fromkeys(chunks[e.chunk_id].title or chunks[e.chunk_id].document_name
                                             for e in statement.evidence))
            rows.append(f'| {escape(statement.label or names)} | {escape(statement.text)} | {references(statement)} |')
        st.markdown('\n'.join(rows))
    else:
        for number, statement in enumerate(answer.statements, 1):
            if fallback:
                st.text(statement.text)
                st.markdown(references(statement))
                continue
            prefix = f'{number}. ' if answer.format == 'steps' else ('- ' if answer.format == 'bullets' else '')
            st.markdown(prefix + escape(statement.text) + ' ' + references(statement))

    st.html(f'<div class="source-separator"><strong>답변 근거 <b>{len(groups)}</b></strong>'
            '<span>출처를 선택하면 정확한 원문을 확인할 수 있습니다</span></div>')
    with st.container(key=f'source_chips_{index}'):
        for key, group in groups.items():
            chunk, number = group['chunk'], numbers[key]
            with st.container(key=f'source_row_{index}_{number}'):
                st.html(f'<span class="citation-target" id="{anchors[key]}" aria-hidden="true"></span>')
                if st.button(f'[{number}] {chunk.document_name} · {_location(chunk)}',
                             key=f'open_source_{index}_{number}', width='stretch',
                             icon=':material/description:'):
                    source_view(chunk, tuple(group['quotes']), view_key=f'{index}_{number}')

    actions = st.columns([1, 1, 3], gap='small')
    with actions[0]:
        if checklists and st.button('체크리스트 열기', key=f'checklist_{index}',
                                    icon=':material/checklist:', width='stretch'):
            if on_checklist:
                on_checklist(checklists, hits, index)
    with actions[1]:
        if on_review and st.button('답변 검토 요청', key=f'review_{index}',
                                   icon=':material/flag:', width='stretch'):
            on_review(index, tuple(source_docs), tuple(c for g in groups.values() for c in g['chunk_ids']))
