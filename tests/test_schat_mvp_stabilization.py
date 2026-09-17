import copy
import json
from pathlib import Path

import pytest

from tools.chroma_baseline_evaluate import load_catalog, validate_evaluation_cases
from tools.multimodal_retrieval_evaluate import (
    build_figure_inventory,
    build_table_units,
    safe_figure_manifest,
    safe_table_manifest,
    table_aware_bm25_ranking,
)
from tools.schat_mvp_stabilization_report import build_stabilization_summary
from tools.schat_mvp_stabilize import (
    EXPANDED_CASE_SPECS,
    REVIEW_DECISIONS,
    apply_review_decisions,
    build_expanded_fixture,
    split_reusable_chroma_cases,
    validate_review_audit,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "transfusion_retrieval_baseline.json"
CATALOG = ROOT / "data" / "library" / "catalog.sqlite3"
PDF = ROOT / "data" / "실무지침서_수혈간호.pdf"
MULTIMODAL_FIXTURE = ROOT / "tests" / "fixtures" / "transfusion_multimodal_retrieval.json"
EXPANDED_RESULTS = ROOT / "artifacts" / "2026-09-17_transfusion-expanded-retrieval"
STABILIZATION_RESULTS = ROOT / "artifacts" / "2026-09-17_schat-mvp-stabilization"
SEDATION_UAT = ROOT / "artifacts" / "2026-09-16_rag-sedation-uat-generalization" / "uat_report.json"
PRIOR_CHROMA = (
    ROOT
    / "artifacts"
    / "2026-09-16_transfusion-chromadb-ragas-baseline"
    / "chroma_results.json"
)


def _baseline_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_review_contract_accepts_only_audited_structural_approval():
    fixture = _baseline_fixture()
    metadata, chunks = load_catalog(CATALOG, document_name=fixture["document"]["name"])
    reviewed = apply_review_decisions(copy.deepcopy(fixture["cases"]), REVIEW_DECISIONS)
    audit = validate_review_audit(reviewed, chunks)

    assert audit == {
        "reviewed_case_count": 14,
        "auto_approved_case_count": 13,
        "remaining_human_review_case_count": 1,
        "approved_structural_table_count": 12,
        "approved_nearby_text_count": 1,
    }
    assert metadata["chunk_count"] == 105
    assert next(case for case in reviewed if case["question_id"] == "TF027")[
        "needs_human_review"
    ] is True
    assert next(case for case in reviewed if case["question_id"] == "TF035")[
        "review_status"
    ] == "approved_nearby_text"


def test_review_contract_rejects_opaque_or_incomplete_autoapproval():
    fixture = _baseline_fixture()
    _, chunks = load_catalog(CATALOG, document_name=fixture["document"]["name"])
    reviewed = apply_review_decisions(copy.deepcopy(fixture["cases"]), REVIEW_DECISIONS)
    target = next(case for case in reviewed if case["question_id"] == "TF003")
    target["review_audit"].pop("relationship")
    with pytest.raises(ValueError, match="review audit"):
        validate_review_audit(reviewed, chunks)

    reviewed = apply_review_decisions(copy.deepcopy(fixture["cases"]), REVIEW_DECISIONS)
    target = next(case for case in reviewed if case["question_id"] == "TF027")
    target["needs_human_review"] = False
    target["review_reasons"] = []
    target["review_status"] = "approved_nearby_text"
    with pytest.raises(ValueError, match="opaque auto-approval"):
        validate_review_audit(reviewed, chunks)


def test_expanded_fixture_adds_40_independent_variants_without_gold_drift():
    base = _baseline_fixture()
    expanded = build_expanded_fixture(base)
    base_by_id = {case["question_id"]: case for case in base["cases"]}

    assert len(EXPANDED_CASE_SPECS) == 40
    assert expanded["schema_version"] == 3
    assert expanded["dataset_version"] == "transfusion-retrieval-v3"
    assert len(expanded["cases"]) == 90
    assert len({case["question"] for case in expanded["cases"]}) == 90

    new_cases = expanded["cases"][50:]
    assert len([case for case in new_cases if case["expected_answerable"]]) == 34
    assert len([case for case in new_cases if not case["expected_answerable"]]) == 6
    for case in new_cases:
        source_id = case.get("metadata", {}).get("variant_of")
        if case["expected_answerable"]:
            assert source_id in base_by_id
            assert case["reference_contexts"] == base_by_id[source_id]["reference_contexts"]
            assert case["reference_context_ids"] == base_by_id[source_id]["reference_context_ids"]
            assert case["needs_human_review"] is False
        else:
            assert source_id is None
            assert case["reference_context_ids"] == []


def test_expanded_fixture_and_review_audit_validate_against_current_catalog():
    base = _baseline_fixture()
    expanded = build_expanded_fixture(base)
    _, chunks = load_catalog(CATALOG, document_name=expanded["document"]["name"])
    audit = validate_evaluation_cases(
        expanded["cases"], chunks, minimum_cases=80, maximum_cases=100
    )
    review = validate_review_audit(expanded["cases"], chunks)

    assert audit == {
        "case_count": 90,
        "positive_case_count": 79,
        "approved_case_count": 78,
        "human_review_case_count": 1,
        "negative_case_count": 11,
    }
    assert review["remaining_human_review_case_count"] == 1


def test_chroma_reuse_is_incremental_and_requires_exact_identity():
    base = _baseline_fixture()
    expanded = build_expanded_fixture(base)
    reusable, pending = split_reusable_chroma_cases(expanded["cases"], PRIOR_CHROMA)
    assert len(reusable) == 45
    assert len(pending) == 45
    assert {case["question_id"] for case in reusable} == {
        case["question_id"]
        for case in base["cases"][:50]
        if case["expected_answerable"]
    }

    drifted = copy.deepcopy(expanded["cases"])
    drifted[0]["question"] += " drift"
    with pytest.raises(ValueError, match="prior Chroma case drift"):
        split_reusable_chroma_cases(drifted, PRIOR_CHROMA)


def test_table_units_are_deterministic_linked_and_raw_free():
    fixture = _baseline_fixture()
    _, chunks = load_catalog(CATALOG, document_name=fixture["document"]["name"])
    first = build_table_units(PDF, fixture["document"]["id"], chunks)

    assert len({unit.unit_id for unit in first}) == len(first)
    assert list(first) == sorted(
        first,
        key=lambda unit: (
            unit.page,
            unit.table_index,
            unit.table_id,
            unit.unit_kind,
            unit.row_index or 0,
        ),
    )
    assert {unit.page for unit in first} >= {2, 9, 15, 17}
    assert {unit.unit_kind for unit in first} == {"whole_table", "row", "header_row"}
    assert all(unit.source_chunk_ids for unit in first)
    assert all(identifier in {chunk.chunk_id for chunk in chunks} for unit in first for identifier in unit.source_chunk_ids)
    page_15_rows = [unit for unit in first if unit.page == 15 and unit.unit_kind == "header_row"]
    assert len(page_15_rows) == 6

    manifest = safe_table_manifest(first)
    encoded = json.dumps(manifest, ensure_ascii=False)
    assert all(chunk.text not in encoded for chunk in chunks)
    assert all("search_text" not in row and "cells" not in row for row in manifest)


def test_table_aware_bm25_returns_distinct_original_chunk_ids():
    fixture = _baseline_fixture()
    _, chunks = load_catalog(CATALOG, document_name=fixture["document"]["name"])
    units = build_table_units(PDF, fixture["document"]["id"], chunks)
    ranked = table_aware_bm25_ranking("PC는 수령하고 몇 분 안에 다 맞혀야 해?", chunks, units, limit=10)
    assert len(ranked["retrieved_context_ids"]) == 10
    assert len(set(ranked["retrieved_context_ids"])) == 10
    assert set(ranked["retrieved_context_ids"]).issubset({chunk.chunk_id for chunk in chunks})
    assert len(ranked["scores"]) == 10


def test_figure_inventory_is_metadata_only_and_never_infers_clinical_content():
    fixture = _baseline_fixture()
    _, chunks = load_catalog(CATALOG, document_name=fixture["document"]["name"])
    figures = build_figure_inventory(PDF, fixture["document"]["id"], chunks)
    assert figures
    assert all(figure.vision_description is None for figure in figures)
    assert all(figure.confidence == "metadata_only" for figure in figures)
    assert all(figure.page >= 1 and len(figure.bbox) == 4 for figure in figures)
    assert any(figure.page == 11 for figure in figures)
    assert any(figure.page == 14 for figure in figures)

    manifest = safe_figure_manifest(figures)
    encoded = json.dumps(manifest, ensure_ascii=False)
    assert all(chunk.text not in encoded for chunk in chunks)
    assert "vision_description" not in encoded


def test_multimodal_fixture_separates_approved_table_and_unsafe_figure_gold():
    fixture = _baseline_fixture()
    multimodal = json.loads(MULTIMODAL_FIXTURE.read_text(encoding="utf-8"))
    by_id = {case["question_id"]: case for case in fixture["cases"]}

    assert multimodal["source_dataset_version"] == fixture["dataset_version"]
    assert {case["case_type"] for case in multimodal["cases"]} == {
        "table_lookup",
        "table_comparison",
        "table_numeric",
        "mixed_text_table",
        "figure_procedure",
        "figure_condition",
    }
    approved = [case for case in multimodal["cases"] if case["evaluation_status"] == "approved"]
    assert approved
    assert all(
        by_id[case["source_question_id"]]["needs_human_review"] is False for case in approved
    )
    figure_condition = next(
        case for case in multimodal["cases"] if case["case_type"] == "figure_condition"
    )
    assert figure_condition["source_question_id"] is None
    assert figure_condition["evaluation_status"] == "not_instantiated_unsafe_gold"


def test_stabilization_summary_selects_bm25_without_production_or_provider_calls():
    fixture = _baseline_fixture()
    comparison = json.loads(
        (EXPANDED_RESULTS / "comparison_summary.json").read_text(encoding="utf-8")
    )
    bm25 = json.loads((EXPANDED_RESULTS / "bm25_results.json").read_text(encoding="utf-8"))
    multimodal = json.loads(
        (STABILIZATION_RESULTS / "multimodal_results.json").read_text(encoding="utf-8")
    )
    sedation = json.loads(SEDATION_UAT.read_text(encoding="utf-8"))

    summary = build_stabilization_summary(
        fixture=fixture,
        comparison=comparison,
        bm25_payload=bm25,
        multimodal=multimodal,
        sedation_uat=sedation,
    )

    assert summary["review"]["reviewed_case_count"] == 14
    assert summary["review"]["remaining_human_review_case_count"] == 1
    assert summary["retrieval"]["selected"] == "bm25"
    assert summary["retrieval"]["production_decision"] == "production_change_not_required"
    assert summary["retrieval"]["production_change_applied"] is False
    assert summary["table_image"]["table_aware_decision"] == "not_materially_better"
    assert summary["uat"]["sedation"]["passed"] == 45
    assert summary["uat"]["sedation"]["q006_zero_call"]["transport_calls"] == 0
    assert summary["uat"]["transfusion"]["top10_hit_case_count"] == 69
    assert summary["uat"]["transfusion"]["top10_miss_case_count"] == 9
    assert summary["provider_comparison"]["status"] == "blocked_security_policy_confirmation_required"
    assert summary["provider_comparison"]["hospital_data_sent_to_groq"] is False
    assert summary["provider_comparison"]["hospital_data_sent_to_gemini"] is False
    assert summary["generation_api_calls"] == 0
    assert summary["production_rag_changed"] is False
