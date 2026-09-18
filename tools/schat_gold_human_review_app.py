"""Streamlit UI for local, explicit operational Positive Gold review."""

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

from tools.schat_gold_draft import (  # noqa: E402
    build_draft_fixture,
    draft_progress,
    load_draft_fixture,
    save_draft_fixture,
)
from tools.schat_gold_human_review import (  # noqa: E402
    DOCUMENT_DECISIONS,
    REVIEW_STATUSES,
    SCOPE_DECISIONS,
    build_assisted_review_updates,
    build_review_fixture,
    load_review_inputs,
    next_unreviewed_case_id,
    retrieve_local_chunk_candidates,
    retrieve_local_table_candidates,
    review_progress,
    save_review_fixture,
    update_review_case,
    validate_review_fixture,
)

DEFAULT_UAT = ROOT / "tests" / "fixtures" / "schat_v1_operational_uat.json"
DEFAULT_GOLD = ROOT / "tests" / "fixtures" / "schat_v1_operational_gold.json"
DEFAULT_REVIEWED = (
    ROOT / "tests" / "fixtures" / "schat_v1_operational_gold_reviewed.json"
)
DEFAULT_DRAFT = ROOT / "data" / "review" / "schat_v1_operational_gold_drafts.json"
DEFAULT_CATALOG = ROOT / "data" / "library" / "catalog.sqlite3"

STATUS_LABELS = {
    "unreviewed": "미검수",
    "reviewing": "검수 중/판단 보류",
    "approved": "승인",
    "rejected": "반려",
    "needs_second_review": "2차 검수 필요",
}
SCOPE_LABELS = {
    "pending": "판단 보류",
    "yes": "예, 병원 지침 범위입니다",
    "no": "아니오, 병원 지침 범위가 아닙니다",
}
DOCUMENT_LABELS = {
    "pending": "판단 보류",
    "sedation": "진정간호",
    "transfusion": "수혈간호",
    "other": "기타 등록 문서",
    "none": "없음",
}
TRISTATE_LABELS = {None: "판단 보류", True: "필요", False: "불필요"}


def _path(env_name: str, default: Path) -> Path:
    return Path(os.environ.get(env_name, default))


def _lines(value: str) -> list[str]:
    return list(dict.fromkeys(line.strip() for line in value.splitlines() if line.strip()))


def _join_lines(values: list[str]) -> str:
    return "\n".join(values)


def _load_state(inputs: dict[str, Any], reviewed_path: Path) -> dict[str, Any]:
    if not reviewed_path.is_file():
        return build_review_fixture(inputs)
    payload = json.loads(reviewed_path.read_text(encoding="utf-8"))
    validate_review_fixture(payload)
    if payload.get("source_uat_sha256") != inputs["uat_sha256"]:
        raise ValueError("운영 UAT 원본이 바뀌었습니다. 기존 검수 파일을 자동 병합하지 않습니다.")
    if payload.get("source_gold_sha256") != inputs["gold_sha256"]:
        raise ValueError("provisional Gold 원본이 바뀌었습니다. 기존 검수 파일을 자동 병합하지 않습니다.")
    return payload


def _load_drafts(inputs: dict[str, Any], draft_path: Path) -> dict[str, Any] | None:
    if not draft_path.is_file():
        return None
    payload = load_draft_fixture(draft_path)
    if payload.get("source_uat_sha256") != inputs["uat_sha256"]:
        raise ValueError("운영 UAT가 변경되어 기존 자동 초안을 사용할 수 없습니다.")
    if payload.get("source_gold_sha256") != inputs["gold_sha256"]:
        raise ValueError("provisional Gold가 변경되어 기존 자동 초안을 사용할 수 없습니다.")
    return payload


def _load_local_candidates(
    case: dict[str, Any], catalog_path: Path
) -> list[dict[str, Any]]:
    if case["expected_evidence_type"] in {"image", "image_pending"}:
        return []
    chunks = retrieve_local_chunk_candidates(
        catalog_path=catalog_path,
        question=case["question"],
        document_scope=case["expected_document_scope"],
        limit=16,
    )
    tables = []
    if case["expected_evidence_type"] in {"table", "mixed"}:
        tables = retrieve_local_table_candidates(
            catalog_path=catalog_path,
            question=case["question"],
            document_scope=case["expected_document_scope"],
            limit=10,
        )
    return [*tables, *chunks]


