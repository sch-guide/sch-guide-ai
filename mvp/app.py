"""실행: 프로젝트 루트에서 python -m streamlit run mvp/app.py --server.port 8502"""

import hashlib

import streamlit as st

from mvp.documents import MAX_FILE_MB, PdfInputError, read_pdf

st.set_page_config(page_title="병원 실무지침 AI · PDF 확인", page_icon="📘", layout="wide")
st.markdown(
    """
    <style>
    .block-container { max-width: 1120px; padding-top: 2.5rem; }
    h1 { letter-spacing: -.045em; }
    [data-testid="stSidebar"] { border-right: 1px solid #deeaee; }
    [data-testid="stTextArea"] textarea:disabled {
        color: #172B3A; -webkit-text-fill-color: #172B3A; opacity: 1;
        font-size: 15px; line-height: 1.7;
    }
    [data-testid="stMetric"] {
        border: 1px solid #dce7eb; border-radius: 14px; padding: 16px; background: white;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def clear_preview() -> None:
    """다른 파일을 선택했을 때 이전 문서 내용이 남지 않게 지웁니다."""
    for key in ("pdf_document", "pdf_signature", "pdf_error", "pdf_page"):
        st.session_state.pop(key, None)


def reset_upload() -> None:
    clear_preview()
    # 업로드 위젯도 새로 만들어 서버 세션에서 이전 파일을 해제합니다.
    st.session_state["upload_generation"] = st.session_state.get("upload_generation", 0) + 1


with st.sidebar:
    st.markdown("### 📘 병원 실무지침 AI")
    st.caption("1단계 · PDF 원문 확인")
    st.divider()
    uploaded = st.file_uploader(
        "PDF 지침서 선택",
        type=["pdf"],
        max_upload_size=MAX_FILE_MB,
        key=f"pdf_upload_{st.session_state.get('upload_generation', 0)}",
        help="텍스트를 선택할 수 있는 PDF로 먼저 확인해 주세요.",
    )
    st.caption(f"파일당 최대 {MAX_FILE_MB}MB · 한 번에 문서 1개")
    st.button("업로드 지우기", on_click=reset_upload, width="stretch")
    st.divider()
    st.caption("API 키 없이 사용합니다.")
    st.caption("문서는 앱을 실행하는 서버에서 읽습니다. 외부 AI로는 전송하지 않습니다.")
    st.caption("인터넷에 배포한 앱에서는 PDF가 해당 서비스의 서버로 전송됩니다.")
    st.caption("환자 이름·등록번호 등 개인정보가 없는 지침서를 사용해 주세요.")

st.caption("PDF 업로드  →  페이지별 원문 확인")
st.title("병원 실무지침 AI")
st.write("지침서를 올리고, 원문과 페이지가 정확히 읽히는지 확인하세요.")

if uploaded is None:
    clear_preview()
    with st.container(border=True):
        st.subheader("왼쪽에서 PDF를 선택해 주세요")
        st.write("파일을 올리면 전체 페이지 수와 페이지별 추출 내용을 확인할 수 있습니다.")
        st.caption("문서에 인쇄된 쪽수와 PDF의 실제 페이지 번호는 다를 수 있습니다.")
    cols = st.columns(3)
    for col, title, detail in zip(
        cols,
        ("1. PDF 업로드", "2. 원문 확인", "3. 검색·답변 연결"),
        (
            "개인정보가 없는 지침서를 선택합니다.",
            "페이지를 바꾸며 글과 숫자가 잘 읽혔는지 확인합니다.",
            "다음 단계에서 문서 검색과 AI 답변을 추가합니다.",
        ),
        strict=True,
    ):
        with col, st.container(border=True):
            st.markdown(f"**{title}**")
            st.caption(detail)
else:
    # Streamlit은 클릭할 때 스크립트를 다시 실행합니다.
    # 같은 파일은 현재 세션의 추출 결과를 재사용해 매번 읽지 않습니다.
    content = uploaded.getvalue()
    signature = (uploaded.name, hashlib.sha256(content).hexdigest())
    if st.session_state.get("pdf_signature") != signature:
        clear_preview()
        st.session_state["pdf_signature"] = signature
        try:
            with st.spinner("PDF의 페이지와 텍스트를 읽고 있습니다…"):
                st.session_state["pdf_document"] = read_pdf(uploaded.name, content)
        except PdfInputError as exc:
            st.session_state["pdf_error"] = str(exc)

    if st.session_state.get("pdf_error"):
        st.error(st.session_state["pdf_error"])

    document = st.session_state.get("pdf_document")
    if document:
        st.text(f"문서명: {document.document_name}")
        c1, c2, c3 = st.columns(3)
        c1.metric("전체 페이지", f"{len(document.pages):,}")
        c2.metric("글이 추출된 페이지", f"{document.text_page_count:,}")
        c3.metric("추출 글자 수", f"{document.character_count:,}")

        empty_count = sum(not page.text and not page.error for page in document.pages)
        failed_count = sum(bool(page.error) for page in document.pages)
        if failed_count:
            st.warning(
                f"{failed_count}개 페이지를 읽지 못했습니다. "
                "아래 페이지별 상태를 확인하고 원본 PDF와 대조해 주세요."
            )
        if empty_count:
            st.warning(
                f"{empty_count}개 페이지에서 글을 찾지 못했습니다. "
                "빈 페이지이거나 이미지·스캔 페이지일 수 있습니다. "
                "스캔 문서의 글을 읽는 OCR은 다음 확장 단계에서 지원합니다."
            )
        if not empty_count and not failed_count:
            st.success("모든 페이지에서 글을 읽었습니다. 아래에서 원문과 비교해 주세요.")

        with st.expander("페이지별 추출 상태"):
            st.dataframe(
                [
                    {
                        "PDF 페이지": page.number,
                        "상태": "추출 실패" if page.error else ("글 추출됨" if page.text else "텍스트 없음"),
                        "글자 수": len(page.text),
                    }
                    for page in document.pages
                ],
                hide_index=True,
                width="stretch",
            )

        selected_page = st.selectbox(
            "확인할 페이지",
            options=list(range(1, len(document.pages) + 1)),
            format_func=lambda number: f"PDF {number}페이지",
            key="pdf_page",
        )
        page = document.pages[selected_page - 1]
        st.text(f"출처: {document.document_name} · PDF {page.number}페이지")
        if page.error:
            st.error(page.error)
        elif not page.text:
            st.info("이 페이지에는 추출된 텍스트가 없습니다. 원본 페이지를 확인해 주세요.")
        else:
            # 추출한 글은 HTML이나 명령으로 실행하지 않고 읽기 전용 텍스트로 보여줍니다.
            st.text_area(
                "이 페이지에서 추출한 원문",
                value=page.text,
                height=420,
                disabled=True,
                key=f"pdf_text_{signature[1]}_{page.number}",
            )
        st.caption(
            "위 내용은 PDF에서 추출한 글입니다. 표의 행·열, 여러 단의 읽기 순서, "
            "숫자·단위는 원본과 비교해 주세요. 제목과 개정일을 임의로 추측하지 않습니다."
        )

st.divider()
st.caption("현재 단계: PDF 읽기와 출처 확인 · 문서 검색과 AI 답변은 아직 연결하지 않았습니다.")
