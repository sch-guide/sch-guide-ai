from __future__ import annotations

import json
import socket
from pathlib import Path

from tools.schat_v1_release_readiness import (
    build_deferred_queue,
    build_live_ragas_plan,
    build_tf027_readiness,
    decide_release_readiness,
    detect_sensitive_identifier_types,
    run_release_readiness,
)

ROOT = Path(__file__).resolve().parents[1]
REVIEWED_GOLD = ROOT / "tests/fixtures/schat_v1_operational_gold_reviewed.json"
OPERATIONAL_GOLD = ROOT / "tests/fixtures/schat_v1_operational_gold.json"
UAT = ROOT / "tests/fixtures/schat_v1_operational_uat.json"
TF027 = ROOT / "tests/fixtures/tf027_image_human_review_checklist.json"
TF027_REVIEWED = ROOT / "tests/fixtures/tf027_image_human_review_reviewed.json"
DEFERRED = ROOT / "workspace/과거작업/평가산출물/2026-09-18_schat-v1-final-stabilization/deferred_analysis.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_tf027_without_reviewed_fixture_stops_for_human_review() -> None:
    status = build_tf027_readiness(_load(TF027), reviewed=None)

    assert status["status"] == "STOP_FOR_TF027_HUMAN_REVIEW"
    assert status["reviewed_check_count"] == 0
    assert status["remaining_check_count"] == 10
    assert status["production_gold_approved"] is False
    assert status["actual_vision_calls"] == 0
    assert len(status["figure_candidates"]) == 7
    assert all(set(row) == {"figure_id", "bbox", "fingerprint"} for row in status["figure_candidates"])


def test_tf027_human_rejection_closes_release_blocker_without_approving_gold() -> None:
    status = build_tf027_readiness(_load(TF027), reviewed=_load(TF027_REVIEWED))

    assert status["status"] == "READY_IMAGE_EXCLUDED_BY_HUMAN_DECISION"
    assert status["reviewed_check_count"] == 10
    assert status["remaining_check_count"] == 0
    assert status["image_excluded_by_human_decision"] is True
    assert status["release_blocker_closed"] is True
    assert status["production_gold_approved"] is False
    assert status["included_in_aggregate"] is False
    assert status["actual_vision_calls"] == 0


def test_live_ragas_plan_uses_only_approved_gold_and_remains_blocked() -> None:
    plan = build_live_ragas_plan(
        reviewed=_load(REVIEWED_GOLD),
        operational_gold=_load(OPERATIONAL_GOLD),
        uat=_load(UAT),
        external_transfer_approval=None,
    )

    assert plan["status"] == "BLOCKED_BY_EXTERNAL_TRANSFER_APPROVAL"
    assert plan["approved_positive_count"] == 21
    assert plan["deferred_count"] == 11
    assert plan["approved_abstention_count"] == 4
    assert plan["actual_provider_calls"] == 0
    assert plan["actual_llm_judge_calls"] == 0
    assert plan["stages"][0] == {
        "stage": "synthetic",
        "case_ids": [],
        "enabled_now": True,
        "requires_external_transfer": False,
        "stop_on_critical_failure": True,
    }
    assert 3 <= len(plan["stages"][1]["case_ids"]) <= 5
    assert len(plan["stages"][2]["case_ids"]) == 21
    assert plan["stages"][1]["enabled_now"] is False
    assert plan["stages"][2]["enabled_now"] is False
    assert not set(plan["deferred_case_ids"]).intersection(plan["stages"][2]["case_ids"])
    assert not set(plan["approved_abstention_case_ids"]).intersection(
        plan["stages"][2]["case_ids"]
    )


def test_live_payload_contract_is_minimal_and_requires_local_identifier_scan() -> None:
    plan = build_live_ragas_plan(
        reviewed=_load(REVIEWED_GOLD),
        operational_gold=_load(OPERATIONAL_GOLD),
        uat=_load(UAT),
        external_transfer_approval=None,
    )

    assert plan["transmitted_fields"] == [
        "request_local_source_unit_id",
        "selected_exact_source_unit_text",
        "normalized_intent",
        "output_json_schema",
        "safety_instruction",
    ]
    assert "original_user_question" in plan["excluded_fields"]
    assert "document_name" in plan["excluded_fields"]
    assert "page" in plan["excluded_fields"]
    assert "production_chunk_id" in plan["excluded_fields"]
    assert "patient_or_staff_identifier" in plan["excluded_fields"]
    assert plan["preflight"]["local_identifier_scan_required"] is True
    assert plan["preflight"]["raw_prompt_persisted"] is False
    assert plan["preflight"]["raw_response_persisted"] is False


