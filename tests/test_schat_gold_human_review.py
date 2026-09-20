import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from tools import schat_gold_human_review as gold_review
from tools.schat_gold_draft import build_draft_fixture, save_draft_fixture
from tools.schat_gold_human_review import (
    aggregate_approved_cases,
    build_candidate_rows,
    build_review_fixture,
    build_table_candidate_rows,
    load_review_inputs,
    review_progress,
    save_review_fixture,
    update_review_case,
    validate_review_case,
)
from tools.structured_table_evidence import (
    TableCellEvidence,
    TableCitation,
    TableRecord,
    TableRowEvidence,
    TableSearchHit,
)

ROOT = Path(__file__).resolve().parents[1]
UAT = ROOT / "tests" / "fixtures" / "schat_v1_operational_uat.json"
GOLD = ROOT / "tests" / "fixtures" / "schat_v1_operational_gold.json"
APP = ROOT / "tools" / "schat_gold_human_review_app.py"


def _inputs():
    return load_review_inputs(UAT, GOLD)


def test_review_inputs_select_only_32_positive_provisional_cases():
    inputs = _inputs()

    assert len(inputs["cases"]) == 32
    assert all(case["expected_abstain"] is False for case in inputs["cases"])
    assert all(not case["case_id"].startswith("UAT-N") for case in inputs["cases"])
    assert {case["case_id"] for case in inputs["cases"]} == {
        case["case_id"] for case in inputs["uat"]["cases"]
        if case["expected_behavior"] != "abstain"
    }


def test_new_review_fixture_is_unreviewed_and_contains_no_question_or_source_text():
    review = build_review_fixture(_inputs())

    assert len(review["cases"]) == 32
    assert all(case["review_status"] == "unreviewed" for case in review["cases"])
    assert all(case["primary_gold_ids"] == [] for case in review["cases"])
    assert all(case["acceptable_gold_ids"] == [] for case in review["cases"])
    assert all(case["final_gold_approved"] is False for case in review["cases"])
    assert all(case["expected_domain"] == "pending" for case in review["cases"])
    encoded = json.dumps(review, ensure_ascii=False)
    for forbidden in ("question", "evidence_text", "retrieval_rank", "retrieval_method"):
        assert f'"{forbidden}"' not in encoded


def test_saving_review_uses_separate_file_and_never_changes_source_fixtures(tmp_path):
    uat_before = UAT.read_bytes()
    gold_before = GOLD.read_bytes()
    review = build_review_fixture(_inputs())
    reviewed_path = tmp_path / "schat_v1_operational_gold_reviewed.json"

    save_review_fixture(
        reviewed_path,
        review,
        protected_paths=(UAT, GOLD),
    )

    assert reviewed_path.is_file()
    assert UAT.read_bytes() == uat_before
    assert GOLD.read_bytes() == gold_before
    with pytest.raises(ValueError, match="protected source fixture"):
        save_review_fixture(UAT, review, protected_paths=(UAT, GOLD))


def test_retrieval_rank_is_reference_only_and_never_auto_promotes_gold():
    candidates = build_candidate_rows(
        [
            {
                "chunk_id": "chunk-top-1",
                "document_name": "guide.pdf",
                "page": 1,
                "section": "section",
                "parent_id": "parent-1",
                "text": "로컬 화면에서만 표시할 근거",
                "score": 0.99,
            }
        ],
        retrieval_method="local_hybrid_reference_only",
    )
    review = build_review_fixture(_inputs())

    assert candidates[0]["retrieval_rank"] == 1
    assert candidates[0]["gold_status"] == "not_assigned"
    assert "is_gold" not in candidates[0]
    assert all(case["primary_gold_ids"] == [] for case in review["cases"])


