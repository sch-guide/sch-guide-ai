"""Offline-only review and dataset expansion helpers for SCHAT stabilization."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Sequence

from tools.chroma_baseline_evaluate import CatalogChunk

_STRUCTURAL_TABLE_CASES = {
    "TF003": ("page_2_product_header_row_column", "verified"),
    "TF004": ("page_2_product_header_row_column", "verified"),
    "TF005": ("page_2_product_header_row_column", "verified"),
    "TF006": ("page_2_product_header_row_column", "verified"),
    "TF007": ("page_2_product_header_row_column", "verified"),
    "TF023": ("page_9_test_header_row_column", "verified"),
    "TF024": ("page_9_test_header_row_column", "verified"),
    "TF037": ("page_15_reaction_row_columns", "not_applicable"),
    "TF038": ("page_15_reaction_row_columns", "not_applicable"),
    "TF039": ("page_15_reaction_row_columns", "not_applicable"),
    "TF040": ("page_15_reaction_row_columns", "not_applicable"),
    "TF041": ("page_15_reaction_row_columns", "not_applicable"),
}

REVIEW_DECISIONS: dict[str, dict[str, Any]] = {
    case_id: {
        "status": "approved_structural_table",
        "relationship": relationship,
        "number_unit_status": number_status,
        "image_interpretation_required": False,
        "evidence_basis": "rendered_page_and_catalog_fingerprint",
    }
    for case_id, (relationship, number_status) in _STRUCTURAL_TABLE_CASES.items()
}
REVIEW_DECISIONS.update(
    {
        "TF027": {
            "status": "needs_human_review",
            "relationship": "workflow_screenshot_sequence_requires_interpretation",
            "number_unit_status": "not_applicable",
            "image_interpretation_required": True,
            "evidence_basis": "rendered_page_and_catalog_fingerprint",
            "review_reason": "image_sequence_interpretation",
        },
        "TF035": {
            "status": "approved_nearby_text",
            "relationship": "nearby_text_fully_states_procedure",
            "number_unit_status": "not_applicable",
            "image_interpretation_required": False,
            "evidence_basis": "rendered_page_and_catalog_fingerprint",
        },
    }
)


# Evaluation fixture content only. These questions never enter production query rules.
EXPANDED_CASE_SPECS: tuple[dict[str, Any], ...] = (
    {"question_id": "TP001", "question": "PRBC는 언제 쓰고 한 단위 용량은 어떻게 돼?", "variant_of": "TF003", "axes": ["abbreviation", "colloquial", "table"]},
    {"question_id": "TP002", "question": "PC는 수령하고 몇 분 안에 다 맞혀야 해?", "variant_of": "TF004", "axes": ["abbreviation", "colloquial", "time", "table"]},
    {"question_id": "TP003", "question": "FFP 녹인 다음 사용 가능한 시간 알려줘", "variant_of": "TF005", "axes": ["abbreviation", "time", "table"]},
    {"question_id": "TP004", "question": "CRYO 해동 뒤 언제까지 쓰고 몇 분 동안 주입해?", "variant_of": "TF006", "axes": ["abbreviation", "time", "table"]},
    {"question_id": "TP005", "question": "수혈 첫 15분은 몇 mL/hr로 시작해?", "variant_of": "TF007", "axes": ["unit", "colloquial", "table"]},
    {"question_id": "TP006", "question": "저장 전에 백혈구를 거른 적혈구가 뭐야?", "variant_of": "TF008", "axes": ["colloquial", "product"]},
    {"question_id": "TP007", "question": "백혈구 제거 혈액을 쓰면 뭐가 좋아?", "variant_of": "TF009", "axes": ["colloquial", "product"]},
    {"question_id": "TP008", "question": "보관 전 필터랑 보관 후 필터 차이를 알려줘", "variant_of": "TF010", "axes": ["comparison", "colloquial"]},
    {"question_id": "TP009", "question": "혈액에 방사선 조사는 왜 하는 거야?", "variant_of": "TF012", "axes": ["purpose", "colloquial"]},
    {"question_id": "TP010", "question": "어떤 혈액제제에 방사선 조사가 필요해?", "variant_of": "TF013", "axes": ["product", "natural_language"]},
    {"question_id": "TP011", "question": "irradiated blood 유효기간 알려줘", "variant_of": "TF014", "axes": ["english_term", "time"]},
    {"question_id": "TP012", "question": "FFP도 방사선 조사해서 써야 하나?", "variant_of": "TF015", "axes": ["abbreviation", "product"]},
    {"question_id": "TP013", "question": "수혈 업무 흐름을 처음부터 끝까지 설명해줘", "variant_of": "TF017", "axes": ["broad_procedure", "natural_language"]},
    {"question_id": "TP014", "question": "수혈 동의는 어느 시점에 받아?", "variant_of": "TF018", "axes": ["colloquial", "time"]},
    {"question_id": "TP015", "question": "여러 제제를 이어서 맞힐 때 동의서는 몇 장 받아?", "variant_of": "TF019", "axes": ["colloquial", "follow_up"]},
    {"question_id": "TP016", "question": "응급실에서 받은 수혈동의서 효력은 언제까지야?", "variant_of": "TF020", "axes": ["colloquial", "time"]},
    {"question_id": "TP017", "question": "수혈 전에 검사를 왜 하는지 알려줘", "variant_of": "TF021", "axes": ["purpose", "preparation"]},
    {"question_id": "TP018", "question": "채혈 전 환자 확인은 두 사람이 어떻게 해야 해?", "variant_of": "TF022", "axes": ["procedure", "preparation"]},
    {"question_id": "TP019", "question": "antibody screening 결과는 며칠까지 유효해?", "variant_of": "TF023", "axes": ["english_term", "time", "table"]},
    {"question_id": "TP020", "question": "cross matching은 매 수혈마다 해야 해?", "variant_of": "TF024", "axes": ["english_term", "frequency", "table"]},
    {"question_id": "TP021", "question": "비예기항체 screen positive면 다음 검사는 뭐야?", "variant_of": "TF025", "axes": ["mixed_language", "follow_up"]},
    {"question_id": "TP022", "question": "Rh negative 결과는 어디서 확인해?", "variant_of": "TF026", "axes": ["mixed_language", "colloquial"]},
    {"question_id": "TP023", "question": "혈액 받아올 때 확인할 항목이 뭐야?", "variant_of": "TF028", "axes": ["colloquial", "preparation"]},
    {"question_id": "TP024", "question": "bedside에서 혈액과 환자 확인 절차 알려줘", "variant_of": "TF029", "axes": ["mixed_language", "procedure"]},
    {"question_id": "TP025", "question": "PRBC 투여 전 활력징후는 언제 재?", "variant_of": "TF030", "axes": ["abbreviation", "time", "monitoring"]},
    {"question_id": "TP026", "question": "수혈세트 연결하고 환자에게 설명하는 순서 알려줘", "variant_of": "TF031", "axes": ["procedure", "natural_language"]},
    {"question_id": "TP027", "question": "4세 미만 아이 수혈 라인은 뭘 써야 해?", "variant_of": "TF032", "axes": ["colloquial", "age_qualifier"]},
    {"question_id": "TP028", "question": "일반 blood filter랑 leukocyte filter 목적 차이가 뭐야?", "variant_of": "TF033", "axes": ["mixed_language", "comparison"]},
    {"question_id": "TP029", "question": "leukocyte filter 사용할 때 조심할 점 알려줘", "variant_of": "TF034", "axes": ["english_term", "caution"]},
    {"question_id": "TP030", "question": "혈소판 채집백으로 PC 투여는 어떻게 해?", "variant_of": "TF035", "axes": ["abbreviation", "colloquial", "nearby_text"]},
    {"question_id": "TP031", "question": "수혈 중 V/S는 언제 체크해?", "variant_of": "TF036", "axes": ["abbreviation", "monitoring", "time"]},
    {"question_id": "TP032", "question": "급성 용혈반응이 보이면 증상과 간호는 어떻게 해?", "variant_of": "TF037", "axes": ["adverse_event", "colloquial", "table"]},
    {"question_id": "TP033", "question": "오한하고 열나는 비용혈성 반응 처치는?", "variant_of": "TF038", "axes": ["symptom_led", "adverse_event", "table"]},
    {"question_id": "TP034", "question": "두드러기 같은 알레르기 수혈반응은 어떻게 조치해?", "variant_of": "TF039", "axes": ["symptom_led", "adverse_event", "table"]},
    {"question_id": "TN006", "question": "인슐린 주입 속도는 어떻게 계산해?", "negative": True, "axes": ["clinical_out_of_scope"]},
    {"question_id": "TN007", "question": "항암제 희석 방법 알려줘", "negative": True, "axes": ["clinical_out_of_scope"]},
    {"question_id": "TN008", "question": "화성 탐사선 궤도 공식 알려줘", "negative": True, "axes": ["non_clinical"]},
    {"question_id": "TN009", "question": "오늘 원달러 환율이 얼마야?", "negative": True, "axes": ["non_clinical"]},
    {"question_id": "TN010", "question": "병원 주차 요금과 할인 방법 알려줘", "negative": True, "axes": ["hospital_non_guideline"]},
    {"question_id": "TN011", "question": "심전도 부정맥 판독 순서 알려줘", "negative": True, "axes": ["clinical_out_of_scope"]},
)


def _review_audit(case: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    references = case["reference_contexts"]
    return {
        "status": decision["status"],
        "page_numbers": sorted({reference["page"] for reference in references}),
        "source_shapes": sorted({reference["source_shape"] for reference in references}),
        "reference_context_ids": list(case["reference_context_ids"]),
        "reference_sha256": [reference["text_sha256"] for reference in references],
        "relationship": decision["relationship"],
        "number_unit_status": decision["number_unit_status"],
        "image_interpretation_required": decision["image_interpretation_required"],
        "evidence_basis": decision["evidence_basis"],
        "raw_source_text_stored": False,
    }


def apply_review_decisions(
    cases: list[dict[str, Any]], decisions: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Apply auditable, evaluation-only review decisions without changing gold IDs."""
    by_id = {case["question_id"]: case for case in cases}
    if not set(decisions).issubset(by_id):
        raise ValueError("review decision coverage drift")
    if all(
        by_id[case_id].get("metadata", {}).get("reviewed_in")
        == "schat-mvp-stabilization-v1"
        for case_id in decisions
    ):
        return cases
    if set(decisions) != {case["question_id"] for case in cases if case["needs_human_review"]}:
        raise ValueError("review decision coverage drift")
    for case_id, decision in decisions.items():
        case = by_id[case_id]
        status = decision["status"]
        case["review_status"] = status
        case["review_audit"] = _review_audit(case, decision)
        case["metadata"] = {**case.get("metadata", {}), "reviewed_in": "schat-mvp-stabilization-v1"}
        if status == "needs_human_review":
            case["needs_human_review"] = True
            case["review_reasons"] = [decision["review_reason"]]
        else:
            case["needs_human_review"] = False
            case["review_reasons"] = []
    return cases


