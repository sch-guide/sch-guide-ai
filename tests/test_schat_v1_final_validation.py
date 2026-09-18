import json
import os
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from mvp.query import resolve_document_scope
from tools.schat_v1_final_validate import (
    _rss_bytes,
    assess_provider_live_readiness,
    audit_artifacts,
    build_tf027_review_html,
    classify_final_version,
    evaluate_case_contract,
    evaluate_operational_uat,
    load_operational_uat,
    safe_uat_result,
    validate_tf027_checklist,
    write_final_artifacts,
)

ROOT = Path(__file__).resolve().parents[1]
UAT = ROOT / "tests" / "fixtures" / "schat_v1_operational_uat.json"
TF027 = ROOT / "tests" / "fixtures" / "tf027_image_human_review_checklist.json"
UAT_APP = ROOT / "tools" / "schat_v1_uat_app.py"
TF027_APP = ROOT / "tools" / "tf027_human_review_app.py"


def test_provider_live_readiness_fails_closed_without_policy_approval():
    readiness = assess_provider_live_readiness(
        credentials={"groq": True, "gemini": True},
        hospital_evidence_transfer_approved=False,
        provider_policy_approved=False,
    )

    assert readiness["live_allowed"] is False
    assert readiness["actual_provider_calls"] == 0
    assert readiness["decision"] == "E"
    assert readiness["reason"] == "external_transfer_approval_missing"


@pytest.mark.skipif(os.name != "nt", reason="Windows RSS fallback contract")
def test_windows_rss_measurement_needs_no_optional_dependency():
    rss = _rss_bytes()

    assert isinstance(rss, int)
    assert rss > 0


def test_operational_uat_contract_is_36_cases_with_fixed_scope_counts():
    fixture = load_operational_uat(UAT)

    assert fixture["case_count"] == 36
    assert fixture["scope_counts"] == {
        "sedation": 14,
        "transfusion": 18,
        "out_of_scope": 4,
    }
    assert fixture["provider_call_budget"] == 0
    assert fixture["vision_call_budget"] == 0
    assert fixture["questions_unique"] is True


def test_document_scope_resolution_preserves_default_and_marks_explicit_context():
    documents = [
        {"id": "sedation", "document_name": "진정간호.pdf"},
        {"id": "transfusion", "document_name": "수혈간호.pdf"},
    ]

    assert resolve_document_scope(documents, "") == (
        ("sedation", "transfusion"),
        (),
    )
    assert resolve_document_scope(documents, "transfusion") == (
        ("transfusion",),
        ("transfusion",),
    )
    assert resolve_document_scope(documents, "missing") == (
        ("sedation", "transfusion"),
        (),
    )


def test_fixed_operational_uat_uses_scoped_text_or_table_evidence_without_calls():
    summary, rows, _ = evaluate_operational_uat(
        catalog_path=ROOT / "data" / "library" / "catalog.sqlite3",
        fixture_path=UAT,
    )

    assert summary["case_count"] == 36
    assert summary["pass_count"] == 36
    assert summary["fail_count"] == 0
    assert summary["actual_provider_calls"] == 0
    assert summary["actual_vision_calls"] == 0
    assert all(row["pass"] for row in rows)
    assert next(row for row in rows if row["case_id"] == "UAT-T11")[
        "table_retrieval_status"
    ] == "candidate_found_not_human_gold"


def test_safe_uat_result_never_serializes_question_or_source_text():
    safe = safe_uat_result(
        {
            "case_id": "UAT-S01",
            "question": "민감하지 않더라도 저장하지 않는 질문",
            "source_text": "병원 원문",
            "exact_text": "병원 원문",
            "actual_kind": "purpose",
            "selected_evidence_count": 1,
            "selected_chunk_ids": ["chunk-local-id"],
            "pass": True,
        }
    )

    encoded = json.dumps(safe, ensure_ascii=False)
    assert "question" not in safe
    assert "source_text" not in safe
    assert "exact_text" not in safe
    assert "병원 원문" not in encoded
    assert safe["case_id"] == "UAT-S01"
    assert safe["selected_evidence_count"] == 1
    assert safe["selected_id_sha256"]
    assert "selected_chunk_ids" not in safe