def test_live_plan_never_expands_beyond_human_approved_case_scope() -> None:
    reviewed = _load(REVIEWED_GOLD)
    approved_ids = [
        case["case_id"]
        for case in reviewed["cases"]
        if case["final_gold_approved"] is True
    ]
    deferred_id = next(
        case["case_id"]
        for case in reviewed["cases"]
        if case["final_gold_approved"] is False
    )
    approval = {
        "hospital_evidence_external_transfer": True,
        "provider": "approved-provider",
        "model": "approved-model",
        "region": "approved-region",
        "retention_logging": "approved-policy",
        "case_scope": approved_ids[:3],
        "credential_reference": "approved-secret-reference",
    }

    limited = build_live_ragas_plan(
        reviewed=reviewed,
        operational_gold=_load(OPERATIONAL_GOLD),
        uat=_load(UAT),
        external_transfer_approval=approval,
    )
    assert limited["status"] == "READY_FOR_HUMAN_INITIATED_LIVE_RUN"
    assert limited["stages"][1]["case_ids"] == approved_ids[:3]
    assert limited["stages"][2]["case_ids"] == approved_ids[:3]

    approval["case_scope"] = [approved_ids[0], deferred_id]
    rejected = build_live_ragas_plan(
        reviewed=reviewed,
        operational_gold=_load(OPERATIONAL_GOLD),
        uat=_load(UAT),
        external_transfer_approval=approval,
    )
    assert rejected["status"] == "BLOCKED_BY_EXTERNAL_TRANSFER_APPROVAL"
    assert "case_scope_not_subset_of_approved_positive" in rejected[
        "missing_approval_fields"
    ]
    assert rejected["stages"][1]["enabled_now"] is False
    assert rejected["stages"][2]["enabled_now"] is False


def test_identifier_detector_returns_categories_without_echoing_values() -> None:
    categories = detect_sensitive_identifier_types(
        ["연락처 010-1234-5678", "메일 nurse@example.com", "번호 900101-1234567"]
    )

    assert categories == ["email", "phone", "resident_registration_number"]
    assert "010-1234-5678" not in json.dumps(categories)


def test_deferred_queue_keeps_all_cases_non_approved() -> None:
    queue = build_deferred_queue(
        reviewed=_load(REVIEWED_GOLD),
        deferred_analysis=_load(DEFERRED),
    )

    assert queue["case_count"] == 11
    assert queue["automatically_approved_count"] == 0
    assert queue["remaining_human_review_count"] == 11
    assert all(row["final_gold_approved"] is False for row in queue["rows"])
    assert all("question" not in row for row in queue["rows"])


def test_release_decision_keeps_two_independent_human_blocks() -> None:
    decision = decide_release_readiness(
        core_regression_passed=True,
        tf027_production_gold_approved=False,
        live_ragas_completed=False,
        image_excluded_by_human_decision=False,
        external_provider_disabled_by_human_decision=False,
    )

    assert decision["status"] == "READY_EXCEPT_IMAGE_AND_LIVE_RAGAS"
    assert decision["blocking_items"] == [
        "TF027_HUMAN_REVIEW_OR_EXPLICIT_V1_SCOPE_EXCLUSION",
        "LIVE_RAGAS_OR_EXPLICIT_NO_EXTERNAL_PROVIDER_DECISION",
    ]


def test_release_evaluator_writes_raw_free_artifacts_without_network(
    tmp_path, monkeypatch
) -> None:
    def reject_network(*_args, **_kwargs):
        raise AssertionError("release readiness attempted network access")

    monkeypatch.setattr(socket, "create_connection", reject_network)

    result = run_release_readiness(output_dir=tmp_path)

    assert result["readiness"]["status"] == "READY_EXCEPT_LIVE_RAGAS"
    assert result["live_ragas"]["actual_provider_calls"] == 0
    assert result["tf027"]["actual_vision_calls"] == 0
    assert result["security"]["pass"] is True
    assert {
        "release_readiness.json",
        "tf027_status.json",
        "live_ragas_plan.json",
        "deferred_queue.json",
        "chromadb_roadmap.json",
        "review.html",
        "security_audit.json",
    } == {path.name for path in tmp_path.iterdir()}
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in tmp_path.iterdir()
        if path.suffix in {".json", ".html"}
    )
    assert '"question"' not in combined
    assert '"exact_text"' not in combined
    assert '"raw_response"' not in combined
    assert "authorization" not in combined.casefold()
    assert ".pdf" not in combined.casefold()
