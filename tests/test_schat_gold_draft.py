import importlib
import json
import socket
from pathlib import Path

import pytest

from tools.schat_gold_human_review import (
    aggregate_approved_cases,
    load_review_inputs,
)

ROOT = Path(__file__).resolve().parents[1]
UAT = ROOT / "tests" / "fixtures" / "schat_v1_operational_uat.json"
GOLD = ROOT / "tests" / "fixtures" / "schat_v1_operational_gold.json"
REVIEWED = ROOT / "tests" / "fixtures" / "schat_v1_operational_gold_reviewed.json"


def _module():
    return importlib.import_module("tools.schat_gold_draft")


def _case(**overrides):
    case = {
        "case_id": "UAT-TXX",
        "question": "수혈 전 확인 사항은?",
        "question_type": "preparation",
        "expected_intent": "preparation",
        "expected_document_scope": "transfusion",
        "expected_domain": "hospital",
        "expected_evidence_type": "text",
        "expected_abstain": False,
        "source_label_status": "provisional_needs_human_review",
        "source_gold_refs": [],
    }
    case.update(overrides)
    return case


def _candidate(
    evidence_id: str,
    text: str,
    *,
    rank: int,
    section: str = "수혈 전 확인",
    document_name: str = "실무지침서_수혈간호.pdf",
    identifier_type: str = "chunk",
):
    return {
        "evidence_id": evidence_id,
        "identifier_type": identifier_type,
        "document_name": document_name,
        "page": 3,
        "section": section,
        "parent_id": "parent-1",
        "evidence_text": text,
        "retrieval_rank": rank,
        "retrieval_method": "local_reference_only",
        "score": 10.0 - rank,
        "gold_status": "not_assigned",
    }


def test_draft_is_never_approved_or_included_in_gold_aggregate():
    draft_module = _module()
    draft = draft_module.build_case_draft(
        _case(),
        [_candidate("direct", "수혈 전 환자와 혈액제제를 확인한다.", rank=1)],
    )

    assert draft["draft_only"] is True
    assert draft["final_gold_approved"] is False
    assert draft["review_status"] == "unreviewed"
    assert draft["primary_gold_ids"] == ["direct"]


def test_rank_one_is_not_primary_when_lower_rank_is_the_direct_evidence():
    draft_module = _module()
    candidates = [
        _candidate(
            "rank-one-unrelated",
            "수혈 후 이상반응 발생 내용을 간호기록에 남긴다.",
            rank=1,
            section="수혈 후 기록",
        ),
        _candidate(
            "rank-two-direct",
            "수혈 전 환자 이름과 혈액제제를 확인한다.",
            rank=2,
        ),
    ]

    draft = draft_module.build_case_draft(_case(), candidates)

    assert draft["primary_gold_ids"] == ["rank-two-direct"]
    assert "rank-one-unrelated" not in draft["primary_gold_ids"]


def test_numeric_monitoring_paraphrase_requires_matching_number_and_time_evidence():
    draft_module = _module()
    case = _case(
        question="성인은 몇 분마다 모니터링해?",
        question_type="fact_specific",
        expected_intent="monitoring",
        expected_document_scope="sedation",
    )
    candidates = [
        _candidate(
            "rank-one-unrelated",
            "진정 후 간호기록을 작성한다.",
            rank=1,
            section="진정 후 기록",
            document_name="진정간호.pdf",
        ),
        _candidate(
            "numeric-direct",
            "15분마다 환자 상태를 확인한다.",
            rank=3,
            section="성인 진정 중 모니터링",
            document_name="진정간호.pdf",
        ),
    ]

    draft = draft_module.build_case_draft(case, candidates)

    assert draft["primary_gold_ids"] == ["numeric-direct"]
    assert draft["critical_numbers"] == ["15"]
    assert draft["critical_units"] == ["분"]
    assert draft["critical_times"] == ["15분마다"]


def test_broad_summary_uses_clinical_action_content_not_retrieval_rank_alone():
    draft_module = _module()
    case = _case(
        question="수혈간호 전체적으로 핵심 요약해줘",
        question_type="summary",
        expected_intent="summary",
    )
    candidates = [
        _candidate(
            "rank-one-unrelated",
            "병원 장비의 정기 점검 결과이다.",
            rank=1,
            section="장비 관리",
        ),
        _candidate(
            "broad-direct",
            "수혈 전 환자를 확인하고 시행 중 활력징후를 관찰한다.",
            rank=4,
            section="수혈 간호 절차",
        ),
    ]

    draft = draft_module.build_case_draft(case, candidates)

    assert "broad-direct" in draft["primary_gold_ids"]
    assert "rank-one-unrelated" not in draft["primary_gold_ids"]