def _draft_initial(saved: dict[str, Any], draft: dict[str, Any] | None) -> dict[str, Any]:
    initial = dict(saved)
    if draft is None or saved["review_status"] != "unreviewed":
        return initial
    for field in (
        "scope_decision",
        "expected_domain",
        "expected_document",
        "expected_abstain",
        "primary_gold_ids",
        "acceptable_gold_ids",
        "critical_facts",
        "critical_numbers",
        "critical_units",
        "critical_times",
        "critical_conditions",
        "critical_contraindications",
        "critical_negations",
        "critical_steps",
        "table_required",
        "image_required",
    ):
        initial[field] = draft[field]
    return initial


def _case_label(case: dict[str, Any], review_by_id: dict[str, dict[str, Any]]) -> str:
    status = review_by_id[case["case_id"]]["review_status"]
    return f"{case['case_id']} · {STATUS_LABELS[status]} · {case['question_type']}"


def _render_candidate(candidate: dict[str, Any]) -> None:
    with st.container(border=True):
        st.markdown(
            f"**{candidate['retrieval_rank']}위 · 정답 미지정**  \n"
            f"문서: **{candidate['document_name']}** · 페이지: **{candidate['page'] or '-'}**  \n"
            f"섹션: **{candidate['section'] or '-'}** · 방식: `{candidate['retrieval_method']}`  \n"
            f"근거 ID ({candidate['identifier_type']}): `{candidate['evidence_id']}` · "
            f"Parent ID: `{candidate['parent_id'] or '-'}`  \n"
            f"참고 점수: `{candidate['score']:.6f}`"
        )
        st.caption("아래 근거 원문은 이 로컬 화면에서만 표시되며 reviewed fixture에 저장되지 않습니다.")
        st.text(candidate["evidence_text"])


