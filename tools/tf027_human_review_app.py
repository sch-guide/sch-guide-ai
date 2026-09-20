"""Local Streamlit surface for explicit human review of the TF027 figure."""

from __future__ import annotations

import copy
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.tf027_human_review import (  # noqa: E402
    CHECK_STATUSES,
    build_reviewed_fixture,
    load_source_checklist,
    normalize_review_state,
    render_review_images,
    save_reviewed_fixture,
    validate_reviewed_fixture,
)

DEFAULT_CHECKLIST = ROOT / "tests" / "fixtures" / "tf027_image_human_review_checklist.json"
DEFAULT_REVIEWED = (
    ROOT / "tests" / "fixtures" / "tf027_image_human_review_reviewed.json"
)
DEFAULT_PDF = ROOT / "data" / "실무지침서_수혈간호.pdf"

CHECK_LABELS = {
    "figure_boundary": "1. Figure 경계",
    "start_and_end_nodes": "2. 시작 node / 종료 node",
    "node_labels": "3. Node label",
    "arrow_direction_and_connections": "4. 화살표 방향과 연결 관계",
    "decision_branch_conditions": "5. Decision branch 조건",
    "workflow_order": "6. Workflow 순서",
    "numbers_units_and_times": "7. 숫자 / 단위 / 시간 / 용량",
    "caption_nearby_text_relationship": "8. Caption / nearby text 관계",
    "illegible_or_occluded_elements": "9. 흐림 / 가림 / 잘림 / 판독 불가",
    "production_gold_approval": "10. Production image Gold 승인 판단",
}
CHECK_HELP = {
    "figure_boundary": "Figure 전체가 보이고 잘린 부분이 없는지 적어 주세요.",
    "start_and_end_nodes": "사람이 읽은 시작점과 종료점을 그대로 적어 주세요.",
    "node_labels": "정확히 읽을 수 있는 node 문구만 적어 주세요.",
    "arrow_direction_and_connections": "A -> B 형식으로 직접 확인한 연결만 적어 주세요.",
    "decision_branch_conditions": "분기 조건과 Yes/No 경로를 직접 확인해 적어 주세요.",
    "workflow_order": "사람이 확인한 단계 순서를 적어 주세요.",
    "numbers_units_and_times": "보이는 숫자·단위·시간·용량만 적어 주세요.",
    "caption_nearby_text_relationship": "Caption과 주변 본문이 figure와 어떤 관계인지 적어 주세요.",
    "illegible_or_occluded_elements": "불명확한 부분이 없으면 '없음', 있으면 위치와 이유를 적어 주세요.",
    "production_gold_approval": "임상적으로 불확실한 값이 모두 해소된 경우에만 확인됨을 선택하세요.",
}
STATUS_LABELS = {
    "unreviewed": "미검수",
    "confirmed": "확인됨",
    "uncertain": "불확실",
    "not_applicable": "해당 없음",
}
DECISION_LABELS = {
    "pending": "아직 승인하지 않음",
    "approve": "Production image Gold 승인 요청",
    "reject": "Production image Gold 사용하지 않음",
}


def _lines(value: str) -> list[str]:
    return list(dict.fromkeys(line.strip() for line in value.splitlines() if line.strip()))


def _joined(values: list[str]) -> str:
    return "\n".join(values)


def _load_review(checklist_path: Path, reviewed_path: Path) -> dict:
    source = load_source_checklist(checklist_path)
    blank = build_reviewed_fixture(source, source_path=checklist_path)
    if not reviewed_path.exists():
        return blank
    loaded = json.loads(reviewed_path.read_text(encoding="utf-8"))
    validate_reviewed_fixture(loaded)
    if loaded["source_checklist_sha256"] != blank["source_checklist_sha256"]:
        raise ValueError("원본 checklist가 변경되어 기존 검수 파일을 열 수 없습니다.")
    return loaded


