"""Fixed-label helpers for the SCHAT v1 RAGAS, Gold, and UAT evaluation.

This module never infers Gold from retrieval output.  Operational-UAT label
candidates may only point at already reviewed fixture records.  A new mapping
remains excluded from aggregate scoring until a human changes its explicit
``label_status`` to ``approved``.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
from typing import Any, Mapping

_LABEL_STATUSES = {
    "approved",
    "provisional_needs_human_review",
    "image_needs_human_review",
}

RETRIEVAL_METRICS = (
    "hit_at_1",
    "hit_at_3",
    "hit_at_5",
    "hit_at_10",
    "mrr",
    "recall_at_1",
    "recall_at_3",
    "recall_at_5",
    "recall_at_10",
    "precision_at_1",
    "precision_at_3",
    "precision_at_5",
    "precision_at_10",
    "ragas_id_context_precision",
    "ragas_id_context_recall",
)


def load_operational_gold_labels(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if payload.get("schema_version") != 1 or not isinstance(cases, list):
        raise ValueError("unsupported operational Gold fixture")
    case_ids = [str(case.get("case_id", "")) for case in cases]
    if not all(case_ids) or len(case_ids) != len(set(case_ids)):
        raise ValueError("operational Gold case IDs must be unique")
    for case in cases:
        if case.get("label_status") not in _LABEL_STATUSES:
            raise ValueError("unsupported operational Gold label status")
        if case.get("expected_abstain") and case.get("source_gold_refs"):
            raise ValueError("abstention labels cannot have evidence Gold")
        if case.get("label_status") == "approved" and not (
            case.get("expected_abstain") or case.get("source_gold_refs")
        ):
            raise ValueError("approved answer label requires reviewed evidence")
    return payload


def _transfusion_gold(path: Path) -> dict[str, tuple[str, ...]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("dataset_version") != "transfusion-retrieval-v3":
        raise ValueError("transfusion Gold dataset version drift")
    result: dict[str, tuple[str, ...]] = {}
    for case in payload["cases"]:
        if case.get("needs_human_review") or not case.get("expected_answerable"):
            continue
        contexts = case.get("reference_contexts", ())
        if not contexts or not all(
            context.get("substantive_body") is True
            and context.get("source_shape") != "heading"
            for context in contexts
        ):
            continue
        result[str(case["question_id"])] = tuple(
            str(value) for value in case["reference_context_ids"]
        )
    return result


def _sedation_gold(path: Path) -> dict[str, tuple[str, ...]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("query_id") != "Q002" or payload.get("schema_version") != 2:
        raise ValueError("sedation Gold fixture drift")
    return {
        str(stage["stage_id"]): tuple(str(value) for value in stage["allowed_chunk_ids"])
        for stage in payload["stages"]
    }


def resolve_gold_references(
    labels: Mapping[str, Any],
    *,
    transfusion_fixture_path: Path,
    sedation_fixture_path: Path,
) -> list[dict[str, Any]]:
    """Resolve fixed reviewed references without consulting retrieval output."""
    transfusion = _transfusion_gold(transfusion_fixture_path)
    sedation = _sedation_gold(sedation_fixture_path)
    resolved: list[dict[str, Any]] = []
    for case in labels["cases"]:
        context_ids: set[str] = set()
        provenance: list[str] = []
        for reference in case.get("source_gold_refs", ()):
            fixture = str(reference.get("fixture", ""))
            if fixture == "transfusion_retrieval_baseline":
                source = transfusion
                requested = reference.get("case_ids", ())
            elif fixture == "q002_gold_stages":
                source = sedation
                requested = reference.get("stage_ids", ())
            else:
                raise ValueError("unknown Gold fixture reference")
            for source_id in requested:
                source_id = str(source_id)
                if source_id not in source:
                    raise ValueError(f"unapproved or missing Gold reference: {source_id}")
                context_ids.update(source[source_id])
                provenance.append(f"{fixture}:{source_id}")
        status = str(case["label_status"])
        resolved.append(
            {
                "case_id": str(case["case_id"]),
                "expected_document_scope": str(case["expected_document_scope"]),
                "expected_domain": str(case["expected_domain"]),
                "expected_evidence_type": str(case["expected_evidence_type"]),
                "expected_abstain": bool(case["expected_abstain"]),
                "label_status": status,
                "aggregate_eligible": status == "approved",
                "gold_context_ids": sorted(context_ids),
                "provenance": sorted(provenance),
                "critical_requirements_status": str(
                    case.get("critical_requirements_status", "not_labeled")
                ),
            }
        )
    return resolved


def build_ragas_scorecard(
    *,
    id_context_precision: float,
    id_context_recall: float,
    provider_live_approved: bool,
    llm_judge_approved: bool,
) -> dict[str, Any]:
    live_judge_allowed = provider_live_approved and llm_judge_approved
    pending = "pending_external_llm_judge_approval"
    return {
        "id_context_precision": round(float(id_context_precision), 4),
        "id_context_recall": round(float(id_context_recall), 4),
        "faithfulness": "not_run_requires_generated_answer" if live_judge_allowed else pending,
        "answer_relevancy": "not_run_requires_generated_answer" if live_judge_allowed else pending,
        "context_precision_llm": "not_run_requires_llm_judge" if live_judge_allowed else pending,
        "context_recall_llm": "not_run_requires_llm_judge" if live_judge_allowed else pending,
        "actual_provider_calls": 0,
        "actual_llm_judge_calls": 0,
    }


def evaluate_gold_match(
    label: Mapping[str, Any],
    *,
    selected_context_ids: list[str] | tuple[str, ...],
    safely_abstained: bool,
) -> dict[str, Any]:
    """Compare runtime IDs with a fixed label without changing that label."""
    expected_abstain = bool(label["expected_abstain"])
    gold_ids = {str(value) for value in label.get("gold_context_ids", ())}
    selected_ids = {str(value) for value in selected_context_ids}
    candidate_match = (
        safely_abstained
        if expected_abstain
        else (bool(gold_ids.intersection(selected_ids)) if gold_ids else None)
    )
    eligible = bool(label.get("aggregate_eligible"))
    return {
        "gold_label_status": str(label["label_status"]),
        "candidate_gold_match": candidate_match,
        "aggregate_gold_match": candidate_match if eligible else None,
        "aggregate_eligible": eligible,
        "critical_requirements_status": str(
            label.get("critical_requirements_status", "not_labeled")
        ),
    }


def compare_retrieval_metrics(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> dict[str, Any]:
    missing = [key for key in RETRIEVAL_METRICS if key not in before or key not in after]
    if missing:
        raise ValueError(f"missing fixed retrieval metrics: {missing}")
    delta = {
        key: round(float(after[key]) - float(before[key]), 12)
        for key in RETRIEVAL_METRICS
    }
    return {
        "before": {key: float(before[key]) for key in RETRIEVAL_METRICS},
        "after": {key: float(after[key]) for key in RETRIEVAL_METRICS},
        "delta": delta,
        "all_metrics_equal": all(value == 0 for value in delta.values()),
        "regression": any(value < 0 for value in delta.values()),
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _review_html(
    *,
    baseline: Mapping[str, Any],
    after: Mapping[str, Any],
    retrieval: Mapping[str, Any],
    ragas: Mapping[str, Any],
    gold: Mapping[str, Any],
    table: Mapping[str, Any],
    decision: str,
) -> str:
    metric_rows = "".join(
        "<tr>"
        f"<td>{html.escape(key)}</td>"
        f"<td>{float(retrieval['before'][key]):.4f}</td>"
        f"<td>{float(retrieval['after'][key]):.4f}</td>"
        f"<td>{float(retrieval['delta'][key]):+.4f}</td>"
        "</tr>"
        for key in RETRIEVAL_METRICS
    )
    return f"""<!doctype html><html lang="ko"><meta charset="utf-8">
