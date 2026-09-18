"""Local-only data contract and candidate lookup for operational Gold review.

Candidate evidence can contain exact registered guideline text while it is in memory.
Persisted review files deliberately contain only identifiers and human-authored labels.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

REVIEW_STATUSES = (
    "unreviewed",
    "reviewing",
    "approved",
    "rejected",
    "needs_second_review",
)
SCOPE_DECISIONS = ("pending", "yes", "no")
DOCUMENT_DECISIONS = ("pending", "sedation", "transfusion", "other", "none")
DOMAIN_DECISIONS = ("pending", "hospital", "out_of_scope")
CRITICAL_LIST_FIELDS = (
    "critical_facts",
    "critical_numbers",
    "critical_units",
    "critical_times",
    "critical_conditions",
    "critical_contraindications",
    "critical_negations",
    "critical_steps",
)
PROHIBITED_PERSISTED_FIELDS = frozenset(
    {
        "question",
        "evidence_text",
        "text",
        "preview",
        "retrieval_rank",
        "retrieval_method",
        "score",
    }
)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ValueError(f"invalid fixture: {path}")
    return payload


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_review_inputs(uat_path: Path, gold_path: Path) -> dict[str, Any]:
    """Join the fixed UAT and provisional Gold fixtures without changing either."""
    uat = _read_json(uat_path)
    gold = _read_json(gold_path)
    uat_by_id = {str(case.get("case_id", "")): case for case in uat["cases"]}
    gold_by_id = {str(case.get("case_id", "")): case for case in gold["cases"]}
    if not all(uat_by_id) or len(uat_by_id) != len(uat["cases"]):
        raise ValueError("UAT case IDs must be present and unique")
    if set(uat_by_id) != set(gold_by_id):
        raise ValueError("UAT and Gold case IDs do not match")

    cases = []
    for case_id, uat_case in uat_by_id.items():
        gold_case = gold_by_id[case_id]
        if bool(gold_case.get("expected_abstain")) or uat_case.get("expected_behavior") == "abstain":
            continue
        cases.append(
            {
                "case_id": case_id,
                "question": str(uat_case["question"]),
                "question_type": str(uat_case["question_type"]),
                "expected_intent": str(uat_case.get("expected_intent", "")),
                "expected_document_scope": str(gold_case["expected_document_scope"]),
                "expected_domain": str(gold_case["expected_domain"]),
                "expected_evidence_type": str(gold_case["expected_evidence_type"]),
                "expected_abstain": False,
                "source_label_status": str(gold_case["label_status"]),
                "source_gold_refs": copy.deepcopy(gold_case.get("source_gold_refs", [])),
            }
        )
    if len(cases) != 32:
        raise ValueError(f"expected 32 positive review cases, got {len(cases)}")
    return {
        "uat": uat,
        "gold": gold,
        "cases": cases,
        "uat_sha256": _sha256_file(uat_path),
        "gold_sha256": _sha256_file(gold_path),
    }


def _blank_review_case(case: Mapping[str, Any]) -> dict[str, Any]:
    image_pending = str(case["source_label_status"]) == "image_needs_human_review"
    return {
        "case_id": str(case["case_id"]),
        "review_status": "unreviewed",
        "scope_decision": "pending",
        "expected_domain": "pending",
        "expected_document": "pending",
        "expected_evidence_type": str(case["expected_evidence_type"]),
        "expected_abstain": False,
        "primary_gold_ids": [],
        "acceptable_gold_ids": [],
        "critical_facts": [],
        "critical_numbers": [],
        "critical_units": [],
        "critical_times": [],
        "critical_conditions": [],
        "critical_contraindications": [],
        "critical_negations": [],
        "critical_steps": [],
        "table_required": None,
        "image_required": True if image_pending else None,
        "image_human_review_completed": False,
        "reviewer": "",
        "reviewed_at": "",
        "reviewer_1_reviewed_at": "",
        "note": "",
        "second_review_required": image_pending,
        "reviewer_1_approved": False,
        "reviewer_2": "",
        "reviewer_2_reviewed_at": "",
        "reviewer_2_approved": False,
        "final_gold_approved": False,
        "final_approved_at": "",
        "draft_disposition": "",
        "source_label_status": str(case["source_label_status"]),
    }


def build_review_fixture(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Create an in-memory unreviewed template; no candidate is selected."""
    cases = [_blank_review_case(case) for case in inputs["cases"]]
    return {
        "schema_version": 1,
        "dataset_id": "schat-v1-operational-gold-reviewed",
        "dataset_version": "schat-v1-operational-gold-reviewed-v1",
        "source_uat_sha256": str(inputs["uat_sha256"]),
        "source_gold_sha256": str(inputs["gold_sha256"]),
        "local_only": True,
        "automatic_gold_assignment": False,
        "cases": cases,
    }