def test_tf027_review_html_contains_only_unreviewed_controls_and_no_inference():
    checklist = json.loads(TF027.read_text(encoding="utf-8"))
    validated = validate_tf027_checklist(checklist)
    rendered = build_tf027_review_html(validated)

    assert validated["needs_human_review"] is True
    assert validated["production_gold_approved"] is False
    assert validated["reviewed_count"] == 0
    assert validated["check_count"] == 10
    assert rendered.count('data-status="unreviewed"') == 10
    assert "needs_human_review=true" in rendered
    assert "production_gold_approved=false" in rendered
    assert "자동 추정하지 않습니다" in rendered
    assert "reviewed_value" not in rendered


def test_tf027_validation_rejects_automatic_or_partial_approval():
    checklist = json.loads(TF027.read_text(encoding="utf-8"))
    checklist["checks"][0]["status"] = "approved"
    checklist["checks"][0]["reviewed_value"] = "inferred"

    with pytest.raises(ValueError, match="human review fixture must remain unreviewed"):
        validate_tf027_checklist(checklist)


def test_case_contract_passes_only_the_approved_fail_closed_boundaries():
    supported = evaluate_case_contract(
        expected_behavior="answer",
        domain="hospital",
        intent_compatible=True,
        pre_supported=True,
        post_supported=True,
        selected_evidence_count=3,
        evidence_route_status="ready",
        provider_calls=0,
        vision_calls=0,
    )
    abstain = evaluate_case_contract(
        expected_behavior="abstain",
        domain="out_of_scope",
        intent_compatible=True,
        pre_supported=False,
        post_supported=False,
        selected_evidence_count=0,
        evidence_route_status="ready",
        provider_calls=0,
        vision_calls=0,
    )
    image = evaluate_case_contract(
        expected_behavior="pending_human_review",
        domain="hospital",
        intent_compatible=True,
        pre_supported=False,
        post_supported=False,
        selected_evidence_count=0,
        evidence_route_status="pending_image_review",
        provider_calls=0,
        vision_calls=0,
    )

    assert supported == {"pass": True, "failure_reasons": []}
    assert abstain == {"pass": True, "failure_reasons": []}
    assert image == {"pass": True, "failure_reasons": []}


def test_unknown_domain_is_still_a_safe_negative_when_nothing_is_admitted():
    result = evaluate_case_contract(
        expected_behavior="abstain",
        domain="unknown",
        intent_compatible=True,
        pre_supported=False,
        post_supported=False,
        selected_evidence_count=0,
        evidence_route_status="ready",
        provider_calls=0,
        vision_calls=0,
    )

    assert result == {"pass": True, "failure_reasons": []}


def test_case_contract_rejects_provider_call_and_unsupported_positive():
    result = evaluate_case_contract(
        expected_behavior="answer",
        domain="hospital",
        intent_compatible=True,
        pre_supported=False,
        post_supported=False,
        selected_evidence_count=0,
        evidence_route_status="ready",
        provider_calls=1,
        vision_calls=0,
    )

    assert result["pass"] is False
    assert result["failure_reasons"] == [
        "unexpected_provider_call",
        "pre_budget_not_supported",
        "post_budget_not_supported",
        "missing_selected_evidence",
    ]


def test_final_version_is_rc1_when_core_passes_but_live_and_image_are_pending():
    decision = classify_final_version(
        local_core_passed=True,
        provider_live_verified=False,
        tf027_approved=False,
        critical_regression=False,
    )

    assert decision == "v1.0-rc1"


