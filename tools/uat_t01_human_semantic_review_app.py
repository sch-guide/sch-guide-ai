"""Local-only Streamlit review for one in-memory UAT-T01 Groq answer."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.groq_live_ragas_evaluate import (  # noqa: E402
    DEFAULT_REVIEWED,
    DEFAULT_UAT,
    GROQ_MODEL,
    GroqLiveError,
    call_groq_live,
    evaluate_live_safety,
    prepare_approved_live_cases,
    safe_groq_error_summary,
    safe_live_call_record,
    scan_source_units_for_identifiers,
)
from tools.provider_controlled_generation_evaluate import (  # noqa: E402
    build_provider_blueprints,
)
from tools.uat_s01_human_semantic_review import (  # noqa: E402
    parse_generated_statements,
)
from tools.uat_t01_human_semantic_review import (  # noqa: E402
    CASE_ID,
    build_human_review_record,
    load_human_review_record,
    save_human_review_record,
)

REVIEW_RESULT_PATH = ROOT / ".tmp" / "uat_t01_human_semantic_review.json"
SESSION_KEY = "uat-t01-human-semantic-review-session"
ATTEMPT_KEY = "uat-t01-human-semantic-review-call-attempted"


def generation_allowed(session_state: Mapping[str, Any]) -> bool:
    """Permit at most one provider generation in the current Streamlit session."""
    return SESSION_KEY not in session_state and ATTEMPT_KEY not in session_state


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("fixture_shape")
    return payload


def _fixture_case(path: Path) -> dict[str, Any]:
    payload = _read_json(path)
    for case in payload.get("cases", ()):
        if isinstance(case, dict) and case.get("case_id") == CASE_ID:
            return dict(case)
    raise ValueError(f"case_not_found:{CASE_ID}")


def format_review_error(error: BaseException) -> str:
    """Return a payload-safe error category for local display."""
    if isinstance(error, GroqLiveError):
        return safe_groq_error_summary(error)
    if isinstance(error, ValueError) and str(error) == "MISSING_GROQ_API_KEY":
        return "GROQ_API_KEY가 현재 Streamlit 프로세스에 없습니다."
    return f"Local preparation failed: {type(error).__name__}"


def _execute_review_call() -> dict[str, Any]:
    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        raise ValueError("MISSING_GROQ_API_KEY")
    case = next(
        (
            item
            for item in prepare_approved_live_cases()
            if item.case_id == CASE_ID
        ),
        None,
    )
    if case is None:
        raise ValueError("approved_case_not_found")
    identifier_codes = scan_source_units_for_identifiers(case.units)
    if identifier_codes:
        raise ValueError("identifier_detected:" + ",".join(identifier_codes))
    blueprint, _ = build_provider_blueprints(
        case_id=case.case_id,
        intent=case.intent,
        units=case.units,
        groq_model=GROQ_MODEL,
        gemini_model="unused",
        requested_phase=case.requested_phase,
        preserve_source_order=bool(case.requested_phase),
        required_coverage=tuple(
            slot.provider_descriptor() for slot in case.required_coverage
        ),
    )
    live = call_groq_live(blueprint, api_key=api_key)
    safety = evaluate_live_safety(
        blueprint,
        live,
        units=case.units,
        intent=case.intent,
        critical=case.critical,
        required_coverage=case.required_coverage,
        requested_phase=case.requested_phase,
    )
    return {
        "case": case,
        "blueprint": blueprint,
        "live": live,
        "safe_call": safe_live_call_record(blueprint, live),
        "safety": safety,
        "statements": parse_generated_statements(live.normalized.content),
    }


def _render_gold(reviewed: Mapping[str, Any]) -> None:
    groups = (
        ("승인 Gold critical facts", "critical_facts"),
        ("승인 Gold condition", "critical_conditions"),
        ("승인 Gold action/steps", "critical_steps"),
    )
    for label, field in groups:
        st.markdown(f"**{label}**")
        values = reviewed.get(field, ())
        if values:
            for value in values:
                st.markdown(f"- {value}")
        else:
            st.caption("등록된 항목 없음")


def _render_validator_results(safety: Mapping[str, Any]) -> None:
    labels = (
        ("schema_pass", "Schema"),
        ("citation_pass", "Citation"),
        ("critical_fact_preservation", "Critical fact"),
        ("number_pass", "Number"),
        ("unit_pass", "Unit"),
        ("time_pass", "Time"),
        ("condition_pass", "Condition"),
        ("negation_pass", "Negation"),
        ("action_pass", "Action/steps"),
    )
    columns = st.columns(3)
    for index, (field, label) in enumerate(labels):
        columns[index % 3].metric(
            label, "PASS" if safety.get(field) is True else "FAIL"
        )
    reason = str(safety.get("validation_reason", ""))
    branch_pass = bool(
        safety.get("schema_pass")
        and reason not in {"branch_mixing", "phase_mixing"}
    )
    st.metric("Branch/phase", "PASS" if branch_pass else "FAIL")
    st.metric("Unsupported clinical claim", "사람 판정 필요")
    st.caption(f"Validator reason: `{reason}`")


def _render_session(session: Mapping[str, Any]) -> None:
    case = session["case"]
    safety = session["safety"]
    safe_call = session["safe_call"]
    statements = session["statements"]
    uat = _fixture_case(DEFAULT_UAT)
    reviewed = _fixture_case(DEFAULT_REVIEWED)

    st.subheader("1. 질문과 승인 Gold")
    st.markdown(f"**Case ID:** `{CASE_ID}`")
    st.markdown(f"**질문:** {uat.get('question', '')}")
    _render_gold(reviewed)

    st.subheader("2. 현재 세션의 Groq generated answer")
    st.caption(
        "아래 원문은 이 Streamlit session 메모리에만 존재하며 저장되지 않습니다."
    )
    for index, statement in enumerate(statements, 1):
        st.markdown(f"**문장 {index}**")
        st.info(statement["text"])
        st.code(
            ", ".join(statement["supporting_source_unit_ids"]),
            language=None,
        )

    st.subheader("3. Request-local SourceUnit evidence와 citation mapping")
    cited_ids = {
        source_id
        for statement in statements
        for source_id in statement["supporting_source_unit_ids"]
    }
    for unit in case.units:
        citation_state = "인용됨" if unit.source_unit_id in cited_ids else "인용 안 됨"
        with st.expander(
            f"{unit.source_unit_id} · {citation_state}", expanded=True
        ):
            st.write(unit.exact_text)

    st.subheader("4. Hash와 deterministic validator")
    st.code(f"Answer hash: {safe_call['generated_answer_sha256']}")
    st.code(f"Evidence hash: {safe_call['evidence_sha256']}")
    _render_validator_results(safety)

    st.subheader("5. 사람 판정")
    reviewer = st.text_input("Reviewer", placeholder="reviewer_1")
    critical = st.selectbox(
        "Critical fact 보존", ("선택하세요", "PASS", "FAIL", "UNCERTAIN")
    )
    condition = st.selectbox(
        "Condition 보존", ("선택하세요", "PASS", "FAIL", "UNCERTAIN")
    )
    action = st.selectbox(
        "Action/steps 보존", ("선택하세요", "PASS", "FAIL", "UNCERTAIN")
    )
    unsupported = st.selectbox(
        "근거 외 임상 내용", ("선택하세요", "없음", "있음", "불확실")
    )
    st.text_area("핵심 누락 내용", help="세션 메모리에만 있으며 저장되지 않습니다.")
    st.text_area("잘못 변경된 조건", help="세션 메모리에만 있으며 저장되지 않습니다.")
    st.text_area("잘못 변경된 단계/행동", help="세션 메모리에만 있으며 저장되지 않습니다.")
    st.text_area("근거 외 임상 내용", help="세션 메모리에만 있으며 저장되지 않습니다.")
    st.text_area("사람 메모", help="세션 메모리에만 있으며 저장되지 않습니다.")

    if st.button("Hash-only 사람 판정 저장", type="primary"):
        if not reviewer.strip():
            st.error("Reviewer를 입력하세요.")
            return
        if "선택하세요" in {critical, condition, action, unsupported}:
            st.error("모든 사람 판정 항목을 선택하세요.")
            return
        unsupported_value = {
            "없음": "NONE",
            "있음": "PRESENT",
            "불확실": "UNCERTAIN",
        }[unsupported]
        record = build_human_review_record(
            case_id=CASE_ID,
            reviewer=reviewer,
            generated_answer_sha256=safe_call["generated_answer_sha256"],
            evidence_sha256=safe_call["evidence_sha256"],
            critical_fact_decision=critical,
            condition_decision=condition,
            action_steps_decision=action,
            unsupported_clinical_claim_decision=unsupported_value,
        )
        save_human_review_record(REVIEW_RESULT_PATH, record)
        if record["human_semantic_support_approved"]:
            st.success("네 항목이 모두 승인되어 hash-only PASS로 저장했습니다.")
        else:
            st.error(
                "FAIL 또는 UNCERTAIN 항목이 있어 subset 확대를 허용하지 않습니다: "
                + ", ".join(record["failure_categories"])
            )


def main() -> None:
    st.set_page_config(page_title="SCHAT UAT-T01 사람 검수", layout="wide")
    st.title("SCHAT UAT-T01 Live Answer 사람 검수")
    st.caption("Local-only · 세션당 Groq 최대 1회 · raw answer/evidence 비저장")

    try:
        existing = load_human_review_record(REVIEW_RESULT_PATH)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        st.error(f"기존 hash-only 결과를 읽을 수 없습니다: {type(exc).__name__}")
        existing = None
    if existing is not None:
        st.info(
            "저장된 판정: "
            f"reviewer `{existing['reviewer']}` · {existing['reviewed_at']} · "
            f"approved={existing['human_semantic_support_approved']}"
        )

    key_present = bool(os.getenv("GROQ_API_KEY", ""))
    st.metric("GROQ_API_KEY", "연결됨" if key_present else "없음")
    if not key_present:
        st.error(
            '같은 PowerShell에서 `$env:GROQ_API_KEY="사용자 API 키"`를 설정한 뒤 '
            "Streamlit을 다시 시작하세요."
        )
        return

    allowed = generation_allowed(st.session_state)
    if st.button(
        "UAT-T01 Groq 1회 생성",
        type="secondary",
        disabled=not allowed,
    ):
        st.session_state[ATTEMPT_KEY] = True
        try:
            with st.spinner("승인 evidence 준비 및 Groq 1회 호출 중..."):
                st.session_state[SESSION_KEY] = _execute_review_call()
        except Exception as exc:  # Keep credentials and provider bodies out of UI.
            st.error("검수용 호출 실패 · " + format_review_error(exc))

    session = st.session_state.get(SESSION_KEY)
    if session is None:
        st.warning("아직 generated answer가 없습니다. 위 버튼을 한 번만 누르세요.")
        return
    st.success("현재 세션의 1회 응답을 고정했습니다. 재생성은 비활성화됩니다.")
    _render_session(session)


if __name__ == "__main__":
    main()
