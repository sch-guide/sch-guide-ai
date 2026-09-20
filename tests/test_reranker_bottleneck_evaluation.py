import inspect
import json
from pathlib import Path

from tools.reranker_bottleneck_evaluate import (
    CandidateRanks,
    decide_production_retrieval,
    evaluate_reranker_bottleneck,
    rerank_candidates,
)

ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT / "workspace" / "과거작업" / "평가산출물" / "2026-09-17_bm25-e5-retrieval-strategy-02"


def _candidates() -> tuple[CandidateRanks, ...]:
    return tuple(
        CandidateRanks(
            chunk_id=f"c{position}",
            raw_rank=position,
            raw_score=1 / (60 + position),
            bm25_rank=position if position % 2 else None,
            e5_rank=position if position % 2 == 0 else None,
            current_rerank_rank=11 - position,
            query_overlap=(position % 3) / 2,
        )
        for position in range(1, 11)
    )


def test_ranking_contract_has_no_gold_or_question_id_inputs():
    parameters = inspect.signature(rerank_candidates).parameters

    assert "gold_ids" not in parameters
    assert "question_id" not in parameters
    assert set(parameters) == {"candidates", "strategy"}


def test_safe_variants_preserve_raw_top10_identity_and_bound_displacement():
    candidates = _candidates()
    raw_ids = {candidate.chunk_id for candidate in candidates}

    for strategy in (
        "no_reranker",
        "normalized_input",
        "candidate_aware",
        "rank_preserving",
        "top_n_rerank",
        "origin_feature",
    ):
        ranked = rerank_candidates(candidates=candidates, strategy=strategy)
        assert {candidate.chunk_id for candidate in ranked} == raw_ids
        assert len(ranked) == len(candidates)

    bounded = rerank_candidates(candidates=candidates, strategy="rank_preserving")
    positions = {candidate.chunk_id: rank for rank, candidate in enumerate(bounded, 1)}
    assert max(abs(positions[item.chunk_id] - item.raw_rank) for item in candidates) <= 2


def test_decision_rejects_slow_or_recall_regressing_candidate():
    summaries = {
        "bm25_current": {
            "overall": {"hit_at_5": 0.79, "hit_at_10": 0.88, "mrr": 0.56, "recall_at_10": 0.84},
            "latency_ms": {"mean": 0.4},
        },
        "no_reranker": {
            "overall": {"hit_at_5": 0.92, "hit_at_10": 0.95, "mrr": 0.68, "recall_at_10": 0.94},
            "latency_ms": {"mean": 194.0},
        },
        "rank_preserving": {
            "overall": {"hit_at_5": 0.84, "hit_at_10": 0.90, "mrr": 0.62, "recall_at_10": 0.82},
            "latency_ms": {"mean": 195.0},
        },
    }

    decision = decide_production_retrieval(summaries)

    assert decision["production_changed"] is False
    assert decision["selected"] == "existing_production_hybrid"
    assert "latency" in decision["reasons"]
    assert "recall_regression" in decision["reasons"]


def test_frozen_evaluation_is_raw_free_and_covers_requested_variants(tmp_path):
    output = tmp_path / "reranker"
    report = evaluate_reranker_bottleneck(
        fixture_path=ROOT / "tests" / "fixtures" / "transfusion_retrieval_baseline.json",
        catalog_path=ROOT / "data" / "library" / "catalog.sqlite3",
        frozen_results_dir=FROZEN,
        output_dir=output,
    )

    assert report["dataset"]["case_count"] == 90
    assert report["dataset"]["approved_positive_count"] == 78
    assert set(report["summaries"]) >= {
        "bm25_current",
        "e5_large",
        "no_reranker",
        "current_reranker",
        "normalized_input",
        "candidate_aware",
        "rank_preserving",
        "top_n_rerank",
        "origin_feature",
    }
    assert report["safety"] == {
        "gold_used_during_ranking": False,
        "question_id_used_during_ranking": False,
        "production_retrieval_changed": False,
        "external_api_calls": 0,
        "hospital_data_external_transfers": 0,
        "source_text_persisted": False,
        "question_text_persisted": False,
    }
    assert (output / "review.html").is_file()
    persisted = "\n".join(
        path.read_text(encoding="utf-8-sig", errors="ignore")
        for path in output.iterdir()
        if path.suffix in {".json", ".csv", ".html"}
    )
    fixture = json.loads(
        (ROOT / "tests" / "fixtures" / "transfusion_retrieval_baseline.json").read_text(
            encoding="utf-8"
        )
    )
    assert '"question"' not in persisted
    assert all(case["question"] not in persisted for case in fixture["cases"])
