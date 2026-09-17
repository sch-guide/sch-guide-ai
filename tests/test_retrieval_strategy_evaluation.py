import json
from pathlib import Path

import numpy as np

from tools.chroma_baseline_evaluate import CatalogChunk, load_catalog
from tools.retrieval_strategy_evaluate import (
    _results_payload,
    _review_html,
    bm25_ranking,
    expand_common_rows,
    load_reused_chroma_results,
    negative_score_diagnostics,
    rrf_ranking,
    select_retrieval_strategy,
    standalone_results_are_complementary,
    validate_dataset_identity,
)
from tools.schat_mvp_stabilize import split_reusable_chroma_cases

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "transfusion_retrieval_baseline.json"
CATALOG = ROOT / "data" / "library" / "catalog.sqlite3"
PRIOR_CHROMA = (
    ROOT
    / "artifacts"
    / "2026-09-16_transfusion-chromadb-ragas-baseline"
    / "chroma_results.json"
)
RESULTS = ROOT / "artifacts" / "2026-09-16_transfusion-retrieval-baseline"


def _chunk(identifier, position, text):
    return CatalogChunk(
        chunk_id=identifier,
        document_id="doc",
        position=position,
        page=1,
        section="수혈 검사",
        parent_id="parent",
        text=text,
        vector=np.array([1.0, 0.0], dtype=np.float32),
        substantive_body=True,
    )


def _case_result(*, answerable=True, review=False, ids=None, scores=None):
    return {
        "question_id": "TF001" if answerable else "TN001",
        "question": "비예기항체 검사는?" if answerable else "화성 궤도 공식은?",
        "question_type": "fact_specific" if answerable else "negative_out_of_scope",
        "expected_answerable": answerable,
        "needs_human_review": review,
        "reference_context_ids": ["gold"] if answerable else [],
        "retrieved_context_ids": ids or ["gold", "other"],
        "scores": scores or [0.9, 0.2],
        "latency_ms": 4.5,
    }


def test_common_rows_use_exact_shared_schema_for_every_cutoff_and_rank():
    rows = expand_common_rows(
        contract={
            "dataset_version": "transfusion-retrieval-v2",
            "document_version": "sha256:abc",
            "chunk_version": 4,
            "cutoffs": (1, 3),
        },
        retriever="chromadb",
        result=_case_result(),
    )
    assert len(rows) == 3
    assert list(rows[0]) == [
        "dataset_version",
        "document_version",
        "chunk_version",
        "retriever",
        "question_id",
        "question_type",
        "expected_answerable",
        "needs_human_review",
        "k",
        "rank",
        "retrieved_chunk_id",
        "score",
        "is_gold",
        "latency_ms",
    ]
    assert rows[0]["k"] == 1 and rows[0]["rank"] == 1 and rows[0]["is_gold"] is True
    assert [(row["k"], row["rank"]) for row in rows] == [(1, 1), (3, 1), (3, 2)]


def test_persisted_result_payload_omits_question_text():
    result = _case_result()
    payload = _results_payload(
        retriever="bm25",
        contract={
            "schema_version": 3,
            "dataset_version": "transfusion-retrieval-v3",
            "document_version": "sha256:abc",
            "chunk_version": 4,
        },
        results=[result],
        summary={"overall": {}},
    )
    assert payload["cases"][0]["question_id"] == result["question_id"]
    assert "question" not in payload["cases"][0]
    assert result["question"] not in json.dumps(payload, ensure_ascii=False)


def test_negative_diagnostics_exclude_review_and_compare_same_engine_scores():
    results = [
        _case_result(scores=[0.8, 0.1]),
        {**_case_result(scores=[0.6, 0.1]), "question_id": "TF002"},
        _case_result(answerable=True, review=True, scores=[0.99, 0.1]),
        _case_result(answerable=False, scores=[0.2, 0.1]),
    ]
    diagnostic = negative_score_diagnostics(results)
    assert diagnostic["positive_top1"]["count"] == 2
    assert diagnostic["positive_top1"]["mean"] == 0.7
    assert diagnostic["negative_top1"]["count"] == 1
    assert diagnostic["negative_top1"]["mean"] == 0.2
    assert diagnostic["mean_score_separation"] == 0.5
    assert diagnostic["negative_returned_count"] == {"mean": 2.0, "minimum": 2, "maximum": 2}


