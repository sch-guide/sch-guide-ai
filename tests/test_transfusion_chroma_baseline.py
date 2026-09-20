import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from src.settings import DIMENSIONS, MODEL
from tools.chroma_baseline_evaluate import (
    CatalogChunk,
    _ragas_id_scores,
    build_chroma_collection,
    build_comparison_schema,
    build_safe_case_result,
    ensure_raw_text_free,
    load_catalog,
    query_chroma,
    validate_evaluation_cases,
    validate_fixture_document,
)
from tools.retrieval_baseline_metrics import (
    aggregate_metrics,
    id_based_context_scores,
    ranked_retrieval_metrics,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "transfusion_retrieval_baseline.json"
CATALOG = ROOT / "data" / "library" / "catalog.sqlite3"


def test_ranked_metrics_use_literal_cutoff_denominators_and_first_relevant_rank():
    metrics = ranked_retrieval_metrics(["a", "x", "b", "y"], ["a", "b"], cutoffs=(1, 3, 5))
    assert metrics == {
        "hit_at_1": 1.0,
        "hit_at_3": 1.0,
        "hit_at_5": 1.0,
        "mrr": 1.0,
        "recall_at_1": 0.5,
        "recall_at_3": 1.0,
        "recall_at_5": 1.0,
        "precision_at_1": 1.0,
        "precision_at_3": 2 / 3,
        "precision_at_5": 0.5,
    }


def test_ranked_metrics_reject_duplicate_or_empty_ids():
    with pytest.raises(ValueError, match="duplicate retrieved context id"):
        ranked_retrieval_metrics(["a", "a"], ["a"], cutoffs=(1,))
    with pytest.raises(ValueError, match="reference context ids"):
        ranked_retrieval_metrics(["a"], [], cutoffs=(1,))


def test_id_based_context_scores_match_ragas_set_formulas():
    scores = id_based_context_scores(
        ["doc-1", "doc-2", "doc-3", "doc-4"], ["doc-1", "doc-4", "doc-5", "doc-6"]
    )
    assert scores == {"context_precision": 0.5, "context_recall": 0.5}


def test_aggregate_metrics_excludes_pending_human_review_cases():
    rows = [
        {"needs_human_review": False, "question_type": "fact", "metrics": {"hit_at_1": 1.0}},
        {"needs_human_review": True, "question_type": "fact", "metrics": {"hit_at_1": 0.0}},
        {"needs_human_review": False, "question_type": "procedure", "metrics": {"hit_at_1": 0.0}},
    ]
    result = aggregate_metrics(rows)
    assert result["evaluated_case_count"] == 2
    assert result["human_review_case_count"] == 1
    assert result["overall"] == {"hit_at_1": 0.5}
    assert result["by_question_type"] == {
        "fact": {"case_count": 1, "hit_at_1": 1.0},
        "procedure": {"case_count": 1, "hit_at_1": 0.0},
    }


def _chunk(text="독립적으로 의미가 완결된 수혈 관련 본문입니다.", *, substantive=True):
    return CatalogChunk(
        chunk_id="chunk-1",
        document_id="doc-1",
        position=0,
        page=1,
        section="검사",
        parent_id="parent-1",
        text=text,
        vector=np.array([0.0, 1.0], dtype=np.float32),
        substantive_body=substantive,
    )


def _case(text, *, source_shape="body", review=False):
    return {
        "question_id": "TF001",
        "question_type": "fact_specific",
        "question": "검사는 어떻게 확인하나요?",
        "expected_answerable": True,
        "reference_context_ids": ["chunk-1"],
        "reference_parent_ids": ["parent-1"],
        "reference_contexts": [
            {
                "chunk_id": "chunk-1",
                "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "page": 1,
                "parent_id": "parent-1",
                "substantive_body": True,
                "source_shape": source_shape,
            }
        ],
        "needs_human_review": review,
        "review_reasons": ["table_layout"] if review else [],
    }


def test_fixture_validation_rejects_fingerprint_or_structure_drift():
    chunk = _chunk()
    case = _case(chunk.text)
    assert (
        validate_evaluation_cases([case], [chunk], minimum_cases=1, maximum_cases=1)["approved_case_count"]
        == 1
    )
    case["reference_contexts"][0]["text_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="fingerprint drift"):
        validate_evaluation_cases([case], [chunk], minimum_cases=1, maximum_cases=1)


def test_fixture_validation_requires_review_for_table_or_image_gold():
    chunk = _chunk()
    with pytest.raises(ValueError, match="table/image reference requires human review"):
        validate_evaluation_cases(
            [_case(chunk.text, source_shape="table")], [chunk], minimum_cases=1, maximum_cases=1
        )
    assert (
        validate_evaluation_cases(
            [_case(chunk.text, source_shape="table", review=True)],
            [chunk],
            minimum_cases=1,
            maximum_cases=1,
        )["human_review_case_count"]
        == 1
    )


def test_registered_fixture_has_expanded_cases_and_valid_stable_gold():
    metadata, chunks = load_catalog(CATALOG, document_name="실무지침서_수혈간호.pdf")
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    validate_fixture_document(fixture["document"], metadata, chunks)
    audit = validate_evaluation_cases(fixture["cases"], chunks, minimum_cases=80, maximum_cases=100)
    assert fixture["schema_version"] == 3
    assert fixture["dataset_version"] == "transfusion-retrieval-v3"
    assert metadata["chunk_count"] == 105
    assert metadata["model"] == MODEL
    assert metadata["chunk_version"] == 4
    assert len(chunks) == 105
    assert all(chunk.vector.shape == (DIMENSIONS,) and np.isfinite(chunk.vector).all() for chunk in chunks)
    assert 80 <= audit["case_count"] <= 100
    assert audit["approved_case_count"] >= 70
    assert audit == {
        "case_count": 90,
        "positive_case_count": 79,
        "approved_case_count": 78,
        "human_review_case_count": 1,
        "negative_case_count": 11,
    }
    assert len({case["question_type"] for case in fixture["cases"]}) >= 8
    ensure_raw_text_free(fixture, forbidden_exact_texts={chunk.text for chunk in chunks})


def test_fixture_v3_separates_positive_gold_and_negative_diagnostics():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    positive = [case for case in fixture["cases"] if case["expected_answerable"]]
    negative = [case for case in fixture["cases"] if not case["expected_answerable"]]
    assert len(positive) == 79
    assert len(negative) == 11
    assert {
        "preparation",
        "procedure",
        "monitoring",
        "adverse_reaction",
        "product_specific",
        "fact_specific",
        "temporal",
        "paraphrase",
        "negative_out_of_scope",
    } == {case["question_type"] for case in fixture["cases"]}
    for case in positive:
        assert case["reference_context_ids"] == [item["chunk_id"] for item in case["reference_contexts"]]
        assert case["reference_parent_ids"] == list(
            dict.fromkeys(item["parent_id"] for item in case["reference_contexts"])
        )
    for case in negative:
        assert case["question_type"] == "negative_out_of_scope"
        assert case["reference_context_ids"] == []
        assert case["reference_parent_ids"] == []
        assert case["reference_contexts"] == []
        assert case["needs_human_review"] is False


def test_fixture_document_contract_rejects_catalog_identity_drift():
    metadata, chunks = load_catalog(CATALOG, document_name="실무지침서_수혈간호.pdf")
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    drifted = {**fixture["document"], "model": "different-model"}
    with pytest.raises(ValueError, match="fixture document metadata drift"):
        validate_fixture_document(drifted, metadata, chunks)


def test_evaluation_set_size_contract_cannot_be_bypassed_by_one_case():
    chunk = _chunk()
    with pytest.raises(ValueError, match="30 to 50"):
        validate_evaluation_cases([_case(chunk.text)], [chunk])


def test_safe_results_and_comparison_schema_contain_ids_not_source_text():
    chunks = [_chunk()]
    result = build_safe_case_result(
        case=_case(chunks[0].text),
        retrieved_ids=["chunk-1"],
        distances=[0.1],
        metrics={"hit_at_1": 1.0},
        ragas={"context_precision": 1.0, "context_recall": 1.0},
        embedding_latency_ms=1.0,
        query_latency_ms=2.0,
    )
    ensure_raw_text_free(result, forbidden_exact_texts={chunks[0].text})
    assert chunks[0].text not in json.dumps(result, ensure_ascii=False)
    schema = build_comparison_schema(cutoffs=(1, 3, 5, 10))
    assert schema["join_keys"] == ["dataset_id", "case_id", "chunk_id"]
    assert schema["metric_fields"] == [
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
    ]


def test_artifact_payload_rejects_source_text_fields_or_exact_source_text():
    ensure_raw_text_free(
        {"case_id": "TF001", "retrieved_context_ids": ["chunk-1"]}, forbidden_exact_texts=set()
    )
    with pytest.raises(ValueError, match="forbidden text field"):
        ensure_raw_text_free({"chunk_text": "민감한 병원 원문"}, forbidden_exact_texts=set())
    with pytest.raises(ValueError, match="exact source text"):
        ensure_raw_text_free({"note": "민감한 병원 원문"}, forbidden_exact_texts={"민감한 병원 원문"})


def test_chroma_collection_stores_embeddings_and_ids_without_documents(tmp_path):
    pytest.importorskip("chromadb")
    first = _chunk("first clinical source")
    second = CatalogChunk(
        chunk_id="chunk-2",
        document_id="doc-1",
        position=1,
        page=1,
        section="section",
        parent_id="parent-1",
        text="second clinical source",
        vector=np.array([1.0, 0.0], dtype=np.float32),
        substantive_body=True,
    )
    _, collection = build_chroma_collection(tmp_path / "index", [first, second], name="test_collection")
    stored = collection.get(include=["documents", "metadatas"])
    assert stored["documents"] is None or all(value is None for value in stored["documents"])
    retrieved, distances = query_chroma(
        collection,
        np.array([0.0, 1.0], dtype=np.float32),
        limit=2,
    )
    assert retrieved == ["chunk-1", "chunk-2"]
    assert distances[0] == pytest.approx(0.0, abs=1e-6)


def test_ragas_id_metrics_run_without_llm_judge():
    pytest.importorskip("ragas")
    assert _ragas_id_scores(["a", "b"], ["b", "c"]) == {
        "context_precision": 0.5,
        "context_recall": 0.5,
    }
