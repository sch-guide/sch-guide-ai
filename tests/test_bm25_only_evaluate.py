import ast
import json
from pathlib import Path

import numpy as np
import pytest

from src.library import Chunk
from tools import bm25_only_evaluate as evaluator

ROOT = Path(__file__).resolve().parents[1]


def test_bm25_only_evaluator_exists_as_an_evaluation_only_tool() -> None:
    assert (ROOT / "tools" / "bm25_only_evaluate.py").is_file()


def _chunk(chunk_id: str, *, text: str, index: int) -> Chunk:
    return Chunk(
        id=chunk_id,
        document_id="d1",
        document_name="guide.pdf",
        page=index + 1,
        title="guide",
        section="section",
        updated_date=None,
        text=text,
        index=index,
        parent_id=f"parent-{chunk_id}",
    )


class _FixedBM25:
    def scores(self, query: str) -> np.ndarray:
        assert query == "expanded query"
        return np.asarray([0.5, 0.5, 1.0], dtype=np.float32)


def test_rank_bm25_uses_score_then_corpus_position_then_chunk_id() -> None:
    chunks = [
        _chunk("z", text="first", index=0),
        _chunk("a", text="second", index=1),
        _chunk("m", text="third", index=2),
    ]

    rows = evaluator.rank_bm25_chunks(
        chunks,
        "expanded query",
        limit=3,
        index=_FixedBM25(),
    )

    assert [(row.chunk_id, row.rank, row.score) for row in rows] == [
        ("m", 1, 1.0),
        ("z", 2, 0.5),
        ("a", 3, 0.5),
    ]


def test_rank_bm25_reuses_existing_index_and_preserves_chunk_mapping() -> None:
    chunks = [
        _chunk("c1", text="수혈 준비", index=0),
        _chunk("c2", text="진정 준비", index=1),
    ]

    rows = evaluator.rank_bm25_chunks(chunks, "수혈", limit=2)

    assert rows[0].chunk_id == "c1"
    assert rows[0].document_id == "d1"
    assert rows[0].page == 1
    assert rows[0].section == "section"
    assert rows[0].score > rows[1].score


def test_contract_prohibits_vector_fusion_reranking_and_generation() -> None:
    assert evaluator.retrieval_contract() == {
        "engine": "bm25_only",
        "bm25_calls_per_query": 1,
        "minilm_calls": 0,
        "vector_retrieval_calls": 0,
        "chromadb_calls": 0,
        "faiss_calls": 0,
        "pgvector_calls": 0,
        "rrf_calls": 0,
        "reranker_calls": 0,
        "context_expansion": False,
        "evidence_gate": False,
        "source_unit": False,
        "llm_calls": 0,
        "validator_calls": 0,
    }

    source = (ROOT / "tools" / "bm25_only_evaluate.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not {"chromadb", "faiss", "sentence_transformers"}.intersection(imports)
    assert "src.retrieval" in imports
    assert "rank_bm25_candidates" not in source
    assert "Embedder(" not in source


def test_case_result_uses_same_gold_metrics_and_failure_classification() -> None:
    case = evaluator.ApprovedCase(
        case_id="A",
        question="q",
        question_type="fact_specific",
        document_scope="sedation",
        expected_evidence_type="text",
        primary_gold_ids=("c2",),
        acceptable_gold_ids=("c4",),
    )
    rows = [
        evaluator.BM25RetrievedChunk("c1", "d1", 1, "A", 1, 3.0),
        evaluator.BM25RetrievedChunk("c2", "d1", 2, "B", 2, 2.0),
        evaluator.BM25RetrievedChunk("c3", "d1", 3, "C", 3, 1.0),
    ]

    result = evaluator.build_case_result(
        case,
        rows,
        latency_ms=1.25,
        corpus_chunk_ids={"c1", "c2", "c3"},
    )

    assert result["metrics"]["hit_at_3"] == 1.0
    assert result["metrics"]["mrr"] == 0.5
    assert result["metrics"]["ragas_id_context_precision"] == pytest.approx(1 / 3)
    assert result["metrics"]["ragas_id_context_recall"] == 0.5
    assert result["failure_category"] == "gold_partially_outside_bm25_corpus"
    assert [row["gold_hit"] for row in result["ranked_results"]] == [False, True, False]


def test_full_evaluation_matches_chromadb_contract_and_is_reproducible(tmp_path: Path) -> None:
    first = evaluator.evaluate(output_dir=tmp_path)
    first_rankings = {
        row["case_id"]: [(hit["rank"], hit["chunk_id"], hit["score"]) for hit in row["ranked_results"]]
        for row in first["per_case"]["cases"]
    }
    second = evaluator.evaluate(output_dir=tmp_path)
    second_rankings = {
        row["case_id"]: [(hit["rank"], hit["chunk_id"], hit["score"]) for hit in row["ranked_results"]]
        for row in second["per_case"]["cases"]
    }
    chroma_contract = json.loads(
        (ROOT / "workspace" / "검색_성능평가" / "ChromaDB" / "2026-09-19_chromadb-only-eval" / "evaluation_contract.json").read_text(
            encoding="utf-8"
        )
    )
    contract = second["contract"]

    assert first_rankings == second_rankings
    assert contract["corpus"] == chroma_contract["corpus"]
    assert contract["gold"] == chroma_contract["gold"]
    assert contract["top_k"] == chroma_contract["top_k"] == [1, 3, 5, 10]
    assert contract["bm25"]["ranking"] == {
        "primary_sort": "bm25_score_descending",
        "tie_break": "original_corpus_position_ascending",
        "final_fallback": "chunk_id_ascending",
    }
    assert contract["query_processing"]["planner"] == "src.query.plan_query"
    assert contract["query_processing"]["query_text"] == "plan.expanded"
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
    failure_analysis = (tmp_path / "failure_analysis.md").read_text(encoding="utf-8")
    assert "BM25 Top-10" in failure_analysis
    assert "ChromaDB Top-10" not in failure_analysis


def test_artifacts_are_raw_source_free(tmp_path: Path) -> None:
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