def test_bm25_ranking_uses_existing_index_and_stable_position_tiebreak():
    chunks = [
        _chunk("first", 0, "수혈 환자 확인"),
        _chunk("gold", 1, "비예기항체 선별검사 결과 확인"),
        _chunk("third", 2, "혈액제제 반납"),
    ]
    result = bm25_ranking("비예기항체 검사는?", chunks, limit=3)
    assert result["retrieved_context_ids"][0] == "gold"
    assert result["scores"][0] > result["scores"][1]
    zero = bm25_ranking("화성 궤도", chunks, limit=3)
    assert zero["retrieved_context_ids"] == ["first", "gold", "third"]
    assert zero["scores"] == [0.0, 0.0, 0.0]


def test_rrf_ranking_fuses_distinct_ids_with_deterministic_source_order():
    ids, scores = rrf_ranking(
        [["a", "b", "c"], ["d", "c", "b"]],
        source_order={"a": 0, "b": 1, "c": 2, "d": 3},
        limit=4,
        rrf_k=60,
    )
    assert ids == ["b", "c", "a", "d"]
    assert len(ids) == len(set(ids)) == len(scores)
    assert scores[0] == scores[1] > scores[2] == scores[3]


def test_complementarity_and_selection_are_metric_driven_with_simple_tiebreak():
    chroma_cases = [
        {"question_id": "A", "metrics": {"hit_at_10": 1.0}},
        {"question_id": "B", "metrics": {"hit_at_10": 0.0}},
    ]
    bm25_cases = [
        {"question_id": "A", "metrics": {"hit_at_10": 0.0}},
        {"question_id": "B", "metrics": {"hit_at_10": 1.0}},
    ]
    assert standalone_results_are_complementary(chroma_cases, bm25_cases) is True

    summaries = {
        "chromadb": {"hit_at_5": 0.5, "hit_at_10": 0.6, "mrr": 0.4, "recall_at_10": 0.6},
        "bm25": {"hit_at_5": 0.6, "hit_at_10": 0.7, "mrr": 0.5, "recall_at_10": 0.7},
        "hybrid_rrf": {"hit_at_5": 0.8, "hit_at_10": 0.9, "mrr": 0.7, "recall_at_10": 0.9},
    }
    selected = select_retrieval_strategy(summaries)
    assert selected["selected"] == "hybrid_rrf"
    assert selected["production_applied"] is False

    tied = {"chromadb": summaries["bm25"], "bm25": summaries["bm25"]}
    assert select_retrieval_strategy(tied)["selected"] == "bm25"


def test_existing_chroma_positive_results_are_reused_only_after_identity_validation():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    _, chunks = load_catalog(CATALOG, document_name=fixture["document"]["name"])
    reusable, pending = split_reusable_chroma_cases(fixture["cases"], PRIOR_CHROMA)
    reused = load_reused_chroma_results(
        reusable, PRIOR_CHROMA, allowed_ids={chunk.chunk_id for chunk in chunks}
    )
    assert len(reused) == 45
    assert len(pending) == 45
    assert [row["question_id"] for row in reused] == [
        case["question_id"] for case in reusable
    ]
    assert all(len(row["retrieved_context_ids"]) == 10 for row in reused)
    assert all(len(row["scores"]) == 10 for row in reused)
    assert all(row["expected_answerable"] is True for row in reused)

    drifted = json.loads(PRIOR_CHROMA.read_text(encoding="utf-8"))
    drifted["cases"][0]["question"] = "drift"
    drifted_path = PRIOR_CHROMA.parent / "_test_drifted_chroma_results.json"
    try:
        drifted_path.write_text(json.dumps(drifted), encoding="utf-8")
        try:
            load_reused_chroma_results(
                reusable,
                drifted_path,
                allowed_ids={chunk.chunk_id for chunk in chunks},
            )
        except ValueError as error:
            assert "prior Chroma case drift" in str(error)
        else:
            raise AssertionError("drifted Chroma result was accepted")
    finally:
        drifted_path.unlink(missing_ok=True)

    drifted = json.loads(PRIOR_CHROMA.read_text(encoding="utf-8"))
    drifted["cases"][0]["retrieved_context_ids"][0] = "unknown-chunk"
    try:
        drifted_path.write_text(json.dumps(drifted), encoding="utf-8")
        try:
            load_reused_chroma_results(
                reusable,
                drifted_path,
                allowed_ids={chunk.chunk_id for chunk in chunks},
            )
        except ValueError as error:
            assert "outside current catalog" in str(error)
        else:
            raise AssertionError("unknown reused Chroma ID was accepted")
    finally:
        drifted_path.unlink(missing_ok=True)


