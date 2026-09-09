"""화면 표현 전용. 검색 알고리즘과 모델을 변경하지 않습니다."""

import html
import re
from functools import lru_cache
from pathlib import Path

import streamlit as st

from mvp.settings import GuideError

EXAMPLES = (
    ('감염 관리', 'CRE 환자 격리 방법은?', ':material/shield:'),
    ('시술 간호', 'PCN irrigation 방법 알려줘', ':material/water_drop:'),
    ('시술 준비', 'Thoracentesis 준비물은?', ':material/checklist:'),
    ('투약 간호', '반코마이신 투여 시 주의사항은?', ':material/medication:'),
)


@lru_cache(maxsize=1)
def stylesheet():
    # CSS 파일은 프로세스당 한 번 읽습니다. 외부 폰트/이미지/JS 요청이 없습니다.
    return Path(__file__).with_name('ui.css').read_text(encoding='utf-8')


def apply_theme():
    # Streamlit은 같은 위치의 스타일을 갱신합니다. rerun마다 DOM에 누적하지 않습니다.
    st.html('<style>' + stylesheet() + '</style>')


def connection_state(settings, turns=()):
    provider = 'Groq' if settings.llm_provider == 'groq_free' else '병원 AI'
    try:
        settings.llm_endpoint()  # 설정 유효성만 확인. 네트워크 호출 없음.
    except GuideError:
        return 'AI 연결 대기', 'muted', provider
    latest = turns[-1] if turns else {}
    if latest.get('error') and 'AI_' in latest['error']:
        return ('일시 대기' if 'LIMIT' in latest['error'] or 'AI_RATE' in latest['error'] else '연결 확인 필요'), 'waiting', provider
    if latest.get('answer') is not None:
        return 'AI 연결됨', 'ready', provider
    return 'AI 연결 설정됨', 'ready', provider


def status_pill(settings, turns=()):
    label, state, provider = connection_state(settings, turns)
    return (f'<span class="status-pill {state}"><i aria-hidden="true"></i>{html.escape(label)}</span>'
            f'<span class="provider-label">{html.escape(provider)}</span>')


def header(settings, turns=(), *, admin=False, page='chat', on_navigate=None):
    with st.container(key='service_header'):
        left, right = st.columns([5, 3] if admin else [3, 1], vertical_alignment='center', gap='small')
        with left:
            st.html('<div class="service-heading"><h1>병원 실무지침 AI</h1>'
                    '<p>등록된 병원 지침을 근거로 답변합니다.</p></div>')
        with right, st.container(horizontal=True, vertical_alignment='center', key='header_actions'):
            st.html('<div class="connection">' + status_pill(settings, turns) + '</div>')
            if admin:
                st.button('지침서 관리' if page == 'chat' else 'AI 채팅',
                          icon=':material/folder_open:' if page == 'chat' else ':material/chat_bubble_outline:',
                          key='header_navigate', on_click=on_navigate,
                          args=('manage' if page == 'chat' else 'chat',))


def brand():
    st.html('<div class="brand"><span class="brand-mark" aria-hidden="true">✚</span>'
            '<div><strong>병원 실무지침 AI</strong><small>Hospital Knowledge Assistant</small></div></div>')


def sidebar_account(settings, turns, admin):
    st.html('<div class="sidebar-connection">' + status_pill(settings, turns) + '</div>')
    role = '관리자 계정' if admin else '직원 계정'
    st.html(f'<div class="account-row"><span class="account-avatar" aria-hidden="true">{role[0]}</span>'
            f'<div><strong>{role}</strong><small>병원 내부 지침 서비스</small></div></div>')
    st.html('<div class="privacy-note"><span aria-hidden="true">▣</span>'
            '<div><strong>개인정보 입력 금지</strong><br>환자 이름·등록번호 등은 입력하지 마세요.</div></div>')


def recent_questions(turns):
    with st.container(key='recent_questions'):
        st.html('<div class="nav-section-label">최근 질문</div>')
        if not turns:
            st.html('<div class="sidebar-empty"><span aria-hidden="true">↳</span>'
                    '<p>새로운 질문을 시작해 보세요.<small>현재 대화의 질문이 여기에 표시됩니다.</small></p></div>')
        for index in range(len(turns)-1, -1, -1):
            text = turns[index]['question']
            st.html(f'<a class="recent-link" href="#turn-{index}" title="{html.escape(text, quote=True)}">'
                    f'<span class="recent-icon" aria-hidden="true">↳</span><span>{html.escape(text)}</span></a>')
        if turns:
            st.caption('현재 대화 · 로그아웃 시 초기화')


def set_draft(text):
    st.session_state['question_input'] = text


