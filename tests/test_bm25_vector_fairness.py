import csv
import json
from pathlib import Path

import numpy as np
import pytest

from tools.bm25_vector_fairness_evaluate import (
    build_representation,
    classify_fairness_verdict,
    common_query,
    ensure_fairness_artifact_safe,
    evaluate_fairness,
    exact_vector_ranking,
    minimal_tokens,
    rank_current_bm25,
)
from tools.chroma_baseline_evaluate import load_catalog

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "library" / "catalog.sqlite3"
FIXTURE = ROOT / "tests" / "fixtures" / "transfusion_retrieval_baseline.json"
PRIOR_BM25 = ROOT / "artifacts" / "2026-09-17_transfusion-expanded-retrieval" / "bm25_results.csv"


def test_common_query_splits_attached_aspect_and_removes_request_noise():
    assert common_query("수혈절차에 대해 알려줘") == "수혈 절차"
    assert common_query("수혈 전 확인사항은 무엇인가요?") == "수혈 전 확인사항"


def test_minimal_tokens_do_not_reuse_schat_ngrams_or_alias_expansion():
    assert minimal_tokens("PRBC 수혈절차 알려줘") == ("prbc", "수혈절차", "알려줘")
    assert all(not token.startswith("ko:") for token in minimal_tokens("수혈절차"))
    assert all(not token.startswith("entity:") for token in minimal_tokens("PRBC"))


def test_exact_vector_ranking_uses_cosine_direction_and_stable_source_tiebreak():
    matrix = np.asarray([[1.0, 0.0], [0.0, 1.0], [1.0, 0.0]], dtype=np.float32)
    query = np.asarray([1.0, 0.0], dtype=np.float32)

    identifiers, scores = exact_vector_ranking(
        query, matrix, ("first", "other", "third"), limit=3
    )

    assert identifiers == ["first", "third", "other"]
    assert scores == pytest.approx([1.0, 1.0, 0.0])


def test_representation_preserves_metadata_order_and_respects_model_limit():
    class Counter:
        def count(self, text):
            return len(text.split())

    representation = build_representation(
        document_name="실무지침서_수혈간호.pdf",
        title="수혈간호",
        section="수혈 전 확인",
        body=" ".join(f"본문{i}" for i in range(20)),
        mode="document_title_section_body",
        embedder=Counter(),
        maximum_tokens=10,
    )

    assert representation.startswith("실무지침서 수혈간호 수혈간호 수혈 전 확인")
    assert Counter().count(representation) <= 10


@pytest.mark.parametrize(
    ("minimal", "current", "vector", "expected"),
    [
        (
            {"hit_at_5": .80, "hit_at_10": .90, "mrr": .60, "recall_at_10": .85},
            {"hit_at_5": .85, "hit_at_10": .92, "mrr": .65, "recall_at_10": .88},
            {"hit_at_5": .60, "hit_at_10": .70, "mrr": .40, "recall_at_10": .65},
            "A",
        ),
        (
            {"hit_at_5": .60, "hit_at_10": .70, "mrr": .40, "recall_at_10": .65},
            {"hit_at_5": .80, "hit_at_10": .90, "mrr": .60, "recall_at_10": .85},
            {"hit_at_5": .61, "hit_at_10": .71, "mrr": .41, "recall_at_10": .66},
            "B",
        ),
        (
            {"hit_at_5": .55, "hit_at_10": .65, "mrr": .35, "recall_at_10": .60},
            {"hit_at_5": .80, "hit_at_10": .90, "mrr": .60, "recall_at_10": .85},
            {"hit_at_5": .78, "hit_at_10": .88, "mrr": .59, "recall_at_10": .83},
            "C",
        ),
        (
            {"hit_at_5": .50, "hit_at_10": .60, "mrr": .30, "recall_at_10": .55},
            {"hit_at_5": .60, "hit_at_10": .70, "mrr": .40, "recall_at_10": .65},
            {"hit_at_5": .80, "hit_at_10": .90, "mrr": .60, "recall_at_10": .85},
            "D",
        ),
    ],
)
def test_fairness_verdict_uses_frozen_core_metric_rules(minimal, current, vector, expected):
    assert classify_fairness_verdict(minimal, current, vector)["code"] == expected


