"""Raw-free persistence and local-only data access for final retrieval review."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from src.library import Chunk

RETRIEVERS = ("bm25", "gemini_chroma", "current_hybrid")
JUDGMENTS = ("적절", "부분적절", "부적절", "추가확인필요")


def load_uat_cases(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {str(case["case_id"]): case for case in payload["cases"]}


def build_review_record(
    *,
    case_id: str,
    retriever_reviews: Mapping[str, str],
    preferred_retriever: str,
    overall_note: str,
    reviewer: str,
    timestamp: str | None = None,
) -> dict[str, Any]:
    if set(retriever_reviews) != set(RETRIEVERS):
        raise ValueError("one relevance judgment is required for every retriever")
    if any(value not in JUDGMENTS for value in retriever_reviews.values()):
        raise ValueError("unknown relevance judgment")
    if preferred_retriever not in (*RETRIEVERS, ""):
        raise ValueError("unknown preferred retriever")
    if not case_id.strip() or not reviewer.strip():
        raise ValueError("case_id and reviewer are required")
    return {
        "case_id": case_id,
        "retriever_reviews": dict(retriever_reviews),
        "preferred_retriever": preferred_retriever,
        "overall_note": overall_note,
        "reviewer": reviewer,
        "timestamp": timestamp or datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def save_review(path: Path, record: Mapping[str, Any]) -> None:
    """Create the file on first explicit save and replace only the same case review."""
    allowed_fields = {
        "case_id",
        "retriever_reviews",
        "preferred_retriever",
        "overall_note",
        "reviewer",
        "timestamp",
    }
    if set(record) != allowed_fields:
        raise ValueError("unexpected review fields")
    validated = build_review_record(
        case_id=str(record["case_id"]),
        retriever_reviews=dict(record["retriever_reviews"]),
        preferred_retriever=str(record["preferred_retriever"]),
        overall_note=str(record["overall_note"]),
        reviewer=str(record["reviewer"]),
        timestamp=str(record["timestamp"]),
    )
    payload: dict[str, Any] = {"schema_version": 1, "reviews": []}
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
    reviews = [
        row
        for row in payload.get("reviews", [])
        if row.get("case_id") != validated["case_id"]
    ]
    reviews.append(validated)
    payload = {"schema_version": 1, "reviews": sorted(reviews, key=lambda row: row["case_id"])}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_source_preview(catalog_path: Path, chunk_id: str) -> dict[str, Any]:
    """Read one source chunk from local SQLite without writing or caching it."""
    resolved = catalog_path.resolve()
    connection = sqlite3.connect(resolved.as_uri() + "?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "select id,payload from chunks where id=?", (chunk_id,)
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise KeyError(chunk_id)
    chunk = Chunk.from_row(json.loads(row["payload"]))
    return {
        "chunk_id": str(row["id"]),
        "document_id": chunk.document_id,
        "document_name": chunk.document_name,
        "page": chunk.page,
        "section": chunk.section,
        "text": chunk.text,
    }
