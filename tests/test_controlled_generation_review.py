from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.controlled_generation_coverage_review import project_failed_coverage_slot
from tools.controlled_generation_review import (
    build_coverage_review_slot,
    build_review_session,
    build_review_source_case,
    candidate_status_notice,
    load_coverage_review_source,
    load_review_source,
    ordered_review_case_ids,
    review_case_label,
    save_coverage_review_record,
    save_review_record,
    validate_review_record,
    write_coverage_review_source,
    write_review_source,
)


def review_payload() -> dict[str, object]:
    return {
        "case_id": "CASE-001",
        "preferred_answer": "controlled",
        "naturalness": "좋음",
        "accuracy": "적절",
        "completeness": "적절",
        "grounding": "적절",
        "semantic_equivalence": "동일",
        "note": "",
        "prompt_version": "v1.0-natural-grounded",
        "model": "gemini-3.8-flash",
        "timestamp": "2026-09-20T12:00:00+09:00",
        "config_sha256": "a" * 64,
        "extractive_sha256": "b" * 64,
        "candidate_sha256": "c" * 64,
    }


def test_review_schema_requires_requested_fields_and_semantic_equivalence() -> None:
    record = validate_review_record(review_payload())

    assert record.case_id == "CASE-001"
    assert record.semantic_equivalence == "동일"
    assert set(record.model_dump()) >= {
        "case_id",
        "preferred_answer",
        "naturalness",
        "accuracy",
        "completeness",
        "grounding",
        "semantic_equivalence",
        "note",
        "prompt_version",
        "model",
        "timestamp",
    }


@pytest.mark.parametrize(
    "value",
    ["동일", "경미한 변화", "의미 변경", "판단 어려움"],
)
def test_semantic_equivalence_accepts_only_approved_values(value: str) -> None:
    payload = review_payload()
    payload["semantic_equivalence"] = value

    assert validate_review_record(payload).semantic_equivalence == value


def test_semantic_equivalence_rejects_unknown_value() -> None:
    payload = review_payload()
    payload["semantic_equivalence"] = "대체로 동일"

    with pytest.raises(ValueError, match="semantic_equivalence"):
        validate_review_record(payload)


@pytest.mark.parametrize(
    "forbidden_field",
    ["question", "evidence", "source_text", "prompt", "provider_response"],
)
def test_review_schema_rejects_raw_content_fields(forbidden_field: str) -> None:
    payload = review_payload()
    payload[forbidden_field] = "raw content"

    with pytest.raises(ValueError):
        validate_review_record(payload)


def test_review_file_is_created_only_on_first_valid_save(tmp_path: Path) -> None:
    output = tmp_path / "reviews" / "human_review.jsonl"
    assert not output.exists()

    record = save_review_record(output, review_payload())

    assert output.exists()
    saved = json.loads(output.read_text(encoding="utf-8").strip())
    assert saved == record.model_dump(mode="json")
    encoded = json.dumps(saved, ensure_ascii=False).casefold()
    assert all(
        forbidden not in encoded
        for forbidden in (
            '"question"',
            '"evidence"',
            '"source_text"',
            '"prompt"',
            '"provider_response"',
            '"extractive_answer"',
            '"controlled_answer"',
        )
    )


def test_review_note_rejects_source_text_and_secret_markers(tmp_path: Path) -> None:
    payload = review_payload()
    payload["note"] = "민감한 근거 문장이 그대로 포함됨"
    with pytest.raises(ValueError, match="review_note_raw_text"):
        save_review_record(
            tmp_path / "review.jsonl",
            payload,
            forbidden_exact_texts=("민감한 근거 문장이 그대로 포함됨",),
        )

    payload["note"] = "Authorization: Bearer secret-value-123456"
    with pytest.raises(ValueError, match="review_note_secret"):
        save_review_record(tmp_path / "review.jsonl", payload)


def test_review_note_rejects_one_copied_paragraph_from_a_long_answer(
    tmp_path: Path,
) -> None:
    payload = review_payload()
    payload["note"] = "첫 번째 민감한 근거 문장입니다."

    with pytest.raises(ValueError, match="review_note_raw_text"):
        save_review_record(
            tmp_path / "review.jsonl",
            payload,
            forbidden_exact_texts=(
                "첫 번째 민감한 근거 문장입니다.\n\n두 번째 근거 문장입니다.",
            ),
        )