def test_table_candidates_keep_header_row_relationship_in_memory_only():
    header = (
        TableCellEvidence(0, 0, "제제", None),
        TableCellEvidence(0, 1, "기준", None),
    )
    row = TableRowEvidence(
        row_id="row-1",
        row_index=1,
        cells=(
            TableCellEvidence(1, 0, "제품 A", None),
            TableCellEvidence(1, 1, "검수용 값", None),
        ),
        source_chunk_ids=("chunk-1",),
    )
    record = TableRecord(
        table_id="table-1",
        document_id="doc-1",
        document_name="guide.pdf",
        page=4,
        table_index=0,
        bbox=(0.0, 0.0, 10.0, 10.0),
        fingerprint="fingerprint",
        header=header,
        rows=(row,),
        source_chunk_ids=("chunk-1",),
    )
    hit = TableSearchHit(
        table_id="table-1",
        row_id="row-1",
        page=4,
        row_index=1,
        score=2.5,
        citation=TableCitation("doc-1", "guide.pdf", 4, "table-1", 1),
    )

    candidates = build_table_candidate_rows((record,), (hit,))

    assert candidates[0]["evidence_id"] == "table-1:row-1"
    assert candidates[0]["identifier_type"] == "table_row"
    assert candidates[0]["evidence_text"] == "제제 | 기준\n제품 A | 검수용 값"
    assert candidates[0]["retrieval_method"] == "local_structured_table_reference_only"
    assert candidates[0]["gold_status"] == "not_assigned"


def test_only_explicitly_final_approved_cases_enter_gold_aggregate():
    review = build_review_fixture(_inputs())
    approved = copy.deepcopy(review["cases"][0])
    approved.update(
        {
            "review_status": "approved",
            "scope_decision": "yes",
            "expected_domain": "hospital",
            "expected_document": "sedation",
            "primary_gold_ids": ["chunk-a"],
            "critical_facts": ["필수 사실"],
            "table_required": False,
            "image_required": False,
            "reviewer": "reviewer_1",
            "reviewed_at": "2026-09-18T10:00:00+09:00",
            "reviewer_1_approved": True,
            "final_gold_approved": True,
        }
    )
    waiting = copy.deepcopy(review["cases"][1])
    waiting.update(
        {
            "review_status": "needs_second_review",
            "scope_decision": "yes",
            "expected_domain": "hospital",
            "expected_document": "sedation",
            "primary_gold_ids": ["chunk-b"],
            "reviewer": "reviewer_1",
            "reviewed_at": "2026-09-18T10:00:00+09:00",
            "reviewer_1_approved": True,
            "second_review_required": True,
        }
    )
    review["cases"][:2] = [approved, waiting]

    aggregate = aggregate_approved_cases(review)

    assert [case["case_id"] for case in aggregate] == [approved["case_id"]]
    assert review_progress(review) == {
        "total": 32,
        "reviewed": 2,
        "approved": 1,
        "rejected": 0,
        "on_hold": 0,
        "needs_second_review": 1,
        "remaining": 31,
    }


