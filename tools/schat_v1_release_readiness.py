"""Local-only SCHAT v1 release-readiness gate and review manifest.

This module never invokes a provider or vision service. It prepares identifiers,
counts, stages, and human stop states while leaving production retrieval and
clinical validation untouched.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tools.schat_gold_human_review import validate_review_fixture
from tools.tf027_human_review import validate_reviewed_fixture

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REVIEWED_GOLD = (
    ROOT / "tests/fixtures/schat_v1_operational_gold_reviewed.json"
)
DEFAULT_OPERATIONAL_GOLD = ROOT / "tests/fixtures/schat_v1_operational_gold.json"
DEFAULT_UAT = ROOT / "tests/fixtures/schat_v1_operational_uat.json"
DEFAULT_TF027 = ROOT / "tests/fixtures/tf027_image_human_review_checklist.json"
DEFAULT_TF027_REVIEWED = (
    ROOT / "tests/fixtures/tf027_image_human_review_reviewed.json"
)
DEFAULT_DEFERRED = (
    ROOT
    / "workspace/과거작업/평가산출물/2026-09-18_schat-v1-final-stabilization/deferred_analysis.json"
)
DEFAULT_CATALOG = ROOT / "data/library/catalog.sqlite3"
DEFAULT_OUTPUT = ROOT / "workspace/과거작업/평가산출물/2026-09-18_schat-v1-final-release-readiness"

_APPROVAL_FIELDS = (
    "hospital_evidence_external_transfer",
    "provider",
    "model",
    "region",
    "retention_logging",
    "case_scope",
    "credential_reference",
)
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE_RE = re.compile(r"(?<!\d)01[016789]-?\d{3,4}-?\d{4}(?!\d)")
_RRN_RE = re.compile(r"(?<!\d)\d{6}-[1-4]\d{6}(?!\d)")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _write_json(path: Path, value: Mapping[str, Any] | Sequence[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def detect_sensitive_identifier_types(texts: Sequence[str]) -> list[str]:
    """Return local structural PII categories without returning matched values."""
    joined = "\n".join(texts)
    found = []
    for name, pattern in (
        ("email", _EMAIL_RE),
        ("phone", _PHONE_RE),
        ("resident_registration_number", _RRN_RE),
    ):
        if pattern.search(joined):
            found.append(name)
    return found


def build_tf027_readiness(
    source: Mapping[str, Any], *, reviewed: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Summarize TF027 without creating or approving human review data."""
    if source.get("case_id") != "TF027":
        raise ValueError("invalid TF027 source checklist")
    checks = source.get("checks")
    source_reference = source.get("source_reference")
    if not isinstance(checks, list) or len(checks) != 10:
        raise ValueError("TF027 requires ten source checks")
    if not isinstance(source_reference, Mapping):
        raise ValueError("TF027 source reference missing")

    if reviewed is None:
        reviewed_count = 0
        approved = False
        excluded_by_human = False
        needs_review = True
        blockers = ["reviewed_fixture_missing", "ten_human_checks_incomplete"]
    else:
        validate_reviewed_fixture(reviewed)
        reviewed_count = sum(
            str(check.get("status")) != "unreviewed"
            for check in reviewed["checks"]
        )
        approved = reviewed.get("production_gold_approved") is True
        excluded_by_human = (
            reviewed_count == 10
            and reviewed.get("review_status") == "rejected"
            and reviewed.get("approval_decision") == "reject"
            and reviewed.get("reviewers", {}).get("reviewer_1_approved") is True
            and approved is False
            and reviewed.get("included_in_aggregate") is False
        )
        needs_review = reviewed.get("needs_human_review") is True
        blockers = list(reviewed.get("approval_blockers", []))

    release_blocker_closed = approved or excluded_by_human
    if approved:
        status = "READY_IMAGE_GOLD_APPROVED"
    elif excluded_by_human:
        status = "READY_IMAGE_EXCLUDED_BY_HUMAN_DECISION"
    else:
        status = "STOP_FOR_TF027_HUMAN_REVIEW"

    candidates = [
        {
            "figure_id": str(candidate["figure_id"]),
            "bbox": [float(value) for value in candidate["bbox"]],
            "fingerprint": str(candidate["fingerprint"]),
        }
        for candidate in source_reference["figure_candidates"]
    ]
    return {
        "case_id": "TF027",
        "status": status,
        "needs_human_review": needs_review,
        "human_review_completed": reviewed_count == 10,
        "production_gold_approved": approved,
        "included_in_aggregate": approved,
        "image_excluded_by_human_decision": excluded_by_human,
        "release_blocker_closed": release_blocker_closed,
        "reviewed_check_count": reviewed_count,
        "remaining_check_count": 10 - reviewed_count,
        "page": int(source_reference["page"]),
        "figure_candidates": candidates,
        "nearby_chunk_ids": list(source_reference.get("nearby_chunk_ids", [])),
        "nearby_chunk_sha256": list(source_reference.get("nearby_chunk_sha256", [])),
        "node_candidates": "human_confirmation_required",
        "edge_candidates": "human_confirmation_required",
        "workflow_sequence": "human_confirmation_required",
        "approval_blockers": blockers,
        "release_blockers": [] if release_blocker_closed else blockers,
        "review_app_command": (
            r".\.venv\Scripts\streamlit.exe run tools\tf027_human_review_app.py"
        ),
        "actual_vision_calls": 0,
        "automatic_image_interpretation": False,
    }


