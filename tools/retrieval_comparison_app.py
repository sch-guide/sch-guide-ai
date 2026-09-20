"""Local, evaluation-only BM25 versus ChromaDB retrieval comparison UI."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import streamlit as st

from src.library import Chunk, Embedder, bounded_embedding_question
from src.retrieval import BM25Index
from tools.chroma_baseline_evaluate import (
    CatalogChunk,
    load_catalog,
    query_chroma,
    validate_fixture_document,
)
from tools.retrieval_strategy_evaluate import bm25_ranking

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "library" / "catalog.sqlite3"
DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "transfusion_retrieval_baseline.json"
TOP_K = 10
PREVIEW_LIMIT = 500
FREE_QUESTION = "__free_question__"


def load_fixture(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    document = payload.get("document")
    if not isinstance(cases, list) or not cases or not isinstance(document, dict):
        raise ValueError("invalid retrieval fixture")
    return payload


def find_fixture_case(question: str, cases: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    normalized = question.strip()
    return next((case for case in cases if case.get("question", "").strip() == normalized), None)


def retrieval_hit_summary(
    retrieved_ids: Sequence[str], gold_ids: set[str]
) -> dict[str, bool | int | None]:
    if not gold_ids:
        return {
            "gold_available": False,
            "first_gold_rank": None,
            "hit_at_1": None,
            "hit_at_3": None,
            "hit_at_5": None,
            "hit_at_10": None,
        }
    first_gold_rank = next(
        (rank for rank, chunk_id in enumerate(retrieved_ids, 1) if chunk_id in gold_ids), None
    )
    return {
        "gold_available": True,
        "first_gold_rank": first_gold_rank,
        "hit_at_1": bool(gold_ids.intersection(retrieved_ids[:1])),
        "hit_at_3": bool(gold_ids.intersection(retrieved_ids[:3])),
        "hit_at_5": bool(gold_ids.intersection(retrieved_ids[:5])),
        "hit_at_10": bool(gold_ids.intersection(retrieved_ids[:10])),
    }


def _preview(text: str) -> str:
    stripped = text.strip()
    if len(stripped) <= PREVIEW_LIMIT:
        return stripped
    return stripped[:PREVIEW_LIMIT].rstrip() + "…"


def build_result_rows(
    retrieved_ids: Sequence[str],
    scores: Sequence[float],
    chunks: Sequence[CatalogChunk],
    gold_ids: set[str],
) -> list[dict[str, Any]]:
    if len(retrieved_ids) != len(scores) or len(retrieved_ids) != len(set(retrieved_ids)):
        raise ValueError("retrieved IDs and scores must be aligned and distinct")
    chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    rows = []
    for rank, (chunk_id, score) in enumerate(zip(retrieved_ids, scores, strict=True), 1):
        chunk = chunks_by_id.get(chunk_id)
        if chunk is None:
            raise ValueError(f"retrieved chunk is outside catalog: {chunk_id}")
        rows.append(
            {
                "rank": rank,
                "chunk_id": chunk.chunk_id,
                "document": chunk.document_name,
                "page": chunk.page,
                "section": chunk.section,
                "parent_id": chunk.parent_id,
                "score": float(score),
                "is_gold": chunk.chunk_id in gold_ids if gold_ids else None,
                "substantive_body": chunk.substantive_body,
                "preview": _preview(chunk.text),
            }
        )
    return rows


def _source_chunk(chunk: CatalogChunk) -> Chunk:
    return Chunk(
        id=chunk.chunk_id,
        document_id=chunk.document_id,
        document_name=chunk.document_name,
        page=chunk.page,
        title=chunk.title,
        section=chunk.section,
        updated_date=None,
        text=chunk.text,
        index=chunk.position,
        source_type=chunk.source_type,
        location=chunk.location,
        parent_id=chunk.parent_id,
    )


def _ephemeral_chroma_collection(chunks: Sequence[CatalogChunk]):
    import chromadb
    from chromadb.config import Settings

    client = chromadb.EphemeralClient(settings=Settings(anonymized_telemetry=False))
    collection = client.create_collection(
        name="transfusion_retrieval_comparison",
        embedding_function=None,
        metadata={"hnsw:space": "cosine"},
    )
    for start in range(0, len(chunks), 64):
        batch = chunks[start : start + 64]
        collection.add(
            ids=[chunk.chunk_id for chunk in batch],
            embeddings=[chunk.vector.tolist() for chunk in batch],
            metadatas=[
                {
                    "document_id": chunk.document_id,
                    "position": chunk.position,
                    "page": chunk.page if chunk.page is not None else -1,
                    "parent_id": chunk.parent_id,
                }
                for chunk in batch
            ],
        )
    if collection.count() != len(chunks):
        raise RuntimeError("Chroma collection count mismatch")
    return client, collection


@dataclass
class RetrievalComparisonEngine:
    chunks: tuple[CatalogChunk, ...]
    bm25_index: BM25Index
    chroma_client: Any
    chroma_collection: Any
    embedder: Embedder

    def compare(self, question: str, *, gold_ids: set[str]) -> dict[str, Any]:
        normalized = question.strip()
        if not normalized:
            raise ValueError("question is required")

        bm25 = bm25_ranking(normalized, self.chunks, limit=TOP_K, index=self.bm25_index)

        chroma_started = time.perf_counter()
        embedding_question = bounded_embedding_question(normalized, self.embedder)
        query_vector = self.embedder.encode([embedding_question])[0]
        chroma_ids, distances = query_chroma(
            self.chroma_collection, query_vector, limit=TOP_K
        )
        chroma_latency_ms = (time.perf_counter() - chroma_started) * 1000
        chroma_scores = [1.0 - distance for distance in distances]

        return {
            "catalog_chunk_count": len(self.chunks),
            "bm25": {
                "rows": build_result_rows(
                    bm25["retrieved_context_ids"], bm25["scores"], self.chunks, gold_ids
                ),
                "summary": retrieval_hit_summary(bm25["retrieved_context_ids"], gold_ids),
                "latency_ms": float(bm25["latency_ms"]),
            },
            "chromadb": {
                "rows": build_result_rows(chroma_ids, chroma_scores, self.chunks, gold_ids),
                "summary": retrieval_hit_summary(chroma_ids, gold_ids),
                "latency_ms": chroma_latency_ms,
            },
        }


def build_comparison_engine(chunks: Sequence[CatalogChunk]) -> RetrievalComparisonEngine:
    if len(chunks) != 105:
        raise ValueError(f"expected the registered 105-chunk transfusion corpus, got {len(chunks)}")
    source_chunks = [_source_chunk(chunk) for chunk in chunks]
    chroma_client, collection = _ephemeral_chroma_collection(chunks)
    return RetrievalComparisonEngine(
        chunks=tuple(chunks),
        bm25_index=BM25Index(source_chunks),
        chroma_client=chroma_client,
        chroma_collection=collection,
        embedder=Embedder(),
    )


@st.cache_resource(show_spinner=False)
def _cached_engine() -> tuple[dict[str, Any], RetrievalComparisonEngine]:
    fixture = load_fixture(DEFAULT_FIXTURE)
    metadata, chunks = load_catalog(
        DEFAULT_CATALOG, document_name=fixture["document"]["name"]
    )
    validate_fixture_document(fixture["document"], metadata, chunks)
    return metadata, build_comparison_engine(chunks)


def _render_summary(name: str, result: dict[str, Any]) -> None:
    summary = result["summary"]
    st.subheader(name)
    st.caption(f"검색 지연: {result['latency_ms']:.2f} ms")
    if not summary["gold_available"]:
        st.info("Gold = 없음 (자유 질문)")
        return
    first_rank = summary["first_gold_rank"]
    st.write(f"First gold rank: **{first_rank if first_rank is not None else '없음'}**")
    metrics = st.columns(4)
    for column, cutoff in zip(metrics, (1, 3, 5, 10), strict=True):
        column.metric(f"Hit@{cutoff}", "✅" if summary[f"hit_at_{cutoff}"] else "❌")


def _render_rows(rows: Sequence[dict[str, Any]], *, score_label: str) -> None:
    for row in rows:
        with st.container(border=True):
            gold = "✅ Gold" if row["is_gold"] else "❌ Non-gold"
            if row["is_gold"] is None:
                gold = "Gold 없음"
            st.markdown(f"### {row['rank']}위 · {gold}")
            st.code(row["chunk_id"], language=None)
            st.markdown(
                f"**문서:** {row['document']}  \n"
                f"**페이지:** {row['page'] if row['page'] is not None else '-'}  \n"
                f"**섹션:** {row['section'] or '-'}  \n"
                f"**Parent ID:** `{row['parent_id']}`  \n"
                f"**{score_label}:** `{row['score']:.6f}`  \n"
                f"**Substantive body:** {'yes' if row['substantive_body'] else 'no'}"
            )
            st.caption("로컬 메모리 전용 원문 preview")
            st.text(row["preview"])


def _selectable_cases(fixture: dict[str, Any], question_type: str) -> list[dict[str, Any]]:
    if question_type == "전체":
        return list(fixture["cases"])
    return [case for case in fixture["cases"] if case["question_type"] == question_type]


def main() -> None:
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
    st.set_page_config(page_title="Retrieval 비교", page_icon="🔎", layout="wide")
    st.title("BM25 vs ChromaDB Retrieval 비교")
    st.caption(
        "로컬 evaluation 전용 · 수혈 105 chunks · Top-K=10 · RRF/reranker/generation 미사용"
    )

    fixture = load_fixture(DEFAULT_FIXTURE)
    question_types = ["전체", *sorted({case["question_type"] for case in fixture["cases"]})]
    selected_type = st.selectbox("질문 유형", question_types)
    cases = _selectable_cases(fixture, selected_type)
    options = [FREE_QUESTION, *[case["question_id"] for case in cases]]
    cases_by_id = {case["question_id"]: case for case in cases}
    selected_id = st.selectbox(
        "기존 평가 질문",
        options,
        format_func=lambda value: (
            "직접 입력"
            if value == FREE_QUESTION
            else f"{value} · {cases_by_id[value]['question']}"
        ),
    )
    if "comparison_question" not in st.session_state:
        st.session_state["comparison_question"] = ""
    if selected_id != FREE_QUESTION and st.button("선택 질문 불러오기"):
        st.session_state["comparison_question"] = cases_by_id[selected_id]["question"]
        st.rerun()

    question = st.text_area(
        "질문을 입력하세요",
        key="comparison_question",
        placeholder="수혈 지침에서 검색할 질문을 입력하세요.",
    )
    compare = st.button("BM25 vs ChromaDB 비교", type="primary", use_container_width=True)
    if not compare:
        return
    if not question.strip():
        st.warning("질문을 입력하세요.")
        return

    fixture_case = find_fixture_case(question, fixture["cases"])
    gold_ids = set(fixture_case["reference_context_ids"]) if fixture_case else set()
    if fixture_case:
        st.success(
            f"평가셋 일치: {fixture_case['question_id']} · {fixture_case['question_type']} · "
            f"Gold {len(gold_ids)}개"
        )
    else:
        st.info("평가셋에 없는 자유 질문입니다. Gold = 없음")

    with st.spinner("동일한 105 chunks에서 두 retriever를 실행하는 중입니다..."):
        metadata, engine = _cached_engine()
        comparison = engine.compare(question, gold_ids=gold_ids)

    st.caption(
        f"문서: {metadata['document_name']} · chunks: {comparison['catalog_chunk_count']} · "
        f"embedding: {metadata['model']} · Top-K: {TOP_K}"
    )
    st.warning("BM25 raw score와 ChromaDB cosine similarity는 척도가 달라 절대값끼리 비교하지 마세요.")
    st.caption("ChromaDB에서 similarity가 완전히 같은 결과는 HNSW 동률 순서가 달라질 수 있습니다.")

    bm25_column, chroma_column = st.columns(2, gap="large")
    with bm25_column:
        _render_summary("BM25", comparison["bm25"])
        _render_rows(comparison["bm25"]["rows"], score_label="BM25 score")
    with chroma_column:
        _render_summary("ChromaDB", comparison["chromadb"])
        _render_rows(comparison["chromadb"]["rows"], score_label="Cosine similarity")


if __name__ == "__main__":
    main()