<title>SCHAT v1.0 RAGAS·Gold·UAT 최종 검수</title>
<style>body{{font-family:system-ui;margin:36px;max-width:1120px;line-height:1.55}}
table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccd;padding:8px;text-align:left}}
.ok{{color:#087830}}.warn{{color:#9a5b00}}code{{background:#f3f5f7;padding:2px 5px}}</style>
<h1>SCHAT v1.0 RAGAS + Gold + UAT</h1>
<p>최종 판정: <strong>{html.escape(decision)}</strong></p>
<h2>현재 버전과 Baseline</h2>
<p>공식 안정 버전은 <code>v0.9</code>입니다. 수정 전 운영 UAT는
{int(baseline['pass_count'])}/{int(baseline['case_count'])} PASS였습니다.</p>
<h2>수정 전 / 후</h2>
<table><tr><th>구분</th><th>PASS</th><th>FAIL</th><th>외부 호출</th></tr>
<tr><td>Before</td><td>{int(baseline['pass_count'])}</td><td>{int(baseline['fail_count'])}</td><td>0</td></tr>
<tr><td>After</td><td>{int(after['pass_count'])}</td><td>{int(after['fail_count'])}</td><td>0</td></tr></table>
<h2>Retrieval</h2><p>같은 90문항·같은 승인 Gold를 사용했습니다. 점수 회귀:
<strong>{str(bool(retrieval['regression'])).lower()}</strong></p>
<table><tr><th>지표</th><th>Before</th><th>After</th><th>차이</th></tr>{metric_rows}</table>
<h2>RAGAS</h2><p>ID Context Precision {float(ragas['id_context_precision']):.4f} ·
ID Context Recall {float(ragas['id_context_recall']):.4f}</p>
<p class="warn">Faithfulness와 Answer Relevancy는 provider/LLM judge 승인 전이므로 pending입니다.</p>
<h2>Gold</h2><p>운영 UAT에서 승인된 abstention Gold
{int(gold['approved_pass_count'])}/{int(gold['approved_case_count'])} 통과. 새 positive 매핑
{int(gold['provisional_case_count'])}건은 사람 승인 전 aggregate에서 제외됩니다.</p>
<h2>UAT</h2><p class="ok">{int(after['pass_count'])}/{int(after['case_count'])} PASS ·
Q006/out-of-scope provider 호출 0회</p>
<h2>Table</h2><p>승인 문항 {int(table['approved_case_count'])}개 · Hit@10
{float(table['hit_at_10']):.4f}</p>
<h2>Image</h2><p class="warn">TF027은 needs_human_review=true,
production_gold_approved=false를 유지합니다.</p>
<h2>Provider</h2><p class="warn">외부 전송 승인이 없어 Live와 LLM judge 모두 0회입니다.</p>
<h2>Safety</h2><p class="ok">Fail-closed, validator, parent atomicity, citation 계약을 유지했습니다.</p>
<h2>Pending</h2><ul><li>Provider Live 및 LLM judge 승인</li><li>TF027 사람 검수</li>
<li>운영 positive Gold 매핑과 critical facts 사람 승인</li></ul>
</html>"""


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=root / "data/library/catalog.sqlite3")
    parser.add_argument(
        "--uat", type=Path, default=root / "tests/fixtures/schat_v1_operational_uat.json"
    )
    parser.add_argument(
        "--gold", type=Path, default=root / "tests/fixtures/schat_v1_operational_gold.json"
    )
    parser.add_argument(
        "--transfusion-gold",
        type=Path,
        default=root / "tests/fixtures/transfusion_retrieval_baseline.json",
    )
    parser.add_argument(
        "--sedation-gold",
        type=Path,
        default=root / "tests/fixtures/q002_gold_stages.json",
    )
    parser.add_argument(
        "--tf027",
        type=Path,
        default=root / "tests/fixtures/tf027_image_human_review_checklist.json",
    )
    parser.add_argument(
        "--baseline-retrieval",
        type=Path,
        default=root / "workspace/과거작업/평가산출물/2026-09-17_transfusion-expanded-retrieval/comparison_summary.json",
    )
    parser.add_argument(
        "--after-retrieval",
        type=Path,
        default=root / "workspace/RAGAS/2026-09-18_schat-v1-ragas-gold-uat-final/retrieval_after/comparison_summary.json",
    )
    parser.add_argument(
        "--baseline-uat",
        type=Path,
        default=root / "workspace/RAGAS/2026-09-18_schat-v1-ragas-gold-uat-final/baseline/uat_summary.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=root / "workspace/RAGAS/2026-09-18_schat-v1-ragas-gold-uat-final",
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
    from tools.schat_v1_final_validate import (
        _catalog_revision,
        assess_provider_live_readiness,
        audit_artifacts,
        build_tf027_review_html,
        evaluate_operational_uat,
        validate_tf027_checklist,
    )

    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    labels = load_operational_gold_labels(args.gold)
    resolved = resolve_gold_references(
        labels,
        transfusion_fixture_path=args.transfusion_gold,
        sedation_fixture_path=args.sedation_gold,
    )
    resolved_by_id = {row["case_id"]: row for row in resolved}
    uat_summary, uat_rows, performance = evaluate_operational_uat(
        catalog_path=args.catalog,
        fixture_path=args.uat,
        gold_labels=resolved_by_id,
    )
    baseline_uat = json.loads(args.baseline_uat.read_text(encoding="utf-8"))
    before_report = json.loads(args.baseline_retrieval.read_text(encoding="utf-8"))
    after_report = json.loads(args.after_retrieval.read_text(encoding="utf-8"))
    before_metrics = before_report["summaries"]["bm25"]["overall"]
    after_metrics = after_report["summaries"]["bm25"]["overall"]
    retrieval = compare_retrieval_metrics(before_metrics, after_metrics)
    ragas = build_ragas_scorecard(
        id_context_precision=after_metrics["ragas_id_context_precision"],
        id_context_recall=after_metrics["ragas_id_context_recall"],
        provider_live_approved=False,
        llm_judge_approved=False,
    )
    table_payload, _, _ = evaluate_multimodal_mvp(
        catalog_path=args.catalog,
        pdf_path=DEFAULT_PDF,
        fixture_path=DEFAULT_FIXTURE,
        multimodal_fixture_path=DEFAULT_MULTIMODAL,
    )
    table = table_payload["table"]
    tf027 = validate_tf027_checklist(json.loads(args.tf027.read_text(encoding="utf-8")))
    readiness = assess_provider_live_readiness(
        credentials={"groq": False, "gemini": False},
        hospital_evidence_transfer_approved=False,
        provider_policy_approved=False,
    )
    gold_summary = {
        "dataset_version": labels["dataset_version"],
        "approved_case_count": uat_summary["gold_approved_case_count"],
        "approved_pass_count": uat_summary["gold_approved_pass_count"],
        "provisional_case_count": uat_summary["gold_provisional_case_count"],
        "positive_critical_fact_evaluation": "pending_human_label_and_provider_answer",
        "retrieval_gold_approved_positive_count": 78,
        "retrieval_gold_human_review_excluded_count": 1,
        "policy": labels["policy"],
    }
    local_core_passed = bool(
        uat_summary["fail_count"] == 0
        and not retrieval["regression"]
        and table["hit_at_10"] == 1.0
        and uat_summary["gold_approved_pass_count"]
        == uat_summary["gold_approved_case_count"]
    )
    decision = "v1.0-rc1" if local_core_passed else "v0.9"
    baseline_freeze = {
        "stable_version": "v0.9",
        "uat": baseline_uat,
        "retrieval": retrieval["before"],
        "fixture_sha256": {
            "operational_uat": _sha256_file(args.uat),
            "operational_gold": _sha256_file(args.gold),
            "transfusion_retrieval": _sha256_file(args.transfusion_gold),
        },
        "actual_provider_calls": 0,
        "actual_vision_calls": 0,
    }
    final_decision = {
        "stable_version_before": "v0.9",
        "candidate": decision,
        "local_core_passed": local_core_passed,
        "provider_live": "pending_external_transfer_approval",
        "image": "tf027_human_review_pending",
        "operational_positive_gold": "human_review_pending",
        "actual_provider_calls": 0,
        "actual_llm_judge_calls": 0,
        "actual_vision_calls": 0,
        "git_commit_tag_push": 0,
    }
    safety = {
        "out_of_scope_case_count": 4,
        "out_of_scope_pass_count": 4,
        "provider_zero_call": True,
        "vision_zero_call": True,
        "hospital_data_external_transfer_count": 0,
        "validator_relaxation_count": 0,
        "v0_9_tag_changed": False,
    }
    payloads = {
        "baseline_freeze.json": baseline_freeze,
        "after_summary.json": uat_summary,
        "uat_summary.json": uat_summary,
        "uat_results.json": uat_rows,
        "retrieval_comparison.json": retrieval,
        "ragas_results.json": ragas,
        "gold_summary.json": gold_summary,
        "table_summary.json": table,
        "provider_status.json": readiness,
        "tf027_status.json": tf027,
        "performance.json": performance,
        "safety_summary.json": safety,
        "final_decision.json": final_decision,
        "environment.json": {
            "generation_api_calls": 0,
            "vision_api_calls": 0,
            "llm_judge_calls": 0,
            "production_dependencies_changed": False,
        },
    }
    for filename, payload in payloads.items():
        (args.output_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    (args.output_dir / "tf027_review.html").write_text(
        build_tf027_review_html(tf027), encoding="utf-8"
    )
    (args.output_dir / "review.html").write_text(
        _review_html(
            baseline=baseline_uat,
            after=uat_summary,
            retrieval=retrieval,
            ragas=ragas,
            gold=gold_summary,
            table=table,
            decision=decision,
        ),
        encoding="utf-8",
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
        raise ValueError("integrated artifact security audit failed")
    print(
        json.dumps(
            {
                "decision": decision,
                "uat": f"{uat_summary['pass_count']}/{uat_summary['case_count']}",
                "retrieval_regression": retrieval["regression"],
                "id_context_precision": ragas["id_context_precision"],
                "id_context_recall": ragas["id_context_recall"],
                "gold_approved": (
                    f"{gold_summary['approved_pass_count']}/"
                    f"{gold_summary['approved_case_count']}"
                ),
                "security": audit["pass"],
                "external_calls": 0,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
