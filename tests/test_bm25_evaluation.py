import csv
import json

import pytest

from src.library import Chunk
from src.retrieval import BM25Index, bm25_corpus_policy, rank_bm25_candidates
from tools.bm25_evaluate import (
    evaluate_chunks,
    render_review_html,
    safe_json_for_script,
    write_outputs,
)


def chunk(identifier, text, page=1, section="목적"):
    return Chunk(identifier, "doc", "가상지침.pdf", page, "가상 지침", section, None, text, page)


def test_engines_use_same_queries_and_chunks_without_mixing_rankings():
    chunks = [
        chunk("purpose", "진정간호 목적은 안전한 교육 절차를 확인하는 것입니다."),
        chunk("procedure", "진정간호 절차는 교육 확인표 순서대로 진행합니다.", page=2, section="절차"),
        chunk("other", "검색과 관계없는 병원 교육 안내입니다.", page=3, section="안내"),
    ]
    documents = [{"id": "doc", "document_name": "가상지침.pdf", "title": "가상 지침"}]

    result = evaluate_chunks(chunks, documents, ["진정간호 목적은?"], top_k=3)[0]

    assert result["schat_bm25"][0]["chunk_id"] == "purpose"
    assert result["rank_bm25"][0]["chunk_id"] == "purpose"
    assert {row["chunk_id"] for row in result["schat_bm25"]} == {chunk.id for chunk in chunks}
    assert {row["chunk_id"] for row in result["rank_bm25"]} == {chunk.id for chunk in chunks}
    assert all(row["engine"] == "schat_bm25" for row in result["schat_bm25"])
    assert all(row["engine"] == "rank_bm25" for row in result["rank_bm25"])
    assert "combined" not in result


def test_top_k_keeps_zero_scores_and_uses_chunk_order_for_ties():
    chunks = [chunk("first", "가상 교육 안내"), chunk("second", "다른 문서 내용", page=2)]
    documents = [{"id": "doc", "document_name": "가상지침.pdf", "title": "가상 지침"}]

    result = evaluate_chunks(chunks, documents, ["완전히없는검색어"], top_k=2)[0]

    for engine in ("schat_bm25", "rank_bm25"):
        assert [row["chunk_id"] for row in result[engine]] == ["first", "second"]
        assert all(row["zero_score"] for row in result[engine])


def test_invalid_question_and_top_k_are_rejected():
    chunks = [chunk("one", "가상 교육 안내")]
    documents = [{"id": "doc", "document_name": "가상지침.pdf"}]

    with pytest.raises(ValueError, match="1~500자"):
        evaluate_chunks(chunks, documents, [""], top_k=1)
    with pytest.raises(ValueError, match="1~100"):
        evaluate_chunks(chunks, documents, ["질문"], top_k=0)


def test_outputs_keep_engine_rows_separate_and_html_compares_side_by_side(tmp_path):
    chunks = [chunk("purpose", "진정간호 목적에 관한 가상 원문")]
    documents = [{"id": "doc", "document_name": "가상지침.pdf", "title": "가상 지침"}]
    queries = evaluate_chunks(chunks, documents, ["진정간호 목적은?"], top_k=1)
    report = {
        "generated_at": "2026-09-12T00:00:00+09:00",
        "documents": [],
        "corpus": {"chunk_count": 1},
        "queries": queries,
    }

    csv_path, schat_csv_path, baseline_csv_path, json_path, html_path = write_outputs(report, tmp_path)

    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    stored = json.loads(json_path.read_text(encoding="utf-8"))
    html = html_path.read_text(encoding="utf-8")
    assert [row["engine"] for row in rows] == ["schat_bm25", "rank_bm25"]
    with schat_csv_path.open(encoding="utf-8-sig", newline="") as handle:
        assert {row["engine"] for row in csv.DictReader(handle)} == {"schat_bm25"}
    with baseline_csv_path.open(encoding="utf-8-sig", newline="") as handle:
        assert {row["engine"] for row in csv.DictReader(handle)} == {"rank_bm25"}
    assert set(stored["queries"][0]) >= {"schat_bm25", "rank_bm25"}
    assert "SCHAT BM25Index Top 10" in html
    assert "rank-bm25 Top 10" in html
    assert "grid-template-columns:minmax(0,1fr) minmax(0,1fr)" in html


def test_embedded_report_escapes_script_termination_and_uses_text_content():
    value = {"chunk_text": "</script><img src=x onerror=alert(1)>"}

    encoded = safe_json_for_script(value)
    html = render_review_html(
        {
            "generated_at": "2026-09-12T00:00:00+09:00",
            "queries": [
                {
                    "query_id": "Q001",
                    "original_query": "질문",
                    "top10_overlap_chunk_ids": [],
                    "schat_bm25": [],
                    "rank_bm25": [],
                }
            ],
        }
    )

    assert "</script>" not in encoded
    assert "\\u003c/script\\u003e" in encoded
    assert "pre.textContent=row.chunk_text" in html


