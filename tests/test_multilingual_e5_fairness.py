import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from tools.chroma_baseline_evaluate import load_catalog
from tools.multilingual_e5_fairness_evaluate import (
    classify_alternate_verdict,
    e5_passage_text,
    e5_query_text,
    evaluate_multilingual_e5,
)

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "library" / "catalog.sqlite3"
FIXTURE = ROOT / "tests" / "fixtures" / "transfusion_retrieval_baseline.json"
BASELINE = ROOT / "artifacts" / "2026-09-17_bm25-vector-fairness-validation"


class FakeLocalE5:
    def embed(self, texts, batch_size=4):
        del batch_size
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            vector = np.frombuffer(digest[:16], dtype=np.uint8).astype(np.float32) + 1
            yield vector / np.linalg.norm(vector)


def _metrics(hit5, hit10, mrr, recall10):
    return {
        "hit_at_5": hit5,
        "hit_at_10": hit10,
        "mrr": mrr,
        "recall_at_10": recall10,
    }


def test_e5_prefixes_follow_official_query_and_passage_contract_once():
    assert e5_query_text("수혈 부작용은?") == "query: 수혈 부작용은?"
    assert e5_passage_text("수혈 근거 문장") == "passage: 수혈 근거 문장"
    assert e5_query_text("query: 이미 처리됨") == "query: 이미 처리됨"
    assert e5_passage_text("passage: 이미 처리됨") == "passage: 이미 처리됨"


@pytest.mark.parametrize(
    ("minimal", "current", "alternate", "expected"),
    [
        (
            _metrics(.60, .70, .45, .65),
            _metrics(.80, .90, .60, .85),
            _metrics(.30, .40, .20, .35),
            "A",
        ),
        (
            _metrics(.60, .70, .45, .65),
            _metrics(.80, .90, .60, .85),
            _metrics(.58, .68, .43, .63),
            "B",
        ),
        (
            _metrics(.60, .70, .45, .65),
            _metrics(.65, .72, .48, .68),
            _metrics(.82, .91, .65, .86),
            "C",
        ),
    ],
)
def test_alternate_verdict_uses_frozen_a_b_c_contract(
    minimal, current, alternate, expected
):
    assert classify_alternate_verdict(minimal, current, alternate)["code"] == expected


def test_fake_e5_full_evaluation_reuses_frozen_dataset_and_writes_raw_free_artifacts(
    tmp_path,
):
    output = tmp_path / "e5-fairness"

    report = evaluate_multilingual_e5(
        catalog_path=CATALOG,
        fixture_path=FIXTURE,
        baseline_dir=BASELINE,
        output_dir=output,
        engine=FakeLocalE5(),
        expected_dimensions=16,
        model_cache_size_bytes=123,
    )

    assert report["dataset"] == {
        "dataset_version": "transfusion-retrieval-v3",
        "chunk_version": 4,
        "chunk_count": 105,
        "case_count": 90,
        "evaluated_positive_count": 78,
        "human_review_count": 1,
        "negative_count": 11,
    }
    assert report["model_audit"]["query_prefix"] == "query: "
    assert report["model_audit"]["passage_prefix"] == "passage: "
    assert report["model_audit"]["dimensions"] == 16
    assert report["model_audit"]["chunk_vector_count"] == 105
    assert report["model_audit"]["model_cache_size_bytes"] == 123
    assert report["summaries"]["vector_multilingual_e5_large"][
        "negative_diagnostics"
    ]["negative_top1"]["count"] == 11
    assert report["verdict"]["code"] in {"A", "B", "C"}
    assert report["safety"] == {
        "production_retrieval_changed": False,
        "production_embedding_changed": False,
        "generation_api_calls": 0,
        "external_embedding_api_calls": 0,
        "hospital_data_external_transfers": 0,
        "source_text_persisted": False,
        "question_text_persisted": False,
    }

    expected_files = {
        "model_audit.json",
        "comparison_metrics.csv",
        "metrics_by_question_type.csv",
        "e5_results.json",
        "e5_summary.json",
        "test_results.json",
        "review.html",
    }
    assert expected_files == {path.name for path in output.iterdir()}

    _, chunks = load_catalog(CATALOG, document_name="실무지침서_수혈간호.pdf")
    persisted = "\n".join(
        path.read_text(encoding="utf-8-sig", errors="ignore")
        for path in output.iterdir()
    )
    assert all(chunk.text not in persisted for chunk in chunks)
    assert '"question"' not in persisted
    assert "수혈 부작용은?" not in persisted
    assert json.loads((output / "test_results.json").read_text(encoding="utf-8"))[
        "status"
    ] == "pending final verification"
