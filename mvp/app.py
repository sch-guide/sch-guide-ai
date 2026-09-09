"""실행: python -m streamlit run mvp/app.py --server.address 127.0.0.1 --server.port 8502"""

import hashlib
import math
import sys
import time
from pathlib import Path

# Streamlit Cloud가 하위 폴더의 실행 파일만 Python 경로에 넣는 경우에도
# mvp 패키지를 찾을 수 있도록 저장소 루트를 명시적으로 등록합니다.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st

from mvp.ai import AI_VERSION, generate
from mvp.answer_ui import render_answer
from mvp.auth import LocalAuth
from mvp.cloud import StaffLibrary
from mvp.library import (
    NO_GUIDELINE,
    SEARCH_VERSION,
    Embedder,
    bounded_embedding_question,
)
from mvp.query import plan_query
from mvp.repository import Repository
from mvp.settings import GuideError, load_settings
from mvp.storage import source_store
from mvp.ui import (
    apply_theme,
    brand,
    empty_state,
    header,
    question_bubble,
    recent_questions,
    sidebar_account,
    source_card,
)

st.set_page_config(page_title="병원 실무지침 AI", page_icon="📘", layout="wide", initial_sidebar_state="auto")
apply_theme()


@st.cache_resource(show_spinner=False)
def search_model():
    return Embedder()


def clear_conversation():
    for key in list(st.session_state):
        if key in {"turns", "pending_question", "follow_up", "question_input", "ui_error"} or key.startswith(("source_context_", "source_open_")):
            st.session_state.pop(key, None)
    st.session_state["ui_page"] = "chat"


def source_view(chunk, quotes=(), *, view_key=""):
    source_card(chunk, quotes, view_key, library, revision)


def navigate(page):
    st.session_state['ui_page'] = page


try:
    settings = load_settings()
except GuideError as exc:
    st.error(str(exc))
    st.stop()


# 로그인 없는 관리자 모드는 없습니다. 최초 계정 설정은 이 PC에서만 허용합니다.
if settings.mode == "local" and st.get_option("server.address") not in {"127.0.0.1", "localhost", "::1"}:
    st.error("이 PC 로그인 모드는 서버 주소 127.0.0.1에서만 실행됩니다. 직원 배포에는 GUIDE_MODE=staff를 사용하세요.")
    st.stop()
if st.get_option("server.enableStaticServing"):
    st.error("내부 자료 보호를 위해 server.enableStaticServing=false로 실행하세요.")
    st.stop()

identity = hashlib.sha256(repr(("repository-v3", settings.mode, settings.supabase_url,
    settings.supabase_key, str(settings.library_dir), settings.storage_backend, settings.storage_bucket)).encode()).hexdigest()
if st.session_state.get("connection_identity") != identity:
    st.session_state.clear()
    st.session_state["connection_identity"] = identity

if settings.mode == "staff" and not settings.cloud_ready:
    st.subheader("직원 전용 로그인 설정이 필요합니다")
    st.info("관리자가 Supabase 로그인 설정을 완료해야 사용할 수 있습니다.")
    st.stop()

if "auth" not in st.session_state:
    st.session_state["auth"] = (LocalAuth(settings.library_dir) if settings.mode == "local" else StaffLibrary(settings))
auth = st.session_state["auth"]

if not auth.token:
    header(settings)
    with st.container(key='login_card'):
        if isinstance(auth, LocalAuth) and auth.needs_setup():
            st.subheader("최초 관리자 계정 만들기")
            st.caption("이 PC에서 한 번만 설정합니다. 설정 후에는 관리자와 직원 모두 로그인해야 합니다.")
            with st.form("bootstrap", clear_on_submit=True):
                username = st.text_input("관리자 아이디")
                password = st.text_input("관리자 비밀번호 (10자 이상)", type="password")
                confirm = st.text_input("비밀번호 확인", type="password")
                submitted = st.form_submit_button("관리자 계정 만들기", type="primary", width='stretch')
            if submitted:
                try:
                    if password != confirm:
                        raise GuideError("비밀번호가 일치하지 않습니다.")
                    auth.bootstrap(username, password)
                    st.rerun()
                except GuideError as exc:
                    st.error(str(exc))
        else:
            st.subheader("직원 로그인")
            st.caption("관리자가 등록한 계정으로 로그인하세요.")
            with st.form("login", clear_on_submit=True):
                username = st.text_input("직원 아이디" if settings.mode == "local" else "직원 이메일")
                password = st.text_input("비밀번호", type="password")
                submitted = st.form_submit_button("로그인", type="primary", width='stretch')
            if submitted:
                try:
                    auth.login(username, password)
                    clear_conversation()
                    st.rerun()
                except GuideError as exc:
                    st.error(str(exc))
        st.stop()

