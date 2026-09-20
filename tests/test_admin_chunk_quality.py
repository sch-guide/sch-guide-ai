from src.admin_ui import chunk_quality_caption


def test_chunk_quality_caption_explains_result_in_plain_language():
    document = {
        "chunk_quality": {
            "status": "통과",
            "repeated_labels_detected": 7,
            "heading_markers_detected": 2,
            "duplicates": 1,
            "max_tokens": 103,
        }
    }

    assert chunk_quality_caption(document) == (
        "자동검사 통과 · 반복 머리말 7개는 검색 위치 표지로 유지하고, "
        "짧은 제목 2개는 답변 본문에서 자동 제외합니다. "
        "같은 내용 1개 · 가장 긴 조각 103/110(제한 안쪽)"
    )


def test_chunk_quality_caption_marks_documents_that_need_reindexing():
    assert chunk_quality_caption({}) == "새 자동검사 전 문서입니다. 재색인하면 검사 결과를 볼 수 있습니다."