def empty_state(disabled=False):
    with st.container(key='welcome'):
        st.html('<div class="welcome-copy"><div class="welcome-kicker"><span aria-hidden="true">✦</span> 병원 실무지침 AI</div>'
                '<h2>무엇을 확인하고 싶으신가요?</h2>'
                '<p>등록된 병원 지침을 검색해<br class="mobile-break"> 근거와 함께 답변합니다.</p></div>')
        st.html('<div class="example-heading">이런 질문으로 시작해 보세요 <span>추천 질문</span></div>')
        for row in range(2):
            with st.container(key=f'example_row_{row}'):
                for col, (category, question, icon) in zip(st.columns(2, gap='small'), EXAMPLES[row*2:row*2+2], strict=True):
                    with col:
                        st.button(question, key=f'example_question_{row}_{category}', icon=icon,
                                  help=category, width='stretch', disabled=disabled,
                                  on_click=set_draft, args=(question,))
        st.caption('예시를 선택하면 질문창에 입력됩니다. 내용을 확인한 후 전송하세요.')


def question_bubble(question, index):
    # HTML은 화면 구조에만 사용하고 사용자 입력은 반드시 이스케이프합니다.
    st.html(f'<div class="user-row" id="turn-{index}"><div class="user-bubble">'
            f'<div class="user-label">사용자</div><p>{html.escape(question)}</p></div></div>')


def source_card(chunk, quotes, view_key, library, revision):
    position = f'PDF {chunk.page}페이지' if chunk.source_type == 'pdf' else chunk.location
    page_badge = f'p.{chunk.page}' if chunk.source_type == 'pdf' else chunk.location
    safe_name = re.sub(r'([\\`*{}\[\]()#+.!|<>_-])', r'\\\1', chunk.document_name)
    with st.container(key=f'source_card_{view_key}_{chunk.id}'):
        # 상세 카드 전체가 접힌 영역 안에 있어 답변보다 원문이 먼저 눈에 들어오지 않습니다.
        label = f'관련 원문 펼쳐보기 · {safe_name} · {position}'
        with st.expander(label, expanded=st.session_state.get(f'source_open_{view_key}_{chunk.id}', False)):
            st.html(f'<div class="source-top"><span class="source-kind">{html.escape(chunk.source_type.upper())}</span>'
                    f'<strong>{html.escape(chunk.document_name)}</strong><span class="page-badge">{html.escape(page_badge)}</span></div>')
            st.caption((chunk.section or chunk.title or '항목 미입력') + ' · 개정일 ' + (chunk.updated_date or '미입력'))
            st.text(f'{chunk.document_name} · {position}')
            st.text(f'제목: {chunk.title}')
            if quotes:
                st.caption('답변을 뒷받침하는 원문')
                for quote in quotes:
                    st.html(f'<blockquote class="source-quote">{html.escape(quote)}</blockquote>')
            st.caption('검색된 원문 일부')
            st.text(chunk.text)
            if st.toggle('앞뒤 문단도 보기', key=f'source_context_{view_key}_{chunk.id}'):
                for nearby in library.source_context(chunk, revision):
                    if nearby.id != chunk.id:
                        where = f'PDF {nearby.page}페이지' if nearby.source_type == 'pdf' else nearby.location
                        st.caption(where + (' · ' + nearby.section if nearby.section else ''))
                        st.text(nearby.text)
            if chunk.source_type == 'pdf':
                st.caption('PDF의 실제 페이지 번호입니다. 인쇄된 쪽수와 다를 수 있습니다.')


def document_summary(docs):
    active = [d for d in docs if d['status'] in {'ready', 'pending', 'error'}]
    ready = [d for d in active if d['status'] == 'ready']
    latest = max((d.get('indexed_at') or '' for d in ready), default='')
    pending = sum(d['status'] != 'ready' or bool(d.get('last_error')) for d in active)
    state = '확인 필요' if pending else ('정상' if ready else '등록 대기')
    values = [('등록 문서', str(len(active)), '개 지침서', '▤'),
              ('총 페이지', str(sum(d.get('page_count', 0) for d in active)), 'PDF 페이지 / Office 영역', '▥'),
              ('검색 인덱스', state, f"검색 문단 {sum(d.get('chunk_count', 0) for d in ready):,}개", '◈'),
              ('마지막 업데이트', latest[:10] or '—', (latest[11:16] + ' UTC') if latest else '색인 대기', '↻')]
    with st.container(key='document_summary'):
        for column, (label, value, note, icon) in zip(st.columns(4), values, strict=True):
            with column:
                st.html(f'<div class="summary-card"><div><span>{label}</span><i aria-hidden="true">{icon}</i></div>'
                        f'<strong>{value}</strong><small>{note}</small></div>')
