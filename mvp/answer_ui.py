"""검증이 끝난 답변만 화면에 표시합니다. 출처 이름은 저장된 metadata를 사용합니다."""

import hashlib
import re

import streamlit as st


def escape(text):
    return re.sub(r'([\\`*{}\[\]()#+.!|<>_~-])', r'\\\1', text).replace('\n', ' ')


def open_source(key):
    opened = st.session_state.get(key, False)
    prefix = key.split('_', 3)
    # 같은 답변에서는 선택한 원문 하나만 보여 줍니다. 검색/AI 재실행은 없습니다.
    prefix = '_'.join(prefix[:3]) + '_'
    for other in list(st.session_state):
        if other.startswith(prefix):
            st.session_state.pop(other, None)
    st.session_state[key] = not opened


def source_anchor(index, chunk_id):
    return 'source-' + hashlib.sha256(f'{index}:{chunk_id}'.encode()).hexdigest()[:20]


def render_answer(answer, hits, index, source_view):
    chunks = {hit.chunk.id: hit.chunk for hit in hits}
    citations, numbers = {}, {}
    for statement in answer.statements:
        for item in statement.evidence:
            numbers.setdefault(item.chunk_id, len(numbers) + 1)
            quotes = citations.setdefault(item.chunk_id, [])
            if item.quote not in quotes:
                quotes.append(item.quote)
    anchors = {key: source_anchor(index, key) for key in citations}

    def references(statement):
        ids = dict.fromkeys(e.chunk_id for e in statement.evidence)
        return ' '.join(f'[[{numbers[key]}]](#{anchors[key]})' for key in ids)

    if answer.conflict:
        st.warning('두 지침의 내용이 다릅니다. 아래 양쪽 근거를 확인하고 담당 부서에 적용 기준을 확인하세요.')
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
    st.html(f'<div class="source-separator"><strong>근거 <b>{len(citations)}</b></strong><span>선택하면 원문을 확인할 수 있습니다</span></div>')
    with st.container(key=f'source_chips_{index}'):
        for key, number in numbers.items():
            chunk = chunks[key]
            location = f'p.{chunk.page}' if chunk.source_type == 'pdf' else chunk.location
            with st.container(key=f'source_row_{index}_{key}'):
                st.html(f'<span class="citation-target" id="{anchors[key]}" aria-hidden="true"></span>')
                st.button(f'[{number}] {chunk.document_name} · {location}', key=f'open_source_{index}_{key}',
                          on_click=open_source, args=(f'source_open_{index}_{key}',),
                          width='stretch', icon=':material/description:',
                          type='primary' if st.session_state.get(f'source_open_{index}_{key}') else 'secondary')
    for key, quotes in citations.items():
        if st.session_state.get(f'source_open_{index}_{key}'):
            st.caption(f'근거 {numbers[key]}')
            source_view(chunks[key], quotes, view_key=str(index))
