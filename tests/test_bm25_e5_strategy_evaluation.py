from pathlib import Path

import numpy as np
import pytest

from tools.bm25_e5_strategy_evaluate import (
    SelectiveSignals,
    _rss_bytes,
    _select_final_strategy,
    decide_selective_e5,
    evaluate_bm25_e5_strategy,
    fuse_rrf,
    load_vector_snapshot,
    save_vector_snapshot,
)

ROOT = Path(__file__).resolve().parents[1]


class FakeE5:
    def embed(self, texts, batch_size=4):
        del batch_size
        for position, text in enumerate(texts):
            seed = sum(text.encode("utf-8")) + position
            vector = np.arange(1, 1025, dtype=np.float32)
            yield np.roll(vector, seed % 1024)


def test_rrf_uses_rank_only_k60_and_source_order_for_ties():
    ids, scores = fuse_rrf(
        [["a", "b", "c"], ["d", "c", "b"]],
        source_order={"a": 0, "b": 1, "c": 2, "d": 3},
        limit=4,
    )

    assert ids == ["b", "c", "a", "d"]
    assert scores[0] == scores[1] > scores[2] == scores[3]
    assert scores[0] == pytest.approx(1 / 62 + 1 / 63)


def test_selective_policy_uses_generic_confidence_and_intent_signals():
    weak_procedure = decide_selective_e5(
        SelectiveSignals(
            kind="procedure",
            requested_phase="",
            bm25_top_score=2.0,
            bm25_gap_ratio=0.03,
            lexical_overlap=0.2,
            has_exact_signal=False,
            follow_up=False,
        )
    )
    exact_numeric = decide_selective_e5(
        SelectiveSignals(
            kind="fact",
            requested_phase="during",
            bm25_top_score=1.0,
            bm25_gap_ratio=0.01,
            lexical_overlap=0.1,
            has_exact_signal=True,
            follow_up=False,
        )
    )
    strong_lexical = decide_selective_e5(
        SelectiveSignals(
            kind="fact",
            requested_phase="",
            bm25_top_score=8.0,
            bm25_gap_ratio=0.4,
            lexical_overlap=0.8,
            has_exact_signal=False,
            follow_up=False,
        )
    )

    assert weak_procedure.use_e5 is True
    assert "semantic_intent" in weak_procedure.reasons
    assert exact_numeric.use_e5 is False
    assert exact_numeric.reasons == ("exact_lexical_priority",)
    assert strong_lexical.use_e5 is False
    assert strong_lexical.reasons == ("bm25_confident",)


def test_vector_snapshot_is_raw_free_and_rejects_contract_or_id_drift(tmp_path):
    path = tmp_path / "e5-snapshot.npz"
    ids = ("c1", "c2")
    vectors = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    contract = {
        "dataset_version": "v3",
        "document_version": "sha256:abc",
        "dimensions": 2,
    }

    save_vector_snapshot(path, identifiers=ids, vectors=vectors, contract=contract)
    loaded_ids, loaded_vectors = load_vector_snapshot(
        path, expected_identifiers=ids, expected_contract=contract
    )

    assert loaded_ids == ids
    np.testing.assert_array_equal(loaded_vectors, vectors)
    with pytest.raises(ValueError, match="contract drift"):
        load_vector_snapshot(
            path,
            expected_identifiers=ids,
            expected_contract={**contract, "document_version": "sha256:other"},
        )
    with pytest.raises(ValueError, match="identifier drift"):
        load_vector_snapshot(
            path,
            expected_identifiers=("c2", "c1"),
            expected_contract=contract,
        )

    raw = path.read_bytes()
    assert b"source_text" not in raw
    assert b"question" not in raw


def test_fake_full_strategy_evaluation_keeps_all_five_configs_raw_free(tmp_path):
    output = tmp_path / "strategy"
    report = evaluate_bm25_e5_strategy(
        catalog_path=ROOT / "data" / "library" / "catalog.sqlite3",
        fixture_path=ROOT / "tests" / "fixtures" / "transfusion_retrieval_baseline.json",
        cache_dir=ROOT / "data" / "models",
        snapshot_path=tmp_path / "snapshot.npz",
        output_dir=output,
        engine=FakeE5(),
    )

    assert report["dataset"]["chunk_count"] == 105
    assert report["dataset"]["case_count"] == 90
    assert set(report["summaries"]) == {
        "bm25_current",
        "e5_large",
        "bm25_e5_rrf",
        "bm25_e5_rrf_rerank",
        "bm25_selective_e5",
    }
    assert report["selective_policy"]["gold_or_question_id_inputs"] is False
    assert report["safety"]["generation_api_calls"] == 0
    assert report["safety"]["hospital_data_external_transfers"] == 0
    assert report["decision"]["code"] in {"A", "B", "C", "D", "E"}
    assert (output / "review.html").is_file()
    persisted = "\n".join(
        path.read_text(encoding="utf-8-sig", errors="ignore")
        for path in output.iterdir()
    )
    assert '"question"' not in persisted


def test_production_selection_requires_improvement_through_existing_reranker():
    def summary(core, latency=100.0):
        return {
            "overall": {
                "hit_at_5": core,
                "hit_at_10": core,
                "mrr": core,
                "recall_at_10": core,
            },
            "latency_ms": {"mean": latency},
        }

    decision = _select_final_strategy(
        {
            "bm25_current": summary(0.77, 0.4),
            "e5_large": summary(0.86, 190.0),
            "bm25_e5_rrf": summary(0.88, 191.0),
            "bm25_e5_rrf_rerank": summary(0.78, 260.0),
            "bm25_selective_e5": summary(0.81, 85.0),
        },
        selective_rate=0.44,
    )

    assert decision["best_accuracy_config"] == "bm25_e5_rrf"
    assert decision["code"] == "E"
    assert decision["production_changed"] is False


def test_process_rss_measurement_is_available_on_supported_runtime():
    assert _rss_bytes() > 0
