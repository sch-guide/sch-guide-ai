"""Local-only Streamlit review for extractive and controlled answer candidates."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.prompt_config import load_evaluation_prompt  # noqa: E402
from tools.controlled_generation_review import (  # noqa: E402
    build_review_session,
    candidate_status_notice,
    load_coverage_review_source,
    load_review_source,
    ordered_review_case_ids,
    review_case_label,
    save_coverage_review_record,
    save_review_record,
)

FOLLOW_UP_ENABLED = False
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_ROOT = (
    ROOT / "workspace" / "임시작업" / "controlled_generation_review_source"
)
DEFAULT_SOURCE_PATH = DEFAULT_SOURCE_ROOT / "review_source.json"
DEFAULT_COVERAGE_SOURCE_PATH = DEFAULT_SOURCE_ROOT / "coverage_review_source.json"
DEFAULT_QUESTION_FIXTURE = ROOT / "tests" / "fixtures" / "schat_v1_operational_uat.json"
DEFAULT_REVIEW_PATH = (
    ROOT
    / "workspace"
    / "데이터_검수"
    / "controlled_generation"
    / "human_review.jsonl"
)
DEFAULT_COVERAGE_REVIEW_PATH = (
    ROOT
    / "workspace"
    / "데이터_검수"
    / "controlled_generation"
    / "coverage_human_review.jsonl"
)


def load_review_catalog(*, prompt_version: str):
    """Load the ignored display source and join local fixture questions."""
    return load_review_source(
        DEFAULT_SOURCE_PATH,
        question_fixture_path=DEFAULT_QUESTION_FIXTURE,
        expected_prompt_version=prompt_version,
    )


def main() -> None:
    config = load_evaluation_prompt()
    st.set_page_config(page_title="SCHAT Controlled Generation 검수", layout="wide")
    st.title("SCHAT 답변 자연어 재구성 검수")
    st.caption(
        f"평가 전용 · {config.prompt_version} · Production 게시 비활성 · 외부 호출 없음"
    )

    try:
        catalog = load_review_catalog(prompt_version=config.prompt_version)
    except ValueError as exc:
        st.error(
            "로컬 review source를 불러오지 못했습니다. "
            f"검수 저장은 비활성화됩니다: {exc}"
        )
        st.code(str(DEFAULT_SOURCE_PATH), language=None)
        st.stop()

    live_only = st.toggle("Gemini live 평가 case만 보기", value=True)
    case_options = ordered_review_case_ids(catalog, live_only=live_only)
    if not case_options:
        st.warning("표시할 Gemini live 평가 case가 없습니다.")
        st.stop()
    case_id = st.selectbox(
        "Case ID",
        case_options,
        format_func=lambda value: review_case_label(catalog[value]),
    )
    case = catalog[case_id]
    st.info(candidate_status_notice(case.candidate_status))
    st.caption(f"candidate model: {case.model} · status: {case.candidate_status}")
    st.text_area("질문", value=case.question, height=100, disabled=True)
    left, right = st.columns(2)
    with left:
        st.text_area(
            "기존 extractive 답변",
            value=case.extractive_answer,
            height=320,
            disabled=True,
        )
    with right:
        st.text_area(
            "controlled candidate",
            value=case.controlled_candidate,
            height=320,
            disabled=True,
        )
        st.caption("문단별 supporting SourceUnit ID")
        for index, identifiers in enumerate(case.supporting_source_unit_ids, 1):
            st.code(f"{index}: {', '.join(identifiers)}", language=None)

    coverage_catalog = load_coverage_review_source(DEFAULT_COVERAGE_SOURCE_PATH)
    coverage_slots = [
        slot for (value_case_id, _), slot in coverage_catalog.items()
        if value_case_id == case_id
    ]
    if coverage_slots:
        st.divider()
        st.subheader("Coverage 상세 비교")
        slot = st.selectbox(
            "required slot",
            coverage_slots,
            format_func=lambda value: value.slot_id,
        )
        st.caption(
            "연결 SourceUnit ID: " + ", ".join(slot.supporting_source_unit_ids)
        )
        source_column, candidate_column = st.columns(2)
        with source_column:
            st.text_area(
                "SourceUnit 원문",
                value=slot.source_text,
                height=180,
                disabled=True,
            )
        with candidate_column:
            st.text_area(
                "관련 Gemini candidate statement",
                value=slot.candidate_statement,
                height=180,
                disabled=True,
            )
        st.metric("기존 token coverage", f"{slot.token_coverage:.1%}")
        with st.form(f"coverage-review-{case_id}-{slot.slot_id}"):
            coverage_judgment = st.selectbox(
                "사람 판정",
                (
                    "의미 동일·표현 차이",
                    "실제 의미 누락",
                    "부분 포함",
                    "판단 어려움",
                ),
            )
            coverage_submitted = st.form_submit_button("Coverage 판정 저장")
        if coverage_submitted:
            try:
                save_coverage_review_record(
                    DEFAULT_COVERAGE_REVIEW_PATH,
                    {
                        "case_id": slot.case_id,
                        "slot_id": slot.slot_id,
                        "source_sha256": slot.source_sha256,
                        "candidate_sha256": slot.candidate_sha256,
                        "judgment": coverage_judgment,
                        "timestamp": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
                    },
                )
            except ValueError as exc:
                st.error(f"Coverage 판정을 저장하지 않았습니다: {exc}")
            else:
                st.success("raw-free Coverage 판정을 저장했습니다.")

    with st.form("controlled-generation-review"):
        preferred_answer = st.selectbox(
            "선호 답변", ("extractive", "controlled", "동률", "판단 어려움")
        )
        naturalness = st.selectbox("자연스러움", ("좋음", "보통", "나쁨", "판단 어려움"))
        accuracy = st.selectbox("정확성", ("적절", "부분적절", "부적절", "판단 어려움"))
        completeness = st.selectbox(
            "완전성", ("적절", "부분적절", "부적절", "판단 어려움")
        )
        grounding = st.selectbox(
            "근거 충실성", ("적절", "부분적절", "부적절", "판단 어려움")
        )
        semantic_equivalence = st.selectbox(
            "의미 동치",
            config.semantic_equivalence_options,
        )
        note = st.text_area("검수 메모", max_chars=500)
        submitted = st.form_submit_button("검수 결과 저장")

    if submitted:
        try:
            session = build_review_session(
                case_id=case_id,
                extractive_text=case.extractive_answer,
                controlled_text=case.controlled_candidate,
                controlled_citation_ids=case.supporting_source_unit_ids,
                prompt_version=config.prompt_version,
                config_sha256=config.config_sha256,
                follow_up_enabled=FOLLOW_UP_ENABLED,
            )
            if (
                session.extractive_sha256 != case.extractive_answer_sha256
                or session.candidate_sha256 != case.controlled_candidate_sha256
            ):
                raise ValueError("review_source_hash")
            save_review_record(
                DEFAULT_REVIEW_PATH,
                {
                    "case_id": session.case_id,
                    "preferred_answer": preferred_answer,
                    "naturalness": naturalness,
                    "accuracy": accuracy,
                    "completeness": completeness,
                    "grounding": grounding,
                    "semantic_equivalence": semantic_equivalence,
                    "note": note,
                    "prompt_version": session.prompt_version,
                    "model": case.model,
                    "timestamp": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
                    "config_sha256": session.config_sha256,
                    "extractive_sha256": session.extractive_sha256,
                    "candidate_sha256": session.candidate_sha256,
                },
                forbidden_exact_texts=(
                    case.question,
                    case.extractive_answer,
                    case.controlled_candidate,
                ),
            )
        except ValueError as exc:
            st.error(f"저장하지 않았습니다: {exc}")
        else:
            st.success("raw-free 검수 결과를 저장했습니다.")


if __name__ == "__main__":
    main()
