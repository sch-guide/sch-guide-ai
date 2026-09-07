"""1단계: PDF를 읽고 실제 페이지 번호와 추출된 글을 함께 보관합니다."""

from dataclasses import dataclass
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PyPdfError

MAX_FILE_MB = 20
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024
MAX_PAGES = 1000


class PdfInputError(ValueError):
    """화면에 그대로 표시해도 되는, 원문을 포함하지 않는 오류입니다."""


@dataclass(frozen=True)
class PdfPage:
    # PDF의 첫 장이 1페이지입니다. 인쇄된 쪽수와 다를 수 있습니다.
    number: int
    text: str
    error: str | None = None


@dataclass(frozen=True)
class PdfDocument:
    document_name: str
    pages: tuple[PdfPage, ...]

    @property
    def text_page_count(self) -> int:
        return sum(bool(page.text) for page in self.pages)

    @property
    def character_count(self) -> int:
        return sum(len(page.text) for page in self.pages)


def read_pdf(file_name: str, content: bytes) -> PdfDocument:
    """외부 API 호출이나 파일 저장 없이, 전달받은 PDF 바이트만 읽습니다."""
    if not file_name.lower().endswith(".pdf"):
        raise PdfInputError("PDF 파일을 선택해 주세요. (PDF_TYPE)")
    if not content:
        raise PdfInputError("파일이 비어 있습니다. 원본 파일을 확인해 주세요. (PDF_EMPTY)")
    if len(content) > MAX_FILE_BYTES:
        raise PdfInputError(f"파일은 {MAX_FILE_MB}MB 이하로 선택해 주세요. (PDF_SIZE)")
    if not content.startswith(b"%PDF-"):
        raise PdfInputError("파일 내용이 PDF 형식이 아닙니다. PDF로 다시 저장해 주세요. (PDF_FORMAT)")

    try:
        # 잘못된 파일을 추측해서 복구하지 않고, 읽을 수 없으면 명확히 안내합니다.
        reader = PdfReader(BytesIO(content), strict=True)
        if reader.is_encrypted:
            raise PdfInputError(
                "암호화된 PDF는 이번 단계에서 지원하지 않습니다. "
                "암호가 해제된 사본을 선택해 주세요. (PDF_ENCRYPTED)"
            )
        page_count = len(reader.pages)
        if not page_count:
            raise PdfInputError("페이지가 없는 PDF입니다. 원본 파일을 확인해 주세요. (PDF_NO_PAGES)")
        if page_count > MAX_PAGES:
            raise PdfInputError(
                f"이번 단계에서는 {MAX_PAGES:,}페이지까지 읽습니다. 문서를 나누어 올려 주세요. (PDF_PAGES)"
            )
    except PdfInputError:
        raise
    except (PyPdfError, ValueError, TypeError, KeyError, IndexError, OSError, RecursionError):
        # 파서의 상세 예외에는 원문 일부가 들어갈 수 있어 사용자용 메시지만 제공합니다.
        raise PdfInputError(
            "PDF 구조를 읽을 수 없습니다. 원본을 열어 확인한 뒤 PDF로 다시 저장해 주세요. (PDF_READ)"
        ) from None

    pages = []
    for index in range(page_count):
        try:
            text = reader.pages[index].extract_text() or ""
            # 문장·수치·단위와 줄 순서를 보존합니다. 요약이나 내용 수정은 하지 않습니다.
            text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").strip()
            pages.append(PdfPage(number=index + 1, text=text))
        except (PyPdfError, ValueError, TypeError, KeyError, IndexError, OSError, RecursionError):
            # 실패한 페이지도 남겨, 이후 페이지의 출처 번호가 당겨지지 않도록 합니다.
            pages.append(
                PdfPage(number=index + 1, text="", error="이 페이지의 텍스트를 추출하지 못했습니다.")
            )

    # 파일명은 표시용으로만 쓰며 디스크 경로로 사용하지 않습니다.
    display_name = file_name.replace("\\", "/").rsplit("/", 1)[-1]
    return PdfDocument(document_name=display_name, pages=tuple(pages))