def test_critical_fact_schema_and_second_review_rules_fail_closed():
    case = build_review_fixture(_inputs())["cases"][0]
    invalid = copy.deepcopy(case)
    invalid["critical_numbers"] = [15]
    with pytest.raises(ValueError, match="critical_numbers"):
        validate_review_case(invalid)

    mismatched_domain = copy.deepcopy(case)
    mismatched_domain.update({"scope_decision": "yes", "expected_domain": "pending"})
    with pytest.raises(ValueError, match="scope and domain decisions"):
        validate_review_case(mismatched_domain)

    premature = copy.deepcopy(case)
    premature.update(
        {
            "review_status": "approved",
            "scope_decision": "yes",
            "expected_domain": "hospital",
            "expected_document": "sedation",
            "primary_gold_ids": ["chunk-a"],
            "reviewer": "reviewer_1",
            "reviewed_at": "2026-09-18T10:00:00+09:00",
            "reviewer_1_approved": True,
            "second_review_required": True,
            "critical_facts": ["필수 사실"],
            "table_required": False,
            "image_required": False,
            "final_gold_approved": True,
        }
    )
    with pytest.raises(ValueError, match="second reviewer"):
        validate_review_case(premature)

    incomplete = copy.deepcopy(case)
    incomplete.update(
        {
            "review_status": "approved",
            "scope_decision": "yes",
            "expected_domain": "hospital",
            "expected_document": "sedation",
            "primary_gold_ids": ["chunk-a"],
            "reviewer": "reviewer_1",
            "reviewed_at": "2026-09-18T10:00:00+09:00",
            "reviewer_1_approved": True,
            "final_gold_approved": True,
        }
    )
    with pytest.raises(ValueError, match="critical fact and table/image decisions"):
        validate_review_case(incomplete)

    image_case = next(
        row
        for row in build_review_fixture(_inputs())["cases"]
        if row["source_label_status"] == "image_needs_human_review"
    )
    image_case.update(
        {
            "review_status": "approved",
            "scope_decision": "yes",
            "expected_domain": "hospital",
            "expected_document": "transfusion",
            "primary_gold_ids": ["figure-evidence-id"],
            "critical_facts": ["사람이 확인한 workflow 사실"],
            "table_required": False,
            "image_required": True,
            "reviewer": "reviewer_1",
            "reviewed_at": "2026-09-18T10:00:00+09:00",
            "reviewer_1_approved": True,
            "reviewer_2": "reviewer_2",
            "reviewer_2_reviewed_at": "2026-09-18T11:00:00+09:00",
            "reviewer_2_approved": True,
            "final_gold_approved": True,
        }
    )
    with pytest.raises(ValueError, match="image human review checklist"):
        validate_review_case(image_case)


def test_manual_update_does_not_persist_candidate_text_or_allow_primary_acceptable_overlap():
    review = build_review_fixture(_inputs())
    case_id = review["cases"][0]["case_id"]
    with pytest.raises(ValueError, match="overlap"):
        update_review_case(
            review,
            case_id,
            {
                "primary_gold_ids": ["chunk-a"],
                "acceptable_gold_ids": ["chunk-a"],
            },
        )

    updated = update_review_case(
        review,
        case_id,
        {
            "review_status": "reviewing",
            "scope_decision": "pending",
            "primary_gold_ids": ["chunk-a"],
            "critical_facts": ["간호사가 확인한 사실"],
            "reviewer": "reviewer_1",
            "reviewed_at": "2026-09-18T10:00:00+09:00",
        },
    )

    encoded = json.dumps(updated, ensure_ascii=False)
    assert "chunk-a" in encoded
    assert "간호사가 확인한 사실" in encoded
    assert "로컬 화면에서만 표시할 근거" not in encoded


def test_approved_submission_auto_stamps_and_saves_first_and_final_approval(tmp_path):
    review = build_review_fixture(_inputs())
    saved = review["cases"][0]
    now = "2026-09-18T14:30:00+09:00"
    updates = {
        "review_status": "approved",
        "scope_decision": "yes",
        "expected_domain": "hospital",
        "expected_document": "sedation",
        "primary_gold_ids": ["chunk-human-selected"],
        "critical_facts": ["human-confirmed critical fact"],
        "table_required": False,
        "image_required": False,
        "reviewer": " reviewer_1 ",
        "reviewer_1_approved": True,
        "second_review_required": False,
        "reviewer_2": "",
        "reviewer_2_approved": False,
        "final_gold_approved": True,
    }

    prepared = gold_review.prepare_review_submission(saved, updates, now=now)
    updated = update_review_case(review, saved["case_id"], prepared)
    reviewed_path = tmp_path / "reviewed.json"
    save_review_fixture(reviewed_path, updated)
    persisted = json.loads(reviewed_path.read_text(encoding="utf-8"))["cases"][0]

    assert persisted["reviewer"] == "reviewer_1"
    assert persisted["reviewed_at"] == now
    assert persisted["reviewer_1_reviewed_at"] == now
    assert persisted["final_approved_at"] == now


def test_human_decision_warns_before_save_when_reviewer_is_empty():
    saved = build_review_fixture(_inputs())["cases"][0]

    with pytest.raises(ValueError, match="reviewer"):
        gold_review.prepare_review_submission(
            saved,
            {
                "review_status": "reviewing",
                "reviewer": "   ",
                "reviewer_1_approved": False,
                "reviewer_2": "",
                "reviewer_2_approved": False,
                "final_gold_approved": False,
            },
            now="2026-09-18T14:30:00+09:00",
        )


