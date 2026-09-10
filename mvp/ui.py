"""순천향대학교 부속 천안병원 지침 AI의 화면 표현 전용 모듈."""

import html
import re
from functools import lru_cache
from pathlib import Path

import streamlit as st

from mvp.settings import GuideError

FALLBACK_EXAMPLES = (
    ('감염 관리', 'CRE 환자 격리 방법은?', ':material/shield:'),
    ('시술 간호', 'PCN irrigation 방법 알려줘', ':material/water_drop:'),
    ('시술 준비', 'Thoracentesis 준비물은?', ':material/checklist:'),
    ('투약 간호', '반코마이신 투여 시 주의사항은?', ':material/medication:'),
)


@lru_cache(maxsize=1)
def stylesheet():
    return Path(__file__).with_name('ui.css').read_text(encoding='utf-8')


def apply_theme():
    st.html('<style>' + stylesheet() + '</style>')


def connection_state(settings, turns=()):
    try:
        settings.llm_endpoint()
    except GuideError:
        return 'AI 연결 대기', 'muted'
    latest = turns[-1] if turns else {}
    if latest.get('error') and 'AI_' in latest['error']:
        waiting = 'LIMIT' in latest['error'] or 'AI_RATE' in latest['error']
        return ('잠시 대기' if waiting else '연결 확인 필요'), 'waiting'
    return ('지침 검색 정상' if latest.get('answer') is not None else '지침 검색 준비'), 'ready'


def status_pill(settings, turns=()):
    label, state = connection_state(settings, turns)
    return f'<span class="status-pill {state}"><i aria-hidden="true"></i>{html.escape(label)}</span>'


def header(settings, turns=(), *, admin=False, page='chat', on_navigate=None, documents=()):
    with st.container(key='service_header'):
        left, right = st.columns([5, 3] if admin else [4, 2], vertical_alignment='center', gap='small')
        with left:
            st.html('<div class="service-heading"><div class="service-eyebrow">'
                    '<b>SCH</b><span>순천향대학교 부속 천안병원</span></div>'
                    '<h1>병원 실무지침 AI</h1><p>등록된 최신 지침을 근거로 답변합니다.</p></div>')
        with right, st.container(horizontal=True, vertical_alignment='center', key='header_actions'):
            if documents:
                st.html(f'<span class="document-count">등록 지침 <b>{len(documents)}</b>개</span>')
            st.html('<div class="connection">' + status_pill(settings, turns) + '</div>')
            if admin:
                st.button('지침서 관리' if page == 'chat' else 'AI 채팅',
                          icon=':material/folder_open:' if page == 'chat' else ':material/chat_bubble_outline:',
                          key='header_navigate', on_click=on_navigate,
                          args=('manage' if page == 'chat' else 'chat',))


def brand():
    st.html('<div class="brand"><span class="brand-mark" aria-hidden="true">SCH</span>'
            '<div><small>순천향대학교 부속 천안병원</small><strong>병원 실무지침 AI</strong></div></div>')


def login_brand():
    st.html('''<section class="login-brand-panel">
        <div class="login-wordmark"><b>SCH</b><span>순천향대학교 부속 천안병원</span></div>
        <div class="login-brand-copy">
          <span class="login-kicker">HOSPITAL KNOWLEDGE ASSISTANT</span>
          <h1>병원 실무지침 AI</h1>
          <p>등록된 최신 실무지침을 빠르게 확인하고<br>근거 원문까지 한 화면에서 살펴보세요.</p>
        </div>
        <div class="trust-list">
          <span><i>✓</i> 직원 전용</span><span><i>✓</i> 근거 기반 답변</span><span><i>!</i> 개인정보 입력 금지</span>
        </div>
      </section>''')


def sidebar_account(settings, turns, admin):
    st.html('<div class="sidebar-connection">' + status_pill(settings, turns) + '</div>')
    role = '관리자' if admin else '직원'
    st.html(f'<div class="account-row"><span class="account-avatar" aria-hidden="true">{role[0]}</span>'
            f'<div><strong>{role} 계정</strong><small>병원 내부 지침 서비스</small></div></div>')
    st.html('<div class="privacy-note"><span aria-hidden="true">!</span>'
            '<div><strong>개인정보 입력 금지</strong><br>환자 이름·등록번호 등은 입력하지 마세요.</div></div>')


def recent_questions(turns):
    with st.container(key='recent_questions'):
        st.html('<div class="nav-section-label">현재 대화</div>')
        if not turns:
            st.html('<div class="sidebar-empty"><span aria-hidden="true">↳</span>'
                    '<p>새로운 질문을 시작해 보세요.<small>질문 기록은 로그아웃하면 삭제됩니다.</small></p></div>')
        for index in range(len(turns)-1, -1, -1):
            text = turns[index]['question']
            st.html(f'<a class="recent-link" href="#turn-{index}" title="{html.escape(text, quote=True)}">'
                    f'<span class="recent-icon" aria-hidden="true">↳</span><span>{html.escape(text)}</span></a>')


def set_draft(text):
    st.session_state['question_input'] = text


