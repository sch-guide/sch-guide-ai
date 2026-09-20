import ast
from pathlib import Path

import numpy as np
import pytest

from src.library import Chunk
from tools import chromadb_only_evaluate as evaluator

ROOT = Path(__file__).resolve().parents[1]


def test_chromadb_only_evaluator_exists_as_an_evaluation_only_tool() -> None:
    assert (ROOT / "tools" / "chromadb_only_evaluate.py").is_file()


def _chunk(chunk_id: str, document_id: str, *, page: int, section: str) -> Chunk:
    return Chunk(
        id=chunk_id,
        document_id=document_id,
        document_name=f"{document_id}.pdf",
        page=page,
        title=document_id,
        section=section,
        updated_date=None,
        text=f"private source text for {chunk_id}",
        index=page,
        parent_id=f"parent-{chunk_id}",
    )


class _FakeCollection:
    def __init__(self) -> None:
        self.add_calls: list[dict] = []
        self.query_calls: list[dict] = []
        self._count = 0

    def add(self, **kwargs) -> None:
        self.add_calls.append(kwargs)
        self._count += len(kwargs["ids"])

    def count(self) -> int:
        return self._count

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        return {
            "ids": [["c2", "c1"]],
            "distances": [[0.1, 0.4]],
            "metadatas": [
                [
                    {"document_id": "d1", "page": 2, "section": "B", "position": 1},
                    {"document_id": "d1", "page": 1, "section": "A", "position": 0},
                ]
            ],
        }


class _NamedCollection:
    def __init__(self, name: str) -> None:
        self.name = name


class _TiedCollection:
    def query(self, **kwargs):
        return {
            "ids": [["c3", "c2", "c1"]],
            "distances": [[0.2, 0.1, 0.1]],
            "metadatas": [
                [
                    {"document_id": "d1", "page": 3, "section": "C", "position": 2},
                    {"document_id": "d1", "page": 2, "section": "B", "position": 1},
                    {"document_id": "d1", "page": 1, "section": "A", "position": 0},
                ]
            ],
        }


class _FakeClient:
    def __init__(self) -> None:
        self.collection = _FakeCollection()
        self.deleted: list[str] = []
        self.created: list[dict] = []

    def list_collections(self):
        return [_NamedCollection(evaluator.COLLECTION_NAME)]

    def delete_collection(self, name: str) -> None:
        self.deleted.append(name)

    def create_collection(self, **kwargs):
        self.created.append(kwargs)
        return self.collection


def test_rebuild_collection_uses_existing_vectors_and_never_stores_documents() -> None:
    chunks = [_chunk("c1", "d1", page=1, section="A"), _chunk("c2", "d1", page=2, section="B")]
    vectors = np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    client = _FakeClient()

    collection = evaluator.rebuild_collection(client, chunks, vectors, batch_size=1)

    assert collection is client.collection
    assert client.deleted == [evaluator.COLLECTION_NAME]
    assert client.created == [
        {
            "name": evaluator.COLLECTION_NAME,
            "embedding_function": None,
            "metadata": {"hnsw:space": "cosine"},
        }
    ]
    assert [call["ids"] for call in collection.add_calls] == [["c1"], ["c2"]]
    assert all("documents" not in call for call in collection.add_calls)
    assert collection.add_calls[0]["metadatas"][0] == {
        "document_id": "d1",
        "page": 1,
        "section": "A",
        "position": 1,
        "parent_id": "parent-c1",
    }


def test_query_collection_returns_scoped_top_k_mapping() -> None:
    collection = _FakeCollection()

    rows = evaluator.query_collection(
        collection,
        np.asarray([1.0, 0.0], dtype=np.float32),
        document_ids=("d1",),
        limit=10,
    )

    assert [(row.chunk_id, row.rank, row.similarity, row.distance) for row in rows] == [
        ("c2", 1, 0.9, 0.1),
        ("c1", 2, 0.6, 0.4),
    ]
    assert collection.query_calls == [
        {
            "query_embeddings": [[1.0, 0.0]],
            "n_results": 10,
            "where": {"document_id": {"$in": ["d1"]}},
            "include": ["distances", "metadatas"],
        }
    ]


def test_query_collection_stably_resolves_equal_distance_before_top_k() -> None:
    collection = _TiedCollection()

    rows = evaluator.query_collection(
        collection,
        np.asarray([1.0, 0.0], dtype=np.float32),
        document_ids=("d1",),
        limit=2,
        candidate_limit=3,
    )

    assert [(row.chunk_id, row.rank, row.distance) for row in rows] == [
        ("c1", 1, 0.1),
        ("c2", 2, 0.1),
    ]


