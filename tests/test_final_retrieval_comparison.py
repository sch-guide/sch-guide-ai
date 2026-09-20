import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.library import Chunk
from tools import final_retrieval_comparison as comparison
from tools import final_retrieval_review as review
from tools.chromadb_only_evaluate import ensure_artifact_safe

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "tools/final_retrieval_review_app.py"
RESULT_DIR = (
    ROOT
    / "workspace/검색_성능평가/3방식_비교/2026-09-20_final-retrieval-comparison"
)


def _keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in _keys(item)}
    return set()


def test_default_inputs_use_gemini_for_primary_chroma_comparison() -> None:
    assert comparison.ENGINE_ORDER == (
        "bm25",
        "gemini_chroma",
        "current_hybrid",
    )
    gemini = comparison.DEFAULT_INPUTS["gemini_chroma"]
    assert "gemini-embedding-2-3072" in gemini.name
    assert "minilm" not in str(gemini).lower()


def test_contract_validation_accepts_only_namespace_and_engine_specific_differences() -> None:
    contracts = comparison.load_input_contracts(comparison.DEFAULT_INPUTS)

    validated = comparison.validate_contracts(contracts)

    assert validated["approved_positive_case_count"] == 21
    assert validated["top_k"] == [1, 3, 5, 10]
    assert validated["query_plan"]["planner"] == "query.plan_query"
    assert validated["engine_specific_differences"]["current_hybrid"][
        "context_expansion"
    ] is True


def test_contract_validation_rejects_document_scope_or_gold_drift() -> None:
    contracts = comparison.load_input_contracts(comparison.DEFAULT_INPUTS)
    changed = json.loads(json.dumps(contracts))
    changed["gemini_chroma"]["corpus"]["document_ids"] = ["different"]

    with pytest.raises(ValueError, match="corpus contract drift"):
        comparison.validate_contracts(changed)

    changed = json.loads(json.dumps(contracts))
    changed["current_hybrid"]["gold"]["dataset_version"] = "different"
    with pytest.raises(ValueError, match="Gold contract drift"):
        comparison.validate_contracts(changed)


def test_comparison_generates_raw_free_outputs_without_empty_review_file(
    tmp_path: Path,
) -> None:
    output = tmp_path / "comparison"

    result = comparison.compare(output_dir=output)

    assert result["summary"]["case_count"] == 21
    assert {path.name for path in output.iterdir()} == {
        "summary.json",
        "metrics.csv",
        "per_case_comparison.json",
        "case_analysis.md",
        "evaluation_contract.json",
        "index.html",
    }
    assert not (output / "human_review.json").exists()
    cases = result["per_case"]["cases"]
    assert len(cases) == 21
    assert set(cases[0]["retrievers"]) == set(comparison.ENGINE_ORDER)
    assert "ranked_results" in cases[0]["retrievers"]["bm25"]
    assert not ({"question", "text", "evidence", "source_text"} & _keys(result["per_case"]))
    assert "minilm" not in result["summary"]["engine_order"]
    assert result["summary"]["minilm_reference"]["primary_comparison"] is False
    assert result["summary"]["latency_breakdown_ms"]["gemini_chroma"] == {
        "query_embedding": 567.688,
        "chromadb_query": 7.096,
        "combined": 574.784,
    }


def test_static_index_has_no_question_or_source_text() -> None:
    fixture = json.loads(
        (ROOT / "tests/fixtures/schat_v1_operational_uat.json").read_text(
            encoding="utf-8"
        )
    )
    html = comparison.render_index(
        {
            "case_count": 1,
            "engine_order": list(comparison.ENGINE_ORDER),
            "aggregate_metrics": {},
            "overall_win_counts": {},
            "single_winner_counts": {},
            "complete_tie_case_count": 0,
            "priority_review_case_ids": [],
            "minilm_reference": {"primary_comparison": False},
        },
        {"cases": []},
    )

    assert "로컬 검수 앱" in html
    assert "final_retrieval_review_app.py" in html
    assert all(str(case["question"]) not in html for case in fixture["cases"])


def test_review_file_is_created_only_when_first_review_is_saved(tmp_path: Path) -> None:
    target = tmp_path / "human_review.json"
    assert not target.exists()

    record = review.build_review_record(
        case_id="UAT-S01",
        retriever_reviews={
            "bm25": "적절",
            "gemini_chroma": "부분적절",
            "current_hybrid": "적절",
        },
        preferred_retriever="current_hybrid",
        overall_note="순위와 근거 ID만 검토함",
        reviewer="reviewer-1",
        timestamp="2026-09-20T12:00:00+09:00",
    )
    review.save_review(target, record)

    stored = json.loads(target.read_text(encoding="utf-8"))
    assert stored == {"schema_version": 1, "reviews": [record]}
    assert set(record["retriever_reviews"]) == set(comparison.ENGINE_ORDER)
    assert not ({"text", "question", "evidence", "source", "provider_payload"} & set(record))