def _validate_string_list(case: Mapping[str, Any], field: str) -> None:
    values = case.get(field)
    if not isinstance(values, list) or len(values) > 40:
        raise ValueError(f"{field} must be a list with at most 40 values")
    if any(not isinstance(value, str) or not value.strip() or len(value) > 500 for value in values):
        raise ValueError(f"{field} must contain non-empty strings up to 500 characters")
    if len(values) != len(set(values)):
        raise ValueError(f"{field} must not contain duplicates")


def validate_review_case(case: Mapping[str, Any]) -> None:
    """Validate one human decision and fail closed on final approval."""
    if case.get("review_status") not in REVIEW_STATUSES:
        raise ValueError("invalid review_status")
    if case.get("scope_decision") not in SCOPE_DECISIONS:
        raise ValueError("invalid scope_decision")
    if case.get("expected_document") not in DOCUMENT_DECISIONS:
        raise ValueError("invalid expected_document")
    if case.get("expected_domain") not in DOMAIN_DECISIONS:
        raise ValueError("invalid expected_domain")
    expected_domain_by_scope = {
        "pending": "pending",
        "yes": "hospital",
        "no": "out_of_scope",
    }
    if case["expected_domain"] != expected_domain_by_scope[case["scope_decision"]]:
        raise ValueError("scope and domain decisions must agree")
    for field in ("primary_gold_ids", "acceptable_gold_ids", *CRITICAL_LIST_FIELDS):
        _validate_string_list(case, field)
    primary = set(case["primary_gold_ids"])
    acceptable = set(case["acceptable_gold_ids"])
    if primary.intersection(acceptable):
        raise ValueError("primary and acceptable Gold IDs overlap")
    if case.get("table_required") not in (None, True, False):
        raise ValueError("table_required must be true, false, or null")
    if case.get("image_required") not in (None, True, False):
        raise ValueError("image_required must be true, false, or null")
    for field in (
        "expected_abstain",
        "second_review_required",
        "reviewer_1_approved",
        "reviewer_2_approved",
        "final_gold_approved",
        "image_human_review_completed",
    ):
        if not isinstance(case.get(field), bool):
            raise ValueError(f"{field} must be boolean")
    if case["scope_decision"] == "no" and not case["expected_abstain"]:
        raise ValueError("out-of-scope review must expect abstention")
    if case["scope_decision"] == "no" and case["expected_document"] != "none":
        raise ValueError("out-of-scope review must use no answer document")
    if case["scope_decision"] == "yes" and case["expected_document"] in {"pending", "none"}:
        raise ValueError("in-scope review requires an answer document")
    if case["expected_abstain"] and (primary or acceptable):
        raise ValueError("abstention Gold must not contain evidence IDs")
    if case.get("draft_disposition", "") not in {
        "",
        "draft_accepted",
        "modified_accepted",
        "on_hold",
    }:
        raise ValueError("invalid draft_disposition")

    status = str(case["review_status"])
    human_decision = status in {"approved", "rejected", "needs_second_review"}
    if human_decision and (not str(case.get("reviewer", "")).strip() or not str(case.get("reviewed_at", "")).strip()):
        raise ValueError("human decision requires reviewer and reviewed_at")
    if status == "needs_second_review":
        if not case["reviewer_1_approved"] or case["final_gold_approved"]:
            raise ValueError("needs_second_review requires first approval and no final approval")
    if status == "approved":
        if case["scope_decision"] == "pending":
            raise ValueError("approved case requires scope decision")
        if case["expected_document"] == "pending":
            raise ValueError("approved case requires document decision")
        if not case["expected_abstain"] and not primary:
            raise ValueError("approved answerable case requires Primary Gold Evidence")
        if not case["expected_abstain"] and (
            not any(case[field] for field in CRITICAL_LIST_FIELDS)
            or case["table_required"] is None
            or case["image_required"] is None
        ):
            raise ValueError(
                "approved case requires critical fact and table/image decisions"
            )
        if (
            case.get("source_label_status") == "image_needs_human_review"
            and not case["image_human_review_completed"]
        ):
            raise ValueError("image human review checklist must be completed")
        if not case["reviewer_1_approved"]:
            raise ValueError("approved case requires reviewer 1 approval")
        if case["second_review_required"] and (
            not str(case.get("reviewer_2", "")).strip()
            or not str(case.get("reviewer_2_reviewed_at", "")).strip()
            or not case["reviewer_2_approved"]
        ):
            raise ValueError("second reviewer approval is required")
        if not case["final_gold_approved"]:
            raise ValueError("approved case requires final_gold_approved")
    elif case["final_gold_approved"]:
        raise ValueError("only approved cases can be final Gold")


