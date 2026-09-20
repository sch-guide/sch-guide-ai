"""Fail-closed, local-only human review contract for the TF027 figure.

The source checklist remains immutable. Exact page pixels are rendered only in
memory, while the reviewed fixture stores identifiers and human-entered values.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

CHECK_IDS = (
    "figure_boundary",
    "start_and_end_nodes",
    "node_labels",
    "arrow_direction_and_connections",
    "decision_branch_conditions",
    "workflow_order",
    "numbers_units_and_times",
    "caption_nearby_text_relationship",
    "illegible_or_occluded_elements",
    "production_gold_approval",
)
CHECK_STATUSES = ("unreviewed", "confirmed", "uncertain", "not_applicable")
OPTIONAL_CHECK_IDS = frozenset(
    {"decision_branch_conditions", "numbers_units_and_times"}
)
STRUCTURED_LIST_FIELDS = (
    "selected_figure_ids",
    "nodes",
    "edges",
    "branches",
    "sequence",
    "numbers",
    "units",
    "times",
    "uncertainties",
)
FORBIDDEN_PERSISTED_KEYS = frozenset(
    {
        "question",
        "exact_text",
        "source_text",
        "evidence_text",
        "raw_image",
        "image_data",
        "prompt",
        "raw_response",
        "authorization",
        "api_key",
    }
)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.casefold())
    )


def _forbidden_keys(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        found = {str(key).casefold() for key in value}.intersection(
            FORBIDDEN_PERSISTED_KEYS
        )
        nested = set().union(*(_forbidden_keys(item) for item in value.values()))
        return found.union(nested)
    if isinstance(value, list):
        return set().union(*(_forbidden_keys(item) for item in value)) if value else set()
    return set()


def load_source_checklist(path: Path) -> dict[str, Any]:
    """Load and verify the immutable, still-pending source checklist."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    checks = payload.get("checks")
    if payload.get("case_id") != "TF027" or not isinstance(checks, list):
        raise ValueError("invalid TF027 source checklist")
    if tuple(str(check.get("check_id", "")) for check in checks) != CHECK_IDS:
        raise ValueError("TF027 source checklist IDs changed")
    if any(check.get("status") != "unreviewed" for check in checks):
        raise ValueError("source checklist must remain unreviewed")
    if (
        payload.get("needs_human_review") is not True
        or payload.get("production_gold_approved") is not False
        or payload.get("included_in_aggregate") is not False
        or payload.get("vision_api_calls") != 0
        or payload.get("raw_image_stored") is not False
    ):
        raise ValueError("source checklist must remain fail closed")
    source = payload.get("source_reference")
    if not isinstance(source, Mapping) or not source.get("figure_candidates"):
        raise ValueError("source checklist figure candidates are missing")
    return payload


def _minimal_source_reference(source: Mapping[str, Any]) -> dict[str, Any]:
    candidates = []
    for candidate in source.get("figure_candidates", []):
        bbox = candidate.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise ValueError("figure candidate bbox must contain four values")
        candidates.append(
            {
                "figure_id": str(candidate["figure_id"]),
                "bbox": [float(value) for value in bbox],
                "fingerprint": str(candidate["fingerprint"]),
            }
        )
    return {
        "document_id": str(source["document_id"]),
        "page": int(source["page"]),
        "figure_candidates": candidates,
    }


def build_reviewed_fixture(
    source: Mapping[str, Any],
    *,
    source_path: Path,
) -> dict[str, Any]:
    """Create an empty reviewed fixture without inventing any clinical value."""
    payload = {
        "schema_version": 1,
        "case_id": "TF027",
        "source_checklist_sha256": _sha256_file(source_path),
        "local_only": True,
        "automatic_image_interpretation": False,
        "review_status": "unreviewed",
        "needs_human_review": True,
        "production_gold_approved": False,
        "included_in_aggregate": False,
        "vision_api_calls": 0,
        "raw_image_stored": False,
        "source_reference": _minimal_source_reference(source["source_reference"]),
        "checks": [
            {
                "check_id": check_id,
                "status": "unreviewed",
                "reviewed_value": None,
                "note": None,
            }
            for check_id in CHECK_IDS
        ],
        "structured_result": {
            "selected_figure_ids": [],
            "nodes": [],
            "edges": [],
            "branches": [],
            "sequence": [],
            "numbers": [],
            "units": [],
            "times": [],
            "caption_relation": "",
            "uncertainties": [],
        },
        "reviewers": {
            "reviewer_1": "",
            "reviewer_1_reviewed_at": "",
            "reviewer_1_approved": False,
            "second_review_required": True,
            "reviewer_2": "",
            "reviewer_2_reviewed_at": "",
            "reviewer_2_approved": False,
        },
        "approval_decision": "pending",
        "approval_blockers": [],
    }
    return normalize_review_state(payload)


