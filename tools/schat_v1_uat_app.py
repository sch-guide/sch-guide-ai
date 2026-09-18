"""Local, metadata-only review UI for the SCHAT v1 operational UAT."""

from __future__ import annotations

import json
import os
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT = (
    ROOT / "artifacts" / "2026-09-18_schat-v1-ragas-gold-uat-final"
)


def _artifact_dir() -> Path:
    return Path(os.environ.get("SCHAT_V1_FINAL_ARTIFACT_DIR", DEFAULT_ARTIFACT))


def _load(name: str):
    path = _artifact_dir() / name
    if not path.is_file():
        raise FileNotFoundError(f"먼저 final validation artifact를 생성하세요: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


st.set_page_config(page_title="SCHAT v1 UAT 검수", layout="wide")
st.title("SCHAT v1 운영 UAT 로컬 검수")
st.warning("로컬 evaluation 전용입니다. Provider/Vision 실제 호출 0회")
st.markdown("**Provider/Vision 실제 호출 0회**")

try:
    summary = _load("uat_summary.json")
    results = _load("uat_results.json")
except (FileNotFoundError, json.JSONDecodeError) as exc:
    st.error(str(exc))
    st.stop()

st.markdown(
    f"전체 **{summary['case_count']}** · PASS **{summary['pass_count']}** · "
    f"FAIL **{summary['fail_count']}**"
)
scope = st.selectbox(
    "문서 범위",
    ["전체", "sedation", "transfusion", "out_of_scope"],
)
outcome = st.selectbox("결과", ["전체", "PASS", "FAIL"])

visible = [
    row for row in results
    if (scope == "전체" or row.get("document_scope") == scope)
    and (outcome == "전체" or bool(row.get("pass")) == (outcome == "PASS"))
]

for row in visible:
    label = "PASS" if row.get("pass") else "FAIL"
    with st.expander(f"{row['case_id']} · {label} · {row.get('question_type', '-')}"):
        left, right = st.columns(2)
        with left:
            st.write("Domain", row.get("actual_domain"))
            st.write("Intent", row.get("actual_kind"))
            st.write("Pre/Post", row.get("pre_budget_reason"), row.get("post_budget_reason"))
        with right:
            st.write("Selected evidence", row.get("selected_evidence_count", 0))
            st.write("Provider calls", row.get("actual_provider_calls", 0))
            st.write("Failure", row.get("failure_category") or "-")

st.caption("질문과 병원 SourceUnit 원문은 이 화면의 artifact에 저장되지 않습니다.")
