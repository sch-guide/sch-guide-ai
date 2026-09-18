from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools.schat_v1_final_stabilize import (
    aggregate_retrieval_rows,
    build_gold_manifest,
    classify_deferred_case,
    collect_valid_evidence_ids,
    evaluate_fixed_rankings,
    final_readiness,
    safe_artifact_row,
    score_ranked_ids,
)

ROOT = Path(__file__).resolve().parents[1]


def _review_case(case_id: str, *, approved: bool) -> dict[str, object]:
    return {
        "case_id": case_id,
        "review_status": "approved" if approved else "reviewing",
        "scope_decision": "yes",
        "expected_domain": "hospital",
        "expected_document": "sedation",
        "expected_evidence_type": "text",
        "expected_abstain": False,
        "primary_gold_ids": [f"{case_id}-primary"] if approved else [],
        "acceptable_gold_ids": [f"{case_id}-acceptable"] if approved else [],
        "critical_facts": ["사람이 확인한 핵심 사실"] if approved else [],
        "critical_numbers": [],
        "critical_units": [],
        "critical_times": [],
        "critical_conditions": [],
        "critical_contraindications": [],
        "critical_negations": [],
        "critical_steps": [],
        "table_required": False,
        "image_required": False,
        "image_human_review_completed": False,
        "reviewer": "reviewer_1" if approved else "",
        "reviewed_at": "2026-09-18T10:00:00+09:00" if approved else "",
        "reviewer_1_reviewed_at": "2026-09-18T10:00:00+09:00" if approved else "",
        "note": "",
        "second_review_required": False,
        "reviewer_1_approved": approved,
        "reviewer_2": "",
        "reviewer_2_reviewed_at": "",
        "reviewer_2_approved": False,
        "final_gold_approved": approved,
        "final_approved_at": "2026-09-18T10:00:00+09:00" if approved else "",
        "draft_disposition": "modified_accepted" if approved else "on_hold",
        "source_label_status": "provisional_needs_human_review",
    }


def _review_fixture() -> dict[str, object]:
    return {
        "schema_version": 1,
        "dataset_id": "schat-v1-operational-gold-reviewed",
        "dataset_version": "schat-v1-operational-gold-reviewed-v1",
        "source_uat_sha256": "a" * 64,
        "source_gold_sha256": "b" * 64,
        "local_only": True,
        "automatic_gold_assignment": False,
        "cases": [_review_case("P1", approved=True), _review_case("P2", approved=False)],
    }


def _operational_gold() -> dict[str, object]:
    return {
        "schema_version": 1,
        "cases": [
            {
                "case_id": "N1",
                "expected_document_scope": "out_of_scope",
                "expected_domain": "out_of_scope",
                "expected_evidence_type": "pre_llm_block",
                "expected_abstain": True,
                "label_status": "approved",
                "source_gold_refs": [],
                "critical_requirements_status": "abstention_zero_call",
            }
        ],
    }


def test_gold_manifest_includes_only_final_positive_and_approved_abstention() -> None:
    manifest = build_gold_manifest(
        _review_fixture(),
        _operational_gold(),
        valid_evidence_ids={"P1-primary", "P1-acceptable"},
    )

    assert manifest["approved_positive_case_ids"] == ["P1"]
    assert manifest["deferred_positive_case_ids"] == ["P2"]
    assert manifest["approved_abstention_case_ids"] == ["N1"]
    assert manifest["aggregate_case_ids"] == ["P1", "N1"]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda data: data["cases"].append(copy.deepcopy(data["cases"][0])), "unique"),
        (
            lambda data: data["cases"][0]["acceptable_gold_ids"].append("P1-primary"),
            "overlap",
        ),
        (lambda data: data["cases"][0].update(critical_facts=[]), "critical fact"),
        (
            lambda data: data["cases"][0]["primary_gold_ids"].append("P1-primary"),
            "duplicate evidence",
        ),
    ],
)
def test_gold_manifest_rejects_invalid_reviewed_gold(mutation, message: str) -> None:
    reviewed = _review_fixture()
    mutation(reviewed)

    with pytest.raises(ValueError, match=message):
        build_gold_manifest(
            reviewed,
            _operational_gold(),
            valid_evidence_ids={"P1-primary", "P1-acceptable"},
        )


def test_ranked_metrics_use_primary_and_acceptable_ids_without_changing_gold() -> None:
    metrics = score_ranked_ids(
        ["x", "P", "z", "A", "q", "r", "s", "t", "u", "v"],
        primary_gold_ids=["P", "P2"],
        acceptable_gold_ids=["A"],
    )

    assert metrics["hit_at_1"] == 0.0
    assert metrics["hit_at_3"] == 1.0
    assert metrics["hit_at_5"] == 1.0
    assert metrics["hit_at_10"] == 1.0
    assert metrics["mrr"] == 0.5
    assert metrics["recall_at_10"] == pytest.approx(2 / 3)
    assert metrics["precision_at_10"] == 0.2
    assert metrics["ragas_id_context_precision"] == 0.2
    assert metrics["ragas_id_context_recall"] == pytest.approx(2 / 3)


def test_ranked_metrics_treat_an_empty_result_as_a_miss() -> None:
    metrics = score_ranked_ids(
        [],
        primary_gold_ids=["P"],
        acceptable_gold_ids=[],
    )

    assert metrics["hit_at_10"] == 0.0
    assert metrics["mrr"] == 0.0
    assert metrics["recall_at_10"] == 0.0
    assert metrics["precision_at_10"] == 0.0