def main() -> None:
    st.set_page_config(page_title="SCHAT Gold 사람 검수", page_icon="✅", layout="wide")
    st.title("SCHAT 운영 Positive Gold 사람 검수")
    st.warning(
        "로컬 전용 화면입니다. 검색 순위는 후보를 찾기 위한 참고값일 뿐이며, "
        "체크하거나 저장하기 전에는 어떤 근거도 Gold가 아닙니다."
    )

    uat_path = _path("SCHAT_GOLD_UAT_FIXTURE", DEFAULT_UAT)
    gold_path = _path("SCHAT_GOLD_PROVISIONAL_FIXTURE", DEFAULT_GOLD)
    reviewed_path = _path("SCHAT_GOLD_REVIEWED_FIXTURE", DEFAULT_REVIEWED)
    draft_path = _path("SCHAT_GOLD_DRAFT_FIXTURE", DEFAULT_DRAFT)
    catalog_path = _path("SCHAT_GOLD_CATALOG", DEFAULT_CATALOG)
    try:
        inputs = load_review_inputs(uat_path, gold_path)
        if "gold_review_payload" not in st.session_state:
            st.session_state["gold_review_payload"] = _load_state(inputs, reviewed_path)
        review = st.session_state["gold_review_payload"]
        if "gold_draft_payload" not in st.session_state or (
            st.session_state["gold_draft_payload"] is None and draft_path.is_file()
        ):
            st.session_state["gold_draft_payload"] = _load_drafts(inputs, draft_path)
        drafts = st.session_state["gold_draft_payload"]
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        st.error(str(exc))
        st.stop()

    progress = review_progress(review)
    st.subheader("Gold 검수 진행률")
    metrics = st.columns(6)
    metrics[0].metric("전체", str(progress["total"]))
    metrics[1].metric("검수 시작", str(progress["reviewed"]))
    metrics[2].metric("승인", str(progress["approved"]))
    metrics[3].metric("판단 보류", str(progress["on_hold"]))
    metrics[4].metric("2차 검수 필요", str(progress["needs_second_review"]))
    metrics[5].metric("최종 미완료", str(progress["remaining"]))
    st.progress((progress["approved"] + progress["rejected"]) / progress["total"])
    st.caption(f"최종 완료 {progress['approved'] + progress['rejected']} / {progress['total']}")

    st.subheader("자동 Gold 초안 준비")
    if st.button("남은 provisional case 자동 초안 생성", type="secondary"):
        try:
            with st.spinner("로컬 지침과 retrieval 후보로 검수 초안을 만드는 중입니다..."):
                generated = build_draft_fixture(
                    inputs,
                    review,
                    lambda item: _load_local_candidates(dict(item), catalog_path),
                )
                save_draft_fixture(
                    draft_path,
                    generated,
                    protected_paths=(uat_path, gold_path, reviewed_path),
                )
        except (OSError, RuntimeError, ValueError) as exc:
            st.error(f"자동 초안을 만들지 못했습니다: {exc}")
        else:
            st.session_state["gold_draft_payload"] = generated
            st.success("기존 승인 case를 제외한 로컬 검수 초안을 저장했습니다.")
            st.rerun()
    if drafts is None:
        st.info("아직 자동 초안이 없습니다. 위 버튼으로 local-only 초안을 생성하세요.")
    else:
        draft_counts = draft_progress(drafts, review)
        draft_metrics = st.columns(4)
        draft_metrics[0].metric("초안 대상", str(draft_counts["total"]))
        draft_metrics[1].metric(
            "그대로 검토 가능", str(draft_counts["ready_for_human_approval"])
        )
        draft_metrics[2].metric("수정 권고", str(draft_counts["edit_recommended"]))
        draft_metrics[3].metric(
            "사람 확인 필요", str(draft_counts["manual_review_required"])
        )

    review_by_id = {case["case_id"]: case for case in review["cases"]}
    filter_status = st.selectbox(
        "상태 필터",
        ["all", *REVIEW_STATUSES],
        format_func=lambda value: "전체" if value == "all" else STATUS_LABELS[value],
    )
    visible = [
        case for case in inputs["cases"]
        if filter_status == "all" or review_by_id[case["case_id"]]["review_status"] == filter_status
    ]
    if not visible:
        st.info("이 상태에 해당하는 질문이 없습니다.")
        return
    visible_ids = [case["case_id"] for case in visible]
    pending_case_id = st.session_state.pop("gold-review-pending-case", None)
    selected_key = "gold-review-selected-case"
    if pending_case_id in visible_ids:
        st.session_state[selected_key] = pending_case_id
    elif st.session_state.get(selected_key) not in visible_ids:
        st.session_state[selected_key] = next(
            (
                case_id
                for case_id in visible_ids
                if review_by_id[case_id]["review_status"] == "unreviewed"
            ),
            visible_ids[0],
        )
    selected_id = st.selectbox(
        "검수할 질문",
        visible_ids,
        format_func=lambda case_id: _case_label(
            next(case for case in visible if case["case_id"] == case_id), review_by_id
        ),
        key=selected_key,
    )
    case = next(case for case in inputs["cases"] if case["case_id"] == selected_id)
    saved = review_by_id[selected_id]
    draft_by_id = (
        {item["case_id"]: item for item in drafts["cases"]} if drafts is not None else {}
    )
    draft = None if saved["final_gold_approved"] else draft_by_id.get(selected_id)

    ordered_ids = [item["case_id"] for item in inputs["cases"]]
    current_index = ordered_ids.index(selected_id)
    nav_left, nav_right = st.columns(2)
    if nav_left.button("이전 case", key=f"previous-{selected_id}"):
        st.session_state["gold-review-pending-case"] = ordered_ids[
            (current_index - 1) % len(ordered_ids)
        ]
        st.rerun()
    next_case_id = next_unreviewed_case_id(review, selected_id)
    if nav_right.button(
        "다음 미검수 case",
        key=f"next-unreviewed-{selected_id}",
        disabled=next_case_id is None,
    ):
        st.session_state["gold-review-pending-case"] = next_case_id
        st.rerun()

    st.divider()
    st.subheader("질문 정보")
    st.markdown(f"### {case['question']}")
    info = st.columns(5)
    info[0].metric("Case ID", case["case_id"])
    info[1].metric("질문 유형", case["question_type"])
    info[2].metric("예상 문서", DOCUMENT_LABELS.get(case["expected_document_scope"], case["expected_document_scope"]))
    info[3].metric("예상 domain", case["expected_domain"])
    info[4].metric("예상 근거", case["expected_evidence_type"])

    st.subheader("후보 근거")
    st.caption(
        "후보는 현재 등록 지침서를 로컬에서 검색한 결과입니다. 순위가 높아도 자동 정답이 아니며, "
        "간호사가 원문을 읽고 Primary/Acceptable을 직접 선택해야 합니다."
    )
    search_query = st.text_input(
        "후보 검색어",
        value=case["question"],
        key=f"candidate-query-{selected_id}",
        help="질문 그대로 검색하거나, 후보를 더 찾기 위해 검색어만 바꿀 수 있습니다.",
    )
    candidate_key = f"gold-candidates-{selected_id}"
    if st.button("후보 근거 불러오기", type="secondary"):
        try:
            with st.spinner("등록 지침서에서 로컬 후보를 찾는 중입니다..."):
                chunk_candidates = retrieve_local_chunk_candidates(
                    catalog_path=catalog_path,
                    question=search_query,
                    document_scope=case["expected_document_scope"],
                    limit=16,
                )
                table_candidates = []
                if case["expected_evidence_type"] in {"table", "mixed"}:
                    table_candidates = retrieve_local_table_candidates(
                        catalog_path=catalog_path,
                        question=search_query,
                        document_scope=case["expected_document_scope"],
                        limit=10,
                    )
                st.session_state[candidate_key] = [*table_candidates, *chunk_candidates]
        except Exception as exc:  # Streamlit must show a recoverable local lookup error.
            st.error(f"후보 근거를 불러오지 못했습니다: {exc}")
    candidates = list(st.session_state.get(candidate_key, []))
    if case["expected_evidence_type"] == "image":
        st.info("이미지/도식 case는 TF027 사람 검수가 완료될 때까지 Gold 승인을 보류하세요.")
    if candidates:
        for candidate in candidates:
            _render_candidate(candidate)
    else:
        st.info("버튼을 눌러 후보를 불러오세요. 화면을 여는 것만으로는 검색이나 저장을 실행하지 않습니다.")

    st.divider()
    st.subheader("자동 Gold 초안")
    if saved["final_gold_approved"]:
        st.success("이미 사람이 최종 승인한 Gold입니다. 자동 초안 대상에서 제외됩니다.")
    elif draft is None:
        st.warning("이 case의 자동 초안이 없습니다. 사람이 직접 검수하거나 초안을 다시 생성하세요.")
    else:
        status_label = {
            "ready_for_human_approval": "초안 그대로 검토 가능",
            "edit_recommended": "수정 권고",
            "manual_review_required": "사람 확인 필요",
        }[draft["draft_status"]]
        st.markdown(
            f"**상태:** {status_label} · **신뢰도:** {draft['confidence']}  \n"
            f"**병원 지침 범위:** {draft['scope_decision']}  \n"
            f"**정답 문서:** {draft['expected_document']}  \n"
            f"**Primary Evidence 후보:** {', '.join(draft['primary_gold_ids']) or '사람 확인 필요'}  \n"
            f"**Acceptable Evidence 후보:** {', '.join(draft['acceptable_gold_ids']) or '없음/사람 확인 필요'}"
        )
        draft_columns = st.columns(2)
        with draft_columns[0]:
            st.markdown("**핵심 사실 제안**")
            st.code("\n".join(draft["critical_facts"]) or "사람 확인 필요")
            st.markdown(
                f"**숫자:** {', '.join(draft['critical_numbers']) or '-'}  \n"
                f"**단위:** {', '.join(draft['critical_units']) or '-'}  \n"
                f"**시간:** {', '.join(draft['critical_times']) or '-'}"
            )
        with draft_columns[1]:
            st.markdown(
                f"**조건:** {' / '.join(draft['critical_conditions']) or '-'}  \n"
                f"**금기:** {' / '.join(draft['critical_contraindications']) or '-'}  \n"
                f"**부정:** {' / '.join(draft['critical_negations']) or '-'}  \n"
                f"**단계/순서:** {' / '.join(draft['critical_steps']) or '-'}  \n"
                f"**표 필요:** {draft['table_required']} · **이미지 필요:** {draft['image_required']}"
            )
        if draft["human_review_reasons"]:
            st.warning("사람 확인 필요: " + ", ".join(draft["human_review_reasons"]))

    initial = _draft_initial(saved, draft)
    candidate_by_id = {candidate["evidence_id"]: candidate for candidate in candidates}
    selectable_ids = list(dict.fromkeys(
        [
            *initial["primary_gold_ids"],
            *initial["acceptable_gold_ids"],
            *candidate_by_id,
        ]
    ))

    st.divider()
    st.subheader("사람 검수 입력")
    if saved["final_gold_approved"]:
        st.info("이 case는 이미 최종 승인되어 자동 초안이나 검수 버튼으로 변경할 수 없습니다.")
        return
    with st.form(f"review-form-{selected_id}"):
        scope_decision = st.selectbox(
            "A. 이 질문은 병원 지침 범위인가요?",
            list(SCOPE_DECISIONS),
            index=list(SCOPE_DECISIONS).index(initial["scope_decision"]),
            format_func=lambda value: SCOPE_LABELS[value],
        )
        expected_document = st.selectbox(
            "B. 정답 문서",
            list(DOCUMENT_DECISIONS),
            index=list(DOCUMENT_DECISIONS).index(initial["expected_document"]),
            format_func=lambda value: DOCUMENT_LABELS[value],
        )
        primary = st.multiselect(
            "C. Primary Gold Evidence · 반드시 포함되어야 하는 근거",
            selectable_ids,
            default=initial["primary_gold_ids"],
            format_func=lambda evidence_id: (
                f"{evidence_id} · {candidate_by_id[evidence_id]['retrieval_rank']}위"
                if evidence_id in candidate_by_id else f"{evidence_id} · 저장된 선택"
            ),
        )
        acceptable = st.multiselect(
            "D. Acceptable Gold Evidence · 보조 정답으로 인정 가능한 근거",
            selectable_ids,
            default=initial["acceptable_gold_ids"],
            format_func=lambda evidence_id: (
                f"{evidence_id} · {candidate_by_id[evidence_id]['retrieval_rank']}위"
                if evidence_id in candidate_by_id else f"{evidence_id} · 저장된 선택"
            ),
        )
        st.markdown("#### E. 핵심 정답 사실 · 항목당 한 줄")
        critical_facts = st.text_area("필수 핵심 사실", _join_lines(initial["critical_facts"]))
        left, right = st.columns(2)
        with left:
            critical_numbers = st.text_area("필수 숫자", _join_lines(initial["critical_numbers"]))
            critical_units = st.text_area("필수 단위", _join_lines(initial["critical_units"]))
            critical_times = st.text_area("필수 시간", _join_lines(initial["critical_times"]))
            critical_steps = st.text_area("필수 단계/순서", _join_lines(initial["critical_steps"]))
        with right:
            critical_conditions = st.text_area("필수 조건", _join_lines(initial["critical_conditions"]))
            critical_contraindications = st.text_area(
                "필수 금기", _join_lines(initial["critical_contraindications"])
            )
            critical_negations = st.text_area("필수 부정 표현", _join_lines(initial["critical_negations"]))
            note = st.text_area("사람 검수 메모", saved["note"])

        requirement_options = [None, True, False]
        table_required = st.selectbox(
            "표 근거 필요 여부",
            requirement_options,
            index=requirement_options.index(initial["table_required"]),
            format_func=lambda value: TRISTATE_LABELS[value],
        )
        image_required = st.selectbox(
            "이미지 근거 필요 여부",
            requirement_options,
            index=requirement_options.index(initial["image_required"]),
            format_func=lambda value: TRISTATE_LABELS[value],
        )
        image_human_review_completed = saved["image_human_review_completed"]
        if case["source_label_status"] == "image_needs_human_review":
            image_human_review_completed = st.checkbox(
                "별도 이미지/도식 사람 검수 checklist 완료",
                saved["image_human_review_completed"],
                help="Figure 경계, node, 화살표, branch, 순서, 숫자·단위를 별도 checklist에서 확인한 경우에만 체크합니다.",
            )
        reviewer = st.text_input("1차 reviewer 로컬 식별자", saved["reviewer"], placeholder="reviewer_1")
        second_review_required = st.checkbox("2차 검수 필수", saved["second_review_required"])
        reviewer_2 = st.text_input("2차 reviewer 로컬 식별자", saved["reviewer_2"], placeholder="reviewer_2")
        reviewer_2_approved = st.checkbox("2차 reviewer 승인", saved["reviewer_2_approved"])
        final_gold_approved = st.checkbox(
            "최종 Gold 승인 · 사람이 명시적으로 확인한 경우에만 체크",
            saved["final_gold_approved"],
        )
        st.caption(
            "최종 Gold 체크 없이 승인 버튼을 누르면 1차 검수만 기록되며 Gold aggregate에는 포함되지 않습니다."
        )
        action_columns = st.columns(3)
        with action_columns[0]:
            accept_draft = st.form_submit_button(
                "초안 그대로 승인",
                type="primary",
                disabled=draft is None,
            )
        with action_columns[1]:
            accept_modified = st.form_submit_button("수정 후 승인")
        with action_columns[2]:
            hold_review = st.form_submit_button("판단 보류")

    action = (
        "draft_accepted"
        if accept_draft
        else "modified_accepted"
        if accept_modified
        else "on_hold"
        if hold_review
        else None
    )
    if action is not None:
        expected_abstain = scope_decision == "no"
        form_updates = {
            "scope_decision": scope_decision,
            "expected_domain": {
                "pending": "pending",
                "yes": "hospital",
                "no": "out_of_scope",
            }[scope_decision],
            "expected_document": expected_document,
            "expected_abstain": expected_abstain,
            "primary_gold_ids": [] if expected_abstain else primary,
            "acceptable_gold_ids": [] if expected_abstain else acceptable,
            "critical_facts": _lines(critical_facts),
            "critical_numbers": _lines(critical_numbers),
            "critical_units": _lines(critical_units),
            "critical_times": _lines(critical_times),
            "critical_conditions": _lines(critical_conditions),
            "critical_contraindications": _lines(critical_contraindications),
            "critical_negations": _lines(critical_negations),
            "critical_steps": _lines(critical_steps),
            "table_required": table_required,
            "image_required": image_required,
            "image_human_review_completed": image_human_review_completed,
            "reviewer": reviewer.strip(),
            "note": note.strip(),
            "second_review_required": second_review_required,
            "reviewer_2": reviewer_2.strip(),
            "reviewer_2_approved": reviewer_2_approved,
        }
        try:
            action_draft = draft or {"case_id": selected_id, "draft_only": True}
            prepared = build_assisted_review_updates(
                saved,
                action_draft,
                action=action,
                form_updates=form_updates,
                reviewer=reviewer,
                final_approval_requested=final_gold_approved,
            )
            updated = update_review_case(review, selected_id, prepared)
            save_review_fixture(
                reviewed_path,
                updated,
                protected_paths=(uat_path, gold_path),
            )
        except (OSError, ValueError) as exc:
            st.error(f"저장하지 않았습니다: {exc}")
        else:
            st.session_state["gold_review_payload"] = updated
            next_case_id = next_unreviewed_case_id(updated, selected_id)
            if next_case_id is not None:
                st.session_state["gold-review-pending-case"] = next_case_id
            st.success(f"{selected_id} 검수 결과를 별도 reviewed fixture에 저장했습니다.")
            st.rerun()

    st.caption(
        f"저장 위치: {reviewed_path} · 원본 UAT/Gold fixture는 덮어쓰지 않습니다. "
        "후보 원문과 검색 순위는 저장되지 않습니다."
    )


if __name__ == "__main__":
    main()