def test_second_reviewer_approval_gets_its_own_timestamp():
    saved = build_review_fixture(_inputs())["cases"][0]
    now = "2026-09-18T15:00:00+09:00"

    prepared = gold_review.prepare_review_submission(
        saved,
        {
            "review_status": "approved",
            "reviewer": "reviewer_1",
            "reviewer_1_approved": True,
            "reviewer_2": " reviewer_2 ",
            "reviewer_2_approved": True,
            "final_gold_approved": True,
        },
        now=now,
    )

    assert prepared["reviewer_2"] == "reviewer_2"
    assert prepared["reviewer_2_reviewed_at"] == now


def test_resaving_an_approval_preserves_existing_approval_timestamps():
    saved = build_review_fixture(_inputs())["cases"][0]
    saved.update(
        {
            "reviewed_at": "2026-09-18T10:00:00+09:00",
            "reviewer_1_reviewed_at": "2026-09-18T10:00:00+09:00",
            "final_approved_at": "2026-09-18T10:05:00+09:00",
        }
    )

    prepared = gold_review.prepare_review_submission(
        saved,
        {
            "review_status": "approved",
            "reviewer": "reviewer_1",
            "reviewer_1_approved": True,
            "reviewer_2": "",
            "reviewer_2_approved": False,
            "final_gold_approved": True,
        },
        now="2026-09-18T16:00:00+09:00",
    )

    assert prepared["reviewed_at"] == "2026-09-18T16:00:00+09:00"
    assert prepared["reviewer_1_reviewed_at"] == "2026-09-18T10:00:00+09:00"
    assert prepared["final_approved_at"] == "2026-09-18T10:05:00+09:00"


def _assisted_draft(case_id):
    return {
        "case_id": case_id,
        "draft_only": True,
        "review_status": "unreviewed",
        "final_gold_approved": False,
        "scope_decision": "yes",
        "expected_domain": "hospital",
        "expected_document": "sedation",
        "expected_abstain": False,
        "primary_gold_ids": ["draft-primary"],
        "acceptable_gold_ids": ["draft-acceptable"],
        "critical_facts": ["human must verify this exact draft fact"],
        "critical_numbers": [],
        "critical_units": [],
        "critical_times": [],
        "critical_conditions": [],
        "critical_contraindications": [],
        "critical_negations": [],
        "critical_steps": [],
        "table_required": False,
        "image_required": False,
    }


def test_accepting_draft_records_first_review_but_does_not_enter_gold_aggregate():
    review = build_review_fixture(_inputs())
    saved = review["cases"][0]
    prepared = gold_review.build_assisted_review_updates(
        saved,
        _assisted_draft(saved["case_id"]),
        action="draft_accepted",
        form_updates={},
        reviewer="reviewer_1",
        final_approval_requested=False,
        now="2026-09-18T17:00:00+09:00",
    )
    updated = update_review_case(review, saved["case_id"], prepared)

    changed = updated["cases"][0]
    assert changed["review_status"] == "reviewing"
    assert changed["reviewer_1_approved"] is True
    assert changed["reviewer_1_reviewed_at"] == "2026-09-18T17:00:00+09:00"
    assert changed["final_gold_approved"] is False
    assert changed["draft_disposition"] == "draft_accepted"
    assert aggregate_approved_cases(updated) == []


def test_final_checkbox_is_required_before_assisted_review_becomes_approved_gold():
    review = build_review_fixture(_inputs())
    saved = review["cases"][0]
    prepared = gold_review.build_assisted_review_updates(
        saved,
        _assisted_draft(saved["case_id"]),
        action="draft_accepted",
        form_updates={},
        reviewer="reviewer_1",
        final_approval_requested=True,
        now="2026-09-18T17:05:00+09:00",
    )
    updated = update_review_case(review, saved["case_id"], prepared)

    changed = updated["cases"][0]
    assert changed["review_status"] == "approved"
    assert changed["final_gold_approved"] is True
    assert changed["final_approved_at"] == "2026-09-18T17:05:00+09:00"
    assert [case["case_id"] for case in aggregate_approved_cases(updated)] == [
        saved["case_id"]
    ]