def test_validate_vectors_requires_l2_normalized_production_embeddings() -> None:
    details = evaluator.validate_vectors(
        np.asarray([[1.0, 0.0], [0.6, 0.8]], dtype=np.float32),
        dimensions=2,
    )

    assert details == {
        "normalization": "l2_unit",
        "minimum_norm": 1.0,
        "maximum_norm": 1.0,
    }
    with pytest.raises(ValueError, match="L2-normalized"):
        evaluator.validate_vectors(np.asarray([[2.0, 0.0]], dtype=np.float32), dimensions=2)


def test_load_approved_cases_uses_only_human_approved_positive_gold() -> None:
    reviewed = {
        "cases": [
            {
                "case_id": "A",
                "final_gold_approved": True,
                "review_status": "approved",
                "expected_document": "sedation",
                "expected_evidence_type": "text",
                "primary_gold_ids": ["c1"],
                "acceptable_gold_ids": ["c2"],
            },
            {
                "case_id": "B",
                "final_gold_approved": False,
                "review_status": "reviewing",
                "expected_document": "transfusion",
                "expected_evidence_type": "text",
                "primary_gold_ids": [],
                "acceptable_gold_ids": [],
            },
        ]
    }
    uat = {
        "cases": [
            {"case_id": "A", "question": "q1", "question_type": "procedure", "document_scope": "sedation"},
            {"case_id": "B", "question": "q2", "question_type": "temporal", "document_scope": "transfusion"},
            {"case_id": "N", "question": "q3", "question_type": "negative", "document_scope": "out_of_scope"},
        ]
    }

    cases = evaluator.load_approved_cases(reviewed, uat)

    assert [case.case_id for case in cases] == ["A"]
    assert cases[0].gold_ids == ("c1", "c2")


def test_case_result_calculates_gold_metrics_and_rank_hits() -> None:
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
        evaluator.RetrievedChunk("c1", "d1", 1, "A", 1, 0.9, 0.1),
        evaluator.RetrievedChunk("c2", "d1", 2, "B", 2, 0.8, 0.2),
        evaluator.RetrievedChunk("c3", "d1", 3, "C", 3, 0.7, 0.3),
    ]

    result = evaluator.build_case_result(case, rows, latency_ms=12.5, corpus_chunk_ids={"c1", "c2", "c3"})

    assert result["metrics"]["hit_at_1"] == 0.0
    assert result["metrics"]["hit_at_3"] == 1.0
    assert result["metrics"]["mrr"] == 0.5
    assert result["metrics"]["ragas_id_context_precision"] == pytest.approx(1 / 3)
    assert result["metrics"]["ragas_id_context_recall"] == 0.5
    assert [row["gold_hit"] for row in result["ranked_results"]] == [False, True, False]
    assert result["failure_category"] == "gold_partially_outside_chromadb_corpus"


def test_artifact_security_rejects_raw_text_fields_and_exact_source_text() -> None:
    forbidden = {"private source text"}

    evaluator.ensure_artifact_safe({"case_id": "A", "chunk_id": "c1"}, forbidden_exact_texts=forbidden)
    evaluator.ensure_artifact_safe({"section": "private source text"}, forbidden_exact_texts=forbidden)
    with pytest.raises(ValueError, match="forbidden artifact field"):
        evaluator.ensure_artifact_safe({"raw_text": "x"}, forbidden_exact_texts=forbidden)
    with pytest.raises(ValueError, match="exact source text"):
        evaluator.ensure_artifact_safe({"value": "private source text"}, forbidden_exact_texts=forbidden)


def test_evaluator_has_no_bm25_rrf_or_reranker_import_path() -> None:
    source = (ROOT / "tools" / "chromadb_only_evaluate.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}

    assert "src.retrieval" not in imports
    assert not any(name.endswith("bm25_e5_strategy_evaluate") for name in imports)
    assert evaluator.retrieval_contract() == {
        "engine": "chromadb_only",
        "bm25_calls": 0,
        "rrf_calls": 0,
        "reranker_calls": 0,
        "context_expansion": False,
        "evidence_gate": False,
        "source_unit": False,
        "llm_calls": 0,
        "validator_calls": 0,
    }


def test_real_chromadb_returns_deterministic_top_k(tmp_path: Path) -> None:
    chromadb = pytest.importorskip("chromadb")
    from chromadb.config import Settings

    client = chromadb.PersistentClient(
        path=str(tmp_path / "index"),
        settings=Settings(anonymized_telemetry=False),
    )
    chunks = [_chunk("c1", "d1", page=1, section="A"), _chunk("c2", "d1", page=2, section="B")]
    vectors = np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    collection = evaluator.rebuild_collection(client, chunks, vectors)

    first = evaluator.query_collection(collection, vectors[0], document_ids=("d1",), limit=2)
    second = evaluator.query_collection(collection, vectors[0], document_ids=("d1",), limit=2)

    assert [row.chunk_id for row in first] == ["c1", "c2"]
    assert [row.chunk_id for row in second] == ["c1", "c2"]