def test_dataset_document_version_is_tied_to_catalog_file_hash():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    metadata, _ = load_catalog(CATALOG, document_name=fixture["document"]["name"])
    validate_dataset_identity(fixture, metadata)

    drifted = {**fixture, "document_version": "sha256:drift"}
    try:
        validate_dataset_identity(drifted, metadata)
    except ValueError as error:
        assert "document version drift" in str(error)
    else:
        raise AssertionError("document version drift was accepted")


def test_strategy_review_reads_dataset_counts_from_manifest():
    report = {
        "manifest": {
            "case_count": 50,
            "approved_case_count": 31,
            "human_review_case_count": 14,
            "negative_case_count": 5,
        },
        "summaries": {
            "bm25": {
                "overall": {
                    "hit_at_5": 0.5,
                    "hit_at_10": 0.7,
                    "mrr": 0.4,
                    "recall_at_10": 0.6,
                },
                "latency_ms": {"mean": 1.2},
            }
        },
        "type_rows": [],
        "selection": {"selected": "bm25"},
    }
    rendered = _review_html(report)
    assert "총 50문항" in rendered
    assert "사람 검토 14" in rendered


def test_final_comparison_artifacts_follow_frozen_contract_without_source_text():
    required = {
        "dataset_manifest.json",
        "chroma_results.csv",
        "chroma_results.json",
        "chroma_summary.json",
        "bm25_results.csv",
        "bm25_results.json",
        "hybrid_rrf_results.csv",
        "hybrid_rrf_results.json",
        "metrics_by_question_type.csv",
        "ragas_results.json",
        "bm25_comparison_template.json",
        "comparison_summary.json",
        "environment.json",
        "test_results.json",
        "review.html",
    }
    assert required <= {path.name for path in RESULTS.iterdir()}

    comparison = json.loads((RESULTS / "comparison_summary.json").read_text(encoding="utf-8"))
    assert comparison["manifest"]["case_count"] == 50
    assert comparison["manifest"]["approved_case_count"] == 31
    assert comparison["manifest"]["human_review_case_count"] == 14
    assert comparison["manifest"]["negative_case_count"] == 5
    assert comparison["hybrid_evaluated"] is True
    assert comparison["selection"]["selected"] == "bm25"
    assert comparison["selection"]["production_applied"] is False
    assert comparison["generation_api_calls"] == 0
    assert comparison["ragas"]["official_ragas_validated_case_engine_pairs"] == 93

    template = json.loads((RESULTS / "bm25_comparison_template.json").read_text(encoding="utf-8"))
    assert template["required_row_count"] == 950
    for name in ("chroma_results.csv", "bm25_results.csv", "hybrid_rrf_results.csv"):
        with (RESULTS / name).open(encoding="utf-8-sig") as handle:
            assert sum(1 for _ in handle) == 951

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    _, chunks = load_catalog(CATALOG, document_name=fixture["document"]["name"])
    artifact_text = "\n".join(
        path.read_text(encoding="utf-8-sig" if path.suffix == ".csv" else "utf-8")
        for path in RESULTS.iterdir()
        if path.suffix in {".csv", ".json", ".html"}
    )
    assert all(chunk.text not in artifact_text for chunk in chunks)
