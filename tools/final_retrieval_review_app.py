"""Local-only Streamlit review UI for the final frozen retrieval comparison."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.final_retrieval_comparison import ENGINE_LABELS, ENGINE_ORDER  # noqa: E402
from tools.final_retrieval_review import (  # noqa: E402
    JUDGMENTS,
    build_review_record,
    load_source_preview,
    load_uat_cases,
    save_review,
)

RESULT_DIR = (
    ROOT
    / "workspace"
    / "검색_성능평가"
    / "3방식_비교"
    / "2026-09-20_final-retrieval-comparison"
)
COMPARISON_PATH = RESULT_DIR / "per_case_comparison.json"
SUMMARY_PATH = RESULT_DIR / "summary.json"
REVIEW_PATH = RESULT_DIR / "human_review.json"
UAT_PATH = ROOT / "tests" / "fixtures" / "schat_v1_operational_uat.json"
CATALOG_PATH = ROOT / "data" / "library" / "catalog.sqlite3"


def _load_comparison() -> dict:
    return json.loads(COMPARISON_PATH.read_text(encoding="utf-8"))


def _load_summary() -> dict:
    return json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))


def _render_rankings(case: dict, engine: str) -> None:
    st.markdown(f"### {ENGINE_LABELS[engine]}")
    st.json(case["retrievers"][engine]["metrics"], expanded=False)
    for result in case["retrievers"][engine]["ranked_results"]:
        label = (
            f"#{result['rank']} · {result['chunk_id']} · p.{result.get('page')}"
            + (" · Gold" if result.get("gold_hit") else "")
        )
        with st.expander(label):
            safe_metadata = {key: value for key, value in result.items() if key != "text"}
            st.json(safe_metadata)
            if st.button(
                "로컬 원문 미리보기",
                key=f"preview-{case['case_id']}-{engine}-{result['rank']}",
            ):
                preview = load_source_preview(CATALOG_PATH, result["chunk_id"])
                st.caption(
                    f"{preview['document_name']} · p.{preview['page']} · {preview['section']}"
                )
                st.text(preview["text"])


def main() -> None:
    st.set_page_config(page_title="SCHAT Retrieval 사람 검수", layout="wide")
    st.title("SCHAT Retrieval 최종 비교 · 사람 검수")
    st.info(
        "질문과 원문은 로컬 파일/DB에서 화면 메모리로만 읽습니다. "
        "저장 파일에는 case ID, 판정, 선호 방식, 메모, 검수자, 시각만 기록합니다."
    )
    comparison = _load_comparison()
    summary = _load_summary()
    uat = load_uat_cases(UAT_PATH)
    cases = comparison["cases"]
    columns = st.columns(3)
    for column, engine in zip(columns, ENGINE_ORDER, strict=True):
        metrics = summary["aggregate_metrics"][engine]
        with column:
            st.markdown(f"### {ENGINE_LABELS[engine]}")
            st.metric("Hit@10", f"{metrics['hit_at_10']:.3f}")
            st.metric("MRR", f"{metrics['mrr']:.3f}")
            st.metric("평균 지연", f"{metrics['latency_mean_ms']:.1f} ms")
            st.caption(
                f"단독 우세 {summary['single_winner_counts'].get(engine, 0)}건"
            )
    query = st.text_input("Case ID/질문 검색")
    question_type = st.selectbox(
        "질문 유형", ["전체", *sorted({row["question_type"] for row in cases})]
    )
    priority_options = ["전체", *summary["priority_review_cases"]]
    priority = st.selectbox("우선 검수 분류", priority_options)
    priority_ids = (
        set(summary["priority_review_cases"][priority]) if priority != "전체" else None
    )
    filtered = [
        row
        for row in cases
        if (
            not query
            or query.lower() in row["case_id"].lower()
            or query.lower() in str(uat[row["case_id"]]["question"]).lower()
        )
        and (question_type == "전체" or row["question_type"] == question_type)
        and (priority_ids is None or row["case_id"] in priority_ids)
    ]
    if not filtered:
        st.warning("조건에 맞는 case가 없습니다.")
        return
    case_id = st.selectbox("Case", [row["case_id"] for row in filtered])
    case = next(row for row in filtered if row["case_id"] == case_id)
    st.subheader(case_id)
    st.write(uat[case_id]["question"])
    st.caption(
        f"{case['question_type']} · {case['document_scope']} · "
        f"Gold IDs {len(case['gold_ids'])}개"
    )
    tabs = st.tabs([ENGINE_LABELS[engine] for engine in ENGINE_ORDER])
    for tab, engine in zip(tabs, ENGINE_ORDER, strict=True):
        with tab:
            _render_rankings(case, engine)

    st.divider()
    with st.form(f"review-{case_id}"):
        reviewer = st.text_input("검수자")
        judgments = {
            engine: st.selectbox(
                f"{ENGINE_LABELS[engine]} relevance judgment",
                JUDGMENTS,
                key=f"judgment-{case_id}-{engine}",
            )
            for engine in ENGINE_ORDER
        }
        preferred = st.selectbox(
            "preferred_retriever", ["", *ENGINE_ORDER], format_func=lambda value: ENGINE_LABELS.get(value, "선택 안 함")
        )
        note = st.text_area("overall_note (원문·검색 결과 원문 저장 금지)")
        submitted = st.form_submit_button("검수 결과 저장")
    if submitted:
        record = build_review_record(
            case_id=case_id,
            retriever_reviews=judgments,
            preferred_retriever=preferred,
            overall_note=note,
            reviewer=reviewer,
        )
        save_review(REVIEW_PATH, record)
        st.success("raw-free 검수 결과를 저장했습니다.")


if __name__ == "__main__":
    main()