def test_explicit_section_title_is_excluded_and_inherited_by_next_body():
    chunks = [
        Chunk("heading", "doc", "가상지침.pdf", 1, "가상 지침", "7. 진정간호", None,
              "7. 진정간호", 0),
        Chunk("body", "doc", "가상지침.pdf", 1, "가상 지침", "", None,
              "환자의 불안을 줄이고 안전하게 치료하기 위한 충분한 본문입니다.", 1),
    ]

    policy = bm25_corpus_policy(chunks)
    scores = BM25Index(chunks).scores("진정간호")

    assert policy.searchable == (False, True)
    assert policy.inherited_titles == ("", "7. 진정간호")
    assert scores[0] == 0
    assert scores[1] > 0


def test_inherited_parent_title_does_not_reweight_body_with_own_section():
    chunks = [
        Chunk("heading", "doc", "가상지침.pdf", 1, "가상 지침", "15. 진정간호", None,
              "15. 진정간호", 0),
        Chunk("body", "doc", "가상지침.pdf", 1, "가상 지침", "1. 목적", None,
              "저혈압과 호흡 기능 억제를 예방하기 위한 충분한 본문입니다.", 1),
    ]

    index = BM25Index(chunks)
    scores = index.scores("진정간호")

    assert index.policy.inherited_titles == ("", "15. 진정간호")
    assert list(scores) == [0, 0]


def test_repeated_pdf_page_header_is_excluded_without_context_inheritance():
    chunks = [
        Chunk("header-1", "doc", "가상지침.pdf", 1, "가상 지침", "", None,
              "간호실무지침", 0),
        Chunk("body-1", "doc", "가상지침.pdf", 1, "가상 지침", "목적", None,
              "첫 페이지에서 환자를 평가하기 위한 충분한 본문입니다.", 1),
        Chunk("header-2", "doc", "가상지침.pdf", 2, "가상 지침", "", None,
              "간호실무지침", 2),
        Chunk("body-2", "doc", "가상지침.pdf", 2, "가상 지침", "절차", None,
              "둘째 페이지에서 절차를 설명하기 위한 충분한 본문입니다.", 3),
    ]

    policy = bm25_corpus_policy(chunks)
    scores = BM25Index(chunks).scores("간호실무지침")

    assert policy.searchable == (False, True, False, True)
    assert policy.inherited_titles == ("", "", "", "")
    assert list(scores) == [0, 0, 0, 0]


def test_short_clinical_instruction_is_not_excluded_only_for_being_short():
    chunks = [chunk("instruction", "금식 유지", section="주의사항")]

    policy = bm25_corpus_policy(chunks)
    scores = BM25Index(chunks).scores("금식 유지")

    assert policy.searchable == (True,)
    assert scores[0] > 0


def test_title_context_does_not_cross_document_boundary():
    chunks = [
        Chunk("heading", "doc-1", "첫지침.pdf", 1, "첫 지침", "특수 제목", None,
              "특수 제목", 0),
        Chunk("body", "doc-2", "둘째지침.pdf", 1, "둘째 지침", "본문", None,
              "다른 문서에 속한 충분히 긴 본문입니다.", 0),
    ]

    policy = bm25_corpus_policy(chunks)

    assert policy.searchable == (False, True)
    assert policy.inherited_titles == ("", "")


def test_all_title_only_corpus_keeps_score_positions_and_returns_zero():
    chunks = [
        Chunk("heading-1", "doc", "가상지침.pdf", 1, "가상 지침", "제목 하나", None,
              "제목 하나", 0),
        Chunk("heading-2", "doc", "가상지침.pdf", 1, "가상 지침", "제목 둘", None,
              "제목 둘", 1),
    ]

    scores = BM25Index(chunks).scores("제목")

    assert len(scores) == len(chunks)
    assert list(scores) == [0, 0]


def test_rank_bm25_baseline_does_not_apply_schat_title_policy():
    chunks = [
        Chunk("heading", "doc", "가상지침.pdf", 1, "가상 지침", "진정간호", None,
              "진정간호", 0),
        Chunk("body", "doc", "가상지침.pdf", 1, "가상 지침", "", None,
              "환자를 안전하게 치료하기 위한 충분한 본문입니다.", 1),
        Chunk("other", "doc", "가상지침.pdf", 2, "가상 지침", "안내", None,
              "교육과 안내에 관한 별도의 충분한 본문입니다.", 2),
    ]
    documents = [{"id": "doc", "document_name": "가상지침.pdf", "title": "가상 지침"}]

    result = evaluate_chunks(chunks, documents, ["진정간호"], top_k=2)[0]
    schat = {row["chunk_id"]: row["bm25_score"] for row in result["schat_bm25"]}
    baseline = {row["chunk_id"]: row["bm25_score"] for row in result["rank_bm25"]}

    assert schat["heading"] == 0
    assert schat["body"] > 0
    assert baseline["heading"] != 0