def suggested_questions(documents):
    """등록된 metadata만 이용합니다. 추천 질문 생성에 AI를 호출하지 않습니다."""
    icons = (':material/description:', ':material/checklist:', ':material/shield:', ':material/medication:')
    candidates = []
    for document in documents:
        raw = document.get('section') or document.get('title') or document.get('document_name', '')
        topic = re.sub(r'\.(?:pdf|docx|xlsx)$', '', str(raw), flags=re.I).strip()
        if topic and topic.lower() not in {c[0].lower() for c in candidates}:
            candidates.append((topic, f'{topic}의 핵심 내용을 알려줘'))
    result = [(topic, question, icons[i % len(icons)]) for i, (topic, question) in enumerate(candidates[:4])]
    for fallback in FALLBACK_EXAMPLES:
        if len(result) == 4:
            break
        if fallback[1] not in {item[1] for item in result}:
            result.append(fallback)
    return tuple(result)


def empty_state(documents=(), disabled=False):
    with st.container(key='welcome'):
        st.html('<div class="welcome-copy"><div class="welcome-kicker"><span aria-hidden="true">✦</span> 근거 중심 지침 검색</div>'
                '<h2>어떤 지침을 확인할까요?</h2>'
                '<p>질문과 관련된 병원 지침을 찾아<br class="mobile-break"> 답변과 정확한 출처를 함께 표시합니다.</p></div>')
        st.html('<div class="example-heading">이런 질문으로 시작해 보세요 <span>등록 지침 기반</span></div>')
        examples = suggested_questions(documents)
        for row in range(2):
            with st.container(key=f'example_row_{row}'):
                for col, (category, question, icon) in zip(st.columns(2, gap='small'), examples[row*2:row*2+2], strict=True):
                    with col:
                        st.button(question, key=f'example_question_{row}_{category}', icon=icon,
                                  help=category, width='stretch', disabled=disabled,
                                  on_click=set_draft, args=(question,))
        st.caption('추천 질문은 문서 정보에서 생성하며 별도의 AI 사용량을 차감하지 않습니다.')


def question_bubble(question, index):
    st.html(f'<div class="user-row" id="turn-{index}"><div class="user-bubble">'
            f'<div class="user-label">내 질문</div><p>{html.escape(question)}</p></div></div>')


@st.dialog('근거 원문', width='large', dismissible=True)
def source_dialog(chunk, quotes, view_key, library, revision):
    position = f'PDF {chunk.page}페이지' if chunk.source_type == 'pdf' else chunk.location
    page_badge = f'p.{chunk.page}' if chunk.source_type == 'pdf' else chunk.location
    with st.container(key=f'source_drawer_{view_key}_{chunk.id}'):
        st.html(f'<div class="source-drawer-header"><span class="source-kind">{html.escape(chunk.source_type.upper())}</span>'
                f'<div><strong>{html.escape(chunk.document_name)}</strong>'
                f'<small>{html.escape(chunk.section or chunk.title or "항목 미입력")}</small></div>'
                f'<span class="page-badge">{html.escape(page_badge)}</span></div>')
        st.html('<div class="source-meta"><span>위치 <b>' + html.escape(position) + '</b></span>'
                '<span>개정일 <b>' + html.escape(chunk.updated_date or '미입력') + '</b></span></div>')
        if quotes:
            st.caption('답변을 직접 뒷받침하는 원문')
            for quote in quotes:
                st.html(f'<blockquote class="source-quote">{html.escape(quote)}</blockquote>')
        with st.expander('검색된 원문 전체 보기'):
            st.text(chunk.text)
        if st.toggle('앞뒤 문단도 보기', key=f'source_context_{view_key}_{chunk.id}'):
            for nearby in library.source_context(chunk, revision):
                if nearby.id != chunk.id:
                    where = f'PDF {nearby.page}페이지' if nearby.source_type == 'pdf' else nearby.location
                    st.caption(where + (' · ' + nearby.section if nearby.section else ''))
                    st.text(nearby.text)
        if chunk.source_type == 'pdf':
            st.caption('PDF 파일의 실제 페이지입니다. 문서에 인쇄된 쪽수와 다를 수 있습니다.')


def source_card(chunk, quotes, view_key, library, revision):
    source_dialog(chunk, quotes, view_key, library, revision)


def document_summary(docs):
    active = [d for d in docs if d['status'] in {'ready', 'pending', 'error', 'active'}]
    ready = [d for d in active if d['status'] in {'ready', 'active'}]
    latest = max((d.get('indexed_at') or d.get('created_at') or '' for d in ready), default='')
    pending = sum(d['status'] not in {'ready', 'active'} or bool(d.get('last_error')) for d in active)
    state = '확인 필요' if pending else ('정상' if ready else '등록 대기')
    values = [('등록 문서', str(len(active)), '개 지침서', '▤'),
              ('총 페이지', str(sum(d.get('page_count', 0) for d in active)), '페이지 / 문서 영역', '▥'),
              ('검색 인덱스', state, f"검색 문단 {sum(d.get('chunk_count', 0) for d in ready):,}개", '◈'),
              ('마지막 업데이트', latest[:10] or '—', '최근 문서 반영', '↻')]
    with st.container(key='document_summary'):
        for column, (label, value, note, icon) in zip(st.columns(4), values, strict=True):
            with column:
                st.html(f'<div class="summary-card"><div><span>{label}</span><i aria-hidden="true">{icon}</i></div>'
                        f'<strong>{value}</strong><small>{note}</small></div>')
