"""실제 병원 자료나 외부 API 없이, 원문 위치와 세션 분리를 확인합니다."""

from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from pypdf import PdfWriter
from reportlab.pdfgen import canvas
from streamlit.testing.v1 import AppTest

from mvp.documents import MAX_FILE_BYTES, PdfInputError, read_pdf

APP = Path(__file__).resolve().parents[1] / "mvp" / "app.py"


def sample_pdf() -> bytes:
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.drawString(72, 720, "Training room: reserve 2 days before use.")
    pdf.showPage()
    pdf.showPage()  # 빈 페이지도 출처 번호 검사에 포함합니다.
    pdf.drawString(72, 720, "Document desk: return 3 copies. Do not discard.")
    pdf.save()
    return buffer.getvalue()


def uploaded_pdf(data: bytes, name: str = "training.pdf") -> BytesIO:
    file = BytesIO(data)
    file.name = name
    return file


def test_page_numbers_survive_blank_page_and_text_is_preserved():
    document = read_pdf("training.pdf", sample_pdf())
    assert [page.number for page in document.pages] == [1, 2, 3]
    assert document.pages[1].text == ""
    assert document.pages[2].text == "Document desk: return 3 copies. Do not discard."
    assert document.text_page_count == 2


@pytest.mark.parametrize(
    ("name", "data", "code"),
    [
        ("training.txt", b"hello", "PDF_TYPE"),
        ("training.pdf", b"", "PDF_EMPTY"),
        ("training.pdf", b"not a pdf", "PDF_FORMAT"),
        ("training.pdf", b"%PDF-1.7\ninvalid data", "PDF_READ"),
        ("training.pdf", b"%PDF-" + b"x" * MAX_FILE_BYTES, "PDF_SIZE"),
    ],
    ids=["wrong_type", "empty", "wrong_format", "damaged", "too_large"],
)
def test_bad_upload_has_clear_error_without_exposing_input(name, data, code):
    with pytest.raises(PdfInputError, match=code):
        read_pdf(name, data)


def test_encrypted_pdf_has_separate_error():
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("test-only")
    buffer = BytesIO()
    writer.write(buffer)
    with pytest.raises(PdfInputError, match="PDF_ENCRYPTED"):
        read_pdf("locked.pdf", buffer.getvalue())


def test_failed_extraction_keeps_original_page_number():
    from pypdf._page import PageObject

    original = PageObject.extract_text
    calls = 0

    def fail_second_page(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("must not expose document text")
        return original(self, *args, **kwargs)

    with patch.object(PageObject, "extract_text", fail_second_page):
        document = read_pdf("training.pdf", sample_pdf())
    assert document.pages[1].number == 2
    assert document.pages[1].error
    assert "must not expose" not in document.pages[1].error
    assert document.pages[2].number == 3
    assert "return 3 copies" in document.pages[2].text


def test_upload_page_change_cached_extraction_and_bad_replacement(monkeypatch):
    upload = uploaded_pdf(sample_pdf())
    monkeypatch.setattr("streamlit.file_uploader", lambda *args, **kwargs: upload)
    with patch("mvp.documents.read_pdf", wraps=read_pdf) as reader:
        app = AppTest.from_file(str(APP), default_timeout=30).run()
        assert not app.exception
        assert app.session_state["pdf_document"].text_page_count == 2
        app.selectbox[0].set_value(3).run()
        assert not app.exception
        assert "return 3 copies" in app.text_area[0].value
        assert any("PDF 3페이지" in text.value for text in app.text)
        assert reader.call_count == 1

        # 새 파일이 손상됐어도 이전 문서를 표시하면 안 됩니다.
        upload = uploaded_pdf(b"%PDF-1.7\ninvalid data", "broken.pdf")
        app.run()
        assert not app.exception
        assert any("PDF_READ" in error.value for error in app.error)
        assert "pdf_document" not in app.session_state


def test_upload_removal_and_other_session_do_not_retain_document(monkeypatch):
    upload = uploaded_pdf(sample_pdf())
    monkeypatch.setattr("streamlit.file_uploader", lambda *args, **kwargs: upload)
    first = AppTest.from_file(str(APP), default_timeout=30).run()
    assert "pdf_document" in first.session_state
    upload = None
    other = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not other.exception
    assert "pdf_document" not in other.session_state
    first.run()
    assert not first.exception
    assert "pdf_document" not in first.session_state
