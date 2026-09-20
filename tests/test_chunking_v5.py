import pytest

from src.chunking import repeated_edge_label_count, repeated_edge_labels, validate_chunk_texts
from src.documents import PdfDocument, PdfPage
from src.library import has_substantive_body, make_chunks
from src.settings import GuideError


class CharacterCounter:
    @staticmethod
    def count(text):
        return len(text)


def test_repeated_page_heading_is_detected_without_classifying_body_text():
    pages = (
        PdfPage(1, "간호실무지침\n첫 문장입니다.\n둘째 문장입니다.\n간호실무지침\n"
                   "셋째 문장입니다.\n넷째 문장입니다.\n다섯째 문장입니다."),
        PdfPage(2, "간호실무지침\n둘째 페이지 내용입니다."),
        PdfPage(3, "간호실무지침\n셋째 페이지 내용입니다."),
    )

    labels = repeated_edge_labels(pages)

    assert labels == frozenset({"간호실무지침"})
    assert repeated_edge_label_count(pages, labels) == 3


def test_repeated_table_rows_and_complete_sentences_are_not_page_labels():
    pages = (
        PdfPage(1, "종류 | 적혈구 | 혈소판\n환자 상태를 확인한다."),
        PdfPage(2, "종류 | 적혈구 | 혈소판\n환자 상태를 확인한다."),
    )

    assert repeated_edge_labels(pages) == frozenset()


def test_quality_summary_counts_empty_oversized_and_duplicate_texts():
    summary = validate_chunk_texts(["정상 내용", "", "123456", "정상   내용"], len, max_tokens=5)

    assert summary == {
        "empty": 1,
        "too_long": 2,
        "duplicates": 1,
        "max_tokens": 7,
    }


def test_make_chunks_audits_labels_and_heading_markers():
    document = PdfDocument(
        "진정간호.pdf",
        (
            PdfPage(1, "간호실무지침\n\n4. 범위\n\n첫 번째 적용 범위를 확인한다."),
            PdfPage(2, "간호실무지침\n\n5. 절차\n\n두 번째 시행 절차를 확인한다."),
            PdfPage(3, "간호실무지침\n\n6. 기록\n\n세 번째 기록 내용을 확인한다."),
        ),
    )

    metadata, chunks = make_chunks(document, CharacterCounter())

    assert any(chunk.text == "간호실무지침" for chunk in chunks)
    heading = next(chunk for chunk in chunks if chunk.text == "4. 범위")
    assert not has_substantive_body(heading)
    assert any(chunk.section.endswith("4. 범위") for chunk in chunks)
    assert metadata["chunk_quality"]["repeated_labels_detected"] == 3
    assert metadata["chunk_quality"]["heading_markers_detected"] == 3


def test_make_chunks_reports_quality_and_keeps_every_chunk_within_limit():
    body = "• 적용되지 않는 경우: " + "환자 상태를 세심하게 확인한다. " * 10
    document = PdfDocument("진정간호.pdf", (PdfPage(1, body),))

    metadata, chunks = make_chunks(document, CharacterCounter())

    assert chunks
    assert all(len(chunk.text) <= 110 for chunk in chunks)
    assert metadata["chunk_quality"] == {
        "status": "통과",
        "empty": 0,
        "too_long": 0,
        "duplicates": 0,
        "max_tokens": max(len(chunk.text) for chunk in chunks),
        "repeated_labels_detected": 0,
        "heading_markers_detected": 0,
    }


def test_version_five_keeps_version_four_chunk_text_and_order():
    document = PdfDocument(
        "비교.pdf",
        (PdfPage(1, "4. 범위\n\n" + "환자 상태를 확인한다. " * 15),),
    )

    legacy_metadata, legacy = make_chunks(document, CharacterCounter(), _chunk_version=4)
    current_metadata, current = make_chunks(document, CharacterCounter())

    assert legacy_metadata["chunk_version"] == 4
    assert "chunk_quality" not in legacy_metadata
    assert current_metadata["chunk_version"] == 5
    assert [chunk.text for chunk in current] == [chunk.text for chunk in legacy]


def test_make_chunks_stops_before_returning_invalid_quality(monkeypatch):
    document = PdfDocument("오류.pdf", (PdfPage(1, "정상적으로 추출된 본문입니다."),))
    monkeypatch.setattr(
        "src.library.validate_chunk_texts",
        lambda *args, **kwargs: {"empty": 0, "too_long": 1, "duplicates": 0, "max_tokens": 111},
    )

    with pytest.raises(GuideError, match="CHUNK_QUALITY"):
        make_chunks(document, CharacterCounter())
