"""Fail-closed local validation helpers for the SCHAT v1.0 candidate.

This module intentionally has no provider client, HTTP transport, credential loader,
or vision interpretation path. It produces only raw-source-free validation records.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import platform
import sqlite3
import subprocess
import time
from collections import Counter
from pathlib import Path
from statistics import fmean
from typing import Any, Mapping

REQUIRED_TF027_CHECKS = (
    "figure_boundary",
    "start_and_end_nodes",
    "node_labels",
    "arrow_direction_and_connections",
    "decision_branch_conditions",
    "workflow_order",
    "numbers_units_and_times",
    "caption_nearby_text_relationship",
    "illegible_or_occluded_elements",
    "production_gold_approval",
)

SAFE_UAT_FIELDS = {
    "case_id",
    "document_scope",
    "question_type",
    "expected_intent",
    "expected_behavior",
    "expected_evidence_type",
    "expected_branch",
    "expected_phase",
    "actual_kind",
    "actual_domain",
    "actual_evidence_types",
    "actual_branch",
    "actual_phase",
    "retrieval_success",
    "correct_evidence",
    "answerability",
    "generation_status",
    "fallback_status",
    "citation_status",
    "style_status",
    "table_rendering_status",
    "table_retrieval_status",
    "image_status",
    "pre_budget_reason",
    "post_budget_reason",
    "selected_evidence_count",
    "selected_source_unit_count",
    "selected_table_row_count",
    "selectable_source_unit_count",
    "actual_provider_calls",
    "actual_vision_calls",
    "latency_ms",
    "pass",
    "failure_category",
    "gold_label_status",
    "candidate_gold_match",
    "aggregate_gold_match",
    "gold_aggregate_eligible",
    "gold_critical_requirements_status",
}


def assess_provider_live_readiness(
    *,
    credentials: Mapping[str, bool],
    hospital_evidence_transfer_approved: bool,
    provider_policy_approved: bool,
) -> dict[str, Any]:
    """Return a metadata-only decision; this function cannot make a provider call."""
    credential_ready = bool(credentials) and all(bool(value) for value in credentials.values())
    if not hospital_evidence_transfer_approved:
        reason = "external_transfer_approval_missing"
    elif not provider_policy_approved:
        reason = "provider_policy_approval_missing"
    elif not credential_ready:
        reason = "provider_credential_missing"
    else:
        reason = "approved_for_separate_live_execution"
    live_allowed = bool(
        hospital_evidence_transfer_approved
        and provider_policy_approved
        and credential_ready
    )
    return {
        "live_allowed": live_allowed,
        "decision": "pending_separate_live_execution" if live_allowed else "E",
        "reason": reason,
        "credential_presence": {
            str(provider): bool(present) for provider, present in credentials.items()
        },
        "actual_provider_calls": 0,
        "hospital_evidence_transfer_approved": hospital_evidence_transfer_approved,
        "provider_policy_approved": provider_policy_approved,
    }


def load_operational_uat(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if payload.get("schema_version") != 1 or not isinstance(cases, list):
        raise ValueError("unsupported operational UAT fixture")
    case_ids = [str(case.get("case_id", "")) for case in cases]
    questions = [str(case.get("question", "")) for case in cases]
    if not all(case_ids) or len(case_ids) != len(set(case_ids)):
        raise ValueError("operational UAT case IDs must be unique")
    if not all(questions) or len(questions) != len(set(questions)):
        raise ValueError("operational UAT questions must be unique")
    scope_counts = Counter(str(case.get("document_scope", "")) for case in cases)
    return {
        **payload,
        "case_count": len(cases),
        "scope_counts": dict(scope_counts),
        "provider_call_budget": sum(int(case.get("actual_provider_calls", 0)) for case in cases),
        "vision_call_budget": int(payload.get("actual_vision_calls", 0)),
        "questions_unique": True,
    }


def _sha256_ids(values: list[str]) -> str:
    joined = "\n".join(sorted(str(value) for value in values))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def safe_uat_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Project one UAT result to artifact-safe metadata."""
    safe = {key: result[key] for key in SAFE_UAT_FIELDS if key in result}
    selected_ids = [str(value) for value in result.get("selected_chunk_ids", ())]
    safe["selected_id_sha256"] = _sha256_ids(selected_ids)
    return safe


