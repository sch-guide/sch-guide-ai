import ast
import json
from pathlib import Path

import pytest

from tools import current_hybrid_evaluate as evaluator

ROOT = Path(__file__).resolve().parents[1]


def test_current_hybrid_evaluator_exists_as_an_evaluation_only_tool() -> None:
    assert (ROOT / "tools" / "current_hybrid_evaluate.py").is_file()


def test_contract_calls_the_existing_production_hybrid_boundary() -> None:
    assert evaluator.retrieval_contract() == {
        "engine": "current_hybrid",
        "production_search": "src.library.LocalLibrary.search",
        "minilm_calls_per_query": 1,
        "bm25_enabled": True,
        "faiss_enabled": True,
        "rrf_enabled": True,
        "reranker_enabled": True,
        "context_expansion": True,
        "evidence_gate": False,
        "source_unit": False,
        "llm_calls": 0,
        "validator_calls": 0,
    }
    source = (ROOT / "tools" / "current_hybrid_evaluate.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "BM25Index" not in imported_names
    assert "rrf" not in imported_names
    assert "rerank" not in imported_names
    assert "expand_context" not in imported_names
    assert ".search(" in source


def test_case_result_uses_the_frozen_metric_and_failure_contract() -> None:
    case = evaluator.ApprovedCase(
        case_id="A",
        question="q",
        question_type="procedure",
        document_scope="sedation",
        expected_evidence_type="text",
        primary_gold_ids=("c2",),
        acceptable_gold_ids=("c4",),
    )
    rows = [
        evaluator.HybridRetrievedChunk("c1", "d1", 1, "A", 1, 0.9, 1.0, 0.1, 0.2),
        evaluator.HybridRetrievedChunk("c2", "d1", 2, "B", 2, 0.8, 0.9, 0.08, 0.18),
        evaluator.HybridRetrievedChunk("c3", "d1", 3, "C", 3, 0.7, 0.8, 0.06, 0.16),
    ]

    result = evaluator.build_case_result(
        case,
        rows,
        latency_ms=3.5,
        embedding_latency_ms=1.0,
        retrieval_latency_ms=2.5,
        corpus_chunk_ids={"c1", "c2", "c3"},
    )

    assert result["metrics"]["hit_at_3"] == 1.0
    assert result["metrics"]["mrr"] == 0.5
    assert result["metrics"]["ragas_id_context_precision"] == pytest.approx(1 / 3)
    assert result["metrics"]["ragas_id_context_recall"] == 0.5
    assert result["failure_category"] == "gold_partially_outside_hybrid_corpus"
    assert result["ranked_results"][1]["gold_hit"] is True


def test_full_hybrid_evaluation_matches_frozen_contract_and_is_reproducible(
    tmp_path: Path,
) -> None:
    first = evaluator.evaluate(output_dir=tmp_path)
    first_rankings = {
        row["case_id"]: [
            (hit["rank"], hit["chunk_id"])
            for hit in row["ranked_results"]
        ]
        for row in first["per_case"]["cases"]
    }
    second = evaluator.evaluate(output_dir=tmp_path)
    second_rankings = {
        row["case_id"]: [
            (hit["rank"], hit["chunk_id"])
            for hit in row["ranked_results"]
        ]
        for row in second["per_case"]["cases"]
    }
    chroma_contract = json.loads(
        (ROOT / "workspace" / "검색_성능평가" / "ChromaDB" / "2026-09-19_chromadb-only-eval" / "evaluation_contract.json")
        .read_text(encoding="utf-8")
    )
    contract = second["contract"]

    assert first_rankings == second_rankings
    assert contract["corpus"] == chroma_contract["corpus"]
    assert contract["gold"] == chroma_contract["gold"]
    assert contract["top_k"] == [1, 3, 5, 10]
    assert contract["query_processing"]["planner"] == "src.query.plan_query"
    assert contract["hybrid"]["implementation"] == "src.library.LocalLibrary.search"
    assert second["summary"]["approved_positive_case_count"] == 21
    assert second["summary"]["deferred_positive_case_count"] == 11
    assert second["summary"]["production_changed"] is False
    assert second["summary"]["gold_changed"] is False
    assert {
        "summary.json",
        "per_case_results.json",
        "metrics.csv",
        "failure_analysis.md",
        "evaluation_contract.json",
    } == {path.name for path in tmp_path.iterdir()}


def test_hybrid_artifacts_are_raw_source_free(tmp_path: Path) -> None:
    evaluator.evaluate(output_dir=tmp_path)
    payload = "\n".join(
        path.read_text(encoding="utf-8-sig")
        for path in tmp_path.iterdir()
        if path.suffix in {".json", ".csv", ".md"}
    ).casefold()

    assert '"question"' not in payload
    assert '"raw_text"' not in payload
    assert '"source_text"' not in payload
    assert '"api_key"' not in payload
    assert "authorization:" not in payload
