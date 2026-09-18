import json
import subprocess
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from tools.tf027_human_review import (
    aggregate_approved_image_gold,
    build_reviewed_fixture,
    image_answerability_enabled,
    load_source_checklist,
    normalize_review_state,
    render_review_images,
    save_reviewed_fixture,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests" / "fixtures" / "tf027_image_human_review_checklist.json"
REVIEWED = ROOT / "tests" / "fixtures" / "tf027_image_human_review_reviewed.json"
PDF = ROOT / "data" / "실무지침서_수혈간호.pdf"
APP = ROOT / "tools" / "tf027_human_review_app.py"


def _blank_review():
    return build_reviewed_fixture(load_source_checklist(SOURCE), source_path=SOURCE)


def _completed_review():
    review = _blank_review()
    for check in review["checks"]:
        check["status"] = "confirmed"
        check["reviewed_value"] = f"사람이 확인한 {check['check_id']} 결과"
        check["note"] = ""
    candidate = review["source_reference"]["figure_candidates"][0]["figure_id"]
    review["structured_result"] = {
        "selected_figure_ids": [candidate],
        "nodes": ["사람이 확인한 시작 node", "사람이 확인한 종료 node"],
        "edges": ["시작 node -> 종료 node"],
        "branches": ["사람이 확인한 분기 조건"],
        "sequence": ["1. 시작 node", "2. 종료 node"],
        "numbers": ["사람이 확인한 숫자"],
        "units": ["사람이 확인한 단위"],
        "times": ["사람이 확인한 시간"],
        "caption_relation": "사람이 확인한 caption/주변 본문 관계",
        "uncertainties": [],
    }
    review["reviewers"] = {
        "reviewer_1": "reviewer_1",
        "reviewer_1_reviewed_at": "2026-09-18T10:00:00+09:00",
        "reviewer_1_approved": True,
        "second_review_required": True,
        "reviewer_2": "reviewer_2",
        "reviewer_2_reviewed_at": "2026-09-18T11:00:00+09:00",
        "reviewer_2_approved": True,
    }
    review["approval_decision"] = "approve"
    return review


def test_blank_review_preserves_original_pending_contract_without_auto_values():
    source_before = SOURCE.read_bytes()

    review = _blank_review()

    assert SOURCE.read_bytes() == source_before
    assert review["review_status"] == "unreviewed"
    assert review["needs_human_review"] is True
    assert review["production_gold_approved"] is False
    assert review["included_in_aggregate"] is False
    assert review["vision_api_calls"] == 0
    assert all(check["status"] == "unreviewed" for check in review["checks"])
    assert review["structured_result"]["nodes"] == []
    encoded = json.dumps(review, ensure_ascii=False)
    assert '"question"' not in encoded
    assert '"raw_image"' not in encoded


def test_incomplete_review_cannot_become_production_gold():
    review = _blank_review()
    review["approval_decision"] = "approve"
    review["reviewers"]["reviewer_1"] = "reviewer_1"
    review["reviewers"]["reviewer_1_reviewed_at"] = "2026-09-18T10:00:00+09:00"
    review["reviewers"]["reviewer_1_approved"] = True

    result = normalize_review_state(review)

    assert result["production_gold_approved"] is False
    assert result["needs_human_review"] is True
    assert result["included_in_aggregate"] is False
    assert result["approval_blockers"]


def test_uncertainty_keeps_gold_false_even_when_all_other_fields_are_complete():
    review = _completed_review()
    review["checks"][3]["status"] = "uncertain"
    review["checks"][3]["note"] = "화살표 한 개가 흐려서 방향을 확정할 수 없음"
    review["structured_result"]["uncertainties"] = ["화살표 방향 판독 불가"]

    result = normalize_review_state(review)

    assert result["production_gold_approved"] is False
    assert result["needs_human_review"] is True
    assert "unresolved_uncertainty" in result["approval_blockers"]


def test_second_review_is_required_before_production_gold():
    review = _completed_review()
    review["reviewers"]["reviewer_2"] = ""
    review["reviewers"]["reviewer_2_reviewed_at"] = ""
    review["reviewers"]["reviewer_2_approved"] = False

    result = normalize_review_state(review)

    assert result["review_status"] == "needs_second_review"
    assert result["production_gold_approved"] is False
    assert result["needs_human_review"] is True


def test_clicking_statuses_without_human_values_or_independent_reviewer_stays_pending():
    review = _completed_review()
    review["checks"][2]["reviewed_value"] = None
    review["reviewers"]["reviewer_2"] = "reviewer_1"

    result = normalize_review_state(review)

    assert result["production_gold_approved"] is False
    assert "missing_reviewed_value:node_labels" in result["approval_blockers"]
    assert "reviewers_not_independent" in result["approval_blockers"]


def test_only_fully_human_approved_review_enters_image_gold_aggregate():
    pending = normalize_review_state(_blank_review())
    approved = normalize_review_state(_completed_review())

    assert approved["review_status"] == "approved"
    assert approved["production_gold_approved"] is True
    assert approved["needs_human_review"] is False
    assert approved["included_in_aggregate"] is True
    assert aggregate_approved_image_gold(pending) == []
    assert aggregate_approved_image_gold(approved) == [approved]
    assert image_answerability_enabled(pending) is False
    assert image_answerability_enabled(approved) is True


def test_review_saves_separately_and_never_overwrites_source(tmp_path):
    source_before = SOURCE.read_bytes()
    review = normalize_review_state(_blank_review())
    destination = tmp_path / REVIEWED.name

    saved = save_reviewed_fixture(
        destination,
        review,
        protected_paths=(SOURCE,),
    )

    assert destination.is_file()
    assert saved["production_gold_approved"] is False
    assert SOURCE.read_bytes() == source_before
    with pytest.raises(ValueError, match="protected source fixture"):
        save_reviewed_fixture(SOURCE, review, protected_paths=(SOURCE,))


def test_rendered_overlay_does_not_modify_original_page_image():
    review = _blank_review()

    original, overlay = render_review_images(
        PDF,
        page_number=review["source_reference"]["page"],
        figure_candidates=review["source_reference"]["figure_candidates"],
        scale=1.0,
    )
    original_bytes = original.tobytes()

    assert original.size == overlay.size
    assert original.tobytes() == original_bytes
    assert overlay.tobytes() != original_bytes


def test_streamlit_review_app_is_local_only_and_does_not_write_on_open(
    tmp_path, monkeypatch
):
    reviewed = tmp_path / REVIEWED.name
    monkeypatch.setenv("SCHAT_TF027_CHECKLIST", str(SOURCE))
    monkeypatch.setenv("SCHAT_TF027_REVIEWED", str(reviewed))
    monkeypatch.setenv("SCHAT_TF027_PDF", str(PDF))

    app = AppTest.from_file(str(APP), default_timeout=30).run()

    assert not app.exception
    assert app.title[0].value == "TF027 이미지/도식 사람 검수"
    assert len(app.selectbox) == 10
    assert all(selectbox.value == "unreviewed" for selectbox in app.selectbox)
    assert any(
        "사람이 입력하지 않은 값은 승인되지 않습니다" in item.value
        for item in app.warning
    )
    assert not reviewed.exists()


def test_tf027_app_imports_when_streamlit_runs_script_outside_package() -> None:
    probe = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            (
                "import runpy; "
                f"runpy.run_path({str(APP)!r}, run_name='streamlit_import_probe')"
            ),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert probe.returncode == 0, probe.stderr


def test_streamlit_review_app_shows_stop_flag_and_ten_review_concepts(
    tmp_path, monkeypatch
):
    reviewed = tmp_path / REVIEWED.name
    monkeypatch.setenv("SCHAT_TF027_CHECKLIST", str(SOURCE))
    monkeypatch.setenv("SCHAT_TF027_REVIEWED", str(reviewed))
    monkeypatch.setenv("SCHAT_TF027_PDF", str(PDF))

    app = AppTest.from_file(str(APP), default_timeout=30).run()

    rendered = "\n".join(
        str(item.value)
        for collection in (app.error, app.warning, app.markdown, app.caption, app.info)
        for item in collection
    )
    assert "STOP_FOR_TF027_HUMAN_REVIEW" in rendered
    for concept in (
        "Figure 경계",
        "시작 node",
        "종료 node",
        "Node label",
        "화살표 방향",
        "Node 연결 관계",
        "Decision branch",
        "Workflow 순서",
        "숫자·단위·시간",
        "Caption·nearby text·crop·blur·occlusion",
    ):
        assert concept in rendered
    assert not reviewed.exists()


def test_review_code_has_no_provider_or_network_transport():
    helper = (ROOT / "tools" / "tf027_human_review.py").read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")
    code = (helper + app).casefold()

    assert "httpx" not in code
    assert "requests" not in code
    assert "groq" not in code
    assert "gemini" not in code
    assert "urlopen" not in code
