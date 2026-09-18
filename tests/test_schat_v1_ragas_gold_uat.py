import json
from pathlib import Path

from tools.schat_v1_final_validate import evaluate_operational_uat
from tools.schat_v1_ragas_gold_uat_evaluate import (
    build_ragas_scorecard,
    compare_retrieval_metrics,
    evaluate_gold_match,
    load_operational_gold_labels,
    resolve_gold_references,
)

ROOT = Path(__file__).resolve().parents[1]
UAT = ROOT / "tests" / "fixtures" / "schat_v1_operational_uat.json"
GOLD = ROOT / "tests" / "fixtures" / "schat_v1_operational_gold.json"
TRANSFUSION = ROOT / "tests" / "fixtures" / "transfusion_retrieval_baseline.json"
SEDATION = ROOT / "tests" / "fixtures" / "q002_gold_stages.json"
CATALOG = ROOT / "data" / "library" / "catalog.sqlite3"


def test_operational_gold_labels_cover_every_uat_case_without_raw_text():
    labels = load_operational_gold_labels(GOLD)
    uat = json.loads(UAT.read_text(encoding="utf-8"))

    assert {case["case_id"] for case in labels["cases"]} == {
        case["case_id"] for case in uat["cases"]
    }
    encoded = GOLD.read_text(encoding="utf-8")
    assert '"question"' not in encoded
    assert '"exact_text"' not in encoded
    assert '"reference_context_ids"' not in encoded


def test_gold_references_resolve_only_from_fixed_human_reviewed_fixtures():
    labels = load_operational_gold_labels(GOLD)
    resolved = resolve_gold_references(
        labels,
        transfusion_fixture_path=TRANSFUSION,
        sedation_fixture_path=SEDATION,
    )

    assert len(resolved) == 36
    assert all(not row["gold_context_ids"] for row in resolved if row["expected_abstain"])
    assert all(
        row["aggregate_eligible"] is False
        for row in resolved
        if row["label_status"] != "approved"
    )
    assert all("question" not in row for row in resolved)
    assert all("source_text" not in row for row in resolved)


def test_ragas_scorecard_keeps_llm_judge_metrics_pending_without_live_approval():
    scorecard = build_ragas_scorecard(
        id_context_precision=0.1192,
        id_context_recall=0.8379,
        provider_live_approved=False,
        llm_judge_approved=False,
    )

    assert scorecard["id_context_precision"] == 0.1192
    assert scorecard["id_context_recall"] == 0.8379
    assert scorecard["faithfulness"] == "pending_external_llm_judge_approval"
    assert scorecard["answer_relevancy"] == "pending_external_llm_judge_approval"
    assert scorecard["actual_provider_calls"] == 0
    assert scorecard["actual_llm_judge_calls"] == 0


def test_gold_match_separates_provisional_diagnostic_from_approved_aggregate():
    provisional = evaluate_gold_match(
        {
            "label_status": "provisional_needs_human_review",
            "aggregate_eligible": False,
            "expected_abstain": False,
            "gold_context_ids": ["gold-a"],
            "critical_requirements_status": "inherited_candidate_needs_review",
        },
        selected_context_ids=["gold-a"],
        safely_abstained=False,
    )
    abstain = evaluate_gold_match(
        {
            "label_status": "approved",
            "aggregate_eligible": True,
            "expected_abstain": True,
            "gold_context_ids": [],
            "critical_requirements_status": "abstention_zero_call",
        },
        selected_context_ids=[],
        safely_abstained=True,
    )

    assert provisional["candidate_gold_match"] is True
    assert provisional["aggregate_gold_match"] is None
    assert abstain["aggregate_gold_match"] is True


def test_operational_uat_reports_gold_without_serializing_gold_ids():
    labels = load_operational_gold_labels(GOLD)
    resolved = resolve_gold_references(
        labels,
        transfusion_fixture_path=TRANSFUSION,
        sedation_fixture_path=SEDATION,
    )
    summary, rows, _ = evaluate_operational_uat(
        catalog_path=CATALOG,
        fixture_path=UAT,
        gold_labels={row["case_id"]: row for row in resolved},
    )

    assert summary["gold_approved_case_count"] == 4
    assert summary["gold_approved_pass_count"] == 4
    assert summary["gold_provisional_case_count"] == 32
    assert all("gold_context_ids" not in row for row in rows)
    assert all("selected_chunk_ids" not in row for row in rows)


def test_retrieval_comparison_requires_same_fixed_metrics_without_regression():
    before = {
        "hit_at_1": 0.4,
        "hit_at_3": 0.6,
        "hit_at_5": 0.8,
        "hit_at_10": 0.9,
        "mrr": 0.55,
        "recall_at_1": 0.3,
        "recall_at_3": 0.5,
        "recall_at_5": 0.7,
        "recall_at_10": 0.84,
        "precision_at_1": 0.4,
        "precision_at_3": 0.2,
        "precision_at_5": 0.18,
        "precision_at_10": 0.12,
        "ragas_id_context_precision": 0.12,
        "ragas_id_context_recall": 0.84,
    }

    comparison = compare_retrieval_metrics(before, dict(before))

    assert comparison["regression"] is False
    assert comparison["all_metrics_equal"] is True
    assert all(delta == 0 for delta in comparison["delta"].values())
