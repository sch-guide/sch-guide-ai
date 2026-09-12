"""합성 chunk로 BM25 기준선 출력·권한·재현 계약을 검증합니다."""

import csv
import json

import pytest

from mvp.bm25_evaluation import (
    CSV_COLUMNS,
    DEFAULT_QUESTIONS,
    evaluate_bm25,
    report_csv,
    save_bm25_artifacts,
)
from mvp.library import Chunk
from mvp.settings import GuideError


def chunk(index, text, *, document="doc", name="가상지침.pdf", page=None, section=""):
    return Chunk(
        f"chunk-{index}",
        document,
        name,
        page if page is not None else index + 1,
        "가상 지침",
        section,
        None,
        text,
        index,
    )


class FixtureAuth:
    def __init__(self, admin=True):
        self.admin = admin
        self.checks = 0

    def require_admin(self):
        self.checks += 1
        if not self.admin:
            raise GuideError("관리자만 사용할 수 있습니다. (ADMIN_REQUIRED)")


class FixtureLibrary:
    def __init__(self, chunks, admin=True):
        self.auth = FixtureAuth(admin)
        self._chunks = chunks
        self._revision = "fixture-revision"
        self.ensure_calls = []

    def revision(self):
        return self._revision

    def documents(self):
        identifiers = list(dict.fromkeys(item.document_id for item in self._chunks))
        return [
            {
                "id": identifier,
                "document_name": next(c.document_name for c in self._chunks if c.document_id == identifier),
                "file_hash": identifier * 8,
                "indexed_at": "2026-09-12T00:00:00Z",
                "updated_date": None,
                "chunk_version": 4,
            }
            for identifier in identifiers
        ]

    def diagnostic_chunks(self, document_id):
        self.auth.require_admin()
        return [item for item in self._chunks if item.document_id == document_id]

    def ensure_revision(self, revision):
        self.ensure_calls.append(revision)
        if revision != self._revision:
            raise GuideError("corpus changed")


def fixture_chunks():
    return [
        chunk(0, "진정간호 목적은 검사 중 안전한 관찰을 위한 가상 설명입니다.", section="진정간호 > 목적"),
        chunk(1, "진정간호 절차를 설명하는 가상 교육 문장입니다.", section="진정간호 > 절차"),
        chunk(2, "CRE 격리 기준을 확인하는 가상 검색 문장입니다.", section="감염관리 > CRE"),
        chunk(3, "PCN irrigation 방법을 찾기 위한 가상 문장입니다.", section="비뇨기계 > PCN 관리"),
    ] + [chunk(index, f"교육실 이용 안내 가상 문장 {index}입니다.") for index in range(4, 14)]


def test_batch_evaluation_uses_only_bm25_and_keeps_raw_top_ten():
    library = FixtureLibrary(fixture_chunks())
    report = evaluate_bm25(
        library,
        ["진정간호 목적은?", "xyznomatchtoken"],
        generated_at="2026-09-12T00:00:00+00:00",
    )
    assert report["dataset_kind"] == "registered_guideline_chunks"
    assert report["document_count"] == 1
    assert report["chunk_count"] == 14
    assert report["question_count"] == 2
    assert report["queries"][0]["top10"][0]["chunk_id"] == "chunk-0"
    assert report["queries"][0]["top10"][0]["bm25_score"] > 0
    assert len(report["queries"][0]["top10"]) == 10
    assert report["queries"][1]["positive_result_count"] == 0
    assert all(row["bm25_score"] == 0 for row in report["queries"][1]["top10"])
    assert library.ensure_calls == ["fixture-revision"]
    assert library.auth.checks >= 3


def test_query_report_keeps_original_actual_and_expanded_forms():
    report = evaluate_bm25(FixtureLibrary(fixture_chunks()), ["PCN irrigation 방법은?"])
    query = report["queries"][0]
    assert query["original_query"] == "PCN irrigation 방법은?"
    assert query["actual_query"] == "PCN irrigation 방법은?"
    assert "세척" in query["expanded_query"]
    assert query["top10"][0]["document_name"] == "가상지침.pdf"


def test_csv_has_exact_required_columns_and_artifacts_round_trip(tmp_path):
    report = evaluate_bm25(FixtureLibrary(fixture_chunks()), DEFAULT_QUESTIONS)
    csv_data = report_csv(report).decode("utf-8-sig")
    rows = list(csv.DictReader(csv_data.splitlines()))
    assert tuple(rows[0]) == CSV_COLUMNS
    assert len(rows) == len(DEFAULT_QUESTIONS) * 10
    assert rows[0]["chunk_text"]

    csv_path, json_path = save_bm25_artifacts(report, tmp_path / "artifacts")
    assert csv_path.name == "bm25_results.csv"
    assert json_path.name == "bm25_results.json"
    assert csv_path.read_bytes().startswith(b"\xef\xbb\xbf")
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved["corpus_fingerprint"] == report["corpus_fingerprint"]
    assert saved["queries"][0]["top10"][0]["chunk_text"]


def test_staff_and_invalid_document_scope_cannot_run_evaluation():
    with pytest.raises(GuideError, match="ADMIN_REQUIRED"):
        evaluate_bm25(FixtureLibrary(fixture_chunks(), admin=False), DEFAULT_QUESTIONS)
    with pytest.raises(GuideError, match="BM25_DOCUMENTS"):
        evaluate_bm25(FixtureLibrary(fixture_chunks()), DEFAULT_QUESTIONS, ["missing"])


@pytest.mark.parametrize("questions", [[], [" "], [f"질문 {i}" for i in range(51)], ["가" * 501]])
def test_invalid_question_sets_are_rejected(questions):
    with pytest.raises(GuideError, match="BM25_QUESTION"):
        evaluate_bm25(FixtureLibrary(fixture_chunks()), questions)