def validate_review_audit(
    cases: Sequence[dict[str, Any]], chunks: Sequence[CatalogChunk]
) -> dict[str, int]:
    chunk_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    baseline_reviewed = [
        case
        for case in cases
        if case.get("metadata", {}).get("reviewed_in") == "schat-mvp-stabilization-v1"
        and case.get("metadata", {}).get("gold_source") == "manual_page_review_v1"
    ]
    if {case["question_id"] for case in baseline_reviewed} != set(REVIEW_DECISIONS):
        raise ValueError("review audit case coverage drift")

    counts = {
        "reviewed_case_count": len(baseline_reviewed),
        "auto_approved_case_count": 0,
        "remaining_human_review_case_count": 0,
        "approved_structural_table_count": 0,
        "approved_nearby_text_count": 0,
    }
    required_audit_fields = {
        "status",
        "page_numbers",
        "source_shapes",
        "reference_context_ids",
        "reference_sha256",
        "relationship",
        "number_unit_status",
        "image_interpretation_required",
        "evidence_basis",
        "raw_source_text_stored",
    }
    for case in baseline_reviewed:
        status = case.get("review_status")
        audit = case.get("review_audit")
        if not isinstance(audit, dict) or set(audit) != required_audit_fields:
            raise ValueError(f"{case['question_id']}: review audit incomplete")
        if status != audit["status"]:
            if status != "needs_human_review":
                raise ValueError(f"{case['question_id']}: opaque auto-approval")
            raise ValueError(f"{case['question_id']}: review audit status drift")
        references = case["reference_contexts"]
        expected_pages = sorted({reference["page"] for reference in references})
        expected_shapes = sorted({reference["source_shape"] for reference in references})
        expected_hashes = [reference["text_sha256"] for reference in references]
        if (
            audit["page_numbers"] != expected_pages
            or audit["source_shapes"] != expected_shapes
            or audit["reference_context_ids"] != case["reference_context_ids"]
            or audit["reference_sha256"] != expected_hashes
            or audit["raw_source_text_stored"] is not False
            or not isinstance(audit["relationship"], str)
            or not audit["relationship"]
        ):
            raise ValueError(f"{case['question_id']}: review audit drift")
        for reference in references:
            chunk = chunk_by_id.get(reference["chunk_id"])
            if chunk is None or chunk.page != reference["page"]:
                raise ValueError(f"{case['question_id']}: review audit catalog drift")

        if status == "needs_human_review":
            if not case["needs_human_review"] or not audit["image_interpretation_required"]:
                raise ValueError(f"{case['question_id']}: pending review contract drift")
            counts["remaining_human_review_case_count"] += 1
        elif status == "approved_structural_table":
            if case["needs_human_review"] or expected_shapes != ["table"]:
                raise ValueError(f"{case['question_id']}: opaque auto-approval")
            counts["auto_approved_case_count"] += 1
            counts["approved_structural_table_count"] += 1
        elif status == "approved_nearby_text":
            if (
                case["needs_human_review"]
                or expected_shapes != ["image_plus_text"]
                or audit["image_interpretation_required"]
            ):
                raise ValueError(f"{case['question_id']}: opaque auto-approval")
            counts["auto_approved_case_count"] += 1
            counts["approved_nearby_text_count"] += 1
        else:
            raise ValueError(f"{case['question_id']}: unsupported review status")
    return counts


