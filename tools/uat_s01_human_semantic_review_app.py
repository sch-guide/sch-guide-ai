"""Local Streamlit review for UAT-S01 Groq semantic support.

The generated answer and hospital evidence exist only in Streamlit session
memory.  Saving writes hashes and reviewer metadata only under ``.tmp``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

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
    numeric_unit_diagnostics,
    prepare_approved_live_cases,
    safe_groq_error_summary,
    safe_live_call_record,
    scan_source_units_for_identifiers,
)
from tools.provider_controlled_generation_evaluate import (  # noqa: E402
    build_provider_blueprints,
)
from tools.uat_s01_human_semantic_review import (  # noqa: E402
    CASE_ID,
    build_human_review_record,
    build_reviewed_case_snapshot,
    load_human_review_record,
    parse_generated_statements,
    save_human_review_record,
    save_reviewed_case_snapshot,
)

REVIEW_RESULT_PATH = ROOT / ".tmp" / "uat_s01_human_semantic_review.json"
REVIEW_SNAPSHOT_PATH = (
    ROOT / ".tmp" / "uat_s01_human_semantic_review_snapshot.json"
)
SESSION_KEY = "uat-s01-human-semantic-review-session"
NON_SEMANTIC_REVIEW_PREREQUISITES = (
    "schema_pass",
    "citation_pass",
    "number_pass",
    "unit_pass",
    "time_pass",
    "condition_pass",
    "negation_pass",
    "action_pass",
)


def semantic_review_ready(safety: dict[str, Any]) -> bool:
    """Allow human semantic review only after every structural safety check passes."""
    return all(safety.get(field) is True for field in NON_SEMANTIC_REVIEW_PREREQUISITES)


def persist_reviewed_session_snapshot(
    session: dict[str, Any],
    review: dict[str, Any],
    *,
    path: Path = REVIEW_SNAPSHOT_PATH,
) -> None:
    """Persist only safe validator metadata for the exact in-memory answer."""
    call_record = safe_live_call_record(session["blueprint"], session["live"])
    snapshot = build_reviewed_case_snapshot(
        review=review,
        call_record=call_record,
        safety=session["safety"],
    )
    save_reviewed_case_snapshot(path, snapshot)


def format_review_error(error: BaseException) -> str:
    """Return a display-safe category without payload, response, or credential data."""
    if isinstance(error, GroqLiveError):
        return safe_groq_error_summary(error)
    if isinstance(error, ValueError) and str(error) == "MISSING_GROQ_API_KEY":
        return "Authentication configuration failed: GROQ_API_KEY가 없습니다."
    return f"Local preparation failed: {type(error).__name__}"


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("fixture_shape")
    return payload


def _fixture_case(path: Path, case_id: str) -> dict[str, Any]:
    payload = _read_json(path)
    for case in payload.get("cases", ()):
        if isinstance(case, dict) and case.get("case_id") == case_id:
            return dict(case)
    raise ValueError(f"case_not_found:{case_id}")


def _execute_review_call() -> dict[str, Any]:
    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        raise ValueError("MISSING_GROQ_API_KEY")
    prepared = prepare_approved_live_cases()
    case = next((item for item in prepared if item.case_id == CASE_ID), None)
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
        "safety": safety,
        "numeric_unit_diagnostics": numeric_unit_diagnostics(
            live.normalized.content, case.units
        ),
        "statements": parse_generated_statements(live.normalized.content),
    }


def _render_validator_results(safety: dict[str, Any]) -> None:
    labels = (
        ("schema_pass", "JSON schema"),
        ("citation_pass", "Citation/source ID"),
        ("critical_fact_preservation", "Critical fact deterministic check"),
        ("number_pass", "Number"),
        ("unit_pass", "Unit"),
        ("time_pass", "Time"),
        ("condition_pass", "Condition"),
        ("negation_pass", "Negation"),
        ("action_pass", "Action"),
    )
    columns = st.columns(3)
    for index, (field, label) in enumerate(labels):
        passed = safety.get(field) is True
        columns[index % 3].metric(label, "PASS" if passed else "FAIL / 미검증")
    st.caption(
        "이 표는 기존 deterministic 검사 결과입니다. Critical fact의 의미 보존 여부는 "
        "아래에서 사람이 별도로 판정합니다."
    )


def _render_review_session(session: dict[str, Any]) -> None:
    case = session["case"]
    blueprint = session["blueprint"]
    live = session["live"]
    safety = session["safety"]
    statements = session["statements"]
    uat = _fixture_case(DEFAULT_UAT, CASE_ID)
    reviewed = _fixture_case(DEFAULT_REVIEWED, CASE_ID)

    st.subheader("1. 질문과 승인 Gold")
    st.markdown(f"**Case ID:** `{CASE_ID}`")
    st.markdown(f"**질문:** {uat.get('question', '')}")
    st.markdown("**승인 Gold critical fact**")
    for fact in reviewed.get("critical_facts", ()):
        st.markdown(f"- {fact}")

    st.subheader("2. 실제 Groq generated answer")
    st.caption(
        f"Model: {live.normalized.model or GROQ_MODEL} · latency: "
        f"{live.latency_ms:.3f} ms · 이 내용은 현재 Streamlit session 메모리에만 있습니다."
    )
    for index, statement in enumerate(statements, 1):
        st.markdown(f"**문장 {index}**")
        st.info(statement["text"])
        st.code(
            ", ".join(statement["supporting_source_unit_ids"]),
            language=None,
        )

    st.subheader("3. Request-local SourceUnit evidence와 citation mapping")
    unit_by_id = {unit.source_unit_id: unit for unit in case.units}
    cited_ids = {
        source_id
        for statement in statements
        for source_id in statement["supporting_source_unit_ids"]
    }
    for source_id, unit in unit_by_id.items():
        status = "인용됨" if source_id in cited_ids else "인용 안 됨"
        with st.expander(f"{source_id} · {status}", expanded=True):
            st.write(unit.exact_text)
    st.caption(
        "Production chunk ID, 문서명과 page는 provider payload에 포함되지 않습니다. "
        f"Evidence hash: `{blueprint.evidence_fingerprint}`"
    )

    st.subheader("4. 기존 deterministic validator")
    _render_validator_results(safety)

    st.markdown("**Number/unit 검사 입력 범위**")
    st.caption(
        "임상 generated statement text만 검사합니다. Source/citation ID, JSON field, "
        "array index, hash, latency와 schema metadata는 검사하지 않습니다."
    )
    for diagnostic in session["numeric_unit_diagnostics"]:
        st.code(
            json.dumps(diagnostic, ensure_ascii=False, indent=2),
            language="json",
        )

    if not semantic_review_ready(safety):
        st.error(
            "현재 답변은 semantic review 이전의 구조·임상 불변성 검사에 실패했습니다. "
            "이 답변에는 사람 PASS를 저장할 수 없습니다."
        )
        return

    st.subheader("5. 사람 판정")
    st.warning(
        "PASS는 위 generated answer가 승인 Gold critical fact의 의미를 빠짐없이 보존하고, "
        "근거에 없는 임상 내용을 추가하지 않았을 때만 선택하세요."
    )
    reviewer = st.text_input("Reviewer", placeholder="reviewer_1")
    decision = st.selectbox(
        "Critical fact 의미 보존",
        ("선택하세요", "PASS", "FAIL", "UNCERTAIN"),
    )
    st.text_area("핵심 누락 내용", help="화면 session 전용이며 저장되지 않습니다.")
    st.text_area("잘못 추가된 임상 내용", help="화면 session 전용이며 저장되지 않습니다.")
    st.text_area("사람 메모", help="화면 session 전용이며 저장되지 않습니다.")
    st.caption(
        "저장 파일에는 case ID, 판정, reviewer, 시각, answer/evidence hash만 기록됩니다. "
        "생성 답변·근거·메모는 저장되지 않습니다."
    )
    if st.button("사람 판정 저장", type="primary"):
        if not reviewer.strip():
            st.error("Reviewer를 입력하세요.")
            return
        if decision == "선택하세요":
            st.error("PASS, FAIL, UNCERTAIN 중 하나를 선택하세요.")
            return
        record = build_human_review_record(
            case_id=CASE_ID,
            decision=decision,
            reviewer=reviewer,
            generated_content=live.normalized.content,
            evidence_sha256=blueprint.evidence_fingerprint,
        )
        save_human_review_record(REVIEW_RESULT_PATH, record)
        if decision == "PASS":
            persist_reviewed_session_snapshot(session, record)
            st.success(
                "PASS 판정을 hash-only 로컬 파일에 저장했습니다. Live subset은 자동으로 "
                "재개하지 않습니다. 결과를 보고한 뒤 별도 실행해야 합니다."
            )
        else:
            st.error(
                f"{decision} 판정을 저장했습니다. Live subset 확대는 허용되지 않습니다."
            )


def main() -> None:
    st.set_page_config(page_title="SCHAT UAT-S01 사람 의미 검수", layout="wide")
    st.title("SCHAT UAT-S01 Critical Fact 사람 검수")
    st.caption("Local-only · Production 비연결 · Raw answer/evidence 비저장")
    st.info(
        "기존 raw response는 저장하지 않아 다시 불러올 수 없습니다. 아래 버튼은 승인된 "
        "UAT-S01 evidence만 Groq에 1회 다시 보내며, 원 질문은 전송하지 않습니다."
    )

    try:
        existing = load_human_review_record(REVIEW_RESULT_PATH)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        st.error(f"기존 로컬 판정 파일을 읽을 수 없습니다: {exc}")
        existing = None
    if existing is not None:
        st.markdown(
            "**현재 저장된 판정:** "
            f"{existing['decision']} · reviewer `{existing['reviewer']}` · "
            f"{existing['reviewed_at']}"
        )

    key_present = bool(os.getenv("GROQ_API_KEY", ""))
    st.metric("GROQ_API_KEY", "연결됨" if key_present else "없음")
    if not key_present:
        st.error(
            '현재 PowerShell에서 `$env:GROQ_API_KEY="사용자 API 키"`를 설정한 뒤 '
            "Streamlit을 같은 터미널에서 다시 실행하세요."
        )
        return

    if st.button("UAT-S01을 Groq에서 1회 다시 생성", type="secondary"):
        try:
            with st.spinner("로컬 retrieval 및 Groq 1회 호출 중입니다..."):
                st.session_state[SESSION_KEY] = _execute_review_call()
        except Exception as exc:  # Keep provider detail and secrets out of the UI.
            st.session_state.pop(SESSION_KEY, None)
            st.error("검수용 호출 실패 · " + format_review_error(exc))

    session = st.session_state.get(SESSION_KEY)
    if session is None:
        st.warning("아직 검수용 generated answer가 없습니다. 위 버튼을 한 번 누르세요.")
        return
    if existing is not None and existing.get("decision") == "PASS":
        try:
            persist_reviewed_session_snapshot(session, existing)
        except ValueError:
            pass
    _render_review_session(session)


if __name__ == "__main__":
    main()