def test_modified_assisted_review_uses_human_fields_not_original_draft_values():
    saved = build_review_fixture(_inputs())["cases"][0]
    prepared = gold_review.build_assisted_review_updates(
        saved,
        _assisted_draft(saved["case_id"]),
        action="modified_accepted",
        form_updates={
            "primary_gold_ids": ["human-primary"],
            "acceptable_gold_ids": [],
            "critical_facts": ["human corrected fact"],
            "table_required": False,
            "image_required": False,
        },
        reviewer="reviewer_1",
        final_approval_requested=False,
        now="2026-09-18T17:10:00+09:00",
    )

    assert prepared["primary_gold_ids"] == ["human-primary"]
    assert prepared["critical_facts"] == ["human corrected fact"]
    assert prepared["draft_disposition"] == "modified_accepted"


def test_assisted_review_cannot_change_an_existing_final_approved_case():
    saved = build_review_fixture(_inputs())["cases"][0]
    saved.update(
        {
            "review_status": "approved",
            "final_gold_approved": True,
            "reviewer": "reviewer_1",
        }
    )

    with pytest.raises(ValueError, match="final-approved"):
        gold_review.build_assisted_review_updates(
            saved,
            _assisted_draft(saved["case_id"]),
            action="draft_accepted",
            form_updates={},
            reviewer="reviewer_2",
            final_approval_requested=False,
        )


def test_hold_and_next_unreviewed_navigation_are_fail_closed():
    review = build_review_fixture(_inputs())
    first = review["cases"][0]
    prepared = gold_review.build_assisted_review_updates(
        first,
        _assisted_draft(first["case_id"]),
        action="on_hold",
        form_updates={},
        reviewer="reviewer_1",
        final_approval_requested=True,
        now="2026-09-18T17:15:00+09:00",
    )
    updated = update_review_case(review, first["case_id"], prepared)

    assert updated["cases"][0]["review_status"] == "reviewing"
    assert updated["cases"][0]["reviewer_1_approved"] is False
    assert updated["cases"][0]["final_gold_approved"] is False
    assert gold_review.next_unreviewed_case_id(updated, first["case_id"]) == updated[
        "cases"
    ][1]["case_id"]


def test_streamlit_review_app_is_local_only_and_does_not_write_on_open(tmp_path, monkeypatch):
    reviewed = tmp_path / "reviewed.json"
    drafts = tmp_path / "drafts.json"
    monkeypatch.setenv("SCHAT_GOLD_REVIEWED_FIXTURE", str(reviewed))
    monkeypatch.setenv("SCHAT_GOLD_DRAFT_FIXTURE", str(drafts))

    app = AppTest.from_file(str(APP), default_timeout=30).run()

    assert not app.exception
    assert app.title[0].value == "SCHAT 운영 Positive Gold 사람 검수"
    assert any("32" in metric.value for metric in app.metric)
    assert any(button.label == "후보 근거 불러오기" for button in app.button)
    assert not reviewed.exists()