def _validate_string_list(value: Any, field: str, *, max_items: int = 100) -> None:
    if not isinstance(value, list) or len(value) > max_items:
        raise ValueError(f"{field} must be a list with at most {max_items} values")
    if any(
        not isinstance(item, str) or not item.strip() or len(item) > 500
        for item in value
    ):
        raise ValueError(f"{field} must contain non-empty strings up to 500 characters")
    if len(value) != len(set(value)):
        raise ValueError(f"{field} must not contain duplicates")


def _is_iso_datetime(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _validate_base(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != 1 or payload.get("case_id") != "TF027":
        raise ValueError("invalid reviewed TF027 fixture")
    if not _is_sha256(payload.get("source_checklist_sha256")):
        raise ValueError("source checklist fingerprint is invalid")
    if payload.get("local_only") is not True:
        raise ValueError("TF027 review must remain local-only")
    if payload.get("automatic_image_interpretation") is not False:
        raise ValueError("automatic image interpretation is forbidden")
    if payload.get("vision_api_calls") != 0 or payload.get("raw_image_stored") is not False:
        raise ValueError("vision calls and raw image storage are forbidden")
    forbidden = _forbidden_keys(payload)
    if forbidden:
        raise ValueError(f"review fixture contains forbidden fields: {sorted(forbidden)}")

    source = payload.get("source_reference")
    if not isinstance(source, Mapping) or not isinstance(source.get("page"), int):
        raise ValueError("source_reference is invalid")
    candidates = source.get("figure_candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("figure candidates are required")
    candidate_ids = []
    for candidate in candidates:
        if set(candidate) != {"figure_id", "bbox", "fingerprint"}:
            raise ValueError("figure candidate must contain metadata only")
        candidate_ids.append(str(candidate["figure_id"]))
        bbox = candidate["bbox"]
        if (
            not isinstance(bbox, list)
            or len(bbox) != 4
            or any(not isinstance(value, (int, float)) for value in bbox)
            or any(not math.isfinite(float(value)) for value in bbox)
            or float(bbox[0]) >= float(bbox[2])
            or float(bbox[1]) >= float(bbox[3])
        ):
            raise ValueError("figure bbox is invalid")
        if not _is_sha256(candidate.get("fingerprint")):
            raise ValueError("figure fingerprint is invalid")
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("figure candidate IDs must be unique")

    checks = payload.get("checks")
    if not isinstance(checks, list) or tuple(
        str(check.get("check_id", "")) for check in checks
    ) != CHECK_IDS:
        raise ValueError("all ten checks are required in fixed order")
    for check in checks:
        status = check.get("status")
        if status not in CHECK_STATUSES:
            raise ValueError("invalid check status")
        for field in ("reviewed_value", "note"):
            value = check.get(field)
            if value is not None and (
                not isinstance(value, str) or len(value) > 2000
            ):
                raise ValueError(f"invalid {field}")
        if status in {"uncertain", "not_applicable"} and not str(
            check.get("note") or ""
        ).strip():
            raise ValueError(f"{status} check requires a human note")

    structured = payload.get("structured_result")
    if not isinstance(structured, Mapping):
        raise ValueError("structured_result is required")
    for field in STRUCTURED_LIST_FIELDS:
        _validate_string_list(structured.get(field), field)
    if not isinstance(structured.get("caption_relation"), str):
        raise ValueError("caption_relation must be text")
    if len(structured["caption_relation"]) > 2000:
        raise ValueError("caption_relation is too long")
    if not set(structured["selected_figure_ids"]).issubset(candidate_ids):
        raise ValueError("selected figure must come from the source checklist")

    reviewers = payload.get("reviewers")
    if not isinstance(reviewers, Mapping):
        raise ValueError("reviewers are required")
    for field in ("reviewer_1", "reviewer_2"):
        if not isinstance(reviewers.get(field), str) or len(reviewers[field]) > 100:
            raise ValueError(f"invalid {field}")
    for field in ("reviewer_1_approved", "second_review_required", "reviewer_2_approved"):
        if not isinstance(reviewers.get(field), bool):
            raise ValueError(f"{field} must be boolean")
    if reviewers.get("second_review_required") is not True:
        raise ValueError("TF027 production Gold requires second review")
    for approved_field, reviewer_field, time_field in (
        ("reviewer_1_approved", "reviewer_1", "reviewer_1_reviewed_at"),
        ("reviewer_2_approved", "reviewer_2", "reviewer_2_reviewed_at"),
    ):
        value = reviewers.get(time_field)
        if not isinstance(value, str):
            raise ValueError(f"invalid {time_field}")
        if reviewers[approved_field] and (
            not reviewers[reviewer_field].strip() or not _is_iso_datetime(value)
        ):
            raise ValueError(f"{approved_field} requires reviewer and reviewed_at")
    if payload.get("approval_decision") not in {"pending", "approve", "reject"}:
        raise ValueError("invalid approval_decision")


def _approval_blockers(payload: Mapping[str, Any]) -> list[str]:
    checks = {str(check["check_id"]): check for check in payload["checks"]}
    blockers: list[str] = []
    if any(check["status"] == "uncertain" for check in checks.values()):
        blockers.append("unresolved_uncertainty")
    if payload["structured_result"]["uncertainties"]:
        blockers.append("unresolved_uncertainty")

    for check_id, check in checks.items():
        status = str(check["status"])
        if status == "unreviewed":
            blockers.append(f"unreviewed:{check_id}")
        elif status == "confirmed" and not str(
            check.get("reviewed_value") or ""
        ).strip():
            blockers.append(f"missing_reviewed_value:{check_id}")
        elif status == "not_applicable" and check_id not in OPTIONAL_CHECK_IDS:
            blockers.append(f"required_check_not_confirmed:{check_id}")
        elif status == "uncertain":
            blockers.append(f"uncertain:{check_id}")

    structured = payload["structured_result"]
    for field in ("selected_figure_ids", "nodes", "edges", "sequence"):
        if not structured[field]:
            blockers.append(f"missing_structured:{field}")
    if not structured["caption_relation"].strip():
        blockers.append("missing_structured:caption_relation")
    if checks["decision_branch_conditions"]["status"] == "confirmed" and not structured[
        "branches"
    ]:
        blockers.append("missing_structured:branches")
    if checks["numbers_units_and_times"]["status"] == "confirmed" and not any(
        structured[field] for field in ("numbers", "units", "times")
    ):
        blockers.append("missing_structured:numbers_units_times")

    reviewers = payload["reviewers"]
    if not reviewers["reviewer_1_approved"]:
        blockers.append("reviewer_1_approval_missing")
    if reviewers["reviewer_1_approved"] and (
        not reviewers["reviewer_1"].strip()
        or not _is_iso_datetime(reviewers["reviewer_1_reviewed_at"])
    ):
        blockers.append("reviewer_1_identity_or_time_missing")
    if not reviewers["reviewer_2_approved"]:
        blockers.append("reviewer_2_approval_missing")
    if reviewers["reviewer_2_approved"] and (
        not reviewers["reviewer_2"].strip()
        or not _is_iso_datetime(reviewers["reviewer_2_reviewed_at"])
    ):
        blockers.append("reviewer_2_identity_or_time_missing")
    if (
        reviewers["reviewer_1_approved"]
        and reviewers["reviewer_2_approved"]
        and reviewers["reviewer_1"].strip().casefold()
        == reviewers["reviewer_2"].strip().casefold()
    ):
        blockers.append("reviewers_not_independent")
    if payload["approval_decision"] != "approve":
        blockers.append("explicit_approval_missing")
    return list(dict.fromkeys(blockers))


def normalize_review_state(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Derive approval flags from human fields; caller booleans cannot bypass it."""
    result = copy.deepcopy(dict(payload))
    _validate_base(result)
    blockers = _approval_blockers(result)
    approved = not blockers
    result["approval_blockers"] = blockers
    result["production_gold_approved"] = approved
    result["needs_human_review"] = not approved
    result["included_in_aggregate"] = approved

    if approved:
        result["review_status"] = "approved"
    elif result["approval_decision"] == "reject":
        result["review_status"] = "rejected"
    elif (
        result["approval_decision"] == "approve"
        and result["reviewers"]["reviewer_1_approved"]
        and "reviewer_2_approval_missing" in blockers
        and not any(
            blocker
            for blocker in blockers
            if blocker not in {"reviewer_2_approval_missing"}
        )
    ):
        result["review_status"] = "needs_second_review"
    else:
        has_activity = any(
            check["status"] != "unreviewed" for check in result["checks"]
        ) or any(result["structured_result"][field] for field in STRUCTURED_LIST_FIELDS)
        has_activity = has_activity or bool(
            result["structured_result"]["caption_relation"].strip()
        )
        has_activity = has_activity or bool(result["reviewers"]["reviewer_1"].strip())
        result["review_status"] = "reviewing" if has_activity else "unreviewed"
    return result


def validate_reviewed_fixture(payload: Mapping[str, Any]) -> None:
    _validate_base(payload)
    normalized = normalize_review_state(payload)
    for field in (
        "review_status",
        "needs_human_review",
        "production_gold_approved",
        "included_in_aggregate",
        "approval_blockers",
    ):
        if payload.get(field) != normalized[field]:
            raise ValueError(f"derived field does not match review evidence: {field}")


def save_reviewed_fixture(
    path: Path,
    payload: Mapping[str, Any],
    *,
    protected_paths: Sequence[Path] = (),
) -> dict[str, Any]:
    """Atomically save metadata and human input to a separate reviewed fixture."""
    resolved = path.resolve()
    if any(resolved == protected.resolve() for protected in protected_paths):
        raise ValueError("refusing to overwrite a protected source fixture")
    normalized = normalize_review_state(payload)
    validate_reviewed_fixture(normalized)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(normalized, ensure_ascii=False, indent=2) + "\n"
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded)
        Path(temporary_name).replace(path)
    finally:
        temporary = Path(temporary_name)
        if temporary.exists():
            temporary.unlink()
    return normalized


def aggregate_approved_image_gold(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Exclude pending/partial reviews from all downstream image aggregates."""
    validate_reviewed_fixture(payload)
    if payload["production_gold_approved"] and payload["included_in_aggregate"]:
        return [copy.deepcopy(dict(payload))]
    return []


def image_answerability_enabled(payload: Mapping[str, Any]) -> bool:
    """Expose the explicit gate without connecting it to production routing."""
    validate_reviewed_fixture(payload)
    return bool(payload["production_gold_approved"] and payload["included_in_aggregate"])


def render_review_images(
    pdf_path: Path,
    *,
    page_number: int,
    figure_candidates: Sequence[Mapping[str, Any]],
    scale: float = 1.5,
):
    """Render an untouched page and an in-memory bbox overlay for local review."""
    if scale <= 0:
        raise ValueError("scale must be positive")
    import pypdfium2 as pdfium
    from PIL import ImageDraw

    document = pdfium.PdfDocument(str(pdf_path))
    try:
        if page_number < 1 or page_number > len(document):
            raise ValueError("page_number is outside the PDF")
        page = document[page_number - 1]
        try:
            original = page.render(scale=scale).to_pil().convert("RGB")
        finally:
            page.close()
    finally:
        document.close()

    overlay = original.copy()
    draw = ImageDraw.Draw(overlay)
    line_width = max(2, round(2 * scale))
    for index, candidate in enumerate(figure_candidates, 1):
        bbox = [float(value) * scale for value in candidate["bbox"]]
        draw.rectangle(bbox, outline="#e11d48", width=line_width)
        draw.text((bbox[0] + 3, bbox[1] + 3), str(index), fill="#e11d48")
    return original, overlay