@pytest.mark.parametrize(
    ("core", "provider", "image", "regression", "expected"),
    [
        (True, True, True, False, "v1.0"),
        (False, False, False, True, "v0.9"),
        (True, False, False, True, "v0.9"),
    ],
)
def test_final_version_never_overstates_readiness(
    core, provider, image, regression, expected
):
    assert classify_final_version(
        local_core_passed=core,
        provider_live_verified=provider,
        tf027_approved=image,
        critical_regression=regression,
    ) == expected


def test_final_artifacts_are_raw_free_and_include_human_review_surface(tmp_path):
    tf027 = validate_tf027_checklist(
        json.loads(TF027.read_text(encoding="utf-8"))
    )
    output = tmp_path / "final"
    write_final_artifacts(
        output,
        readiness={"live_allowed": False, "actual_provider_calls": 0},
        uat_summary={"case_count": 36, "pass_count": 28, "fail_count": 8},
        uat_results=[
            safe_uat_result(
                {
                    "case_id": "UAT-S01",
                    "question": "저장 금지 질문",
                    "source_text": "절대 저장하면 안 되는 병원 원문",
                    "selected_chunk_ids": ["local-chunk"],
                    "pass": True,
                }
            )
        ],
        performance={"provider_latency": "not_measured_no_live_call"},
        table_summary={"approved_case_count": 5, "hit_at_10": 1.0},
        tf027=tf027,
        final_version="v0.9",
    )

    names = {path.name for path in output.iterdir()}
    assert {
        "provider_readiness.json",
        "uat_summary.json",
        "uat_results.json",
        "performance.json",
        "table_summary.json",
        "tf027_status.json",
        "tf027_review.html",
        "summary.json",
        "review.html",
    }.issubset(names)
    audit = audit_artifacts(
        output,
        source_texts=["절대 저장하면 안 되는 병원 원문"],
    )
    assert audit["exact_source_match_count"] == 0
    assert audit["forbidden_key_count"] == 0
    assert audit["secret_marker_count"] == 0
    assert "저장 금지 질문" not in "".join(
        path.read_text(encoding="utf-8")
        for path in output.iterdir()
        if path.suffix in {".json", ".html"}
    )


def test_local_uat_review_app_renders_without_provider_transport(tmp_path, monkeypatch):
    output = tmp_path / "final"
    output.mkdir()
    (output / "uat_summary.json").write_text(
        json.dumps({"case_count": 36, "pass_count": 28, "fail_count": 8}),
        encoding="utf-8",
    )
    (output / "uat_results.json").write_text(
        json.dumps([
            {
                "case_id": "UAT-S01",
                "document_scope": "sedation",
                "question_type": "fact_specific",
                "actual_domain": "hospital",
                "pre_budget_reason": "supported",
                "post_budget_reason": "supported",
                "selected_evidence_count": 1,
                "actual_provider_calls": 0,
                "pass": True,
                "failure_category": None,
                "selected_id_sha256": "0" * 64,
            }
        ]),
        encoding="utf-8",
    )
    monkeypatch.setenv("SCHAT_V1_FINAL_ARTIFACT_DIR", str(output))

    app = AppTest.from_file(str(UAT_APP), default_timeout=30).run()

    assert not app.exception
    assert any("36" in element.value for element in app.markdown)
    assert any("Provider/Vision 실제 호출 0회" in element.value for element in app.markdown)


def test_tf027_local_review_app_keeps_all_fields_unreviewed(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHAT_TF027_CHECKLIST", str(TF027))
    reviewed = tmp_path / "tf027_image_human_review_reviewed.json"
    monkeypatch.setenv("SCHAT_TF027_REVIEWED", str(reviewed))

    app = AppTest.from_file(str(TF027_APP), default_timeout=30).run()

    assert not app.exception
    assert len(app.selectbox) == 10
    assert all(selectbox.value == "unreviewed" for selectbox in app.selectbox)
    assert all(not checkbox.value for checkbox in app.checkbox)
    assert any(
        "사람이 입력하지 않은 값은 승인되지 않습니다" in element.value
        for element in app.warning
    )
    assert not reviewed.exists()
