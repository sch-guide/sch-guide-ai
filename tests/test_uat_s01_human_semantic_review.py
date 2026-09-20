from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.uat_s01_human_semantic_review import (
    apply_human_semantic_review,
    build_human_review_record,
    build_reviewed_case_snapshot,
    generated_answer_sha256,
    human_review_allows_subset_start,
    load_human_review_record,
    load_reviewed_case_snapshot,
    parse_generated_statements,
    save_human_review_record,
    save_reviewed_case_snapshot,
)

GENERATED = json.dumps(
    {
        "statements": [
            {
                "text": "검수할 생성 문장입니다.",
                "supporting_source_unit_ids": ["su001"],
            }
        ]
    },
    ensure_ascii=False,
)


def _failed_safety() -> dict[str, object]:
    return {
        "case_id": "UAT-S01",
        "critical_fact_preservation": False,
        "semantic_support": "pending_llm_judge_not_authorized_by_payload_allowlist",
        "failure_codes": ["critical_fact_preservation"],
        "critical_safety_error_count": 1,
    }


def test_pass_review_record_contains_only_hashes_and_metadata() -> None:
    record = build_human_review_record(
        case_id="UAT-S01",
        decision="PASS",
        reviewer="reviewer_1",
        generated_content=GENERATED,
        evidence_sha256="e" * 64,
        reviewed_at="2026-09-18T12:34:56+09:00",
    )

    assert record == {
        "schema_version": 1,
        "case_id": "UAT-S01",
        "decision": "PASS",
        "reviewer": "reviewer_1",
        "reviewed_at": "2026-09-18T12:34:56+09:00",
        "generated_answer_sha256": generated_answer_sha256(GENERATED),
        "evidence_sha256": "e" * 64,
        "human_semantic_support_approved": True,
    }
    serialized = json.dumps(record, ensure_ascii=False)
    assert "검수할 생성 문장" not in serialized
    assert "exact_text" not in serialized
    assert "raw_response" not in serialized


@pytest.mark.parametrize("decision", ["FAIL", "UNCERTAIN"])
def test_non_pass_review_never_approves_semantic_support(decision: str) -> None:
    record = build_human_review_record(
        case_id="UAT-S01",
        decision=decision,
        reviewer="reviewer_1",
        generated_content=GENERATED,
        evidence_sha256="e" * 64,
    )

    assert record["human_semantic_support_approved"] is False
    assert human_review_allows_subset_start(record) is False


def test_pass_review_is_required_before_subset_can_start() -> None:
    record = build_human_review_record(
        case_id="UAT-S01",
        decision="PASS",
        reviewer="reviewer_1",
        generated_content=GENERATED,
        evidence_sha256="e" * 64,
    )

    assert human_review_allows_subset_start(None) is False
    assert human_review_allows_subset_start(record) is True


def test_review_requires_reviewer() -> None:
    with pytest.raises(ValueError, match="reviewer_required"):
        build_human_review_record(
            case_id="UAT-S01",
            decision="PASS",
            reviewer="  ",
            generated_content=GENERATED,
            evidence_sha256="e" * 64,
        )


def test_review_result_round_trip_never_persists_raw_content(tmp_path: Path) -> None:
    target = tmp_path / "uat_s01_human_semantic_review.json"
    record = build_human_review_record(
        case_id="UAT-S01",
        decision="PASS",
        reviewer="reviewer_1",
        generated_content=GENERATED,
        evidence_sha256="e" * 64,
    )

    save_human_review_record(target, record)

    assert load_human_review_record(target) == record
    persisted = target.read_text(encoding="utf-8")
    assert "검수할 생성 문장" not in persisted
    assert set(json.loads(persisted)) == {
        "schema_version",
        "case_id",
        "decision",
        "reviewer",
        "reviewed_at",
        "generated_answer_sha256",
        "evidence_sha256",
        "human_semantic_support_approved",
    }


def test_matching_pass_review_clears_only_critical_fact_failure() -> None:
    review = build_human_review_record(
        case_id="UAT-S01",
        decision="PASS",
        reviewer="reviewer_1",
        generated_content=GENERATED,
        evidence_sha256="e" * 64,
    )

    updated = apply_human_semantic_review(
        _failed_safety(),
        case_id="UAT-S01",
        generated_content=GENERATED,
        evidence_sha256="e" * 64,
        review=review,
    )

    assert updated["critical_fact_preservation"] is True
    assert updated["semantic_support"] == "human_approved_exact_hash_match"
    assert updated["human_semantic_support_approved"] is True
    assert updated["failure_codes"] == []
    assert updated["critical_safety_error_count"] == 0


@pytest.mark.parametrize(
    ("case_id", "generated_content", "evidence_sha256"),
    [
        ("UAT-S02", GENERATED, "e" * 64),
        ("UAT-S01", GENERATED + " ", "e" * 64),
        ("UAT-S01", GENERATED, "f" * 64),
    ],
)
def test_review_mismatch_fails_closed(
    case_id: str, generated_content: str, evidence_sha256: str
) -> None:
    review = build_human_review_record(
        case_id="UAT-S01",
        decision="PASS",
        reviewer="reviewer_1",
        generated_content=GENERATED,
        evidence_sha256="e" * 64,
    )

    updated = apply_human_semantic_review(
        _failed_safety(),
        case_id=case_id,
        generated_content=generated_content,
        evidence_sha256=evidence_sha256,
        review=review,
    )

    assert updated["critical_fact_preservation"] is False
    assert updated["human_semantic_support_approved"] is False
    assert updated["failure_codes"] == ["critical_fact_preservation"]
    assert updated["critical_safety_error_count"] == 1