def test_streamlit_assisted_review_shows_draft_and_keeps_first_approval_non_final(
    tmp_path, monkeypatch
):
    reviewed_path = tmp_path / "reviewed.json"
    draft_path = tmp_path / "drafts.json"
    inputs = _inputs()
    blank_review = build_review_fixture(inputs)

    def candidates(case):
        document_name = (
            "진정간호.pdf"
            if case["expected_document_scope"] == "sedation"
            else "실무지침서_수혈간호.pdf"
        )
        return [
            {
                "evidence_id": f"draft-{case['case_id']}",
                "identifier_type": "chunk",
                "document_name": document_name,
                "page": 1,
                "section": case["question"],
                "parent_id": "parent-1",
                "evidence_text": f"{case['question']} 관련 지침 근거이다.",
                "retrieval_rank": 2,
                "retrieval_method": "local_reference_only",
                "score": 0.5,
                "gold_status": "not_assigned",
            }
        ]

    drafts = build_draft_fixture(inputs, blank_review, candidates)
    save_draft_fixture(draft_path, drafts)
    monkeypatch.setenv("SCHAT_GOLD_REVIEWED_FIXTURE", str(reviewed_path))
    monkeypatch.setenv("SCHAT_GOLD_DRAFT_FIXTURE", str(draft_path))

    app = AppTest.from_file(str(APP), default_timeout=60).run()

    assert not app.exception
    assert any(element.value == "자동 Gold 초안" for element in app.subheader)
    assert any(button.label == "초안 그대로 승인" for button in app.button)
    assert any(button.label == "수정 후 승인" for button in app.button)
    assert any(button.label == "판단 보류" for button in app.button)

    reviewer = next(widget for widget in app.text_input if "1차 reviewer" in widget.label)
    reviewer.set_value("reviewer_1")
    next(button for button in app.button if button.label == "초안 그대로 승인").click()
    app.run(timeout=60)

    persisted = json.loads(reviewed_path.read_text(encoding="utf-8"))["cases"][0]
    assert persisted["review_status"] == "reviewing"
    assert persisted["reviewer_1_approved"] is True
    assert persisted["final_gold_approved"] is False
    assert app.selectbox[1].value == "UAT-S02"


def test_streamlit_form_saves_reviewer_1_and_final_approval_with_timestamps(
    tmp_path, monkeypatch
):
    reviewed = tmp_path / "reviewed.json"
    drafts = tmp_path / "drafts.json"
    monkeypatch.setenv("SCHAT_GOLD_REVIEWED_FIXTURE", str(reviewed))
    monkeypatch.setenv("SCHAT_GOLD_DRAFT_FIXTURE", str(drafts))
    app = AppTest.from_file(str(APP), default_timeout=60).run()

    next(button for button in app.button if button.label == "후보 근거 불러오기").click()
    app.run(timeout=60)
    candidate_id = app.multiselect[0].options[0].split()[0]
    next(widget for widget in app.selectbox if widget.label.startswith("A.")).set_value("yes")
    next(widget for widget in app.selectbox if widget.label.startswith("B.")).set_value(
        "sedation"
    )
    app.multiselect[0].set_value([candidate_id])
    next(widget for widget in app.text_area if widget.label == "필수 핵심 사실").set_value(
        "human-confirmed critical fact"
    )
    next(widget for widget in app.selectbox if widget.label == "표 근거 필요 여부").set_value(
        False
    )
    next(
        widget for widget in app.selectbox if widget.label == "이미지 근거 필요 여부"
    ).set_value(False)
    next(widget for widget in app.text_input if "1차 reviewer" in widget.label).set_value(
        "reviewer_1"
    )
    next(widget for widget in app.checkbox if "최종 Gold 승인" in widget.label).check()
    next(button for button in app.button if button.label == "수정 후 승인").click()
    app.run(timeout=60)

    assert not app.exception
    assert not app.error
    persisted = json.loads(reviewed.read_text(encoding="utf-8"))["cases"][0]
    assert persisted["reviewer"] == "reviewer_1"
    assert persisted["reviewed_at"]
    assert persisted["reviewer_1_reviewed_at"]
    assert persisted["final_approved_at"]


def test_streamlit_script_loads_without_pytest_pythonpath(tmp_path):
    """Catch the real `streamlit run tools/...` import boundary."""
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    probe = (
        "import runpy; "
        f"runpy.run_path({str(APP)!r}, run_name='streamlit_import_probe')"
    )

    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_review_module_contains_no_provider_or_network_transport():
    helper = (ROOT / "tools" / "schat_gold_human_review.py").read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")
    code = helper + app

    assert "httpx" not in code
    assert "requests" not in code
    assert "groq" not in code.casefold()
    assert "gemini" not in code.casefold()
    assert "api_key" not in code.casefold()