def test_review_session_keeps_text_in_memory_and_follow_up_disabled() -> None:
    session = build_review_session(
        case_id="CASE-001",
        extractive_text="기존 답변",
        controlled_text="자연스럽게 재구성한 답변",
        controlled_citation_ids=(("su001",),),
        prompt_version="v1.0-natural-grounded",
        config_sha256="a" * 64,
        follow_up_enabled=False,
    )

    assert session.extractive_text == "기존 답변"
    assert session.controlled_text == "자연스럽게 재구성한 답변"
    assert len(session.extractive_sha256) == 64
    assert len(session.candidate_sha256) == 64
    assert session.follow_up_enabled is False


def test_review_session_rejects_follow_up_activation() -> None:
    with pytest.raises(ValueError, match="follow_up_disabled"):
        build_review_session(
            case_id="CASE-001",
            extractive_text="기존 답변",
            controlled_text="후보 답변",
            controlled_citation_ids=(("su001",),),
            prompt_version="v1.0-natural-grounded",
            config_sha256="a" * 64,
            follow_up_enabled=True,
        )


def test_review_session_requires_controlled_citation_ids() -> None:
    with pytest.raises(ValueError, match="controlled_citation_ids"):
        build_review_session(
            case_id="CASE-001",
            extractive_text="기존 답변",
            controlled_text="후보 답변",
            controlled_citation_ids=(),
            prompt_version="v1.0-natural-grounded",
            config_sha256="a" * 64,
            follow_up_enabled=False,
        )


def test_candidate_status_notice_distinguishes_live_and_fallback() -> None:
    assert "Gemini" in candidate_status_notice("validated_live")
    assert "fallback" in candidate_status_notice("extractive_fallback").casefold()
    assert "deterministic" in candidate_status_notice(
        "deterministic_projection"
    ).casefold()


def test_review_case_labels_and_default_order_make_live_results_explicit() -> None:
    live = type(
        "Case",
        (),
        {
            "case_id": "UAT-T01",
            "model": "gemini-3.8-flash",
            "candidate_status": "validated_live",
        },
    )()
    fallback = type(
        "Case",
        (),
        {
            "case_id": "UAT-T02",
            "model": "gemini-3.8-flash",
            "candidate_status": "extractive_fallback",
        },
    )()
    deterministic = type(
        "Case",
        (),
        {
            "case_id": "UAT-S03",
            "model": "deterministic-source-unit-projection",
            "candidate_status": "deterministic_projection",
        },
    )()
    catalog = {
        deterministic.case_id: deterministic,
        fallback.case_id: fallback,
        live.case_id: live,
    }

    assert ordered_review_case_ids(catalog, live_only=True) == (
        "UAT-T01",
        "UAT-T02",
    )
    assert ordered_review_case_ids(catalog, live_only=False) == (
        "UAT-T01",
        "UAT-T02",
        "UAT-S03",
    )
    assert review_case_label(live) == "UAT-T01 · Gemini live · validated"
    assert review_case_label(fallback) == "UAT-T02 · Gemini live · fallback"
    assert review_case_label(deterministic) == "UAT-S03 · deterministic"