def _prohibited_keys(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        keys = {str(key) for key in value}.intersection(PROHIBITED_PERSISTED_FIELDS)
        return keys.union(*(_prohibited_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_prohibited_keys(item) for item in value)) if value else set()
    return set()


def validate_review_fixture(payload: Mapping[str, Any]) -> None:
    cases = payload.get("cases")
    if payload.get("schema_version") != 1 or not isinstance(cases, list) or len(cases) != 32:
        raise ValueError("review fixture must contain the 32 positive cases")
    ids = [str(case.get("case_id", "")) for case in cases]
    if not all(ids) or len(ids) != len(set(ids)):
        raise ValueError("review case IDs must be present and unique")
    forbidden = _prohibited_keys(payload)
    if forbidden:
        raise ValueError(f"review fixture contains forbidden fields: {sorted(forbidden)}")
    for case in cases:
        validate_review_case(case)


def prepare_review_submission(
    saved_case: Mapping[str, Any],
    updates: Mapping[str, Any],
    *,
    now: str | None = None,
) -> dict[str, Any]:
    """Complete human-review metadata before validation and atomic persistence.

    The generic ``reviewed_at`` field remains the existing review contract. The
    two approval timestamps are additive review-audit metadata and do not alter
    the provisional Gold or any production answer/retrieval schema.
    """
    prepared = copy.deepcopy(dict(updates))
    status = str(prepared.get("review_status", saved_case.get("review_status", "")))
    reviewer = str(prepared.get("reviewer", saved_case.get("reviewer", ""))).strip()
    reviewer_1_approved = bool(
        prepared.get("reviewer_1_approved", saved_case.get("reviewer_1_approved", False))
    )
    reviewer_2 = str(
        prepared.get("reviewer_2", saved_case.get("reviewer_2", ""))
    ).strip()
    reviewer_2_approved = bool(
        prepared.get("reviewer_2_approved", saved_case.get("reviewer_2_approved", False))
    )
    final_gold_approved = bool(
        prepared.get("final_gold_approved", saved_case.get("final_gold_approved", False))
    )
    timestamp = now or datetime.now().astimezone().isoformat(timespec="seconds")

    if (status != "unreviewed" or reviewer_1_approved or final_gold_approved) and not reviewer:
        raise ValueError("저장 전에 1차 reviewer를 입력하세요.")
    if reviewer_2_approved and not reviewer_2:
        raise ValueError("2차 승인 전에 2차 reviewer를 입력하세요.")

    prepared["reviewer"] = reviewer
    prepared["reviewed_at"] = timestamp if status != "unreviewed" else ""
    prepared["reviewer_1_reviewed_at"] = (
        str(saved_case.get("reviewer_1_reviewed_at", "")).strip()
        or (
            str(saved_case.get("reviewed_at", "")).strip()
            if saved_case.get("reviewer_1_approved") is True
            else ""
        )
        or timestamp
        if reviewer_1_approved
        else ""
    )
    prepared["reviewer_2"] = reviewer_2
    prepared["reviewer_2_reviewed_at"] = (
        str(saved_case.get("reviewer_2_reviewed_at", "")).strip() or timestamp
        if reviewer_2_approved
        else ""
    )
    prepared["final_approved_at"] = (
        str(saved_case.get("final_approved_at", "")).strip()
        or (
            str(saved_case.get("reviewed_at", "")).strip()
            if saved_case.get("final_gold_approved") is True
            else ""
        )
        or timestamp
        if final_gold_approved
        else ""
    )
    return prepared


_DRAFT_REVIEW_FIELDS = (
    "scope_decision",
    "expected_domain",
    "expected_document",
    "expected_abstain",
    "primary_gold_ids",
    "acceptable_gold_ids",
    "critical_facts",
    "critical_numbers",
    "critical_units",
    "critical_times",
    "critical_conditions",
    "critical_contraindications",
    "critical_negations",
    "critical_steps",
    "table_required",
    "image_required",
)


def build_assisted_review_updates(
    saved_case: Mapping[str, Any],
    draft: Mapping[str, Any],
    *,
    action: str,
    form_updates: Mapping[str, Any],
    reviewer: str,
    final_approval_requested: bool,
    now: str | None = None,
) -> dict[str, Any]:
    """Build one explicit human decision without allowing draft auto-approval."""
    if saved_case.get("final_gold_approved") is True:
        raise ValueError("a final-approved Gold case is immutable")
    if draft.get("draft_only") is not True:
        raise ValueError("assisted review requires a draft-only record")
    if str(draft.get("case_id", "")) != str(saved_case.get("case_id", "")):
        raise ValueError("draft and review case IDs do not match")
    if action not in {"draft_accepted", "modified_accepted", "on_hold"}:
        raise ValueError("invalid assisted review action")

    updates: dict[str, Any] = {}
    if action in {"draft_accepted", "modified_accepted"}:
        updates.update(
            {
                field: copy.deepcopy(draft[field])
                for field in _DRAFT_REVIEW_FIELDS
                if field in draft
            }
        )
    if action in {"modified_accepted", "on_hold"}:
        updates.update(copy.deepcopy(dict(form_updates)))
    else:
        for field in (
            "image_human_review_completed",
            "note",
            "second_review_required",
            "reviewer_2",
            "reviewer_2_approved",
        ):
            if field in form_updates:
                updates[field] = copy.deepcopy(form_updates[field])

    human_approved = action in {"draft_accepted", "modified_accepted"}
    final_approved = human_approved and bool(final_approval_requested)
    updates.update(
        {
            "review_status": "approved" if final_approved else "reviewing",
            "reviewer": reviewer,
            "reviewer_1_approved": human_approved,
            "final_gold_approved": final_approved,
            "draft_disposition": action,
        }
    )
    return prepare_review_submission(saved_case, updates, now=now)


def next_unreviewed_case_id(
    payload: Mapping[str, Any], current_case_id: str
) -> str | None:
    """Return the next unreviewed case in fixture order, wrapping once."""
    cases = list(payload["cases"])
    if not cases:
        return None
    current_index = next(
        (index for index, case in enumerate(cases) if case["case_id"] == current_case_id),
        -1,
    )
    for offset in range(1, len(cases) + 1):
        case = cases[(current_index + offset) % len(cases)]
        if (
            case.get("review_status") == "unreviewed"
            and case.get("final_gold_approved") is not True
        ):
            return str(case["case_id"])
    return None


def update_review_case(
    payload: Mapping[str, Any],
    case_id: str,
    updates: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a validated copy after an explicit human form submission."""
    forbidden = set(updates).intersection(PROHIBITED_PERSISTED_FIELDS)
    if forbidden:
        raise ValueError(f"cannot persist local evidence fields: {sorted(forbidden)}")
    result = copy.deepcopy(dict(payload))
    target = next((case for case in result["cases"] if case["case_id"] == case_id), None)
    if target is None:
        raise ValueError(f"unknown review case: {case_id}")
    target.update(copy.deepcopy(dict(updates)))
    validate_review_case(target)
    return result


def save_review_fixture(
    path: Path,
    payload: Mapping[str, Any],
    *,
    protected_paths: Sequence[Path] = (),
) -> None:
    """Atomically save the minimal reviewed fixture, never a source fixture."""
    resolved = path.resolve()
    if any(resolved == protected.resolve() for protected in protected_paths):
        raise ValueError("refusing to overwrite a protected source fixture")
    validate_review_fixture(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded)
        Path(temp_name).replace(path)
    finally:
        temp_path = Path(temp_name)
        if temp_path.exists():
            temp_path.unlink()


def aggregate_approved_cases(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return only explicitly final-approved cases for downstream Gold metrics."""
    validate_review_fixture(payload)
    return [
        copy.deepcopy(case)
        for case in payload["cases"]
        if case.get("review_status") == "approved"
        and case.get("final_gold_approved") is True
    ]


def review_progress(payload: Mapping[str, Any]) -> dict[str, int]:
    cases = list(payload["cases"])
    statuses = [str(case["review_status"]) for case in cases]
    approved = sum(status == "approved" for status in statuses)
    rejected = sum(status == "rejected" for status in statuses)
    second = sum(status == "needs_second_review" for status in statuses)
    on_hold = sum(status == "reviewing" for status in statuses)
    reviewed = sum(status != "unreviewed" for status in statuses)
    return {
        "total": len(cases),
        "reviewed": reviewed,
        "approved": approved,
        "rejected": rejected,
        "on_hold": on_hold,
        "needs_second_review": second,
        "remaining": len(cases) - approved - rejected,
    }


def build_candidate_rows(
    chunks: Sequence[Mapping[str, Any]],
    *,
    retrieval_method: str,
) -> list[dict[str, Any]]:
    """Build memory-only display rows; no row contains an automatic Gold decision."""
    rows = []
    for rank, chunk in enumerate(chunks, 1):
        identifier = str(chunk.get("chunk_id", "")).strip()
        text = str(chunk.get("text", "")).strip()
        if not identifier or not text:
            raise ValueError("candidate requires chunk_id and text")
        rows.append(
            {
                "evidence_id": identifier,
                "identifier_type": "chunk",
                "document_name": str(chunk.get("document_name", "")),
                "page": chunk.get("page"),
                "section": str(chunk.get("section", "")),
                "parent_id": str(chunk.get("parent_id", "")),
                "evidence_text": text,
                "retrieval_rank": rank,
                "retrieval_method": retrieval_method,
                "score": float(chunk.get("score", 0.0)),
                "gold_status": "not_assigned",
            }
        )
    return rows


def build_table_candidate_rows(
    records: Sequence[Any],
    hits: Sequence[Any],
) -> list[dict[str, Any]]:
    """Build local-only structured table rows while preserving header context."""
    record_by_id = {record.table_id: record for record in records}
    rows = []
    for rank, hit in enumerate(hits, 1):
        record = record_by_id.get(hit.table_id)
        if record is None:
            raise ValueError(f"table candidate is outside the extracted records: {hit.table_id}")
        source_row = next((row for row in record.rows if row.row_id == hit.row_id), None)
        if source_row is None:
            raise ValueError(f"table row candidate not found: {hit.row_id}")
        header_text = " | ".join(cell.text for cell in record.header)
        row_text = " | ".join(cell.text for cell in source_row.cells)
        rows.append(
            {
                "evidence_id": f"{record.table_id}:{source_row.row_id}",
                "identifier_type": "table_row",
                "document_name": record.document_name,
                "page": record.page,
                "section": "구조화 표 행",
                "parent_id": record.table_id,
                "evidence_text": f"{header_text}\n{row_text}",
                "retrieval_rank": rank,
                "retrieval_method": "local_structured_table_reference_only",
                "score": float(hit.score),
                "gold_status": "not_assigned",
            }
        )
    return rows


def _catalog_revision(path: Path) -> int:
    with sqlite3.connect(path) as db:
        row = db.execute("select version from corpus").fetchone()
    if row is None:
        raise ValueError("catalog revision missing")
    return int(row[0])


def _document_scopes(documents: Sequence[Mapping[str, Any]]) -> dict[str, tuple[str, ...]]:
    result: dict[str, list[str]] = {"sedation": [], "transfusion": []}
    for document in documents:
        name = str(document.get("document_name", ""))
        if "진정" in name:
            result["sedation"].append(str(document["id"]))
        if "수혈" in name:
            result["transfusion"].append(str(document["id"]))
    if any(not ids for ids in result.values()):
        raise ValueError("registered sedation and transfusion documents are required")
    return {scope: tuple(ids) for scope, ids in result.items()}


@lru_cache(maxsize=2)
def _candidate_engine(catalog_path: str):
    from mvp.library import Embedder
    from mvp.repository import snapshot

    path = Path(catalog_path)
    library = snapshot(str(path.resolve()), _catalog_revision(path))
    return library, Embedder(), _document_scopes(library.docs)


def retrieve_local_chunk_candidates(
    *,
    catalog_path: Path,
    question: str,
    document_scope: str,
    limit: int = 16,
) -> list[dict[str, Any]]:
    """Run the existing local retrieval path and return memory-only display rows."""
    from mvp.library import bounded_embedding_question
    from mvp.query import plan_query

    library, embedder, scopes = _candidate_engine(str(catalog_path.resolve()))
    allowed = scopes.get(document_scope)
    if not allowed:
        raise ValueError(f"unsupported document scope: {document_scope}")
    plan = plan_query(
        question,
        documents=library.docs,
        context_document_ids=allowed,
    )
    query_text = bounded_embedding_question(plan.expanded, embedder)
    vector = embedder.encode([query_text])[0]
    hits = library.search(
        plan.query,
        vector,
        list(allowed),
        0.38,
        plan=plan,
        trace={},
    )[:limit]
    chunks = [
        {
            "chunk_id": hit.chunk.id,
            "document_name": hit.chunk.document_name,
            "page": hit.chunk.page,
            "section": hit.chunk.section,
            "parent_id": hit.chunk.parent_id,
            "text": hit.chunk.text,
            "score": hit.rerank_score or hit.fusion_score or hit.similarity,
        }
        for hit in hits
    ]
    return build_candidate_rows(
        chunks,
        retrieval_method="local_bm25_minilm_rrf_reranker_reference_only",
    )


def retrieve_local_table_candidates(
    *,
    catalog_path: Path,
    question: str,
    document_scope: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Retrieve exact table rows locally; exact cells remain in process memory."""
    if document_scope != "transfusion":
        return []
    from tools.chroma_baseline_evaluate import load_catalog
    from tools.structured_table_evidence import extract_table_records, search_table_records

    library, _, scopes = _candidate_engine(str(catalog_path.resolve()))
    document_id = scopes[document_scope][0]
    document = next(doc for doc in library.docs if str(doc["id"]) == document_id)
    document_name = str(document["document_name"])
    _, chunks = load_catalog(catalog_path, document_name=document_name)
    pdf_path = catalog_path.resolve().parents[1] / document_name
    records = extract_table_records(pdf_path, document_id, document_name, chunks)
    hits = search_table_records(question, records, limit=limit)
    return build_table_candidate_rows(records, hits)