def evaluate_case_contract(
    *,
    expected_behavior: str,
    domain: str,
    intent_compatible: bool,
    pre_supported: bool,
    post_supported: bool,
    selected_evidence_count: int,
    evidence_route_status: str,
    provider_calls: int,
    vision_calls: int,
    structured_supported: bool = False,
    structured_evidence_count: int = 0,
) -> dict[str, Any]:
    """Apply UAT pass/fail rules without weakening production validators."""
    reasons: list[str] = []
    if provider_calls:
        reasons.append("unexpected_provider_call")
    if vision_calls:
        reasons.append("unexpected_vision_call")
    if expected_behavior == "abstain":
        if domain == "hospital":
            reasons.append("out_of_scope_domain_mismatch")
        if pre_supported or post_supported or selected_evidence_count:
            reasons.append("unsafe_out_of_scope_admission")
    elif expected_behavior == "pending_human_review":
        if evidence_route_status != "pending_image_review":
            reasons.append("image_review_gate_missing")
        if selected_evidence_count:
            reasons.append("unapproved_image_evidence_selected")
    elif expected_behavior == "answer":
        evidence_supported = pre_supported or structured_supported
        output_supported = post_supported or structured_supported
        total_evidence_count = selected_evidence_count + structured_evidence_count
        if domain != "hospital":
            reasons.append("hospital_domain_mismatch")
        if not intent_compatible:
            reasons.append("intent_mismatch")
        if not evidence_supported:
            reasons.append("pre_budget_not_supported")
        if not output_supported:
            reasons.append("post_budget_not_supported")
        if not total_evidence_count:
            reasons.append("missing_selected_evidence")
    else:
        reasons.append("unknown_expected_behavior")
    return {"pass": not reasons, "failure_reasons": reasons}


_INTENT_COMPATIBILITY = {
    "purpose": {"purpose", "fact"},
    "procedure": {"procedure", "cautions"},
    "preparation": {"preparation", "materials", "fact", "procedure"},
    "cautions": {"cautions", "fact"},
    "monitoring": {"cautions", "fact", "preparation"},
    "adverse_reaction": {"cautions", "fact", "procedure", "summary"},
    "product_specific": {
        "fact", "summary", "comparison", "preparation", "procedure", "cautions"
    },
    "fact_specific": {"fact", "preparation", "cautions"},
    "comparison": {"comparison"},
    "summary": {"summary"},
    "out_of_scope": {"fact", "summary", "procedure", "cautions"},
}


def _intent_compatible(expected: str, actual: str) -> bool:
    return actual in _INTENT_COMPATIBILITY.get(expected, {expected})


def _catalog_revision(path: Path) -> int:
    resolved = path.resolve()
    if not resolved.is_file():
        raise ValueError(f"catalog not found: {resolved}")
    with sqlite3.connect(resolved.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        row = db.execute("select version from corpus").fetchone()
    if row is None:
        raise ValueError("catalog revision missing")
    return int(row[0])


def _scope_document_ids(documents: list[dict[str, Any]]) -> dict[str, tuple[str, ...]]:
    scopes: dict[str, list[str]] = {"sedation": [], "transfusion": []}
    for document in documents:
        name = str(document.get("document_name", ""))
        if "진정" in name:
            scopes["sedation"].append(str(document["id"]))
        if "수혈" in name:
            scopes["transfusion"].append(str(document["id"]))
    if any(len(scopes[name]) != 1 for name in scopes):
        raise ValueError("expected one ready sedation and one ready transfusion document")
    return {name: tuple(values) for name, values in scopes.items()}


def _rss_bytes() -> int | None:
    try:
        import psutil

        return int(psutil.Process(os.getpid()).memory_info().rss)
    except (ImportError, OSError):
        if os.name != "nt":
            return None
    try:
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        get_current_process = ctypes.windll.kernel32.GetCurrentProcess
        get_current_process.restype = ctypes.c_void_p
        handle = get_current_process()
        get_process_memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
        get_process_memory_info.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        )
        get_process_memory_info.restype = ctypes.c_int
        if not get_process_memory_info(
            handle, ctypes.byref(counters), counters.cb
        ):
            raise OSError("GetProcessMemoryInfo failed")
        return int(counters.WorkingSetSize)
    except (AttributeError, OSError, ctypes.ArgumentError):
        pass
    try:
        executable = Path(
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        )
        output = subprocess.check_output(
            [
                str(executable),
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"(Get-Process -Id {os.getpid()}).WorkingSet64",
            ],
            text=True,
            timeout=5,
        )
        return int(output.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _mean_p95(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * 0.95) - 1))
    return {
        "mean_ms": round(fmean(values), 3),
        "p95_ms": round(ordered[index], 3),
    }


