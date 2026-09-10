"""관리자만 실행하는 등록·교체·삭제·재색인 화면."""

import streamlit as st

from mvp.ai import Quota
from mvp.auth import LocalAuth
from mvp.documents import MAX_FILE_MB, PdfInputError, read_document
from mvp.library import CHUNK_VERSION, protect_private
from mvp.operations import create_backup, verify_backup
from mvp.pdf_layout import EXTRACTION_VERSION, ocr_status
from mvp.settings import GuideError
from mvp.ui import document_summary

REVIEW_LABEL = "원본 지침서를 검토했으며, 환자 개인정보가 없는 등록용 자료입니다."


def finish(message, error=False):
    # 파일 업로더도 새로 만들어 업로드 임시 바이트를 다음 화면에 남기지 않습니다.
    st.session_state["upload_epoch"] = st.session_state.get("upload_epoch", 0) + 1
    st.session_state["notice_error" if error else "notice"] = message
    st.rerun()


def extraction_status(document):
    """관리자에게 추출 상태만 알립니다. 별도 PDF 원문 보기 도구는 제공하지 않습니다."""
    for warning in document.warnings:
        st.warning(warning)
    missing = sum(bool(p.error) or not p.text for p in document.pages)
    unit = "페이지" if document.source_type == "pdf" else "문단·표·행"
    st.caption(f"텍스트 추출: {len(document.pages) - missing} / {len(document.pages)}개 {unit}")
    if missing:
        st.warning("글을 읽지 못한 페이지·영역은 검색에 포함되지 않습니다. 원본 파일에서 확인해 주세요.")
        st.text("확인할 위치: " + ", ".join(str(p.number) if p.number is not None else p.location
                                          for p in document.pages if p.error or not p.text))


@st.dialog('지침서 삭제 확인', width='small', dismissible=True)
def delete_document_dialog(library, doc_id, document_name):
    st.warning('삭제하면 이 문서의 검색 인덱스와 승인 체크리스트도 함께 제거됩니다.')
    st.write(document_name)
    confirmed = st.checkbox('선택한 지침서를 삭제하는 것을 확인했습니다.', key='confirm_delete_' + doc_id)
    if st.button('지침서 삭제', type='primary', disabled=not confirmed, width='stretch'):
        try:
            library.retire(doc_id)
            finish('문서와 연결된 검색 데이터를 삭제했습니다.')
        except GuideError as exc:
            finish(str(exc), error=True)