try:
    profile = auth.authorize()
    admin, user_id = profile["role"] == "admin", auth.user_id
    # 이 객체는 경로/접속 정보만 갖습니다. 원본이나 전체 문단은 세션에 보관하지 않습니다.
    library = Repository(settings, auth, source_store(settings, auth))
    revision = library.revision()
    documents = library.documents()
    selected = [d["id"] for d in documents]  # 직원은 관리자가 등록한 전체 지침을 자동 검색
except GuideError as exc:
    st.session_state.clear()
    st.error(str(exc))
    if st.button("로그인 화면으로"):
        st.rerun()
    st.stop()

scope = (user_id, profile["role"], revision, SEARCH_VERSION, AI_VERSION, settings.llm_provider, settings.llm_model)
if st.session_state.get("scope") != scope:
    clear_conversation()
    st.session_state["scope"] = scope
for key, show in (("notice", st.success), ("notice_error", st.error)):
    if st.session_state.get(key):
        show(st.session_state.pop(key))

turns = st.session_state.setdefault('turns', [])
if not admin:
    st.session_state['ui_page'] = 'chat'
page = st.session_state.get('ui_page', 'chat')
header(settings, turns, admin=admin, page=page, on_navigate=navigate)
with st.sidebar:
    with st.container(key='sidebar_main'):
        brand()
        with st.container(key='sidebar_new'):
            st.button('새 대화 시작', icon=':material/add:', type='primary', width='stretch', on_click=clear_conversation)
        if admin:
            with st.container(key='sidebar_navigation'):
                st.button('AI 채팅', icon=':material/chat_bubble_outline:', key='nav_chat', width='stretch',
                          type='primary' if page == 'chat' else 'secondary', on_click=navigate, args=('chat',))
                st.button('지침서 관리', icon=':material/folder_open:', key='nav_manage', width='stretch',
                          type='primary' if page == 'manage' else 'secondary', on_click=navigate, args=('manage',))
        recent_questions(turns)
    with st.container(key='sidebar_footer'):
        sidebar_account(settings, turns, admin)
        if st.button('로그아웃', icon=':material/logout:', width='stretch'):
            try:
                auth.logout()
            except GuideError:
                pass
            st.session_state.clear()
            st.rerun()


def show_turn(turn, index):
    with st.container(key=f'user_turn_{index}'):
        question_bubble(turn["question"], index)
    with st.container(key=f'answer_turn_{index}'):
        st.html('<div class="answer-heading"><div class="answer-identity"><span aria-hidden="true">✦</span><strong>AI 답변</strong></div><span class="answer-badge">등록 지침 기반</span></div>')
        answer = turn.get("answer")
        hits = turn.get("hits", [])
        if answer:
            if answer.answerable:
                render_answer(answer, hits, index, source_view)
            else:
                st.write(NO_GUIDELINE)
        elif turn.get("clarification"):
            st.info(turn["clarification"])
        elif turn.get("error"):
            st.info(turn["error"])
            if turn.get("retry_at"):
                if st.button("같은 질문 다시 시도", key=f"retry_{index}"):
                    remaining = math.ceil(turn["retry_at"] - time.time())
                    if remaining > 0:
                        st.warning(f"아직 약 {remaining}초 남았습니다. 시간이 지난 뒤 버튼을 다시 눌러 주세요.")
                    else:
                        st.session_state["pending_question"] = {
                            "question": turn["question"], "query": turn["query"], "plan": turn.get("plan")}
                        st.rerun()
        elif not hits:
            st.write(NO_GUIDELINE)
        if not hits and not turn.get("error") and not turn.get("clarification"):
            st.caption("확인 가능한 근거가 부족합니다. 질문을 구체적으로 적거나 관리자에게 지침서 등록 상태를 문의하세요.")
        if hits and (not answer or not answer.answerable):
            st.caption("검색된 참고 원문 · AI 답변이 아닙니다")
            for hit in hits:
                source_view(hit.chunk, view_key=str(index))
        with st.popover(f"{turn['elapsed']:.1f}초 · 처리 정보", icon=':material/schedule:'):
            st.caption(f"검색·응답 처리 {turn['elapsed']:.1f}초")
            if turn.get("reused"):
                st.caption("같은 대화의 동일 질문에 대해 검증된 답변을 다시 표시했습니다. AI를 추가 호출하지 않았습니다.")
            elif "search_elapsed" in turn:
                st.caption(f"근거 검색 {turn['search_elapsed']:.1f}초 · AI 생성·검증 {turn.get('ai_elapsed', 0):.1f}초")
            if ' / 추가 질문: ' in turn["query"]:
                st.caption("이전 질문의 맥락을 이어서 확인했습니다.")
            if turn.get('plan') and turn['plan'].corrections:
                st.caption('검색어 철자 보완: ' + ' · '.join(f'{a} → {b}' for a, b in turn['plan'].corrections))