def _variant_case(spec: dict[str, Any], base_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if spec.get("negative"):
        return {
            "question_id": spec["question_id"],
            "question": spec["question"],
            "question_type": "negative_out_of_scope",
            "expected_answerable": False,
            "reference_context_ids": [],
            "reference_parent_ids": [],
            "reference_contexts": [],
            "needs_human_review": False,
            "review_reasons": [],
            "metadata": {
                "gold_source": "negative_diagnostic_v3",
                "variation_axes": list(spec["axes"]),
            },
        }
    source = copy.deepcopy(base_by_id[spec["variant_of"]])
    if source["needs_human_review"]:
        raise ValueError(f"expanded variant source is not approved: {spec['variant_of']}")
    source["question_id"] = spec["question_id"]
    source["question"] = spec["question"]
    source["metadata"] = {
        "gold_source": "curated_paraphrase_v3",
        "variant_of": spec["variant_of"],
        "variation_axes": list(spec["axes"]),
    }
    return source


def build_expanded_fixture(base_fixture: dict[str, Any]) -> dict[str, Any]:
    fixture = copy.deepcopy(base_fixture)
    if fixture.get("dataset_version") == "transfusion-retrieval-v3":
        expected_ids = {spec["question_id"] for spec in EXPANDED_CASE_SPECS}
        actual_ids = {case["question_id"] for case in fixture.get("cases", [])}
        if len(fixture.get("cases", [])) != 90 or not expected_ids.issubset(actual_ids):
            raise ValueError("expanded v3 fixture drift")
        return fixture
    if fixture.get("dataset_version") != "transfusion-retrieval-v2" or len(fixture["cases"]) != 50:
        raise ValueError("expected the frozen v2 baseline")
    fixture["cases"] = apply_review_decisions(fixture["cases"], REVIEW_DECISIONS)
    base_by_id = {case["question_id"]: case for case in fixture["cases"]}
    variants = [_variant_case(spec, base_by_id) for spec in EXPANDED_CASE_SPECS]
    fixture["cases"].extend(variants)
    fixture["schema_version"] = 3
    fixture["dataset_version"] = "transfusion-retrieval-v3"
    fixture["gold_policy"] = {
        **fixture["gold_policy"],
        "approved_aggregate": "needs_human_review_false_only_after_audited_review",
        "structural_table_approval": "rendered_page_plus_catalog_fingerprint",
        "image_interpretation": "never_auto_approved",
        "expanded_variants": "independently_authored_semantic_variants_of_approved_gold",
    }
    return fixture


def split_reusable_chroma_cases(
    cases: Sequence[dict[str, Any]], prior_results_path: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    payload = json.loads(prior_results_path.read_text(encoding="utf-8"))
    prior_by_id = {row.get("case_id"): row for row in payload.get("cases", [])}
    if not prior_by_id:
        raise ValueError("prior Chroma results are empty")
    current_by_id = {case["question_id"]: case for case in cases}
    reusable: list[dict[str, Any]] = []
    for case_id, prior in prior_by_id.items():
        case = current_by_id.get(case_id)
        if case is None or any(
            (
                prior.get("question") != case["question"],
                prior.get("reference_context_ids") != case["reference_context_ids"],
            )
        ):
            raise ValueError(f"prior Chroma case drift: {case_id}")
        reusable.append(case)
    reusable_ids = {case["question_id"] for case in reusable}
    pending = [case for case in cases if case["question_id"] not in reusable_ids]
    return reusable, pending


def write_expanded_fixture(source: Path, target: Path) -> None:
    base = json.loads(source.read_text(encoding="utf-8"))
    expanded = build_expanded_fixture(base)
    target.write_text(json.dumps(expanded, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
