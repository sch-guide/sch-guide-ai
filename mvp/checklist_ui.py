"""관리자가 승인한 원문 기반 체크리스트만 표시합니다."""

import re

import streamlit as st

PROCEDURE_TERMS = ('시술', '수술', '절차', '방법', '준비', '준비물', '시행', '전후', '간호', '관리', 'irrigation')


def matching_checklists(question, entries):
    normalized = re.sub(r'\s+', '', question).lower()
    if not any(term.lower() in normalized for term in PROCEDURE_TERMS):
        return []
    matched = []
    for entry in entries:
        keywords = entry.get('keywords') or []
        if any(re.sub(r'\s+', '', str(word)).lower() in normalized for word in keywords):
            matched.append(entry)
    return matched[:3]


@st.dialog('승인된 체크리스트', width='large', dismissible=True)
def checklist_dialog(entries, hits, index):
    chunks = {hit.chunk.id: hit.chunk for hit in hits}
    for entry in entries:
        chunks.update({chunk.id: chunk for chunk in entry.get('_chunks', [])})
    st.caption('관리자가 지침 원문을 확인하고 승인한 항목입니다. 환자 상황에 따른 별도 처방과 부서 기준도 확인하세요.')
    for list_number, entry in enumerate(entries, 1):
        st.subheader(entry.get('title') or f'체크리스트 {list_number}')
        for item_number, item in enumerate(entry.get('items') or [], 1):
            chunk = chunks.get(item.get('chunk_id'))
            source = ''
            if chunk:
                place = f'p.{chunk.page}' if chunk.source_type == 'pdf' else chunk.location
                source = f' · [{item_number}] {chunk.document_name} {place}'
            label = f"{item.get('stage', '기타')} · {item.get('quote', '')}{source}"
            st.checkbox(label, key=f'approved_check_{index}_{list_number}_{item_number}')
        if list_number < len(entries):
            st.divider()
    st.html('<div class="checklist-foot"><b>확인 기록은 저장되지 않습니다.</b><span>업무 보조용으로 사용하세요.</span></div>')
