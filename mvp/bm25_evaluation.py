"""관리자용 BM25 기준선 평가. 벡터 검색이나 LLM을 호출하지 않습니다."""

import csv
import hashlib
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from uuid import uuid4

import numpy as np

from mvp.query import plan_query
from mvp.retrieval import BM25Index
from mvp.settings import ROOT, GuideError

EVALUATION_VERSION = 1
TOP_K = 10
CSV_COLUMNS = (
    "query",
    "rank",
    "bm25_score",
    "document_name",
    "page_number",
    "section_title",
    "chunk_id",
    "chunk_text",
)
DEFAULT_QUESTIONS = (
    "진정간호 목적은?",
    "진정간호 절차는?",
    "진정 전 준비사항은?",
    "CRE 격리 기준은?",
    "PCN irrigation 방법은?",
)


def validated_questions(questions):
    """빈 질문과 중복을 제거하고 앱의 질문 길이 제한을 그대로 적용합니다."""
    result = []
    for question in questions:
        value = str(question).strip()
        if not value or value in result:
            continue
        if len(value) > 500:
            raise GuideError("BM25 평가 질문은 각각 500자 이하여야 합니다. (BM25_QUESTION)")
        result.append(value)
    if not 1 <= len(result) <= 50:
        raise GuideError("BM25 평가 질문을 1~50개 입력하세요. (BM25_QUESTION)")
    return result


def corpus_fingerprint(documents, chunks):
    """원문을 노출하지 않고 같은 corpus인지 확인할 수 있는 해시를 만듭니다."""
    document_rows = [
        (
            doc.get("id"),
            doc.get("file_hash"),
            doc.get("indexed_at"),
            doc.get("updated_date"),
            doc.get("chunk_version"),
        )
        for doc in documents
    ]
    chunk_rows = [
        (chunk.id, chunk.document_id, hashlib.sha256(chunk.text.encode("utf-8")).hexdigest())
        for chunk in chunks
    ]
    payload = json.dumps(
        {"documents": sorted(document_rows), "chunks": sorted(chunk_rows)},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def result_rows(report):
    return [row for query in report["queries"] for row in query["top10"]]


def report_csv(report):
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(result_rows(report))
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def report_json(report):
    return json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")


def evaluate_bm25(library, questions=DEFAULT_QUESTIONS, document_ids=None, *, generated_at=None):
    """권한 있는 저장 chunk에서 BM25만 실행해 재현 가능한 원시 Top-10을 반환합니다."""
    library.auth.require_admin()
    revision = library.revision()
    documents = library.documents()
    requested = set(document_ids) if document_ids is not None else {doc["id"] for doc in documents}
    selected = [doc for doc in documents if doc["id"] in requested]
    if not selected:
        raise GuideError("BM25 평가에 사용할 검색 가능 지침서가 없습니다. (BM25_DOCUMENTS)")
    if len(selected) != len(requested):
        raise GuideError("선택한 지침서 중 현재 검색할 수 없는 문서가 있습니다. (BM25_DOCUMENTS)")

    chunks = []
    document_counts = []
    for document in selected:
        parts = library.diagnostic_chunks(document["id"])
        chunks.extend(parts)
        document_counts.append(
            {
                "document_id": document["id"],
                "document_name": document.get("document_name", ""),
                "chunk_count": len(parts),
            }
        )
    if not chunks:
        raise GuideError("BM25 평가에 사용할 저장 chunk가 없습니다. (BM25_CHUNKS)")

    questions = validated_questions(questions)
    index = BM25Index(chunks)
    query_results = []
    for original_query in questions:
        started = time.perf_counter()
        plan = plan_query(original_query, documents=selected)
        scores = index.scores(plan.expanded)
        positions = np.argsort(-scores, kind="stable")[: min(TOP_K, len(chunks))]
        rows = []
        for rank, position in enumerate(positions, 1):
            chunk = chunks[int(position)]
            rows.append(
                {
                    "query": plan.query,
                    "rank": rank,
                    "bm25_score": float(scores[position]),
                    "document_name": chunk.document_name,
                    "page_number": chunk.page,
                    "section_title": chunk.section,
                    "chunk_id": chunk.id,
                    "chunk_text": chunk.text,
                }
            )
        query_results.append(
            {
                "original_query": original_query,
                "actual_query": plan.query,
                "expanded_query": plan.expanded,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                "positive_result_count": int(np.count_nonzero(scores > 0)),
                "top10": rows,
            }
        )

    library.ensure_revision(revision)
    library.auth.require_admin()
    return {
        "schema_version": 1,
        "evaluation_version": EVALUATION_VERSION,
        "dataset_kind": "registered_guideline_chunks",
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "corpus_revision": str(revision),
        "corpus_fingerprint": corpus_fingerprint(selected, chunks),
        "document_count": len(selected),
        "chunk_count": len(chunks),
        "question_count": len(questions),
        "documents": document_counts,
        "tokenization": {
            "normalization": "NFC + lowercase",
            "korean": "2글자 이상 단어 + character bigram",
            "korean_bigram_weight": 0.25,
            "english": "[a-z][a-z0-9-]*",
            "medical_alias_entity_tokens": True,
        },
        "bm25": {"k1": 1.5, "b": 0.75, "top_k": TOP_K, "zero_scores_retained": True},
        "chunk_counts_by_document": dict(Counter(chunk.document_name for chunk in chunks)),
        "queries": query_results,
    }


def save_bm25_artifacts(report, destination=None):
    """CSV와 JSON을 임시 파일에 쓴 뒤 교체해 중간 파일이 결과로 남지 않게 합니다."""
    target = Path(destination) if destination is not None else ROOT / "artifacts"
    try:
        target.mkdir(parents=True, exist_ok=True)
        outputs = {
            target / "bm25_results.csv": report_csv(report),
            target / "bm25_results.json": report_json(report),
        }
        for path, content in outputs.items():
            temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
            try:
                temporary.write_bytes(content)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        return tuple(outputs)
    except OSError:
        raise GuideError("BM25 결과 파일을 저장하지 못했습니다. artifacts 쓰기 권한을 확인하세요. (BM25_SAVE)") from None