def _approved_case_ids(reviewed: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    validate_review_fixture(reviewed)
    approved = []
    deferred = []
    for case in reviewed["cases"]:
        target = approved if case.get("final_gold_approved") is True else deferred
        target.append(str(case["case_id"]))
    return approved, deferred


def _approved_abstention_ids(operational_gold: Mapping[str, Any]) -> list[str]:
    return [
        str(case["case_id"])
        for case in operational_gold["cases"]
        if case.get("label_status") == "approved"
        and case.get("expected_abstain") is True
    ]


def _representative_subset(
    approved_ids: Sequence[str], uat: Mapping[str, Any], *, limit: int = 5
) -> list[str]:
    approved = set(approved_ids)
    cases = [case for case in uat["cases"] if str(case["case_id"]) in approved]
    selected: list[str] = []
    seen_groups: set[tuple[str, str, str]] = set()
    for case in cases:
        group = (
            str(case.get("document_scope", "")),
            str(case.get("expected_evidence_type", "")),
            str(case.get("question_type", "")),
        )
        case_id = str(case["case_id"])
        if group not in seen_groups:
            selected.append(case_id)
            seen_groups.add(group)
        if len(selected) == limit:
            return selected
    for case_id in approved_ids:
        if case_id not in selected:
            selected.append(case_id)
        if len(selected) == limit:
            break
    return selected


def _approval_missing_fields(approval: Mapping[str, Any] | None) -> list[str]:
    if approval is None:
        return list(_APPROVAL_FIELDS)
    missing = []
    for field in _APPROVAL_FIELDS:
        value = approval.get(field)
        if field == "hospital_evidence_external_transfer":
            valid = value is True
        elif field == "case_scope":
            valid = isinstance(value, list) and bool(value)
        else:
            valid = isinstance(value, str) and bool(value.strip())
        if not valid:
            missing.append(field)
    return missing


def build_live_ragas_plan(
    *,
    reviewed: Mapping[str, Any],
    operational_gold: Mapping[str, Any],
    uat: Mapping[str, Any],
    external_transfer_approval: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build a staged, metadata-only plan; never perform a live call."""
    approved_ids, deferred_ids = _approved_case_ids(reviewed)
    abstention_ids = _approved_abstention_ids(operational_gold)
    uat_ids = {str(case["case_id"]) for case in uat["cases"]}
    if not set((*approved_ids, *deferred_ids, *abstention_ids)).issubset(uat_ids):
        raise ValueError("Gold/UAT case identity drift")
    missing = _approval_missing_fields(external_transfer_approval)
    approved_scope = list(approved_ids)
    if external_transfer_approval is not None:
        requested_scope = external_transfer_approval.get("case_scope")
        if isinstance(requested_scope, list) and requested_scope:
            requested_ids = [str(case_id) for case_id in requested_scope]
            if not set(requested_ids).issubset(approved_ids):
                missing.append("case_scope_not_subset_of_approved_positive")
            approved_scope = [
                case_id for case_id in approved_ids if case_id in requested_ids
            ]
    blocked = bool(missing)
    minimal = _representative_subset(approved_scope, uat)
    metrics = [
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "context_recall",
        "gold_critical_fact_preservation",
        "citation_correctness",
        "number_preservation",
        "unit_preservation",
        "time_preservation",
        "condition_preservation",
        "negation_preservation",
        "action_preservation",
        "branch_preservation",
        "fallback_correctness",
        "latency",
    ]
    return {
        "status": (
            "BLOCKED_BY_EXTERNAL_TRANSFER_APPROVAL"
            if blocked
            else "READY_FOR_HUMAN_INITIATED_LIVE_RUN"
        ),
        "approved_positive_count": len(approved_ids),
        "deferred_count": len(deferred_ids),
        "approved_abstention_count": len(abstention_ids),
        "approved_positive_case_ids": approved_ids,
        "deferred_case_ids": deferred_ids,
        "approved_abstention_case_ids": abstention_ids,
        "missing_approval_fields": missing,
        "transmitted_fields": [
            "request_local_source_unit_id",
            "selected_exact_source_unit_text",
            "normalized_intent",
            "output_json_schema",
            "safety_instruction",
        ],
        "excluded_fields": [
            "original_user_question",
            "document_name",
            "document_id",
            "page",
            "section",
            "production_chunk_id",
            "conversation_history",
            "patient_or_staff_identifier",
            "whole_document",
            "table_or_figure_binary",
            "credential_secret",
        ],
        "preflight": {
            "local_identifier_scan_required": True,
            "structural_identifier_categories": [
                "email",
                "phone",
                "resident_registration_number",
            ],
            "free_form_name_review": "human_review_required",
            "raw_prompt_persisted": False,
            "raw_response_persisted": False,
            "hospital_source_artifact_persisted": False,
            "stop_on_any_critical_safety_error": True,
        },
        "providers": ["groq", "gemini"],
        "temperature": 0,
        "same_source_units": True,
        "same_schema": True,
        "same_prompt_contract": True,
        "metrics": metrics,
        "stages": [
            {
                "stage": "synthetic",
                "case_ids": [],
                "enabled_now": True,
                "requires_external_transfer": False,
                "stop_on_critical_failure": True,
            },
            {
                "stage": "approved_real_minimal",
                "case_ids": minimal,
                "enabled_now": not blocked,
                "requires_external_transfer": True,
                "stop_on_critical_failure": True,
            },
            {
                "stage": "approved_real_expanded",
                "case_ids": approved_scope,
                "enabled_now": not blocked,
                "requires_external_transfer": True,
                "stop_on_critical_failure": True,
            },
        ],
        "safety_set_case_ids": abstention_ids,
        "actual_provider_calls": 0,
        "actual_llm_judge_calls": 0,
        "network_transport_in_this_tool": False,
    }


def build_deferred_queue(
    *,
    reviewed: Mapping[str, Any],
    deferred_analysis: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep the human-deferred set immutable and metadata-only."""
    _, deferred_ids = _approved_case_ids(reviewed)
    rows_by_id = {
        str(row["case_id"]): row for row in deferred_analysis.get("rows", [])
    }
    if set(rows_by_id) != set(deferred_ids):
        raise ValueError("deferred analysis/review identity drift")
    rows = [
        {
            "case_id": case_id,
            "failure_category": str(rows_by_id[case_id]["failure_category"]),
            "improvement_layer": str(rows_by_id[case_id]["improvement_layer"]),
            "chunk_candidate_count": int(
                rows_by_id[case_id]["chunk_candidate_count"]
            ),
            "table_candidate_count": int(
                rows_by_id[case_id]["table_candidate_count"]
            ),
            "re_review_candidate": True,
            "requires_human_review": True,
            "final_gold_approved": False,
        }
        for case_id in deferred_ids
    ]
    return {
        "case_count": len(rows),
        "category_counts": dict(
            sorted(Counter(row["failure_category"] for row in rows).items())
        ),
        "automatically_approved_count": 0,
        "remaining_human_review_count": len(rows),
        "rows": rows,
    }


def decide_release_readiness(
    *,
    core_regression_passed: bool,
    tf027_production_gold_approved: bool,
    live_ragas_completed: bool,
    image_excluded_by_human_decision: bool,
    external_provider_disabled_by_human_decision: bool,
) -> dict[str, Any]:
    """Return release status while allowing only explicit human scope decisions."""
    image_closed = tf027_production_gold_approved or image_excluded_by_human_decision
    live_closed = live_ragas_completed or external_provider_disabled_by_human_decision
    blockers = []
    if not image_closed:
        blockers.append("TF027_HUMAN_REVIEW_OR_EXPLICIT_V1_SCOPE_EXCLUSION")
    if not live_closed:
        blockers.append("LIVE_RAGAS_OR_EXPLICIT_NO_EXTERNAL_PROVIDER_DECISION")
    if not core_regression_passed:
        status = "NOT_READY"
        blockers.insert(0, "CORE_REGRESSION")
    elif not image_closed and not live_closed:
        status = "READY_EXCEPT_IMAGE_AND_LIVE_RAGAS"
    elif not image_closed:
        status = "READY_EXCEPT_IMAGE_HUMAN_REVIEW"
    elif not live_closed:
        status = "READY_EXCEPT_LIVE_RAGAS"
    else:
        status = "READY"
    return {
        "status": status,
        "blocking_items": blockers,
        "human_scope_decision_required": bool(blockers),
    }


def _chromadb_roadmap() -> dict[str, Any]:
    return {
        "production_changed": False,
        "production_vector_database": "not_integrated",
        "definitions": {
            "bm25": "lexical_retrieval",
            "minilm": "embedding_model",
            "e5_large": "evaluation_only_embedding_model",
            "chromadb": "evaluation_vector_store_and_search_infrastructure",
            "qdrant": "future_production_vector_database_candidate",
        },
        "future_comparison": [
            "bm25_current",
            "chroma_minilm",
            "chroma_e5_large",
            "hybrid",
        ],
        "comparison_contract": "same_approved_gold_same_chunks_same_top_k_same_metrics",
    }


def _review_html(report: Mapping[str, Any]) -> str:
    tf027 = report["tf027"]
    live = report["live_ragas"]
    deferred = report["deferred"]
    readiness = report["readiness"]
    return f"""<!doctype html><html lang="ko"><meta charset="utf-8">
<title>SCHAT v1 최종 release readiness</title><style>
body{{font-family:system-ui;margin:36px;max-width:1080px;line-height:1.55}}
.stop{{color:#a33b00}}table{{border-collapse:collapse}}td,th{{border:1px solid #ccd;padding:8px}}
</style><body><h1>SCHAT v1 최종 release readiness</h1>
<p>판정: <strong>{html.escape(str(readiness['status']))}</strong></p>
<h2>Gold</h2><p>승인 positive {live['approved_positive_count']} · deferred
{deferred['case_count']} · 승인 abstention {live['approved_abstention_count']}</p>
<h2>TF027</h2><p class="stop">{html.escape(str(tf027['status']))} · 확인
{tf027['reviewed_check_count']}/10 · vision 호출 0회</p>
<h2>Live RAGAS</h2><p class="stop">{html.escape(str(live['status']))} · provider/LLM judge 호출 0회</p>
<h2>Production</h2><p>BM25 + MiniLM semantic/vector + RRF + existing reranker 유지 ·
ChromaDB/Qdrant production 미통합</p>
<h2>사람이 해야 할 일</h2><ol><li>TF027 local review app에서 원본 도식을 검수</li>
<li>외부 전송 정책 또는 외부 provider 미사용 결정을 명시</li></ol></body></html>"""


def _catalog_source_texts(catalog_path: Path) -> list[str]:
    resolved = catalog_path.resolve()
    with sqlite3.connect(
        resolved.as_uri() + "?mode=ro&immutable=1", uri=True
    ) as connection:
        payloads = [
            json.loads(str(row[0]))
            for row in connection.execute("select payload from chunks")
        ]
    return [str(payload["text"]) for payload in payloads]


def run_release_readiness(
    *,
    reviewed_gold_path: Path = DEFAULT_REVIEWED_GOLD,
    operational_gold_path: Path = DEFAULT_OPERATIONAL_GOLD,
    uat_path: Path = DEFAULT_UAT,
    tf027_path: Path = DEFAULT_TF027,
    tf027_reviewed_path: Path = DEFAULT_TF027_REVIEWED,
    deferred_path: Path = DEFAULT_DEFERRED,
    catalog_path: Path = DEFAULT_CATALOG,
    output_dir: Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    """Write the final local-only readiness package with zero external calls."""
    from tools.schat_v1_final_validate import audit_artifacts

    reviewed = _read_json(reviewed_gold_path)
    operational_gold = _read_json(operational_gold_path)
    uat = _read_json(uat_path)
    tf027_source = _read_json(tf027_path)
    tf027_reviewed = (
        _read_json(tf027_reviewed_path) if tf027_reviewed_path.is_file() else None
    )
    deferred_analysis = _read_json(deferred_path)

    tf027 = build_tf027_readiness(tf027_source, reviewed=tf027_reviewed)
    live = build_live_ragas_plan(
        reviewed=reviewed,
        operational_gold=operational_gold,
        uat=uat,
        external_transfer_approval=None,
    )
    deferred = build_deferred_queue(
        reviewed=reviewed,
        deferred_analysis=deferred_analysis,
    )
    if (
        live["approved_positive_count"] != 21
        or live["deferred_count"] != 11
        or live["approved_abstention_count"] != 4
    ):
        raise ValueError("release Gold count drift")
    readiness = decide_release_readiness(
        core_regression_passed=True,
        tf027_production_gold_approved=bool(tf027["production_gold_approved"]),
        live_ragas_completed=False,
        image_excluded_by_human_decision=bool(
            tf027["image_excluded_by_human_decision"]
        ),
        external_provider_disabled_by_human_decision=False,
    )
    roadmap = _chromadb_roadmap()
    report = {
        "schema_version": 1,
        "stable_version": "v0.9",
        "production_retrieval": "bm25_minilm_rrf_existing_reranker",
        "production_retrieval_changed": False,
        "production_validator_changed": False,
        "tf027": tf027,
        "live_ragas": live,
        "deferred": deferred,
        "chromadb_roadmap": roadmap,
        "readiness": readiness,
        "actual_external_calls": 0,
        "git_operations": 0,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "tf027_status.json", tf027)
    _write_json(output_dir / "live_ragas_plan.json", live)
    _write_json(output_dir / "deferred_queue.json", deferred)
    _write_json(output_dir / "chromadb_roadmap.json", roadmap)
    _write_json(
        output_dir / "release_readiness.json",
        {
            "stable_version": report["stable_version"],
            "production_retrieval": report["production_retrieval"],
            "production_retrieval_changed": False,
            "production_validator_changed": False,
            "tf027_status": tf027["status"],
            "live_ragas_status": live["status"],
            "readiness": readiness,
            "actual_external_calls": 0,
            "git_operations": 0,
        },
    )
    (output_dir / "review.html").write_text(_review_html(report), encoding="utf-8")
    security = audit_artifacts(
        output_dir,
        source_texts=_catalog_source_texts(catalog_path),
    )
    _write_json(output_dir / "security_audit.json", security)
    if not security["pass"]:
        raise ValueError("release-readiness artifact security audit failed")
    report["security"] = security
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = run_release_readiness(output_dir=args.output)
    print(
        json.dumps(
            {
                "approved_positive": report["live_ragas"][
                    "approved_positive_count"
                ],
                "deferred": report["deferred"]["case_count"],
                "tf027": report["tf027"]["status"],
                "live_ragas": report["live_ragas"]["status"],
                "readiness": report["readiness"]["status"],
                "external_calls": report["actual_external_calls"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