def main() -> None:
    checklist_path = Path(os.environ.get("SCHAT_TF027_CHECKLIST", DEFAULT_CHECKLIST))
    reviewed_path = Path(os.environ.get("SCHAT_TF027_REVIEWED", DEFAULT_REVIEWED))
    pdf_path = Path(os.environ.get("SCHAT_TF027_PDF", DEFAULT_PDF))

    st.set_page_config(page_title="TF027 사람 검수", layout="wide")
    st.title("TF027 이미지/도식 사람 검수")
    st.warning(
        "사람이 입력하지 않은 값은 승인되지 않습니다. 화살표·순서·분기·숫자를 "
        "AI가 추정하지 않으며, 명시적으로 저장 버튼을 누르기 전에는 파일도 만들지 않습니다."
    )

    try:
        review = _load_review(checklist_path, reviewed_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        st.error(f"TF027 검수 데이터를 열 수 없습니다: {exc}")
        st.stop()

    confirmed = sum(check["status"] == "confirmed" for check in review["checks"])
    uncertain = sum(check["status"] == "uncertain" for check in review["checks"])
    metric_cols = st.columns(4)
    metric_cols[0].metric("확인됨", f"{confirmed} / 10")
    metric_cols[1].metric("불확실", uncertain)
    metric_cols[2].metric(
        "1차 검수",
        "완료" if review["reviewers"]["reviewer_1_approved"] else "미완료",
    )
    metric_cols[3].metric(
        "Production Gold",
        "승인" if review["production_gold_approved"] else "미승인",
    )
    st.caption(
        f"현재 상태: {review['review_status']} · needs_human_review="
        f"{str(review['needs_human_review']).lower()} · vision API 호출 0회"
    )

    if not review["production_gold_approved"]:
        st.error(
            "STOP_FOR_TF027_HUMAN_REVIEW — 사람이 원본 도식을 직접 확인하기 전에는 "
            "Production image Gold로 승인하거나 답변 경로에 연결할 수 없습니다."
        )

    st.subheader("한 화면 검수 순서")
    st.markdown(
        """
1. **Figure 경계** — 도식 전체와 잘림 여부를 확인합니다.
2. **시작 node** — 사람이 읽은 시작점을 기록합니다.
3. **종료 node** — 사람이 읽은 종료점을 기록합니다.
4. **Node label** — 실제로 읽히는 문구만 기록합니다.
5. **화살표 방향** — 각 화살표의 방향을 직접 확인합니다.
6. **Node 연결 관계** — 어떤 node가 연결되는지 확인합니다.
7. **Decision branch** — 분기 조건과 각 경로를 확인합니다.
8. **Workflow 순서** — 단계 순서를 직접 확인합니다.
9. **숫자·단위·시간** — 보이는 값만 원문 그대로 기록합니다.
10. **Caption·nearby text·crop·blur·occlusion** — 주변 문맥과 판독 방해 요소를 기록합니다.
"""
    )
    st.caption(
        "아래 저장 필드는 기존 안전 스키마를 유지합니다. 위 항목을 참고해 사람이 확인한 "
        "값만 입력하며, 빈 값은 시스템이 자동으로 채우지 않습니다."
    )

    check_values: dict[str, tuple[str, str, str]] = {}
    structured = review["structured_result"]
    reviewers = review["reviewers"]

    with st.form("tf027-human-review-form", clear_on_submit=False):
        left, right = st.columns([1.05, 1.35], gap="large")
        with left:
            st.subheader("원본 page / 후보 경계")
            st.caption(
                "빨간 경계와 번호는 후보 위치를 찾기 위한 표시입니다. 원본 PDF는 수정되지 않습니다."
            )
            try:
                original, overlay = render_review_images(
                    pdf_path,
                    page_number=review["source_reference"]["page"],
                    figure_candidates=review["source_reference"]["figure_candidates"],
                )
            except (OSError, ValueError, RuntimeError) as exc:
                st.error(f"원본 PDF page를 표시할 수 없습니다: {exc}")
            else:
                st.image(
                    original,
                    caption=f"원본 PDF page {review['source_reference']['page']}",
                    width="stretch",
                )
                st.image(
                    overlay,
                    caption="후보 bbox 오버레이(검수 보조용)",
                    width="stretch",
                )
            candidate_options = [
                candidate["figure_id"]
                for candidate in review["source_reference"]["figure_candidates"]
            ]
            selected_figure_ids = st.multiselect(
                "사람이 검수 대상으로 확인한 figure ID",
                candidate_options,
                default=structured["selected_figure_ids"],
                help="후보 순위는 정답이 아닙니다. 실제 원본을 보고 직접 선택하세요.",
            )
            for index, candidate in enumerate(
                review["source_reference"]["figure_candidates"], 1
            ):
                st.code(
                    f"{index}. {candidate['figure_id']}\n"
                    f"bbox={candidate['bbox']}\n"
                    f"fingerprint={candidate['fingerprint']}",
                    language=None,
                )

        with right:
            st.subheader("10개 사람 검수 항목")
            for check in review["checks"]:
                check_id = str(check["check_id"])
                st.markdown(f"**{CHECK_LABELS[check_id]}**")
                status = st.selectbox(
                    "상태",
                    list(CHECK_STATUSES),
                    index=list(CHECK_STATUSES).index(str(check["status"])),
                    format_func=lambda value: STATUS_LABELS[value],
                    key=f"status-{check_id}",
                    label_visibility="collapsed",
                )
                reviewed_value = st.text_area(
                    "사람이 확인한 값",
                    value=str(check.get("reviewed_value") or ""),
                    key=f"value-{check_id}",
                    placeholder=CHECK_HELP[check_id],
                )
                note = st.text_input(
                    "불확실/해당 없음 사유 또는 짧은 메모",
                    value=str(check.get("note") or ""),
                    key=f"note-{check_id}",
                )
                check_values[check_id] = (status, reviewed_value, note)
                st.divider()

        st.subheader("사람이 확인한 구조화 결과")
        st.caption("한 줄에 한 항목씩 입력하세요. 빈 값은 AI가 채우지 않습니다.")
        struct_cols = st.columns(2)
        with struct_cols[0]:
            nodes = st.text_area("Nodes", _joined(structured["nodes"]))
            edges = st.text_area("Edges (예: A -> B)", _joined(structured["edges"]))
            branches = st.text_area("Branches", _joined(structured["branches"]))
            sequence = st.text_area("Workflow sequence", _joined(structured["sequence"]))
        with struct_cols[1]:
            numbers = st.text_area("Numbers", _joined(structured["numbers"]))
            units = st.text_area("Units", _joined(structured["units"]))
            times = st.text_area("Times", _joined(structured["times"]))
            caption_relation = st.text_area(
                "Caption / nearby text 관계", structured["caption_relation"]
            )
            uncertainties = st.text_area(
                "남은 불확실성", _joined(structured["uncertainties"])
            )

        st.subheader("Reviewer와 승인")
        reviewer_cols = st.columns(2)
        with reviewer_cols[0]:
            reviewer_1 = st.text_input(
                "1차 reviewer 로컬 식별자",
                reviewers["reviewer_1"],
                placeholder="reviewer_1",
            )
            reviewer_1_approved = st.checkbox(
                "1차 reviewer 승인",
                reviewers["reviewer_1_approved"],
                help="실제로 모든 항목을 확인한 사람만 체크하세요.",
            )
        with reviewer_cols[1]:
            reviewer_2 = st.text_input(
                "2차 reviewer 로컬 식별자",
                reviewers["reviewer_2"],
                placeholder="reviewer_2",
            )
            reviewer_2_approved = st.checkbox(
                "2차 reviewer 승인",
                reviewers["reviewer_2_approved"],
                help="TF027 production Gold에는 독립적인 2차 확인을 요구합니다.",
            )
        approval_decision = st.radio(
            "최종 판단",
            list(DECISION_LABELS),
            index=list(DECISION_LABELS).index(review["approval_decision"]),
            format_func=lambda value: DECISION_LABELS[value],
            horizontal=True,
        )
        submitted = st.form_submit_button("TF027 검수 결과 별도 저장", type="primary")

    if submitted:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        updated = copy.deepcopy(review)
        for check in updated["checks"]:
            status, reviewed_value, note = check_values[str(check["check_id"])]
            check["status"] = status
            check["reviewed_value"] = reviewed_value.strip() or None
            check["note"] = note.strip() or None
        updated["structured_result"] = {
            "selected_figure_ids": selected_figure_ids,
            "nodes": _lines(nodes),
            "edges": _lines(edges),
            "branches": _lines(branches),
            "sequence": _lines(sequence),
            "numbers": _lines(numbers),
            "units": _lines(units),
            "times": _lines(times),
            "caption_relation": caption_relation.strip(),
            "uncertainties": _lines(uncertainties),
        }
        updated["reviewers"] = {
            "reviewer_1": reviewer_1.strip(),
            "reviewer_1_reviewed_at": (
                reviewers["reviewer_1_reviewed_at"] or now
                if reviewer_1_approved
                else ""
            ),
            "reviewer_1_approved": reviewer_1_approved,
            "second_review_required": True,
            "reviewer_2": reviewer_2.strip(),
            "reviewer_2_reviewed_at": (
                reviewers["reviewer_2_reviewed_at"] or now
                if reviewer_2_approved
                else ""
            ),
            "reviewer_2_approved": reviewer_2_approved,
        }
        updated["approval_decision"] = approval_decision
        try:
            normalized = normalize_review_state(updated)
            saved = save_reviewed_fixture(
                reviewed_path,
                normalized,
                protected_paths=(checklist_path,),
            )
        except (OSError, ValueError) as exc:
            st.error(f"저장하지 못했습니다: {exc}")
        else:
            if saved["production_gold_approved"]:
                st.success("두 사람의 확인과 모든 안전 조건을 충족해 image Gold로 승인됐습니다.")
            else:
                st.warning(
                    "검수 내용은 별도 파일에 저장했지만 Production Gold는 승인되지 않았습니다. "
                    f"남은 차단 사유: {', '.join(saved['approval_blockers'])}"
                )
            st.rerun()

    if review["approval_blockers"]:
        with st.expander("현재 Production Gold 승인 차단 사유"):
            for blocker in review["approval_blockers"]:
                st.write(f"- {blocker}")
    st.info(
        f"저장 위치: {reviewed_path}\n\n"
        "원본 checklist와 PDF는 덮어쓰지 않으며, reviewed 파일에는 이미지 픽셀이나 원문을 저장하지 않습니다."
    )


if __name__ == "__main__":
    main()