if page == 'manage' and admin:
    from mvp.admin_ui import render_admin
    try:
        render_admin(library, auth, settings, search_model)
    except GuideError as exc:
        st.error(str(exc))
else:
    if not documents:
        st.info('관리자가 지침서를 등록하면 AI 채팅을 사용할 수 있습니다.')
    if not turns:
        empty_state(disabled=not selected)
    for i, turn in enumerate(turns):
        show_turn(turn, i)
    with st.container(key='conversation_tools'):
        follow_up = st.checkbox('이전 질문에 이어서 묻기', key='follow_up', disabled=not turns)
        st.caption('Enter 전송 · Shift+Enter 줄바꿈 · 환자 개인정보를 입력하지 마세요.')
    # 컨테이너 밖의 네이티브 chat_input은 모바일/데스크톱 하단에 고정됩니다.
    draft = st.chat_input('병원 지침에 대해 질문하세요…', key='question_input', max_chars=500,
                          disabled=not selected, submit_mode='disable', height='content')
    typed_question = draft.strip() if draft is not None else None
    if draft is not None and not typed_question:
        st.warning('질문을 입력해 주세요.')
    pending = st.session_state.pop('pending_question', None)
    question = typed_question or (pending['question'] if pending else None)
    if question:
        start = time.perf_counter()
        try:
            previous = turns[-1]["query"] if turns else ""
            previous_answer = turns[-1].get('answer') if turns else None
            previous_cited = {e.chunk_id for s in previous_answer.statements for e in s.evidence} if previous_answer else set()
            previous_sources = [hit.chunk.document_id for hit in turns[-1].get('hits', [])
                                if hit.chunk.id in previous_cited] if turns else []
            plan = (pending.get('plan') if pending and not typed_question else None) or plan_query(
                question, previous, follow_up, documents, previous_sources)
            query = plan.query
            turn = dict(question=question, query=query, plan=plan, hits=[], answer=None, error=None)
            if plan.clarification:
                turn.update(clarification=plan.clarification, elapsed=time.perf_counter() - start)
                st.session_state['turns'] = (turns + [turn])[-8:]
                st.rerun()
            # 현재 직원의 같은 대화에서만 재사용합니다. 문서/모델/검색 버전 변경 시 대화가 초기화됩니다.
            cached = next((t for t in reversed(turns) if t["query"] == query
                           and t.get('plan') == plan
                           and t.get("answer") and t["answer"].answerable and not t.get("error")), None)
            if cached and not pending:
                library.ensure_revision(revision)
                turns.append({**cached, "question": question, "elapsed": time.perf_counter() - start, "reused": True})
                st.session_state["turns"] = turns[-8:]
                st.rerun()
            with st.status("관련 지침을 찾고 있습니다…", expanded=False) as status:
                library.ensure_revision(revision)
                model = search_model()
                vector = model.encode([bounded_embedding_question(plan.expanded, model)])[0]
                hits = library.search(query, vector, selected, settings.min_similarity, plan=plan)
                turn["hits"] = hits
                turn["search_elapsed"] = time.perf_counter() - start
                if hits:
                    ai_start = time.perf_counter()
                    status.update(label="근거를 확인하고 있습니다…")
                    # 권한과 문서 버전을 재확인한 뒤 검색된 일부 chunk만 AI에 보냅니다.
                    library.ensure_revision(revision)
                    try:
                        status.update(label="답변을 작성하고 있습니다…")
                        answer, used = generate(settings, query, hits, user_id, plan=plan)
                        turn["answer"], turn["hits"] = answer, used
                    except GuideError as exc:
                        turn["error"] = str(exc)
                        if getattr(exc, "retry_after", None):
                            turn["retry_at"] = time.time() + exc.retry_after
                    finally:
                        turn["ai_elapsed"] = time.perf_counter() - ai_start
                library.ensure_revision(revision)
                status.update(label="확인 완료", state="complete")
            turn["elapsed"] = time.perf_counter() - start
            turns.append(turn)
            st.session_state["turns"] = turns[-8:]
            st.rerun()
        except GuideError as exc:
            # 입력 오류로 이전 대화를 지우지 않습니다. 다음 실행에서도 권한과 문서 버전을 검사합니다.
            st.error(str(exc))