def test_temporal_ranking_uses_match_neutral_mismatch_tiers_stably():
    chunks = [
        chunk("before", "진정 치료 전 환자를 평가합니다."),
        chunk("neutral", "진정 환자를 평가합니다."),
        chunk("during", "진정 치료 중 환자를 관찰합니다."),
        chunk("all-phases", "진정 전·중·후 환자를 평가합니다."),
    ]
    scores = [1.0, 4.0, 3.0, 2.0]

    ranking = rank_bm25_candidates("진정 전 준비사항은?", chunks, scores)

    assert ranking.requested_phase == "before"
    assert [chunks[position].id for position in ranking.positions] == [
        "all-phases", "before", "neutral", "during"
    ]
    assert ranking.tiers == ("match", "match", "neutral", "mismatch")


@pytest.mark.parametrize("question", [
    "진정간호 목적은?",
    "전 직원과 중환자, 후배, 오전 교육을 확인해줘",
    "진정 전·중 준비사항은?",
])
def test_temporal_ranking_is_inactive_without_one_unambiguous_phase(question):
    chunks = [chunk("low", "낮은 점수 본문"), chunk("high", "높은 점수 본문")]
    scores = [1.0, 2.0]

    ranking = rank_bm25_candidates(question, chunks, scores)

    assert ranking.requested_phase is None
    assert ranking.positions == (1, 0)
    assert ranking.tiers == ("inactive", "inactive")


def test_temporal_match_with_zero_score_is_not_promoted():
    chunks = [
        chunk("zero-before", "진정 치료 전 환자를 평가합니다."),
        chunk("positive-neutral", "진정 환자를 평가합니다."),
        chunk("positive-after", "진정 치료 후 환자를 평가합니다."),
    ]
    scores = [0.0, 1.0, 0.5]

    ranking = rank_bm25_candidates("진정 전 준비사항은?", chunks, scores)

    assert ranking.positions == (1, 2, 0)
    assert ranking.tiers == ("neutral", "mismatch", "non_positive")


def test_temporal_ranking_preserves_candidate_set_scores_and_limit():
    chunks = [
        chunk("after", "진정 치료 후 환자를 평가합니다."),
        chunk("neutral", "진정 환자를 평가합니다."),
        chunk("before", "진정 치료 전 환자를 평가합니다."),
        chunk("outside", "진정 치료 전 추가 본문입니다."),
    ]
    scores = [4.0, 3.0, 2.0, 1.0]

    ranking = rank_bm25_candidates("진정 전 준비사항은?", chunks, scores, limit=3)

    assert ranking.positions == (2, 1, 0)
    assert set(ranking.positions) == {0, 1, 2}
    assert scores == [4.0, 3.0, 2.0, 1.0]


def test_temporal_ranking_rejects_invalid_input_contract():
    chunks = [chunk("one", "진정 치료 전 환자를 평가합니다.")]

    with pytest.raises(ValueError, match="길이"):
        rank_bm25_candidates("진정 전", chunks, [])
    with pytest.raises(ValueError, match="limit"):
        rank_bm25_candidates("진정 전", chunks, [1.0], limit=0)
    with pytest.raises(ValueError, match="유한"):
        rank_bm25_candidates("진정 전", chunks, [float("nan")])


def test_evaluator_applies_temporal_tiers_only_to_schat():
    chunks = [
        chunk("during", "진정 치료 중 환자 상태를 평가하고 기록합니다."),
        chunk("before", "진정 치료 전 환자 상태를 평가하고 준비합니다.", page=2),
        chunk("neutral", "진정 환자 상태를 평가하고 기록합니다.", page=3),
    ]
    documents = [{"id": "doc", "document_name": "가상지침.pdf", "title": "가상 지침"}]

    result = evaluate_chunks(chunks, documents, ["진정 전 준비사항은?"], top_k=3)[0]

    assert result["schat_bm25"][0]["chunk_id"] == "before"
    assert result["schat_bm25"][0]["requested_temporal_phase"] == "before"
    assert result["schat_bm25"][0]["temporal_tier"] == "match"
    assert {row["temporal_tier"] for row in result["rank_bm25"]} == {"baseline"}
