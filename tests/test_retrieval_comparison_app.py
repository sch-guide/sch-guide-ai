import json
from pathlib import Path

import numpy as np
import pytest
from streamlit.testing.v1 import AppTest

from tools.chroma_baseline_evaluate import CatalogChunk, load_catalog
from tools.retrieval_comparison_app import (
    TOP_K,
    build_comparison_engine,
    build_result_rows,
    find_fixture_case,
    load_fixture,
    retrieval_hit_summary,
)

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "tools" / "retrieval_comparison_app.py"
CATALOG = ROOT / "data" / "library" / "catalog.sqlite3"
FIXTURE = ROOT / "tests" / "fixtures" / "transfusion_retrieval_baseline.json"
RESULTS = ROOT / "workspace" / "과거작업" / "평가산출물" / "2026-09-17_transfusion-expanded-retrieval"


def _case(payload: dict, case_id: str) -> dict:
    return next(case for case in payload["cases"] if case["question_id"] == case_id)


def test_fixture_lookup_supports_exact_questions_and_free_questions():
    fixture = load_fixture(FIXTURE)
    expected = _case(fixture, "TF001")

    assert find_fixture_case(f"  {expected['question']}  ", fixture["cases"]) == expected
    assert find_fixture_case("평가셋에 없는 자유 질문", fixture["cases"]) is None


def test_hit_summary_reports_first_gold_rank_and_all_cutoffs():
    summary = retrieval_hit_summary(["c1", "gold", "c3"], {"gold"})

    assert summary == {
        "gold_available": True,
        "first_gold_rank": 2,
        "hit_at_1": False,
        "hit_at_3": True,
        "hit_at_5": True,
        "hit_at_10": True,
    }
    assert retrieval_hit_summary(["c1"], set()) == {
        "gold_available": False,
        "first_gold_rank": None,
        "hit_at_1": None,
        "hit_at_3": None,
        "hit_at_5": None,
        "hit_at_10": None,
    }


def test_result_rows_keep_rank_metadata_score_gold_and_memory_only_preview():
    chunk = CatalogChunk(
        chunk_id="chunk-1",
        document_id="document-1",
        document_name="guide.pdf",
        position=0,
        page=3,
        section="section",
        parent_id="parent-1",
        text="임상 근거 " * 100,
        vector=np.ones(384, dtype="float32"),
        substantive_body=True,
    )

    rows = build_result_rows(["chunk-1"], [0.75], [chunk], {"chunk-1"})

    assert rows[0]["rank"] == 1
    assert rows[0]["chunk_id"] == "chunk-1"
    assert rows[0]["document"] == "guide.pdf"
    assert rows[0]["page"] == 3
    assert rows[0]["section"] == "section"
    assert rows[0]["parent_id"] == "parent-1"
    assert rows[0]["score"] == 0.75
    assert rows[0]["is_gold"] is True
    assert rows[0]["substantive_body"] is True
    assert rows[0]["preview"].endswith("…")
    assert len(rows[0]["preview"]) <= 501


def test_live_retrieval_matches_frozen_baseline_rankings_for_same_question():
    pytest.importorskip("chromadb")
    fixture = load_fixture(FIXTURE)
    case = _case(fixture, "TF003")
    _, chunks = load_catalog(CATALOG, document_name=fixture["document"]["name"])
    engine = build_comparison_engine(chunks)

    comparison = engine.compare(case["question"], gold_ids=set(case["reference_context_ids"]))
    bm25_baseline = _case(
        json.loads((RESULTS / "bm25_results.json").read_text(encoding="utf-8")), "TF003"
    )
    chroma_baseline = _case(
        json.loads((RESULTS / "chroma_results.json").read_text(encoding="utf-8")), "TF003"
    )

    assert TOP_K == 10
    assert comparison["catalog_chunk_count"] == 105
    assert [row["chunk_id"] for row in comparison["bm25"]["rows"]] == bm25_baseline[
        "retrieved_context_ids"
    ]
    assert [row["chunk_id"] for row in comparison["chromadb"]["rows"]] == chroma_baseline[
        "retrieved_context_ids"
    ]


def test_streamlit_evaluation_app_has_input_filter_and_comparison_button():
    app = AppTest.from_file(str(APP), default_timeout=30).run()

    assert not app.exception
    assert app.title[0].value == "BM25 vs ChromaDB Retrieval 비교"
    assert app.text_area[0].label == "질문을 입력하세요"
    assert app.button[0].label == "BM25 vs ChromaDB 비교"
    assert "전체" in app.selectbox[0].options
    assert "procedure" in app.selectbox[0].options