def test_review_app_import_has_no_storage_side_effect(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    module = importlib.import_module("tools.controlled_generation_review_app")

    assert module.FOLLOW_UP_ENABLED is False
    assert list(tmp_path.rglob("human_review*")) == []


def source_case() -> dict[str, object]:
    extractive = "기존 첫 문장\n\n기존 둘째 문장"
    controlled = "재구성 첫 문장\n\n재구성 둘째 문장"
    mapping = [["su001"], ["su002", "su003"]]
    return {
        "case_id": "CASE-001",
        "prompt_version": "v1.0-natural-grounded",
        "model": "gemini-3.8-flash",
        "candidate_status": "validated_live",
        "extractive_answer": extractive,
        "controlled_candidate": controlled,
        "supporting_source_unit_ids": mapping,
        "source_unit_mapping_sha256": hashlib.sha256(
            json.dumps(
                mapping,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
        "extractive_answer_sha256": hashlib.sha256(extractive.encode()).hexdigest(),
        "controlled_candidate_sha256": hashlib.sha256(controlled.encode()).hexdigest(),
    }


def _write_question_fixture(path: Path) -> None:
    path.write_text(
        json.dumps(
            {"cases": [{"case_id": "CASE-001", "question": "로컬 질문"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_review_source_round_trip_loads_dropdown_and_display_values(tmp_path: Path) -> None:
    source_path = tmp_path / "private" / "review_source.json"
    fixture_path = tmp_path / "questions.json"
    _write_question_fixture(fixture_path)

    write_review_source(
        source_path,
        [source_case()],
        allowed_root=tmp_path,
    )
    catalog = load_review_source(
        source_path,
        question_fixture_path=fixture_path,
        expected_prompt_version="v1.0-natural-grounded",
    )

    assert tuple(catalog) == ("CASE-001",)
    case = catalog["CASE-001"]
    assert case.question == "로컬 질문"
    assert case.extractive_answer == "기존 첫 문장\n\n기존 둘째 문장"
    assert case.controlled_candidate == "재구성 첫 문장\n\n재구성 둘째 문장"
    assert case.supporting_source_unit_ids == (("su001",), ("su002", "su003"))
    assert case.model == "gemini-3.8-flash"
    assert case.candidate_status == "validated_live"


def test_review_source_builder_computes_hashes_without_provider_data() -> None:
    case = build_review_source_case(
        case_id="CASE-001",
        prompt_version="v1.0-natural-grounded",
        model="gemini-3.8-flash",
        candidate_status="validated_live",
        extractive_answer="기존 답변",
        controlled_candidate="재구성 답변",
        supporting_source_unit_ids=(("su001",),),
    )

    assert case.extractive_answer_sha256 == hashlib.sha256(
        "기존 답변".encode()
    ).hexdigest()
    assert case.controlled_candidate_sha256 == hashlib.sha256(
        "재구성 답변".encode()
    ).hexdigest()
    assert not hasattr(case, "provider_payload")
    assert not hasattr(case, "provider_response")


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("prompt_version", "v0-legacy", "prompt_version"),
        ("extractive_answer_sha256", "0" * 64, "extractive_answer_sha256"),
        ("controlled_candidate_sha256", "0" * 64, "controlled_candidate_sha256"),
        ("supporting_source_unit_ids", [["su001"]], "source_unit_mapping"),
        (
            "supporting_source_unit_ids",
            [["su999"], ["su002", "su003"]],
            "source_unit_mapping",
        ),
    ],
)
def test_review_source_fails_closed_on_contract_drift(
    tmp_path: Path,
    field: str,
    value: object,
    reason: str,
) -> None:
    source_path = tmp_path / "review_source.json"
    fixture_path = tmp_path / "questions.json"
    _write_question_fixture(fixture_path)
    case = source_case()
    case[field] = value
    source_path.write_text(
        json.dumps({"schema_version": 1, "cases": [case]}, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=reason):
        load_review_source(
            source_path,
            question_fixture_path=fixture_path,
            expected_prompt_version="v1.0-natural-grounded",
        )


def test_review_source_fails_closed_when_case_is_not_in_question_fixture(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "review_source.json"
    source_path.write_text(
        json.dumps({"schema_version": 1, "cases": [source_case()]}, ensure_ascii=False),
        encoding="utf-8",
    )
    fixture_path = tmp_path / "questions.json"
    fixture_path.write_text(json.dumps({"cases": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="case_id"):
        load_review_source(
            source_path,
            question_fixture_path=fixture_path,
            expected_prompt_version="v1.0-natural-grounded",
        )


def test_review_source_writer_refuses_paths_outside_local_only_root(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="review_source_path"):
        write_review_source(
            tmp_path.parent / "outside" / "review_source.json",
            [source_case()],
            allowed_root=tmp_path,
        )


def test_local_review_source_path_is_explicitly_gitignored() -> None:
    patterns = Path(".gitignore").read_text(encoding="utf-8").splitlines()

    assert "/workspace/임시작업/controlled_generation_review_source/" in patterns


def test_review_app_has_no_production_or_network_connection() -> None:
    app_source = Path("tools/controlled_generation_review_app.py").read_text(
        encoding="utf-8"
    )
    production_source = Path("src/ai.py").read_text(encoding="utf-8")

    assert "src.ai" not in app_source
    assert "generate(" not in app_source
    assert "requests" not in app_source
    assert "httpx" not in app_source
    assert "controlled_generation_review" not in production_source


def test_review_app_uses_dropdown_and_read_only_auto_loaded_text() -> None:
    app_source = Path("tools/controlled_generation_review_app.py").read_text(
        encoding="utf-8"
    )

    assert "st.selectbox(" in app_source
    assert '"Case ID"' in app_source
    assert "format_func=" in app_source
    assert 'st.text_input("Case ID"' not in app_source
    assert 'st.text_area("질문"' in app_source
    assert "disabled=True" in app_source
    assert "load_review_source(" in app_source


def test_coverage_review_source_round_trip_keeps_display_text_local(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "local-only"
    source_path = source_root / "coverage_review_source.json"
    slot = build_coverage_review_slot(
        case_id="UAT-T01",
        slot_id="required_fact_001",
        supporting_source_unit_ids=("su001",),
        source_text="수혈 처방이 나면 동의서를 확인한다.",
        candidate_statement="수혈 처방이 확인되면 동의서를 확인한다.",
        token_coverage=5 / 7,
    )

    write_coverage_review_source(source_path, [slot], allowed_root=source_root)
    catalog = load_coverage_review_source(source_path)

    loaded = catalog[("UAT-T01", "required_fact_001")]
    assert loaded.source_text.startswith("수혈 처방")
    assert loaded.candidate_statement.startswith("수혈 처방")
    assert loaded.token_coverage == pytest.approx(5 / 7)
    assert loaded.source_sha256 == hashlib.sha256(
        loaded.source_text.encode("utf-8")
    ).hexdigest()
    assert loaded.candidate_sha256 == hashlib.sha256(
        loaded.candidate_statement.encode("utf-8")
    ).hexdigest()


def test_coverage_review_record_is_raw_free_and_uses_closed_judgments(
    tmp_path: Path,
) -> None:
    output = tmp_path / "coverage_human_review.jsonl"
    saved = save_coverage_review_record(
        output,
        {
            "case_id": "UAT-T01",
            "slot_id": "required_fact_001",
            "source_sha256": "a" * 64,
            "candidate_sha256": "b" * 64,
            "judgment": "의미 동일·표현 차이",
            "timestamp": "2026-09-20T12:00:00+09:00",
        },
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload == saved.model_dump(mode="json")
    assert set(payload) == {
        "case_id",
        "slot_id",
        "source_sha256",
        "candidate_sha256",
        "judgment",
        "timestamp",
    }
    assert "수혈" not in output.read_text(encoding="utf-8")

    invalid = dict(payload)
    invalid["judgment"] = "validator 오류"
    with pytest.raises(ValueError, match="judgment"):
        save_coverage_review_record(output, invalid)


def test_coverage_review_source_and_record_fail_closed_on_hash_or_raw_field(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "coverage_review_source.json"
    slot = build_coverage_review_slot(
        case_id="UAT-T01",
        slot_id="required_fact_001",
        supporting_source_unit_ids=("su001",),
        source_text="근거 원문",
        candidate_statement="후보 문장",
        token_coverage=0.5,
    ).model_dump(mode="json")
    slot["source_sha256"] = "0" * 64
    source_path.write_text(
        json.dumps({"schema_version": 1, "slots": [slot]}, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="source_sha256"):
        load_coverage_review_source(source_path)

    raw_record = {
        "case_id": "UAT-T01",
        "slot_id": "required_fact_001",
        "source_sha256": "a" * 64,
        "candidate_sha256": "b" * 64,
        "judgment": "부분 포함",
        "timestamp": "2026-09-20T12:00:00+09:00",
        "source_text": "저장 금지",
    }
    with pytest.raises(ValueError):
        save_coverage_review_record(tmp_path / "review.jsonl", raw_record)


def test_review_app_exposes_coverage_comparison_without_network_or_production() -> None:
    app_source = Path("tools/controlled_generation_review_app.py").read_text(
        encoding="utf-8"
    )

    for label in (
        "required slot",
        "연결 SourceUnit ID",
        "SourceUnit 원문",
        "관련 Gemini candidate statement",
        "기존 token coverage",
        "의미 동일·표현 차이",
        "실제 의미 누락",
        "부분 포함",
        "판단 어려움",
    ):
        assert label in app_source
    assert "save_coverage_review_record(" in app_source
    assert "httpx" not in app_source
    assert "src.ai" not in app_source


def test_failed_coverage_projection_uses_linked_source_and_related_statements() -> None:
    case = SimpleNamespace(
        case_id="UAT-T01",
        units=(
            SimpleNamespace(source_unit_id="su001", exact_text="필수 근거"),
            SimpleNamespace(source_unit_id="su002", exact_text="다른 근거"),
        ),
        required_coverage=(
            SimpleNamespace(
                slot_id="required_fact_001",
                requirement="필수 근거를 확인한다",
                supporting_source_unit_ids=("su001",),
            ),
        ),
    )
    diagnostic = {
        "error_code": "required_coverage:required_fact_001",
        "statements": [
            {
                "index": 0,
                "text": "필수 근거는 확인됩니다.",
                "supporting_source_unit_ids": ["su001"],
            },
            {
                "index": 1,
                "text": "관련 없는 문장",
                "supporting_source_unit_ids": ["su002"],
            },
        ],
    }

    slot = project_failed_coverage_slot(case=case, diagnostic=diagnostic)

    assert slot is not None
    assert slot.case_id == "UAT-T01"
    assert slot.slot_id == "required_fact_001"
    assert slot.source_text == "필수 근거"
    assert slot.candidate_statement == "필수 근거는 확인됩니다."
    assert 0 < slot.token_coverage <= 1
