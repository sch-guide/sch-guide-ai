"""검증이 끝난 답변과 출처를 NotebookLM 방식으로 표시합니다."""

import hashlib
import re
from datetime import date

import streamlit as st


def escape(text):
    return re.sub(r'([\\`*{}\[\]()#+.!|<>_~-])', r'\\\1', text).replace('\n', ' ')


def source_anchor(index, group_key):
    return 'source-' + hashlib.sha256(f'{index}:{group_key}'.encode()).hexdigest()[:20]


def _location(chunk):
    return f'p.{chunk.page}' if chunk.source_type == 'pdf' else (chunk.location or '위치 미입력')


def _group_key(chunk):
    return chunk.document_id, chunk.page, chunk.location


def _is_stale(updated_date):
    try:
        return (date.today() - date.fromisoformat(updated_date)).days > 730
    except (TypeError, ValueError):
        return False


def render_answer(answer, hits, index, source_view, *, on_review=None, checklists=(), on_checklist=None):
    """문장별 인용은 유지하면서 같은 문서·위치의 chunk는 하나의 출처로 묶습니다."""
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
