from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.uat_t01_human_semantic_review import (
    CASE_ID,
    build_human_review_record,
    load_human_review_record,
    save_human_review_record,
)


def _build_record(**overrides):
    values = {
        "case_id": CASE_ID,
        "reviewer": "reviewer_1",
        "generated_answer_sha256": "a" * 64,
        "evidence_sha256": "e" * 64,
        "critical_fact_decision": "PASS",
        "condition_decision": "PASS",
        "action_steps_decision": "PASS",
        "unsupported_clinical_claim_decision": "NONE",
        "reviewed_at": "2026-09-18T22:30:00+09:00",
    }
    values.update(overrides)
    return build_human_review_record(**values)


def test_all_pass_and_no_unsupported_claim_is_the_only_approval_path() -> None:
    record = _build_record()

    assert record["human_semantic_support_approved"] is True
    assert record["failure_categories"] == []


@pytest.mark.parametrize(
    ("overrides", "failure_category"),
    [
        ({"critical_fact_decision": "FAIL"}, "critical_fact_fail"),
        ({"condition_decision": "UNCERTAIN"}, "condition_uncertain"),
        ({"action_steps_decision": "FAIL"}, "action_steps_fail"),
        (
            {"unsupported_clinical_claim_decision": "PRESENT"},
            "unsupported_clinical_claim_present",
        ),
        (
            {"unsupported_clinical_claim_decision": "UNCERTAIN"},
            "unsupported_clinical_claim_uncertain",
        ),
    ],
)
def test_any_failed_or_uncertain_decision_blocks_approval(
    overrides, failure_category
) -> None:
    record = _build_record(**overrides)

    assert record["human_semantic_support_approved"] is False
    assert failure_category in record["failure_categories"]


def test_review_requires_reviewer_and_valid_hashes() -> None:
    with pytest.raises(ValueError, match="reviewer_required"):
        _build_record(reviewer=" ")
    with pytest.raises(ValueError, match="generated_answer_sha256_invalid"):
        _build_record(generated_answer_sha256="not-a-hash")


def test_hash_only_review_round_trip_excludes_raw_content(tmp_path: Path) -> None:
    target = tmp_path / "uat_t01_human_semantic_review.json"
    record = _build_record(condition_decision="FAIL")

    save_human_review_record(target, record)

    assert load_human_review_record(target) == record
    persisted = target.read_text(encoding="utf-8")
    payload = json.loads(persisted)
    assert set(payload) == {
        "schema_version",
        "case_id",
        "reviewer",
        "reviewed_at",
        "generated_answer_sha256",
        "evidence_sha256",
        "critical_fact_decision",
        "condition_decision",
        "action_steps_decision",
        "unsupported_clinical_claim_decision",
        "failure_categories",
        "human_semantic_support_approved",
    }
    for forbidden_key in (
        "generated_answer",
        "evidence_text",
        "raw_prompt",
        "raw_response",
        "api_key",
        "authorization",
        "note",
    ):
        assert forbidden_key not in payload


def test_streamlit_app_is_local_only_and_locks_generation_per_session() -> None:
    from tools import uat_t01_human_semantic_review_app as app

    assert app.CASE_ID == "UAT-T01"
    assert app.REVIEW_RESULT_PATH.parent.name == ".tmp"
    assert app.REVIEW_RESULT_PATH.name == "uat_t01_human_semantic_review.json"
    assert app.generation_allowed({}) is True
    assert app.generation_allowed({app.SESSION_KEY: object()}) is False
    assert app.generation_allowed({app.ATTEMPT_KEY: True}) is False
    assert callable(app.main)