def test_current_bm25_reproduces_existing_baseline_ranking_for_first_case():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    _, chunks = load_catalog(CATALOG, document_name=fixture["document"]["name"])
    case = fixture["cases"][0]
    with PRIOR_BM25.open(encoding="utf-8-sig", newline="") as handle:
        rows = [
            row for row in csv.DictReader(handle)
            if row["question_id"] == case["question_id"] and row["k"] == "10"
        ]
    expected = [row["retrieved_chunk_id"] for row in sorted(rows, key=lambda row: int(row["rank"]))]

    identifiers, _scores, _latency = rank_current_bm25(case["question"], chunks, limit=10)

    assert identifiers == expected


def test_artifact_safety_rejects_questions_and_registered_exact_text():
    exact = {"병원 원문 전체 문장"}
    ensure_fairness_artifact_safe({"case_id": "TF001", "metric": 1.0}, exact)

    with pytest.raises(ValueError, match="question"):
        ensure_fairness_artifact_safe({"question": "수혈 질문"}, exact)
    with pytest.raises(ValueError, match="exact source"):
        ensure_fairness_artifact_safe({"note": "병원 원문 전체 문장"}, exact)


def test_full_fairness_evaluation_uses_frozen_corpus_and_writes_raw_free_artifacts(tmp_path):
    output = tmp_path / "fairness"

    report = evaluate_fairness(
        catalog_path=CATALOG,
        fixture_path=FIXTURE,
        prior_results_dir=PRIOR_BM25.parent,
        output_dir=output,
    )

    assert report["dataset"]["case_count"] == 90
    assert report["dataset"]["chunk_count"] == 105
    assert report["dataset"]["evaluated_positive_count"] == 78
    assert report["dataset"]["negative_count"] == 11
    assert {
        "bm25_minimal_raw",
        "bm25_current_baseline",
        "bm25_production_query",
        "vector_current_minilm",
        "vector_title_section_body",
    }.issubset(report["summaries"])
    assert report["safety"] == {
        "production_retrieval_changed": False,
        "generation_api_calls": 0,
        "external_embedding_api_calls": 0,
        "source_text_persisted": False,
    }
    assert report["vector_audit"]["catalog_vector_count"] == 105
    assert report["vector_audit"]["catalog_dimensions"] == 384
    assert report["vector_audit"]["deterministic_rebuild"] is True
    assert report["baseline_reproduction"]["bm25_top10_exact_match_rate"] == 1.0
    assert report["preprocessing_audit"]["production_bm25_candidate_limit"] == 40
    assert report["summaries"]["bm25_current_baseline"]["negative_diagnostics"][
        "positive_top1"
    ]["count"] == 78
    assert report["summaries"]["bm25_current_baseline"]["negative_diagnostics"][
        "negative_top1"
    ]["count"] == 11
    assert report["verdict"]["code"] in {"A", "B", "C", "D"}
    expected_files = {
        "preprocessing_audit.json",
        "bm25_ablation.json",
        "vector_config_audit.json",
        "embedding_comparison.json",
        "representation_comparison.json",
        "metrics.csv",
        "metrics_by_type.csv",
        "latency.json",
        "test_results.json",
        "review.html",
    }
    assert expected_files == {path.name for path in output.iterdir()}

    _, chunks = load_catalog(CATALOG, document_name="실무지침서_수혈간호.pdf")
    persisted = "\n".join(
        path.read_text(encoding="utf-8-sig", errors="ignore")
        for path in output.iterdir()
        if path.suffix in {".json", ".csv", ".html"}
    )
    assert all(chunk.text not in persisted for chunk in chunks)
    assert '"question"' not in persisted