def test_retrieval_aggregate_uses_only_approved_positive_rows() -> None:
    rows = [
        {"case_id": "P1", "aggregate_eligible": True, "metrics": {"hit_at_10": 1.0}},
        {"case_id": "P2", "aggregate_eligible": False, "metrics": {"hit_at_10": 0.0}},
    ]

    summary = aggregate_retrieval_rows(rows)

    assert summary == {"evaluated_case_count": 1, "hit_at_10": 1.0}


def test_fixed_rankings_share_one_gold_and_return_table_subset() -> None:
    gold_cases = [
        {
            "case_id": "P1",
            "question_type": "fact_specific",
            "expected_evidence_type": "text",
            "primary_gold_ids": ["g1"],
            "acceptable_gold_ids": [],
            "aggregate_eligible": True,
        },
        {
            "case_id": "P2",
            "question_type": "table_lookup",
            "expected_evidence_type": "table",
            "primary_gold_ids": ["t1"],
            "acceptable_gold_ids": [],
            "aggregate_eligible": True,
        },
    ]
    rankings = {
        "bm25_current": {"P1": ["g1", "x"], "P2": ["x", "y"]},
        "production_hybrid": {"P1": ["x", "g1"], "P2": ["t1", "x"]},
        "e5_large_eval": {"P1": ["g1", "x"], "P2": ["x", "t1"]},
        "bm25_e5_rrf_eval": {"P1": ["g1", "x"], "P2": ["t1", "x"]},
    }

    result = evaluate_fixed_rankings(gold_cases, rankings)

    assert set(result["summaries"]) == set(rankings)
    assert result["summaries"]["bm25_current"]["evaluated_case_count"] == 2
    assert result["table_subset"]["case_ids"] == ["P2"]
    assert result["table_subset"]["summaries"]["production_hybrid"]["hit_at_1"] == 1.0


def test_real_reviewed_gold_freezes_21_approved_and_11_deferred() -> None:
    reviewed = json.loads(
        (ROOT / "tests/fixtures/schat_v1_operational_gold_reviewed.json").read_text(
            encoding="utf-8"
        )
    )
    operational_gold = json.loads(
        (ROOT / "tests/fixtures/schat_v1_operational_gold.json").read_text(
            encoding="utf-8"
        )
    )
    valid_ids = collect_valid_evidence_ids(ROOT / "data/library/catalog.sqlite3")

    manifest = build_gold_manifest(
        reviewed,
        operational_gold,
        valid_evidence_ids=valid_ids,
    )

    assert manifest["approved_positive_count"] == 21
    assert manifest["deferred_positive_count"] == 11
    assert manifest["approved_abstention_count"] == 4
    assert {"UAT-S01", "UAT-S02"}.issubset(manifest["approved_positive_case_ids"])


@pytest.mark.parametrize(
    ("review", "uat", "chunk_count", "table_count", "expected"),
    [
        ({"image_required": True, "primary_gold_ids": [], "note": ""}, {"expected_evidence_type": "image"}, 2, 0, "image_human_review_required"),
        ({"image_required": False, "table_required": True, "primary_gold_ids": [], "note": ""}, {"expected_evidence_type": "table"}, 2, 2, "table_structure_or_mapping"),
        ({"image_required": False, "table_required": True, "primary_gold_ids": [], "note": "질문 의미가 모호함"}, {"expected_evidence_type": "table"}, 2, 2, "ambiguous_question"),
        ({"image_required": False, "table_required": False, "primary_gold_ids": [], "note": ""}, {"expected_evidence_type": "text"}, 0, 0, "retrieval_miss"),
        ({"image_required": False, "table_required": False, "primary_gold_ids": [], "note": ""}, {"expected_evidence_type": "text"}, 3, 0, "insufficient_candidate_evidence"),
        ({"image_required": False, "table_required": False, "primary_gold_ids": ["x"], "note": ""}, {"expected_evidence_type": "text"}, 3, 0, "other"),
    ],
)
def test_deferred_classification_is_signal_based(
    review: dict[str, object],
    uat: dict[str, object],
    chunk_count: int,
    table_count: int,
    expected: str,
) -> None:
    result = classify_deferred_case(
        review,
        {"case_id": "arbitrary", "question": "질문 문자열과 무관", **uat},
        chunk_candidate_count=chunk_count,
        table_candidate_count=table_count,
    )

    assert result["failure_category"] == expected
    assert result["automatic_gold_approval"] is False


def test_final_readiness_keeps_image_and_live_ragas_as_independent_blocks() -> None:
    result = final_readiness(
        core_regression_passed=True,
        tf027_production_gold_approved=False,
        external_transfer_approved=False,
    )

    assert result["status"] == "READY_EXCEPT_IMAGE_AND_LIVE_RAGAS"
    assert result["provider_live_status"] == "BLOCKED_BY_EXTERNAL_TRANSFER_APPROVAL"
    assert result["provider_calls"] == 0
    assert result["tf027_needs_human_review"] is True


def test_artifact_row_removes_question_source_and_human_fact_text() -> None:
    row = safe_artifact_row(
        {
            "case_id": "P1",
            "question": "local question",
            "note": "human note",
            "critical_facts": ["exact local fact"],
            "evidence_text": "hospital source",
            "retrieved_context_ids": ["c1"],
            "metrics": {"hit_at_10": 1.0},
        }
    )

    assert row == {
        "case_id": "P1",
        "retrieved_context_ids": ["c1"],
        "metrics": {"hit_at_10": 1.0},
    }