def test_generated_statement_parser_preserves_request_local_mapping() -> None:
    statements = parse_generated_statements(GENERATED)

    assert statements == [
        {
            "text": "검수할 생성 문장입니다.",
            "supporting_source_unit_ids": ["su001"],
        }
    ]


def test_streamlit_review_app_uses_git_ignored_local_result_path() -> None:
    from tools import uat_s01_human_semantic_review_app as app

    assert app.CASE_ID == "UAT-S01"
    assert app.REVIEW_RESULT_PATH.parent.name == ".tmp"
    assert app.REVIEW_RESULT_PATH.name == "uat_s01_human_semantic_review.json"
    assert app.REVIEW_SNAPSHOT_PATH.parent.name == ".tmp"
    assert app.REVIEW_SNAPSHOT_PATH.name == "uat_s01_human_semantic_review_snapshot.json"
    assert callable(app.main)


def test_streamlit_review_app_shows_safe_groq_error_category() -> None:
    from tools import uat_s01_human_semantic_review_app as app
    from tools.groq_live_ragas_evaluate import GroqLiveError

    assert app.format_review_error(GroqLiveError("provider_connection")) == (
        "Connection failed: DNS/TCP/proxy/firewall 상태를 확인하세요."
    )
    assert "GroqLiveError" not in app.format_review_error(
        GroqLiveError("provider_http_401")
    )


def test_human_semantic_review_requires_all_non_semantic_safety_checks() -> None:
    from tools import uat_s01_human_semantic_review_app as app

    passing = {
        field: True for field in app.NON_SEMANTIC_REVIEW_PREREQUISITES
    }
    assert app.semantic_review_ready(passing) is True

    for field in app.NON_SEMANTIC_REVIEW_PREREQUISITES:
        failed = {**passing, field: False}
        assert app.semantic_review_ready(failed) is False


def test_streamlit_app_persists_same_session_safe_snapshot(monkeypatch, tmp_path) -> None:
    from tools import uat_s01_human_semantic_review_app as app

    target = tmp_path / "snapshot.json"
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        app,
        "safe_live_call_record",
        lambda _blueprint, _live: {"safe": "call"},
    )
    monkeypatch.setattr(
        app,
        "build_reviewed_case_snapshot",
        lambda **kwargs: {"safe": kwargs},
    )
    monkeypatch.setattr(
        app,
        "save_reviewed_case_snapshot",
        lambda path, snapshot: captured.update(path=path, snapshot=snapshot),
    )

    app.persist_reviewed_session_snapshot(
        {"blueprint": object(), "live": object(), "safety": {"ok": True}},
        {"decision": "PASS"},
        path=target,
    )

    assert captured["path"] == target
    assert captured["snapshot"]["safe"]["review"] == {"decision": "PASS"}


def _safe_call_record() -> dict[str, object]:
    return {
        "case_id": "UAT-S01",
        "provider": "groq",
        "model": "openai/gpt-oss-20b",
        "generated_answer_sha256": generated_answer_sha256(GENERATED),
        "evidence_sha256": "e" * 64,
        "latency_ms": 123.4,
        "http_status": 200,
    }


def _structurally_safe_result() -> dict[str, object]:
    return {
        "schema_pass": True,
        "citation_pass": True,
        "critical_fact_preservation": False,
        "number_pass": True,
        "unit_pass": True,
        "time_pass": True,
        "condition_pass": True,
        "negation_pass": True,
        "action_pass": True,
        "validation_reason": "semantic_support_pending",
        "failure_codes": ["critical_fact_preservation"],
    }


def test_reviewed_case_snapshot_contains_only_safe_structured_result(tmp_path) -> None:
    review = build_human_review_record(
        case_id="UAT-S01",
        decision="PASS",
        reviewer="reviewer_1",
        generated_content=GENERATED,
        evidence_sha256="e" * 64,
        reviewed_at="2026-09-18T21:37:14+09:00",
    )

    snapshot = build_reviewed_case_snapshot(
        review=review,
        call_record=_safe_call_record(),
        safety=_structurally_safe_result(),
    )
    target = tmp_path / "snapshot.json"
    save_reviewed_case_snapshot(target, snapshot)

    loaded = load_reviewed_case_snapshot(target)
    assert loaded == snapshot
    assert loaded["human_semantic_support_approved"] is True
    assert loaded["critical_fact_preservation"] is True
    assert loaded["deterministic_critical_fact_preservation"] is False
    assert loaded["branch_phase_pass"] is True
    assert loaded["unsupported_clinical_claim_pass"] is True
    serialized = target.read_text(encoding="utf-8")
    assert "exact_text" not in serialized
    assert "raw_response" not in serialized
    assert "statements" not in serialized
    assert "검수할 생성 문장" not in serialized


def test_reviewed_case_snapshot_rejects_answer_hash_mismatch() -> None:
    review = build_human_review_record(
        case_id="UAT-S01",
        decision="PASS",
        reviewer="reviewer_1",
        generated_content=GENERATED,
        evidence_sha256="e" * 64,
    )
    call_record = {**_safe_call_record(), "generated_answer_sha256": "a" * 64}

    with pytest.raises(ValueError, match="reviewed_answer_hash_mismatch"):
        build_reviewed_case_snapshot(
            review=review,
            call_record=call_record,
            safety=_structurally_safe_result(),
        )


def test_reviewed_case_snapshot_rejects_failed_structural_validator() -> None:
    review = build_human_review_record(
        case_id="UAT-S01",
        decision="PASS",
        reviewer="reviewer_1",
        generated_content=GENERATED,
        evidence_sha256="e" * 64,
    )
    safety = {**_structurally_safe_result(), "number_pass": False}

    with pytest.raises(ValueError, match="reviewed_snapshot_safety_failed"):
        build_reviewed_case_snapshot(
            review=review,
            call_record=_safe_call_record(),
            safety=safety,
        )
