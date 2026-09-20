"""Read-only diagnostics for the frozen SCHAT ChromaDB-only evaluation.

The script prints IDs, ranks, distances, vector statistics, and aggregate
metrics only. It never prints or persists chunk text, questions, or Gold text.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import faiss
import numpy as np

from src.library import Embedder, bounded_embedding_question
from src.query import plan_query
from src.repository import snapshot
from tools import chromadb_only_evaluate as common
from tools.retrieval_baseline_metrics import ranked_retrieval_metrics
from tools.schat_v1_final_validate import _scope_document_ids

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "data" / "library" / "catalog.sqlite3"
INDEX_DIR = ROOT / "workspace" / "검색_성능평가" / "ChromaDB" / "2026-09-19_chromadb-only-eval" / "chroma_index"
PER_CASE = ROOT / "workspace" / "검색_성능평가" / "ChromaDB" / "2026-09-19_chromadb-only-eval" / "per_case_results.json"
SEED = 20260919


def _round(value: float) -> float:
    return round(float(value), 8)


def _vector_hash(vector: np.ndarray) -> str:
    return hashlib.sha256(np.asarray(vector, dtype=np.float32).tobytes()).hexdigest()[:12]


def _metrics(ranked_ids: list[str], gold_ids: tuple[str, ...]) -> dict[str, float]:
    values = ranked_retrieval_metrics(ranked_ids, list(gold_ids), cutoffs=(10,))
    return {key: float(value) for key, value in values.items()}


def main() -> None:
    import chromadb
    from chromadb.config import Settings

    revision = common._catalog_revision(CATALOG)
    library = snapshot(str(CATALOG.resolve()), revision)
    common.validate_vectors(library.vectors, dimensions=common.DIMENSIONS)
    embedder = Embedder()

    client = chromadb.PersistentClient(
        path=str(INDEX_DIR), settings=Settings(anonymized_telemetry=False)
    )
    collection = client.get_collection(
        name=common.COLLECTION_NAME, embedding_function=None
    )

    rng = random.Random(SEED)
    sample_positions = sorted(rng.sample(range(len(library.chunks)), 10))
    sample_chunks = [library.chunks[position] for position in sample_positions]
    fresh_vectors = embedder.encode([chunk.text for chunk in sample_chunks])
    self_search: list[dict[str, Any]] = []
    for position, chunk, fresh_vector in zip(
        sample_positions, sample_chunks, fresh_vectors, strict=True
    ):
        result = collection.query(
            query_embeddings=[fresh_vector.tolist()],
            n_results=10,
            include=["distances", "metadatas"],
        )
        ids = [str(value) for value in result["ids"][0]]
        distances = [float(value) for value in result["distances"][0]]
        self_search.append(
            {
                "corpus_position": position,
                "chunk_id": chunk.id,
                "document_id": chunk.document_id,
                "page": chunk.page,
                "rank": ids.index(chunk.id) + 1 if chunk.id in ids else None,
                "rank_1": bool(ids and ids[0] == chunk.id),
                "top_1_chunk_id": ids[0] if ids else None,
                "top_1_distance": _round(distances[0]) if distances else None,
                "stored_vs_fresh_cosine": _round(
                    np.dot(library.vectors[position], fresh_vector)
                ),
                "stored_vector_hash": _vector_hash(library.vectors[position]),
                "fresh_vector_hash": _vector_hash(fresh_vector),
            }
        )

    hash_positions: dict[str, list[int]] = defaultdict(list)
    for position, vector in enumerate(library.vectors):
        hash_positions[_vector_hash(vector)].append(position)
    duplicate_groups = sorted(
        (positions for positions in hash_positions.values() if len(positions) > 1),
        key=lambda positions: (-len(positions), positions[0]),
    )
    vector_duplicates = {
        "total_vectors": len(library.vectors),
        "unique_exact_vectors": len(hash_positions),
        "duplicate_vector_groups": len(duplicate_groups),
        "largest_groups": [
            {
                "count": len(positions),
                "unique_text_hashes": len(
                    {
                        hashlib.sha256(
                            library.chunks[position].text.encode("utf-8")
                        ).hexdigest()
                        for position in positions
                    }
                ),
                "all_text_identical": len(
                    {library.chunks[position].text for position in positions}
                )
                == 1,
                "text_lengths": sorted(
                    {len(library.chunks[position].text) for position in positions}
                ),
                "positions": positions,
                "chunk_ids": [library.chunks[position].id for position in positions],
                "pages": [library.chunks[position].page for position in positions],
            }
            for positions in duplicate_groups[:10]
        ],
    }

    reviewed = common._read_json(common.DEFAULT_REVIEWED_GOLD)
    uat = common._read_json(common.DEFAULT_UAT)
    approved = common.load_approved_cases(reviewed, uat)
    approved_by_id = {case.case_id: case for case in approved}
    scopes = _scope_document_ids(library.docs)
    scope_chunk_counts = {
        scope: sum(chunk.document_id in document_ids for chunk in library.chunks)
        for scope, document_ids in scopes.items()
    }
    previous: dict[str, dict[str, Any]] = {}
    faiss_cases: list[dict[str, Any]] = []
    for uat_case in uat["cases"]:
        scope = str(uat_case.get("document_scope", ""))
        case_id = str(uat_case.get("case_id", ""))
        if scope not in scopes:
            continue
        is_follow_up = str(uat_case.get("question_type")) == "follow_up"
        prior = previous.get(scope, {}) if is_follow_up else {}
        allowed_documents = scopes[scope]
        plan = plan_query(
            str(uat_case["question"]),
            previous=str(prior.get("question", "")),
            follow_up=is_follow_up,
            documents=library.docs,
            previous_sources=tuple(prior.get("document_ids", ())),
            context_document_ids=allowed_documents,
        )
        case = approved_by_id.get(case_id)
        if case is not None:
            effective_documents = set(allowed_documents)
            if plan.document_ids:
                effective_documents.intersection_update(plan.document_ids)
            positions = [
                index
                for index, chunk in enumerate(library.chunks)
                if chunk.document_id in effective_documents
            ]
            query_text = bounded_embedding_question(plan.expanded, embedder)
            query_vector = embedder.encode([query_text])[0]
            index = faiss.IndexFlatIP(common.DIMENSIONS)
            index.add(np.asarray(library.vectors[positions], dtype=np.float32))
            scores, local_positions = index.search(
                np.asarray([query_vector], dtype=np.float32), min(10, len(positions))
            )
            ranked_ids = [
                library.chunks[positions[int(local_position)]].id
                for local_position in local_positions[0]
                if local_position >= 0
            ]
            faiss_cases.append(
                {
                    "case_id": case_id,
                    "question_type": case.question_type,
                    "ranked_ids": ranked_ids,
                    "scores": [_round(score) for score in scores[0][: len(ranked_ids)]],
                    "metrics": _metrics(ranked_ids, case.gold_ids),
                }
            )
        if str(uat_case.get("expected_behavior")) == "answer":
            previous[scope] = {
                "question": str(uat_case["question"]),
                "document_ids": allowed_documents,
            }

    faiss_summary = {
        "case_count": len(faiss_cases),
        "hit_at_10": _round(np.mean([row["metrics"]["hit_at_10"] for row in faiss_cases])),
        "mrr": _round(np.mean([row["metrics"]["mrr"] for row in faiss_cases])),
        "recall_at_10": _round(
            np.mean([row["metrics"]["recall_at_10"] for row in faiss_cases])
        ),
    }

    chroma_cases = common._read_json(PER_CASE)["cases"]
    chroma_table = [
        {
            "case_id": row["case_id"],
            "question_type": row["question_type"],
            "hit_at_10": row["metrics"]["hit_at_10"],
            "failure_category": row["failure_category"],
            "top_10": [
                {
                    "rank": result["rank"],
                    "chunk_id": result["chunk_id"],
                    "distance": result["distance"],
                    "gold_hit": result["gold_hit"],
                }
                for result in row["ranked_results"]
            ],
        }
        for row in chroma_cases
    ]
    distance_patterns = {
        row["case_id"]: {
            "unique_distance_count": len(
                {result["distance"] for result in row["ranked_results"]}
            ),
            "distance_counts": dict(
                Counter(str(result["distance"]) for result in row["ranked_results"])
            ),
        }
        for row in chroma_cases
        if row["case_id"] in {"UAT-T01", "UAT-T02", "UAT-T03", "UAT-T12", "UAT-T13"}
    }

    payload = {
        "read_only": True,
        "catalog_revision": revision,
        "collection": {
            "name": collection.name,
            "count": collection.count(),
            "metadata": collection.metadata,
            "embedding_function": None,
            "query_n_results": scope_chunk_counts,
            "metadata_filter": "document_id in active document scope",
        },
        "self_search": self_search,
        "self_search_rank_1_count": sum(row["rank_1"] for row in self_search),
        "vector_duplicates": vector_duplicates,
        "faiss_only": {"summary": faiss_summary, "cases": faiss_cases},
        "chroma_cases": chroma_table,
        "transfusion_distance_patterns": distance_patterns,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