def test_clinical_invariants_are_extracted_only_when_exact_evidence_contains_them():
    draft_module = _module()
    no_value = draft_module.build_case_draft(
        _case(),
        [_candidate("plain", "수혈 전 환자와 혈액제제를 확인한다.", rank=1)],
    )
    exact = draft_module.build_case_draft(
        _case(
            question="수혈 금기 기준은?",
            question_type="cautions",
            expected_intent="cautions",
        ),
        [
            _candidate(
                "exact",
                "체온이 38℃ 이상인 경우 수혈하지 않는다.",
                rank=3,
                section="수혈 금기",
            )
        ],
    )

    assert no_value["critical_numbers"] == []
    assert no_value["critical_units"] == []
    assert no_value["critical_times"] == []
    assert exact["critical_numbers"] == ["38"]
    assert exact["critical_units"] == ["℃"]
    assert exact["critical_conditions"] == ["체온이 38℃ 이상인 경우 수혈하지 않는다."]
    assert exact["critical_contraindications"] == [
        "체온이 38℃ 이상인 경우 수혈하지 않는다."
    ]
    assert exact["critical_negations"] == ["체온이 38℃ 이상인 경우 수혈하지 않는다."]


def test_leading_list_marker_is_a_step_but_not_a_clinical_number():
    draft_module = _module()
    draft = draft_module.build_case_draft(
        _case(question="수혈 절차는?", question_type="procedure", expected_intent="procedure"),
        [_candidate("step", "1) 수혈 전 환자와 혈액제제를 확인한다.", rank=2)],
    )

    assert draft["critical_numbers"] == []
    assert draft["critical_steps"] == ["1) 수혈 전 환자와 혈액제제를 확인한다."]


def test_out_of_scope_case_never_gets_clinical_gold_draft():
    draft_module = _module()
    draft = draft_module.build_case_draft(
        _case(
            case_id="UAT-NXX",
            question="오늘 날씨 알려줘",
            expected_document_scope="out_of_scope",
            expected_domain="out_of_scope",
            expected_evidence_type="pre_llm_block",
            expected_abstain=True,
        ),
        [_candidate("clinical", "수혈 전 환자를 확인한다.", rank=1)],
    )

    assert draft["expected_abstain"] is True
    assert draft["primary_gold_ids"] == []
    assert draft["acceptable_gold_ids"] == []
    assert draft["critical_facts"] == []
    assert draft["draft_status"] == "manual_review_required"


def test_batch_excludes_two_existing_approved_cases_and_keeps_aggregate_unchanged():
    draft_module = _module()
    inputs = load_review_inputs(UAT, GOLD)
    reviewed = json.loads(REVIEWED.read_text(encoding="utf-8"))
    # Keep this unit test independent from the mutable human-review progress file.
    # Its contract is specifically the original two-approved-cases scenario.
    for case in reviewed["cases"]:
        if case["case_id"] not in {"UAT-S01", "UAT-S02"}:
            case["final_gold_approved"] = False
            case["review_status"] = "reviewing"
    approved_before = aggregate_approved_cases(reviewed)

    def candidates(case):
        return [
            _candidate(
                f"evidence-{case['case_id']}",
                f"{case['question']} 관련 지침 근거이다.",
                rank=2,
                document_name=(
                    "진정간호.pdf"
                    if case["expected_document_scope"] == "sedation"
                    else "실무지침서_수혈간호.pdf"
                ),
            )
        ]

    drafts = draft_module.build_draft_fixture(inputs, reviewed, candidates)

    assert len(drafts["cases"]) == 30
    assert {case["case_id"] for case in drafts["cases"]}.isdisjoint(
        {"UAT-S01", "UAT-S02"}
    )
    assert aggregate_approved_cases(reviewed) == approved_before
    assert all(case["draft_only"] is True for case in drafts["cases"])
    assert all(case["final_gold_approved"] is False for case in drafts["cases"])


def test_local_draft_save_never_overwrites_reviewed_fixture(tmp_path):
    draft_module = _module()
    inputs = load_review_inputs(UAT, GOLD)
    reviewed_before = REVIEWED.read_bytes()
    reviewed = json.loads(reviewed_before.decode("utf-8"))

    drafts = draft_module.build_draft_fixture(
        inputs,
        reviewed,
        lambda case: [
            _candidate(
                f"evidence-{case['case_id']}",
                f"{case['question']} 관련 지침 근거이다.",
                rank=1,
            )
        ],
    )
    draft_path = tmp_path / "drafts.json"
    draft_module.save_draft_fixture(
        draft_path,
        drafts,
        protected_paths=(REVIEWED, UAT, GOLD),
    )

    assert draft_path.is_file()
    assert REVIEWED.read_bytes() == reviewed_before
    with pytest.raises(ValueError, match="protected"):
        draft_module.save_draft_fixture(REVIEWED, drafts, protected_paths=(REVIEWED,))


def test_draft_generation_does_not_open_network_connections(monkeypatch):
    draft_module = _module()

    def fail_connect(*_args, **_kwargs):
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket.socket, "connect", fail_connect)
    draft = draft_module.build_case_draft(
        _case(),
        [_candidate("direct", "수혈 전 환자와 혈액제제를 확인한다.", rank=1)],
    )

    assert draft["primary_gold_ids"] == ["direct"]
