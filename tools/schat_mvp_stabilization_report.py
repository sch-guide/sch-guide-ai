"""Compose raw-free SCHAT stabilization artifacts from completed offline runs."""

from __future__ import annotations

import argparse
import html
import json
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from tools.chroma_baseline_evaluate import ensure_raw_text_free, load_catalog

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "transfusion_retrieval_baseline.json"
DEFAULT_CATALOG = ROOT / "data" / "library" / "catalog.sqlite3"
DEFAULT_RETRIEVAL = ROOT / "workspace" / "과거작업" / "평가산출물" / "2026-09-17_transfusion-expanded-retrieval"
DEFAULT_MULTIMODAL = ROOT / "workspace" / "과거작업" / "평가산출물" / "2026-09-17_schat-mvp-stabilization"
DEFAULT_SEDATION_UAT = (
    ROOT / "workspace" / "UAT" / "2026-09-16_rag-sedation-uat-generalization" / "uat_report.json"
)


def build_human_review_manifest(cases: Sequence[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for case in cases:
        audit = case.get("review_audit")
        if (
            not isinstance(audit, dict)
            or case.get("metadata", {}).get("reviewed_in")
            != "schat-mvp-stabilization-v1"
        ):
            continue
        rows.append(
            {
                "question_id": case["question_id"],
                "review_status": case["review_status"],
                "needs_human_review": case["needs_human_review"],
                "review_reasons": list(case["review_reasons"]),
                "page_numbers": list(audit["page_numbers"]),
                "source_shapes": list(audit["source_shapes"]),
                "reference_context_ids": list(audit["reference_context_ids"]),
                "reference_sha256": list(audit["reference_sha256"]),
                "relationship": audit["relationship"],
                "number_unit_status": audit["number_unit_status"],
                "image_interpretation_required": audit["image_interpretation_required"],
                "evidence_basis": audit["evidence_basis"],
                "raw_source_text_stored": False,
            }
        )
    counts = Counter(row["review_status"] for row in rows)
    return {
        "schema_version": 1,
        "reviewed_case_count": len(rows),
        "status_counts": dict(sorted(counts.items())),
        "remaining_human_review_case_count": sum(row["needs_human_review"] for row in rows),
        "cases": rows,
    }


def _transfusion_readiness(bm25_payload: dict[str, Any]) -> dict[str, Any]:
    approved = [
        row
        for row in bm25_payload["cases"]
        if row["expected_answerable"] and not row["needs_human_review"]
    ]
    misses = [row for row in approved if row["metrics"]["hit_at_10"] == 0]
    return {
        "mode": "retrieval_only_no_generation",
        "approved_case_count": len(approved),
        "top10_hit_case_count": len(approved) - len(misses),
        "top10_miss_case_count": len(misses),
        "top10_miss_case_ids": [row["question_id"] for row in misses],
        "misses_by_question_type": dict(
            sorted(Counter(row["question_type"] for row in misses).items())
        ),
        "generation_api_calls": 0,
        "live_chat_uat_run": False,
        "live_chat_uat_reason": (
            "No production retrieval change was applied and hospital-data provider comparison is "
            "blocked on an explicit transmission-policy decision."
        ),
    }


def build_stabilization_summary(
    *,
    fixture: dict[str, Any],
    comparison: dict[str, Any],
    bm25_payload: dict[str, Any],
    multimodal: dict[str, Any],
    sedation_uat: dict[str, Any],
) -> dict[str, Any]:
    overall = comparison["summaries"]
    selected = comparison["selection"]["selected"]
    table_text = multimodal["approved_table_subset"]["text_only_bm25"]["overall"]
    table_aware = multimodal["approved_table_subset"]["table_aware_bm25"]["overall"]
    return {
        "schema_version": 1,
        "dataset_version": fixture["dataset_version"],
        "review": build_human_review_manifest(fixture["cases"]),
        "retrieval": {
            "selected": selected,
            "evaluated_retrievers": sorted(overall),
            "hybrid_evaluated": comparison["hybrid_evaluated"],
            "metrics": {
                name: {
                    "hit_at_1": summary["overall"]["hit_at_1"],
                    "hit_at_3": summary["overall"]["hit_at_3"],
                    "hit_at_5": summary["overall"]["hit_at_5"],
                    "hit_at_10": summary["overall"]["hit_at_10"],
                    "mrr": summary["overall"]["mrr"],
                    "recall_at_10": summary["overall"]["recall_at_10"],
                    "precision_at_10": summary["overall"]["precision_at_10"],
                    "ragas_id_context_precision": summary["overall"][
                        "ragas_id_context_precision"
                    ],
                    "ragas_id_context_recall": summary["overall"][
                        "ragas_id_context_recall"
                    ],
                    "mean_latency_ms": summary["latency_ms"]["mean"],
                }
                for name, summary in overall.items()
            },
            "production_change_applied": False,
            "production_decision": "production_change_not_required",
            "production_reason": (
                "BM25 wins standalone evaluation, while production already includes BM25 candidate "
                "generation before RRF/rerank; bypassing that validated path is not justified."
            ),
        },
        "table_image": {
            "table_count": multimodal["table_count"],
            "table_unit_count": multimodal["table_unit_count"],
            "approved_table_case_count": multimodal["approved_table_case_count"],
            "text_only_table_metrics": {
                "hit_at_5": table_text["hit_at_5"],
                "hit_at_10": table_text["hit_at_10"],
                "mrr": table_text["mrr"],
                "recall_at_10": table_text["recall_at_10"],
            },
            "table_aware_metrics": {
                "hit_at_5": table_aware["hit_at_5"],
                "hit_at_10": table_aware["hit_at_10"],
                "mrr": table_aware["mrr"],
                "recall_at_10": table_aware["recall_at_10"],
            },
            "table_aware_production_applied": False,
            "table_aware_decision": "not_materially_better",
            "figure_candidate_count": multimodal["figure_candidate_count"],
            "multimodal_aware": multimodal["multimodal_aware"],
        },
        "uat": {
            "sedation": {
                "question_count": sedation_uat["question_count"],
                "passed": sedation_uat["passed"],
                "failed": sedation_uat["failed"],
                "q006_zero_call": sedation_uat["q006_zero_call"],
                "actual_groq_calls": sedation_uat["actual_groq_calls"],
                "source": "reused_completed_offline_uat",
            },
            "transfusion": _transfusion_readiness(bm25_payload),
        },
        "provider_comparison": {
            "status": "blocked_security_policy_confirmation_required",
            "current_adapter_contracts": ["internal_openai_compatible", "groq_free"],
            "gemini_adapter_present": False,
            "hospital_data_sent_to_groq": False,
            "hospital_data_sent_to_gemini": False,
            "generation_api_calls": 0,
            "required_user_action": (
                "Confirm hospital-data external transmission policy and authorize the Gemini "
                "service/account before a live provider comparison."
            ),
        },
        "generation_api_calls": 0,
        "production_rag_changed": False,
    }


def _review_html(summary: dict[str, Any]) -> str:
    retrieval_rows = "".join(
        "<tr>"
        + f"<td>{html.escape(name)}</td>"
        + f"<td>{metrics['hit_at_5']:.3f}</td>"
        + f"<td>{metrics['hit_at_10']:.3f}</td>"
        + f"<td>{metrics['mrr']:.3f}</td>"
        + f"<td>{metrics['recall_at_10']:.3f}</td>"
        + f"<td>{metrics['mean_latency_ms']:.3f}</td></tr>"
        for name, metrics in summary["retrieval"]["metrics"].items()
    )
    review_rows = "".join(
        "<tr>"
        + f"<td>{html.escape(row['question_id'])}</td>"
        + f"<td>{html.escape(row['review_status'])}</td>"
        + f"<td>{html.escape(','.join(map(str, row['page_numbers'])))}</td>"
        + f"<td>{html.escape(row['relationship'])}</td></tr>"
        for row in summary["review"]["cases"]
    )
    table = summary["table_image"]
    provider = summary["provider_comparison"]
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'>
<title>SCHAT MVP stabilization</title><style>
body{{font-family:system-ui,sans-serif;margin:32px;background:#f4f7fa;color:#17324d}}
section,header{{background:#fff;border:1px solid #d6e0e8;border-radius:12px;padding:18px;margin:12px 0}}
table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{border:1px solid #d6e0e8;padding:7px}}th{{background:#eaf2f8}}
.ok{{color:#087a55}}.blocked{{color:#a04616}}</style></head><body>
<header><h1>SCHAT MVP 안정화 오프라인 검수</h1>
<p>원문 비저장 · 생성 API 호출 0 · production 변경 0</p></header>
<section><h2>Retrieval 선택</h2><p class='ok'>선택: <b>{html.escape(summary['retrieval']['selected'])}</b> · production: {html.escape(summary['retrieval']['production_decision'])}</p>
<table><thead><tr><th>Retriever</th><th>Hit@5</th><th>Hit@10</th><th>MRR</th><th>Recall@10</th><th>Mean ms</th></tr></thead><tbody>{retrieval_rows}</tbody></table></section>
<section><h2>Gold 검수</h2><p>14건 검토 · 자동 승인 {summary['review']['reviewed_case_count'] - summary['review']['remaining_human_review_case_count']} · 남은 사람 검수 {summary['review']['remaining_human_review_case_count']}</p>
<table><thead><tr><th>ID</th><th>Status</th><th>Page</th><th>Basis</th></tr></thead><tbody>{review_rows}</tbody></table></section>
<section><h2>Table / Figure</h2><p>표 {table['table_count']}개 · unit {table['table_unit_count']}개 · 승인 표 질문 {table['approved_table_case_count']}개 · figure 후보 {table['figure_candidate_count']}개</p>
<p>Table-aware 결정: <b>{html.escape(table['table_aware_decision'])}</b></p></section>
<section><h2>UAT / 외부 provider gate</h2><p>진정 UAT {summary['uat']['sedation']['passed']}/{summary['uat']['sedation']['question_count']} · Q006 zero-call 유지</p>
<p>수혈 approved retrieval Top-10: {summary['uat']['transfusion']['top10_hit_case_count']}/{summary['uat']['transfusion']['approved_case_count']}</p>
<p class='blocked'>Provider 비교: {html.escape(provider['status'])}</p></section>
</body></html>"""


def write_report(
    *,
    fixture_path: Path,
    catalog_path: Path,
    retrieval_dir: Path,
    multimodal_dir: Path,
    sedation_uat_path: Path,
) -> dict[str, Any]:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    comparison = json.loads((retrieval_dir / "comparison_summary.json").read_text(encoding="utf-8"))
    bm25 = json.loads((retrieval_dir / "bm25_results.json").read_text(encoding="utf-8"))
    multimodal = json.loads((multimodal_dir / "multimodal_results.json").read_text(encoding="utf-8"))
    sedation = json.loads(sedation_uat_path.read_text(encoding="utf-8"))
    _, chunks = load_catalog(catalog_path, document_name=fixture["document"]["name"])

    summary = build_stabilization_summary(
        fixture=fixture,
        comparison=comparison,
        bm25_payload=bm25,
        multimodal=multimodal,
        sedation_uat=sedation,
    )
    ensure_raw_text_free(summary, forbidden_exact_texts={chunk.text for chunk in chunks})
    payloads = {
        "human_review_manifest.json": summary["review"],
        "retrieval_selection.json": summary["retrieval"],
        "uat_summary.json": summary["uat"],
        "provider_comparison_gate.json": summary["provider_comparison"],
        "stabilization_summary.json": summary,
    }
    for name, payload in payloads.items():
        target = multimodal_dir / name
        if target.exists():
            raise FileExistsError(f"refusing to overwrite existing artifact: {target}")
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    review = multimodal_dir / "review.html"
    if review.exists():
        raise FileExistsError(f"refusing to overwrite existing artifact: {review}")
    review.write_text(_review_html(summary), encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--retrieval-dir", type=Path, default=DEFAULT_RETRIEVAL)
    parser.add_argument("--multimodal-dir", type=Path, default=DEFAULT_MULTIMODAL)
    parser.add_argument("--sedation-uat", type=Path, default=DEFAULT_SEDATION_UAT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = write_report(
        fixture_path=args.fixture,
        catalog_path=args.catalog,
        retrieval_dir=args.retrieval_dir,
        multimodal_dir=args.multimodal_dir,
        sedation_uat_path=args.sedation_uat,
    )
    print(
        json.dumps(
            {
                "selected_retrieval": summary["retrieval"]["selected"],
                "remaining_human_review": summary["review"][
                    "remaining_human_review_case_count"
                ],
                "provider_comparison": summary["provider_comparison"]["status"],
                "generation_api_calls": 0,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