def _post_budget_selection(plan, pre):
    from src.ai import GROQ_REQUEST_TOKEN_BUDGET, prompt_messages
    from src.evidence import admitted_plan, assess_evidence

    plan = admitted_plan(plan, pre)

    trace: dict[str, Any] = {}
    _, selected, catalog, _ = prompt_messages(
        plan.query,
        pre.hits,
        14000,
        token_budget=GROQ_REQUEST_TOKEN_BUDGET,
        plan=plan,
        groups=pre.groups,
        trace=trace,
        return_catalog=True,
        return_contract=True,
    )
    branch_by_chunk = {
        hit.chunk.id: group.branch for group in pre.groups for hit in group.hits
    }
    post = assess_evidence(plan, selected, branch_by_chunk=branch_by_chunk)
    return selected, catalog, post, trace


def evaluate_operational_uat(
    *,
    catalog_path: Path,
    fixture_path: Path,
    gold_labels: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Execute local production retrieval/evidence contracts for all UAT cases.

    Provider and vision calls are deliberately absent. Exact questions and source
    text are used only in memory and are removed by ``safe_uat_result``.
    """
    from src.evidence import assess_evidence
    from src.library import Embedder, bounded_embedding_question
    from src.query import plan_query
    from src.repository import snapshot
    from src.settings import GuideError
    from tools.chroma_baseline_evaluate import load_catalog
    from tools.schat_v1_ragas_gold_uat_evaluate import evaluate_gold_match
    from tools.structured_table_evidence import (
        extract_table_records,
        search_table_records,
    )

    fixture = load_operational_uat(fixture_path)
    revision = _catalog_revision(catalog_path)
    load_started = time.perf_counter()
    library = snapshot(str(catalog_path.resolve()), revision)
    embedder = Embedder()
    model_load_ms = (time.perf_counter() - load_started) * 1000
    scopes = _scope_document_ids(library.docs)
    transfusion_document = next(
        document
        for document in library.docs
        if str(document["id"]) in scopes["transfusion"]
    )
    transfusion_name = str(transfusion_document["document_name"])
    _, transfusion_chunks = load_catalog(
        catalog_path,
        document_name=transfusion_name,
    )
    table_records = extract_table_records(
        catalog_path.resolve().parents[1] / transfusion_name,
        scopes["transfusion"][0],
        transfusion_name,
        transfusion_chunks,
    )
    table_source_ids = {
        (record.table_id, row.row_id): tuple(row.source_chunk_ids)
        for record in table_records
        for row in record.rows
    }
    rss_after_load = _rss_bytes()
    previous: dict[str, dict[str, Any]] = {}
    safe_results: list[dict[str, Any]] = []
    detailed_latencies: list[float] = []
    phase_latencies: dict[str, list[float]] = {
        "planning": [],
        "query_embedding": [],
        "retrieval_and_reranker": [],
        "pre_budget_evidence": [],
        "selection_and_post_validation": [],
    }

    for case in fixture["cases"]:
        started = time.perf_counter()
        scope = str(case["document_scope"])
        expected_behavior = str(case["expected_behavior"])
        is_follow_up = str(case["question_type"]) == "follow_up"
        prior = previous.get(scope, {}) if is_follow_up else {}
        documents = library.docs
        allowed = scopes.get(scope, tuple(scopes["sedation"] + scopes["transfusion"]))
        explicit_context = allowed if scope in scopes else ()
        phase_started = time.perf_counter()
        plan = plan_query(
            str(case["question"]),
            previous=str(prior.get("question", "")),
            follow_up=is_follow_up,
            documents=documents,
            previous_sources=tuple(prior.get("document_ids", ())),
            context_document_ids=explicit_context,
        )
        phase_latencies["planning"].append((time.perf_counter() - phase_started) * 1000)
        selected = []
        catalog = ()
        hits = []
        pre = None
        post = None
        trace: dict[str, Any] = {}
        selection_error = ""
        table_hits = ()
        if (
            scope == "transfusion"
            and str(case["expected_evidence_type"]) in {"table", "mixed"}
            and plan.domain != "out_of_scope"
            and not plan.clarification
        ):
            table_hits = search_table_records(
                str(case["question"]),
                table_records,
                limit=10,
            )
        if expected_behavior == "pending_human_review":
            # Image questions remain metadata-only and never select clinical evidence.
            pass
        elif plan.domain != "out_of_scope" and not plan.clarification:
            query_text = bounded_embedding_question(plan.expanded, embedder)
            phase_started = time.perf_counter()
            vector = embedder.encode([query_text])[0]
            phase_latencies["query_embedding"].append(
                (time.perf_counter() - phase_started) * 1000
            )
            phase_started = time.perf_counter()
            hits = library.search(
                plan.query,
                vector,
                list(allowed),
                0.38,
                plan=plan,
                trace=trace,
            )
            phase_latencies["retrieval_and_reranker"].append(
                (time.perf_counter() - phase_started) * 1000
            )
            phase_started = time.perf_counter()
            pre = assess_evidence(plan, hits)
            phase_latencies["pre_budget_evidence"].append(
                (time.perf_counter() - phase_started) * 1000
            )
            if pre.sufficient:
                phase_started = time.perf_counter()
                try:
                    selected, catalog, post, _ = _post_budget_selection(plan, pre)
                except GuideError as exc:
                    selection_error = type(exc).__name__
                phase_latencies["selection_and_post_validation"].append(
                    (time.perf_counter() - phase_started) * 1000
                )
        pre_supported = bool(pre and pre.sufficient)
        post_supported = bool(post and post.sufficient)
        table_supported = bool(table_hits)
        effective_domain = (
            "hospital"
            if table_supported and explicit_context and plan.domain != "out_of_scope"
            else (post.admitted_domain if post and post.admitted_domain else plan.domain)
        )
        contract = evaluate_case_contract(
            expected_behavior=expected_behavior,
            domain=effective_domain,
            intent_compatible=_intent_compatible(
                str(case["expected_intent"]), plan.kind
            ),
            pre_supported=pre_supported,
            post_supported=post_supported,
            selected_evidence_count=len(selected),
            evidence_route_status=plan.evidence_route_status,
            provider_calls=0,
            vision_calls=0,
            structured_supported=table_supported,
            structured_evidence_count=len(table_hits),
        )
        reasons = list(contract["failure_reasons"])
        if selection_error:
            reasons.append("selection_contract_error")
        selected_context_ids = [hit.chunk.id for hit in selected]
        for table_hit in table_hits:
            selected_context_ids.extend(
                table_source_ids.get((table_hit.table_id, table_hit.row_id), ())
            )
        gold_result: dict[str, Any] = {}
        if gold_labels and str(case["case_id"]) in gold_labels:
            gold_result = evaluate_gold_match(
                gold_labels[str(case["case_id"])],
                selected_context_ids=selected_context_ids,
                safely_abstained=(expected_behavior == "abstain" and not reasons),
            )
        elapsed_ms = (time.perf_counter() - started) * 1000
        detailed_latencies.append(elapsed_ms)
        result = {
            **case,
            "actual_kind": plan.kind,
            "actual_domain": effective_domain,
            "actual_evidence_types": list(plan.evidence_types),
            "actual_branch": list(plan.monitoring_branches),
            "actual_phase": plan.monitoring_phase or None,
            "retrieval_success": bool(hits or table_hits),
            "correct_evidence": (
                "server_assessment_supported_not_human_gold"
                if post_supported
                else (
                    "table_candidate_found_not_human_gold"
                    if table_supported
                    else "not_confirmed"
                )
            ),
            "answerability": post_supported or table_supported,
            "generation_status": "not_executed_external_approval_missing",
            "fallback_status": (
                "verified_extractive_ready"
                if post_supported
                else (
                    "table_candidate_ready_for_local_validation"
                    if table_supported
                    else "not_available"
                )
            ),
            "citation_status": (
                "server_reconstruction_ready"
                if post_supported
                else (
                    "table_row_citation_ready"
                    if table_supported
                    else "not_executed"
                )
            ),
            "style_status": plan.format,
            "table_rendering_status": (
                "local_table_route_ready"
                if "table" in plan.evidence_types
                else "not_applicable"
            ),
            "table_retrieval_status": (
                "candidate_found_not_human_gold"
                if table_supported
                else (
                    "no_candidate"
                    if str(case["expected_evidence_type"]) in {"table", "mixed"}
                    else "not_applicable"
                )
            ),
            "image_status": (
                "needs_human_review"
                if plan.evidence_route_status == "pending_image_review"
                else "not_applicable"
            ),
            "pre_budget_reason": (
                pre.reason
                if pre
                else ("table_candidate_supported" if table_supported else "not_evaluated")
            ),
            "post_budget_reason": (
                post.reason
                if post
                else ("table_candidate_supported" if table_supported else "not_evaluated")
            ),
            "selected_evidence_count": len(selected) + len(table_hits),
            "selected_source_unit_count": 0,
            "selected_table_row_count": len(table_hits),
            "selectable_source_unit_count": sum(
                bool(getattr(unit, "selectable", False)) for unit in catalog
            ),
            "selected_chunk_ids": selected_context_ids,
            "actual_provider_calls": 0,
            "actual_vision_calls": 0,
            "latency_ms": round(elapsed_ms, 3),
            "pass": not reasons,
            "failure_category": reasons[0] if reasons else None,
            "gold_label_status": gold_result.get("gold_label_status"),
            "candidate_gold_match": gold_result.get("candidate_gold_match"),
            "aggregate_gold_match": gold_result.get("aggregate_gold_match"),
            "gold_aggregate_eligible": gold_result.get("aggregate_eligible"),
            "gold_critical_requirements_status": gold_result.get(
                "critical_requirements_status"
            ),
        }
        safe_results.append(safe_uat_result(result))
        if scope in scopes and expected_behavior == "answer":
            previous[scope] = {
                "question": str(case["question"]),
                "document_ids": scopes[scope],
            }

    counts = Counter("pass" if row["pass"] else "fail" for row in safe_results)
    failure_counts = Counter(
        str(row["failure_category"])
        for row in safe_results
        if row.get("failure_category")
    )
    gold_approved = [row for row in safe_results if row.get("gold_aggregate_eligible")]
    gold_provisional = [
        row
        for row in safe_results
        if row.get("gold_label_status")
        and not row.get("gold_aggregate_eligible")
    ]
    summary = {
        "schema_version": 1,
        "dataset_version": fixture["dataset_version"],
        "case_count": len(safe_results),
        "pass_count": counts["pass"],
        "fail_count": counts["fail"],
        "scope_counts": fixture["scope_counts"],
        "failure_counts": dict(sorted(failure_counts.items())),
        "actual_provider_calls": 0,
        "actual_vision_calls": 0,
        "generation_status": "pending_external_transfer_approval",
        "correct_evidence_contract": "server_assessment_not_human_gold",
        "gold_approved_case_count": len(gold_approved),
        "gold_approved_pass_count": sum(
            row.get("aggregate_gold_match") is True for row in gold_approved
        ),
        "gold_provisional_case_count": len(gold_provisional),
    }
    performance = {
        "model_and_catalog_load_ms": round(model_load_ms, 3),
        "total_local_case": _mean_p95(detailed_latencies),
        "phases": {
            name: _mean_p95(values) if values else {"mean_ms": 0.0, "p95_ms": 0.0}
            for name, values in phase_latencies.items()
        },
        "rss_after_model_load_bytes": rss_after_load,
        "provider_latency": "not_measured_no_live_call",
        "vision_latency": "not_measured_no_live_call",
        "answer_assembly_latency": "not_measured_no_provider_answer",
    }
    return summary, safe_results, performance


def validate_tf027_checklist(checklist: Mapping[str, Any]) -> dict[str, Any]:
    checks = checklist.get("checks")
    if checklist.get("case_id") != "TF027" or not isinstance(checks, list):
        raise ValueError("invalid TF027 checklist")
    if tuple(check.get("check_id") for check in checks) != REQUIRED_TF027_CHECKS:
        raise ValueError("TF027 checklist contract drift")
    if any(
        check.get("status") != "unreviewed"
        or check.get("reviewed_value") is not None
        or check.get("note") is not None
        for check in checks
    ):
        raise ValueError("human review fixture must remain unreviewed")
    if (
        checklist.get("needs_human_review") is not True
        or checklist.get("production_gold_approved") is not False
        or checklist.get("vision_api_calls") != 0
    ):
        raise ValueError("TF027 fail-closed state drift")
    approval = checklist.get("approval") or {}
    if any(approval.get(key) is not None for key in ("reviewer", "reviewed_at")):
        raise ValueError("TF027 approval must be entered by a human")
    if approval.get("decision") != "pending" or approval.get("approved_gold_ids"):
        raise ValueError("TF027 production gold must remain pending")
    return {
        "case_id": "TF027",
        "review_status": "needs_human_review",
        "needs_human_review": True,
        "production_gold_approved": False,
        "check_count": len(checks),
        "reviewed_count": 0,
        "checks": [
            {"check_id": str(check["check_id"]), "status": "unreviewed"}
            for check in checks
        ],
        "source_reference": {
            "page": (checklist.get("source_reference") or {}).get("page"),
            "figure_candidates": [
                {
                    "figure_id": str(candidate.get("figure_id", "")),
                    "bbox": list(candidate.get("bbox", ())),
                    "fingerprint": str(candidate.get("fingerprint", "")),
                }
                for candidate in (checklist.get("source_reference") or {}).get(
                    "figure_candidates", ()
                )
            ],
        },
    }


def build_tf027_review_html(checklist: Mapping[str, Any]) -> str:
    rows = "".join(
        "<tr data-status=\"unreviewed\">"
        f"<td>{index}</td><td>{html.escape(str(check['check_id']))}</td>"
        "<td>미검수</td><td>사람 입력 필요</td></tr>"
        for index, check in enumerate(checklist["checks"], 1)
    )
    return f"""<!doctype html><html lang="ko"><meta charset="utf-8">
<title>TF027 사람 검수 체크리스트</title>
<style>body{{font-family:system-ui;margin:36px;max-width:1000px}}table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #ccd;padding:8px;text-align:left}}.pending{{color:#8a5a00}}</style>
<h1>TF027 이미지/도식 사람 검수</h1>
<p class="pending"><strong>needs_human_review=true</strong> ·
<strong>production_gold_approved=false</strong></p>
<p>화살표·순서·수치·임상 의미는 자동 추정하지 않습니다. 아래 항목은 사람이 원본을 보고
별도 승인 절차에서 기록해야 합니다.</p>
<table><thead><tr><th>#</th><th>검수 항목</th><th>상태</th><th>조치</th></tr></thead>
<tbody>{rows}</tbody></table>
<p>Vision API calls: 0 · Production gold: pending</p>
</html>"""


def classify_final_version(
    *,
    local_core_passed: bool,
    provider_live_verified: bool,
    tf027_approved: bool,
    critical_regression: bool,
) -> str:
    if critical_regression or not local_core_passed:
        return "v0.9"
    if provider_live_verified and tf027_approved:
        return "v1.0"
    return "v1.0-rc1"


FORBIDDEN_ARTIFACT_KEYS = {
    "authorization",
    "api_key",
    "chunk_text",
    "content",
    "contents",
    "exact_text",
    "full_prompt",
    "prompt",
    "question",
    "quote",
    "raw_response",
    "source_text",
    "source_unit_text",
}

SECRET_MARKERS = (
    "authorization:",
    "bearer ",
    "gsk_",
    "AIza",
    "api_key",
    "llm_api_key",
)


def _forbidden_key_count(value: Any) -> int:
    if isinstance(value, dict):
        return sum(
            int(str(key).casefold() in FORBIDDEN_ARTIFACT_KEYS)
            + _forbidden_key_count(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return sum(_forbidden_key_count(item) for item in value)
    return 0


def audit_artifacts(output_dir: Path, *, source_texts: list[str]) -> dict[str, Any]:
    files = sorted(
        path for path in output_dir.rglob("*")
        if path.is_file() and path.suffix.casefold() in {".json", ".html", ".csv"}
        and path.name != "security_audit.json"
    )
    exact_matches = 0
    forbidden_keys = 0
    secret_markers = 0
    for path in files:
        text = path.read_text(encoding="utf-8")
        exact_matches += sum(
            1 for source in source_texts if source and len(source) >= 8 and source in text
        )
        lowered = text.casefold()
        secret_markers += sum(lowered.count(marker.casefold()) for marker in SECRET_MARKERS)
        if path.suffix.casefold() == ".json":
            forbidden_keys += _forbidden_key_count(json.loads(text))
    return {
        "scanned_file_count": len(files),
        "exact_source_match_count": exact_matches,
        "forbidden_key_count": forbidden_keys,
        "secret_marker_count": secret_markers,
        "pass": exact_matches == forbidden_keys == secret_markers == 0,
    }


def _final_review_html(
    *,
    readiness: Mapping[str, Any],
    uat_summary: Mapping[str, Any],
    table_summary: Mapping[str, Any],
    final_version: str,
) -> str:
    return f"""<!doctype html><html lang="ko"><meta charset="utf-8">
<title>SCHAT v1.0 최종 검증</title>
<style>body{{font-family:system-ui;margin:36px;max-width:1040px}}.ok{{color:#087830}}.warn{{color:#9a5b00}}
table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccd;padding:9px;text-align:left}}</style>
<h1>SCHAT v1.0 최종 검증</h1>
<p>최종 판정: <strong>{html.escape(final_version)}</strong></p>
<h2>Provider Live</h2><p class="warn">실행하지 않음 · 실제 호출 0회 ·
사유: {html.escape(str(readiness.get('reason', 'unknown')))}</p>
<h2>운영 UAT</h2>
<table><tr><th>전체</th><th>PASS</th><th>FAIL</th></tr>
<tr><td>{int(uat_summary.get('case_count', 0))}</td><td>{int(uat_summary.get('pass_count', 0))}</td>
<td>{int(uat_summary.get('fail_count', 0))}</td></tr></table>
<p>실패 유형: {html.escape(json.dumps(uat_summary.get('failure_counts', {}), ensure_ascii=False))}</p>
<h2>Table</h2><p>Approved cases {int(table_summary.get('approved_case_count', 0))} ·
Hit@10 {float(table_summary.get('hit_at_10', 0)):.4f}</p>
<h2>Image</h2><p class="warn">TF027 needs_human_review=true · production gold 미승인</p>
<h2>안전 경계</h2><p class="ok">외부 데이터 전송 0 · Provider/Vision 호출 0 · v0.9 보존</p>
</html>"""


def write_final_artifacts(
    output_dir: Path,
    *,
    readiness: Mapping[str, Any],
    uat_summary: Mapping[str, Any],
    uat_results: list[Mapping[str, Any]],
    performance: Mapping[str, Any],
    table_summary: Mapping[str, Any],
    tf027: Mapping[str, Any],
    final_version: str,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=False)
    payloads = {
        "provider_readiness.json": dict(readiness),
        "uat_summary.json": dict(uat_summary),
        "uat_results.json": list(uat_results),
        "performance.json": dict(performance),
        "table_summary.json": dict(table_summary),
        "tf027_status.json": dict(tf027),
        "summary.json": {
            "stable_version_before": "v0.9",
            "final_version_decision": final_version,
            "provider_live_executed": False,
            "actual_provider_calls": 0,
            "actual_vision_calls": 0,
            "hospital_data_external_transfers": 0,
            "uat": {
                "case_count": uat_summary.get("case_count"),
                "pass_count": uat_summary.get("pass_count"),
                "fail_count": uat_summary.get("fail_count"),
            },
            "tf027": {
                "needs_human_review": tf027.get("needs_human_review"),
                "production_gold_approved": tf027.get("production_gold_approved"),
            },
        },
        "environment.json": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "network_generation_calls": 0,
        },
    }
    for name, payload in payloads.items():
        (output_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    (output_dir / "tf027_review.html").write_text(
        build_tf027_review_html(tf027), encoding="utf-8"
    )
    (output_dir / "review.html").write_text(
        _final_review_html(
            readiness=readiness,
            uat_summary=uat_summary,
            table_summary=table_summary,
            final_version=final_version,
        ),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog", type=Path, default=root / "data" / "library" / "catalog.sqlite3"
    )
    parser.add_argument(
        "--uat-fixture",
        type=Path,
        default=root / "tests" / "fixtures" / "schat_v1_operational_uat.json",
    )
    parser.add_argument(
        "--tf027-fixture",
        type=Path,
        default=root / "tests" / "fixtures" / "tf027_image_human_review_checklist.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=root / "workspace" / "과거작업" / "평가산출물" / "2026-09-18_schat-v1-final-validation",
    )
    return parser.parse_args()


def main() -> None:
    from src.repository import snapshot
    from tools.schat_multimodal_mvp_evaluate import (
        DEFAULT_FIXTURE,
        DEFAULT_MULTIMODAL,
        DEFAULT_PDF,
        evaluate_multimodal_mvp,
    )

    args = parse_args()
    readiness = assess_provider_live_readiness(
        credentials={"groq": False, "gemini": False},
        hospital_evidence_transfer_approved=False,
        provider_policy_approved=False,
    )
    tf027 = validate_tf027_checklist(
        json.loads(args.tf027_fixture.read_text(encoding="utf-8"))
    )
    uat_summary, uat_results, performance = evaluate_operational_uat(
        catalog_path=args.catalog,
        fixture_path=args.uat_fixture,
    )
    table_started = time.perf_counter()
    table, _, _ = evaluate_multimodal_mvp(
        catalog_path=args.catalog,
        pdf_path=DEFAULT_PDF,
        fixture_path=DEFAULT_FIXTURE,
        multimodal_fixture_path=DEFAULT_MULTIMODAL,
    )
    table_summary = table["table"]
    performance["table_inventory_and_retrieval_ms"] = round(
        (time.perf_counter() - table_started) * 1000, 3
    )
    final_version = classify_final_version(
        local_core_passed=(
            uat_summary["fail_count"] == 0 and table_summary["hit_at_10"] == 1.0
        ),
        provider_live_verified=False,
        tf027_approved=False,
        critical_regression=False,
    )
    write_final_artifacts(
        args.output_dir,
        readiness=readiness,
        uat_summary=uat_summary,
        uat_results=uat_results,
        performance=performance,
        table_summary=table_summary,
        tf027=tf027,
        final_version=final_version,
    )
    library = snapshot(str(args.catalog.resolve()), _catalog_revision(args.catalog))
    audit = audit_artifacts(
        args.output_dir,
        source_texts=[chunk.text for chunk in library.chunks],
    )
    (args.output_dir / "security_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not audit["pass"]:
        raise ValueError("final artifact security audit failed")
    print(json.dumps({
        "final_version_decision": final_version,
        "uat_pass": uat_summary["pass_count"],
        "uat_fail": uat_summary["fail_count"],
        "table_hit_at_10": table_summary["hit_at_10"],
        "actual_provider_calls": 0,
        "actual_vision_calls": 0,
        "security_audit": audit["pass"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