def test_review_save_rejects_extra_raw_content_fields(tmp_path: Path) -> None:
    target = tmp_path / "human_review.json"
    record = review.build_review_record(
        case_id="UAT-S01",
        retriever_reviews={engine: "적절" for engine in comparison.ENGINE_ORDER},
        preferred_retriever="bm25",
        overall_note="",
        reviewer="reviewer-1",
    )
    record["source_text"] = "must not be persisted"

    with pytest.raises(ValueError, match="review fields"):
        review.save_review(target, record)

    assert not target.exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("judgment", "unknown"),
        ("preferred", "minilm"),
    ],
)
def test_review_schema_rejects_unknown_judgment_or_retriever(
    field: str, value: str
) -> None:
    judgments = {
        "bm25": "적절",
        "gemini_chroma": "부분적절",
        "current_hybrid": "부적절",
    }
    if field == "judgment":
        judgments["bm25"] = value
    preferred = value if field == "preferred" else "bm25"

    with pytest.raises(ValueError):
        review.build_review_record(
            case_id="UAT-S01",
            retriever_reviews=judgments,
            preferred_retriever=preferred,
            overall_note="",
            reviewer="reviewer-1",
        )


def test_review_runtime_loads_question_but_does_not_persist_it(tmp_path: Path) -> None:
    cases = review.load_uat_cases(ROOT / "tests/fixtures/schat_v1_operational_uat.json")
    case = cases["UAT-S01"]
    assert case["question"]

    target = tmp_path / "human_review.json"
    record = review.build_review_record(
        case_id=case["case_id"],
        retriever_reviews={engine: "추가확인필요" for engine in comparison.ENGINE_ORDER},
        preferred_retriever="",
        overall_note="",
        reviewer="reviewer-1",
    )
    review.save_review(target, record)
    assert case["question"] not in target.read_text(encoding="utf-8")


def test_local_review_app_opens_without_creating_review_file() -> None:
    target = RESULT_DIR / "human_review.json"
    before = target.read_bytes() if target.exists() else None

    app = AppTest.from_file(str(APP), default_timeout=30).run()

    assert not app.exception
    assert app.title[0].value == "SCHAT Retrieval 최종 비교 · 사람 검수"
    labels = [widget.label for widget in app.selectbox]
    assert len(app.metric) == 9
    assert app.text_input[0].label == "Case ID/질문 검색"
    assert "우선 검수 분류" in labels
    assert all(
        f"{comparison.ENGINE_LABELS[engine]} relevance judgment" in labels
        for engine in comparison.ENGINE_ORDER
    )
    assert "preferred_retriever" in labels
    after = target.read_bytes() if target.exists() else None
    assert after == before


def test_review_app_imports_when_streamlit_uses_tools_as_working_directory() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import runpy; "
                "runpy.run_path('final_retrieval_review_app.py', run_name='__test__')"
            ),
        ],
        cwd=ROOT / "tools",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_source_preview_reads_one_local_chunk_without_modifying_catalog() -> None:
    comparison_payload = json.loads(
        (RESULT_DIR / "per_case_comparison.json").read_text(encoding="utf-8")
    )
    chunk_id = comparison_payload["cases"][0]["retrievers"]["bm25"][
        "ranked_results"
    ][0]["chunk_id"]
    catalog = ROOT / "data/library/catalog.sqlite3"
    before = catalog.stat().st_mtime_ns

    preview = review.load_source_preview(catalog, chunk_id)

    assert preview["chunk_id"] == chunk_id
    assert preview["text"]
    assert catalog.stat().st_mtime_ns == before


def test_generated_static_artifacts_contain_no_question_or_exact_source_text() -> None:
    uat = json.loads(
        (ROOT / "tests/fixtures/schat_v1_operational_uat.json").read_text(
            encoding="utf-8"
        )
    )
    questions = {str(case["question"]) for case in uat["cases"]}
    catalog = ROOT / "data/library/catalog.sqlite3"
    connection = sqlite3.connect(catalog.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        source_texts = {
            Chunk.from_row(json.loads(payload)).text
            for (payload,) in connection.execute("select payload from chunks")
        }
    finally:
        connection.close()
    forbidden = questions | source_texts
    for name in ("summary.json", "per_case_comparison.json", "evaluation_contract.json"):
        ensure_artifact_safe(
            json.loads((RESULT_DIR / name).read_text(encoding="utf-8")),
            forbidden_exact_texts=forbidden,
        )
    rendered = "\n".join(
        (RESULT_DIR / name).read_text(encoding="utf-8")
        for name in ("index.html", "case_analysis.md", "metrics.csv")
    )
    assert not any(value and value in rendered for value in forbidden)