def render_admin(library, auth, settings, model_factory):
    # 탭을 숨기는 것과 별개로 서버에서 관리자 권한을 다시 확인합니다.
    auth.require_admin()
    docs = library.documents(all_status=True)
    with st.container(key='admin_heading'):
        heading, action = st.columns([3, 1], vertical_alignment='center')
        with heading:
            st.subheader('지침서 관리')
            st.caption('병원 지침서를 등록하고 최신 검색 상태를 관리하세요.')
        with action:
            if st.button('문서 추가', icon=':material/add:', type='primary', width='stretch'):
                st.session_state['show_upload'] = True
    document_summary(docs)
    ready = {d["id"]: d["document_name"] for d in docs if d["status"] == "ready"}
    labels = {"ready": "● 검색 가능", "pending": "○ 색인 대기", "error": "⚠ 색인 실패",
              "replaced": "교체 완료", "retired": "교체·삭제 완료", "deleted": "삭제 완료"}
    if docs:
        st.caption('등록 문서 · 행을 선택하면 아래에서 해당 문서를 관리할 수 있습니다.')
        rows = [{"문서명": d["document_name"], "페이지/영역": d.get('page_count', 0),
            "상태": labels[d["status"]] + (" · 확인 필요" if d["last_error"] else ""),
            "업데이트(UTC)": (d.get("indexed_at") or d["created_at"])[:10], "관리": "행 선택"} for d in docs]
        table = st.dataframe(rows, hide_index=True, width='stretch', on_select='rerun',
                             selection_mode='single-row', key='document_table',
                             column_config={'문서명': st.column_config.TextColumn(width='large')})
        if table.selection.rows and table.selection.rows[0] < len(docs):
            selected_id = docs[table.selection.rows[0]]['id']
            if st.session_state.get('last_table_choice') != selected_id:
                st.session_state['manage_document'] = selected_id
                st.session_state['last_table_choice'] = selected_id
        outdated = [d for d in docs if d["status"] == "ready" and
                    (d.get("extraction_version", 1) < EXTRACTION_VERSION or d.get('chunk_version', 1) < CHUNK_VERSION)]
        if outdated:
            st.info(f"기존 방식으로 추출된 문서 {len(outdated)}개가 있습니다. 재색인하면 표·문맥 보완이 반영됩니다.")
        with st.expander("등록 문서의 추출 누락·검토 사항"):
            for doc in docs:
                if doc["status"] != "ready":
                    continue
                st.text(doc["document_name"])
                if doc.get("missing_locations"):
                    st.warning("검색에서 빠진 위치: " + ", ".join(map(str, doc["missing_locations"])))
                for warning in doc.get("warnings", []):
                    st.caption(warning)
                if not doc.get("warnings") and not doc.get("missing_locations"):
                    st.caption("저장된 추출 경고 없음 · 내용의 정확성을 자동 보증하지는 않습니다.")
    else:
        st.info("등록된 지침서가 없습니다. 검색할 병원 지침서를 등록하세요.")

    with st.expander("새 지침서 등록 / 기존 지침서 교체", icon=':material/upload_file:',
                     expanded=not docs or st.session_state.get('show_upload', False)):
        replacement = st.selectbox("등록 방식", [""] + list(ready),
            format_func=lambda x: "새 지침서 등록" if not x else "교체: " + ready[x])
        epoch = st.session_state.get("upload_epoch", 0)
        upload = st.file_uploader("PDF / Word / Excel 지침서", type=["pdf", "docx", "xlsx"],
                                  max_upload_size=MAX_FILE_MB, key=f"upload_{epoch}")
        st.caption(".doc·.xls는 .docx·.xlsx로 변환하세요. 스캔 PDF에는 OCR이 필요합니다.")
        if upload is not None:
            try:
                # 등록 가능 여부만 확인합니다. 파싱 결과나 원본을 session_state에 저장하지 않습니다.
                document = read_document(upload.name, upload.getvalue(), ocr=settings.ocr_enabled)
                protect_private(document.document_name + "\n" + "\n".join(p.text for p in document.pages))
                extraction_status(document)
                with st.form(f"register_{epoch}"):
                    title = st.text_input("문서 제목", value=document.document_name, max_chars=250)
                    section = st.text_input("공통 항목·분류", max_chars=250)
                    updated = st.date_input("원문 개정일 (모르면 비워두세요)", value=None)
                    reviewed = st.checkbox(REVIEW_LABEL)
                    submitted = st.form_submit_button("저장하고 검색 인덱스 생성", type="primary")
                if submitted:
                    if not reviewed:
                        raise GuideError("원본 지침서와 개인정보 여부를 확인한 뒤 등록하세요.")
                    try:
                        with st.status('1/4 · 원본 파일을 확인하고 있습니다…', expanded=True) as progress:
                            progress.write('2/4 · 문서에서 텍스트와 페이지 위치를 추출합니다.')
                            progress.write('3/4 · 관련 단위로 나누고 검색 embedding을 생성합니다.')
                            library.register(upload.name, upload.getvalue(), model_factory(), title=title, section=section,
                                updated_date=updated.isoformat() if updated else None, replaces_id=replacement or None)
                            progress.write('4/4 · 검색 저장소에 반영하고 권한을 확인합니다.')
                            progress.update(label='검색 준비 완료', state='complete', expanded=False)
                    except GuideError as exc:
                        finish(str(exc), error=True)
                    finish("검색 준비가 완료되었습니다. AI 채팅에서 질문하세요.")
            except (PdfInputError, GuideError) as exc:
                st.error(str(exc))

    if docs:
        with st.expander("문서 재색인 / 삭제", expanded=bool(st.session_state.get('last_table_choice'))):
            by_id = {d["id"]: d for d in docs}
            chosen = st.selectbox("관리할 지침서", list(by_id),
                format_func=lambda x: by_id[x]["document_name"] + " · " + labels[by_id[x]["status"]] + " · " + x[:8],
                key='manage_document')
            doc = by_id[chosen]
            st.caption(f"검색 문단 {doc.get('chunk_count', 0)}개 · 임베딩 재사용 {doc.get('embedding_reused', 0)}개 · 새 계산 {doc.get('embedding_computed', 0)}개")
            st.caption('등록일(UTC): ' + doc['created_at'][:10])
            st.caption('마지막 색인(UTC): ' + (doc.get('indexed_at') or '미생성'))
            if doc["last_error"]:
                st.warning(doc["last_error"])
            if doc["status"] in {"pending", "error", "ready"}:
                if st.button("이 문서 검색 인덱스 재생성"):
                    try:
                        with st.spinner("문서를 다시 읽고 검색 인덱스를 생성합니다…"):
                            library.reindex(chosen, model_factory())
                        finish("재색인을 완료했습니다. AI 채팅에서 변경된 지침서를 검색할 수 있습니다.")
                    except GuideError as exc:
                        finish(str(exc), error=True)
            if st.button('선택 문서 삭제', icon=':material/delete:', width='stretch'):
                delete_document_dialog(library, chosen, doc['document_name'])
        with st.expander("전체 검색 인덱스 재생성"):
            st.caption("문서별로 새 색인이 완성된 뒤 검색에 반영합니다.")
            if st.button("등록 중인 모든 문서 재색인"):
                failures = 0
                for doc in docs:
                    if doc["status"] in {"pending", "error", "ready"}:
                        try:
                            with st.spinner("검색 인덱스를 재생성합니다…"):
                                library.reindex(doc["id"], model_factory())
                        except GuideError:
                            failures += 1
                finish(f"재색인 처리 완료 · 실패 {failures}개. 문서 상태를 확인하세요.", error=bool(failures))

    with st.expander("관리자 설정 안내 / 직원 계정"):
        st.write("설정 파일: mvp/.env · API 키는 화면이나 GitHub에 올리지 마세요.")
        st.text("원본 저장소: " + settings.storage_backend)
        st.write("검색은 " + ("Supabase pgvector" if settings.mode == "staff" else "이 PC의 FAISS") + "에서 실행하며, AI 서버에는 검색된 일부 문단만 전달합니다.")
        st.caption(f"최근 24시간 AI 제한: 전체 {settings.daily_limit}회 / 직원별 {settings.user_daily_limit}회")
        usage = Quota().summary(settings)
        left, middle, right = st.columns(3)
        left.metric("최근 24시간 AI 요청", f"{usage['calls']}회")
        middle.metric("앱의 남은 요청 한도", f"{usage['calls_remaining']}회")
        right.metric("최근 24시간 토큰", f"{usage['tokens']:,}")
        recent = st.session_state.get('turns', [])
        times = [t['ai_elapsed'] for t in recent if 'ai_elapsed' in t and not t.get('error') and not t.get('reused')]
        limits = sum('AI_RATE' in (t.get('error') or '') or 'AI_LIMIT' in (t.get('error') or '') for t in recent)
        if times:
            st.caption(f'현재 대화 · AI 생성·검증 평균 {sum(times)/len(times):.2f}초 · 대기/한도 안내 {limits}회')
        else:
            st.caption(f'현재 대화 · 측정된 AI 응답 없음 · 대기/한도 안내 {limits}회')
        st.caption('위 응답 시간은 이 로그인 세션의 대화 기준이며 전체 직원 통계나 응답 시작 시간은 아닙니다.')
        st.caption("앱이 기록한 사용량이며 Groq 계정의 전체 사용량·과금 상태와는 다릅니다. 한도를 자동으로 높이지 않습니다.")
        if settings.mode == "local":
            st.info("현재 이 PC용 로그인입니다. 직원 배포용 Supabase·HTTPS 연결은 아직 적용되지 않았습니다.")
        st.caption("OCR: " + ("사용 설정됨 · " if settings.ocr_enabled else "사용 안 함 · ") + ocr_status()[1])
        st.caption("설치 후 mvp/.env의 GUIDE_OCR_ENABLED=true로 활성화하고 문서를 재색인하세요.")
        if settings.storage_backend == "local" and st.button("지침서·계정·색인 백업"):
            with st.spinner("서버의 별도 백업 폴더에 저장합니다…"):
                backup = create_backup(library)
                if not verify_backup(backup):
                    raise GuideError("백업 파일 검증에 실패했습니다. (BACKUP)")
            st.success("백업과 무결성 검사를 완료했습니다.")
            st.text(str(backup))
        if isinstance(auth, LocalAuth):
            account_editor(auth)
        else:
            st.write("Supabase Authentication에서 계정을 만들고 guide_profiles에 역할과 active 상태를 등록하세요.")
            st.caption("자세한 SQL·Storage 설정은 mvp/README.md에 있습니다.")


def account_editor(auth):
    st.caption("이 PC 계정입니다. 직원 배포에는 Supabase 로그인 모드를 사용하세요.")
    users = auth.users()
    st.dataframe([{"아이디": u["username"], "역할": u["role"], "활성": bool(u["active"])} for u in users],
                 hide_index=True)
    with st.form("create_staff", clear_on_submit=True):
        username = st.text_input("새 직원 아이디")
        password = st.text_input("새 직원 비밀번호 (10자 이상)", type="password")
        submitted = st.form_submit_button("직원 계정 등록")
    if submitted:
        try:
            auth.create_user(username, password)
            finish("직원 계정을 등록했습니다.")
        except GuideError as exc:
            st.error(str(exc))
    candidates = {u["id"]: u["username"] for u in users if u["active"] and u["id"] != auth.user_id}
    if candidates:
        user_id = st.selectbox("사용을 중지할 계정", list(candidates), format_func=candidates.get)
        if st.button("선택 직원 계정 비활성화"):
            auth.deactivate(user_id)
            finish("직원 계정을 비활성화했습니다.")
