"""Conservative, local-only draft generation for operational Gold review.

Drafts are review aids, never approved Gold. Exact clinical fragments may be
stored only in the ignored local draft file; candidate bodies and retrieval
scores are never persisted.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

DRAFT_STATUSES = (
    "ready_for_human_approval",
    "edit_recommended",
    "manual_review_required",
)
_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣%℃°]+")
_NUMBER_RE = re.compile(r"(?<![0-9])\d+(?:[.,]\d+)?")
_UNIT_RE = re.compile(
    r"(?i)(?:mg|mcg|μg|g|kg|mL|ml|cc|L|mmHg|cm|mm|%|℃|°C|회|초|분|시간|일)"
)
_TIME_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:초|분|시간|일)(?:\s*(?:마다|간격|이내|이상|이하|후|전))?"
)
_LEADING_STEP_RE = re.compile(r"^\s*(?:\d+[.)]|[①-⑳]|[가-힣][.)]|[-•])\s*")
_STOPWORDS = frozenset(
    {
        "알려줘",
        "알려주세요",
        "설명해줘",
        "설명해주세요",
        "무엇인가요",
        "뭐야",
        "대해",
        "관련",
        "내용",
        "간호",
        "전체적으로",
        "핵심",
        "하는",
        "어떻게",
        "해야",
        "하나요",
    }
)
_INTENT_MARKERS = {
    "preparation": ("전", "준비", "확인", "체크"),
    "procedure": ("절차", "순서", "방법", "시행", "단계"),
    "monitoring": ("관찰", "확인", "모니터링", "상태"),
    "cautions": ("주의", "금기", "조심", "안전"),
    "adverse_reaction": ("이상반응", "부작용", "반응", "조치"),
    "product_specific": ("제제", "혈액", "제품", "기준"),
    "comparison": ("비교", "차이", "각각"),
    "summary": ("요약", "전체", "핵심"),
}
_CONDITION_MARKERS = (
    "경우",
    "필요 시",
    "이상",
    "이하",
    "이내",
    "전에",
    "후에",
    "동안",
)
_CONTRAINDICATION_MARKERS = (
    "금기",
    "금지",
    "하지 않는다",
    "해서는 안",
    "사용하지",
    "투여하지",
    "시행하지",
    "불가",
)
_NEGATION_MARKERS = ("않", "아니", "없", "금지", "불가", "제외")
_PROHIBITED_DRAFT_FIELDS = frozenset(
    {"question", "evidence_text", "text", "retrieval_rank", "retrieval_method", "score"}
)


def _unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(
        token.casefold()
        for token in _TOKEN_RE.findall(value)
        if token.casefold() not in _STOPWORDS
    )


def _document_matches(case: Mapping[str, Any], candidate: Mapping[str, Any]) -> bool:
    scope = str(case.get("expected_document_scope", ""))
    name = str(candidate.get("document_name", ""))
    if scope == "sedation":
        return "진정" in name
    if scope == "transfusion":
        return "수혈" in name
    return False


def _directness(case: Mapping[str, Any], candidate: Mapping[str, Any]) -> float:
    if not _document_matches(case, candidate):
        return 0.0
    question_tokens = _tokens(str(case.get("question", "")))
    combined = (
        f"{candidate.get('section', '')} {candidate.get('evidence_text', '')}"
    ).casefold()
    if not question_tokens:
        return 0.0
    matched = sum(token in combined for token in question_tokens)
    lexical = matched / len(question_tokens)
    intent = str(case.get("expected_intent") or case.get("question_type") or "")
    markers = _INTENT_MARKERS.get(intent, ())
    marker_match = any(
        marker in str(case.get("question", "")) and marker in combined
        for marker in markers
    )
    intent_support = any(marker in combined for marker in markers)
    question_type = str(case.get("question_type", ""))
    action_count = sum(
        marker in combined
        for marker in ("확인", "시행", "관찰", "준비", "투여", "교육", "측정")
    )
    broad_support = (
        min(0.45, action_count * 0.15)
        if question_type in {"summary", "procedure"} and action_count >= 2
        else 0.0
    )
    question = str(case.get("question", ""))
    numeric_support = 0.25 if "몇" in question and _TIME_RE.search(combined) else 0.0
    temporal_support = (
        0.15
        if question_type == "temporal"
        and any(marker in combined for marker in ("전", "중", "후", "시작", "종료"))
        else 0.0
    )
    type_match = {
        "table": candidate.get("identifier_type") == "table_row",
        "mixed": candidate.get("identifier_type") in {"chunk", "table_row"},
        "text": candidate.get("identifier_type") == "chunk",
    }.get(str(case.get("expected_evidence_type", "")), True)
    return min(
        1.0,
        lexical
        + (0.15 if marker_match else 0.0)
        + (0.12 if intent_support and not marker_match else 0.0)
        + broad_support
        + numeric_support
        + temporal_support
        + (0.05 if type_match else 0.0),
    )


def _sentences(text: str) -> list[str]:
    return _unique(re.split(r"(?<=[.!?。])\s+|\r?\n+", text.strip()))


def _sentence_directness(question: str, sentence: str) -> float:
    tokens = _tokens(question)
    if not tokens:
        return 0.0
    folded = sentence.casefold()
    return sum(token in folded for token in tokens) / len(tokens)


def _critical_facts(
    case: Mapping[str, Any],
    selected: Sequence[Mapping[str, Any]],
) -> list[str]:
    facts: list[str] = []
    question = str(case.get("question", ""))
    for candidate in selected:
        options = _sentences(str(candidate.get("evidence_text", "")))
        if not options:
            continue
        ranked = sorted(
            options,
            key=lambda sentence: (-_sentence_directness(question, sentence), options.index(sentence)),
        )
        best = ranked[0]
        if _sentence_directness(question, best) > 0 or len(options) == 1:
            facts.append(best[:500])
    return _unique(facts)[:6]


def _exact_invariants(facts: Sequence[str]) -> dict[str, list[str]]:
    joined = "\n".join(_LEADING_STEP_RE.sub("", fact, count=1) for fact in facts)
    return {
        "critical_numbers": _unique(_NUMBER_RE.findall(joined)),
        "critical_units": _unique(_UNIT_RE.findall(joined)),
        "critical_times": _unique(_TIME_RE.findall(joined)),
        "critical_conditions": [
            fact for fact in facts if any(marker in fact for marker in _CONDITION_MARKERS)
        ],
        "critical_contraindications": [
            fact
            for fact in facts
            if any(marker in fact for marker in _CONTRAINDICATION_MARKERS)
        ],
        "critical_negations": [
            fact for fact in facts if any(marker in fact for marker in _NEGATION_MARKERS)
        ],
        "critical_steps": [fact for fact in facts if _LEADING_STEP_RE.match(fact)],
    }


def _empty_draft(case: Mapping[str, Any], reason: str) -> dict[str, Any]:
    return {
        "case_id": str(case.get("case_id", "")),
        "draft_only": True,
        "review_status": "unreviewed",
        "final_gold_approved": False,
        "scope_decision": "no" if case.get("expected_abstain") else "pending",
        "expected_domain": (
            "out_of_scope" if case.get("expected_abstain") else "pending"
        ),
        "expected_document": "none" if case.get("expected_abstain") else "pending",
        "expected_abstain": bool(case.get("expected_abstain")),
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
        "image_required": None,
        "confidence": "low",
        "draft_status": "manual_review_required",
        "human_review_reasons": [reason],
        "candidate_count": 0,
        "source_fingerprint": "",
    }


def build_case_draft(
    case: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build a non-approved draft using evidence content, never rank alone."""
    if case.get("expected_abstain") or case.get("expected_domain") == "out_of_scope":
        return _empty_draft(case, "out_of_scope_no_clinical_draft")
    if case.get("expected_evidence_type") in {"image", "image_pending"}:
        draft = _empty_draft(case, "image_human_review_required")
        draft.update(
            {
                "scope_decision": "yes",
                "expected_domain": "hospital",
                "expected_document": str(case.get("expected_document_scope", "pending")),
                "image_required": True,
                "table_required": False,
            }
        )
        return draft

    scored = [
        (candidate, _directness(case, candidate))
        for candidate in candidates
        if str(candidate.get("evidence_id", "")).strip()
        and str(candidate.get("evidence_text", "")).strip()
    ]
    scored.sort(
        key=lambda item: (
            -item[1],
            int(item[0].get("retrieval_rank", 10_000)),
            str(item[0].get("evidence_id", "")),
        )
    )
    top_score = scored[0][1] if scored else 0.0
    primary_limit = (
        4
        if str(case.get("question_type", ""))
        in {"procedure", "comparison", "summary", "temporal"}
        else 2
    )
    primary_rows = [
        candidate
        for candidate, score in scored
        if score >= 0.45 and score >= top_score - 0.12
    ][:primary_limit]
    primary_ids = [str(candidate["evidence_id"]) for candidate in primary_rows]
    acceptable_rows = [
        candidate
        for candidate, score in scored
        if 0.28 <= score < max(0.45, top_score - 0.12)
        and str(candidate["evidence_id"]) not in primary_ids
    ][:4]
    facts = _critical_facts(case, primary_rows)
    invariants = _exact_invariants(facts)
    evidence_type = str(case.get("expected_evidence_type", "text"))
    table_required = True if evidence_type == "table" else (None if evidence_type == "mixed" else False)
    image_required = False

    reasons: list[str] = []
    if not primary_ids:
        reasons.append("direct_primary_evidence_not_confident")
    if not facts:
        reasons.append("critical_fact_not_confident")
    if evidence_type == "mixed":
        reasons.append("mixed_evidence_requires_human_choice")
    if evidence_type == "table" and not any(
        candidate.get("identifier_type") == "table_row" for candidate in primary_rows
    ):
        reasons.append("table_row_requires_human_confirmation")

    if not primary_ids or not facts:
        draft_status = "manual_review_required"
        confidence = "low"
    elif reasons or top_score < 0.72:
        draft_status = "edit_recommended"
        confidence = "medium"
    else:
        draft_status = "ready_for_human_approval"
        confidence = "high"

    fingerprint_source = "\n".join(
        f"{candidate.get('evidence_id', '')}:{hashlib.sha256(str(candidate.get('evidence_text', '')).encode('utf-8')).hexdigest()}"
        for candidate in candidates
    )
    draft = {
        "case_id": str(case["case_id"]),
        "draft_only": True,
        "review_status": "unreviewed",
        "final_gold_approved": False,
        "scope_decision": "yes",
        "expected_domain": "hospital",
        "expected_document": str(case.get("expected_document_scope", "pending")),
        "expected_abstain": False,
        "primary_gold_ids": primary_ids,
        "acceptable_gold_ids": [
            str(candidate["evidence_id"]) for candidate in acceptable_rows
        ],
        "critical_facts": facts,
        **invariants,
        "table_required": table_required,
        "image_required": image_required,
        "confidence": confidence,
        "draft_status": draft_status,
        "human_review_reasons": reasons,
        "candidate_count": len(candidates),
        "source_fingerprint": hashlib.sha256(
            fingerprint_source.encode("utf-8")
        ).hexdigest(),
    }
    validate_case_draft(draft)
    return draft


def validate_case_draft(draft: Mapping[str, Any]) -> None:
    if draft.get("draft_only") is not True:
        raise ValueError("Gold draft must remain draft_only")
    if draft.get("final_gold_approved") is not False:
        raise ValueError("Gold draft cannot be finally approved")
    if draft.get("review_status") != "unreviewed":
        raise ValueError("Gold draft cannot contain a human review decision")
    if draft.get("draft_status") not in DRAFT_STATUSES:
        raise ValueError("invalid draft_status")
    primary = draft.get("primary_gold_ids")
    acceptable = draft.get("acceptable_gold_ids")
    if not isinstance(primary, list) or not isinstance(acceptable, list):
        raise ValueError("draft evidence IDs must be lists")
    if set(primary).intersection(acceptable):
        raise ValueError("draft Primary and Acceptable evidence IDs overlap")
    for field in (
        "critical_facts",
        "critical_numbers",
        "critical_units",
        "critical_times",
        "critical_conditions",
        "critical_contraindications",
        "critical_negations",
        "critical_steps",
        "human_review_reasons",
    ):
        values = draft.get(field)
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError(f"{field} must be a string list")


def _prohibited_keys(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        found = set(value).intersection(_PROHIBITED_DRAFT_FIELDS)
        for child in value.values():
            found.update(_prohibited_keys(child))
        return found
    if isinstance(value, list):
        found: set[str] = set()
        for child in value:
            found.update(_prohibited_keys(child))
        return found
    return set()


def validate_draft_fixture(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != 1 or payload.get("local_only") is not True:
        raise ValueError("invalid local Gold draft fixture")
    if payload.get("automatic_gold_approval") is not False:
        raise ValueError("automatic Gold approval must remain disabled")
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise ValueError("draft fixture cases must be a list")
    if int(payload.get("draft_case_count", -1)) != len(cases):
        raise ValueError("draft case count mismatch")
    ids = [str(case.get("case_id", "")) for case in cases]
    if not all(ids) or len(ids) != len(set(ids)):
        raise ValueError("draft case IDs must be present and unique")
    approved_ids = set(payload.get("excluded_final_approved_case_ids", []))
    if approved_ids.intersection(ids):
        raise ValueError("final-approved cases cannot appear in a draft fixture")
    forbidden = _prohibited_keys(payload)
    if forbidden:
        raise ValueError(f"draft fixture contains forbidden fields: {sorted(forbidden)}")
    for case in cases:
        validate_case_draft(case)


def build_draft_fixture(
    inputs: Mapping[str, Any],
    review: Mapping[str, Any],
    candidate_loader: Callable[[Mapping[str, Any]], Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Generate drafts only for cases without an existing final human approval."""
    review_by_id = {str(case["case_id"]): case for case in review["cases"]}
    approved_ids = {
        case_id
        for case_id, case in review_by_id.items()
        if case.get("final_gold_approved") is True
    }
    drafts = []
    for case in inputs["cases"]:
        case_id = str(case["case_id"])
        if case_id in approved_ids:
            continue
        try:
            candidates = list(candidate_loader(case))
            draft = build_case_draft(case, candidates)
        except (OSError, RuntimeError, ValueError):
            draft = _empty_draft(case, "local_candidate_generation_failed")
        drafts.append(draft)
    payload = {
        "schema_version": 1,
        "dataset_id": "schat-v1-operational-gold-drafts",
        "dataset_version": "schat-v1-operational-gold-drafts-v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "local_only": True,
        "automatic_gold_approval": False,
        "source_uat_sha256": str(inputs["uat_sha256"]),
        "source_gold_sha256": str(inputs["gold_sha256"]),
        "excluded_final_approved_case_ids": sorted(approved_ids),
        "draft_case_count": len(drafts),
        "cases": drafts,
    }
    validate_draft_fixture(payload)
    return payload


def save_draft_fixture(
    path: Path,
    payload: Mapping[str, Any],
    *,
    protected_paths: Sequence[Path] = (),
) -> None:
    resolved = path.resolve()
    if any(resolved == protected.resolve() for protected in protected_paths):
        raise ValueError("refusing to overwrite a protected fixture")
    validate_draft_fixture(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    handle, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded)
        Path(temp_name).replace(path)
    finally:
        temp_path = Path(temp_name)
        if temp_path.exists():
            temp_path.unlink()


def load_draft_fixture(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    validate_draft_fixture(payload)
    return payload


def draft_progress(
    payload: Mapping[str, Any], review: Mapping[str, Any]
) -> dict[str, int]:
    validate_draft_fixture(payload)
    review_by_id = {str(case["case_id"]): case for case in review["cases"]}
    cases = list(payload["cases"])
    approved = sum(
        review_by_id.get(case["case_id"], {}).get("final_gold_approved") is True
        for case in cases
    )
    on_hold = sum(
        review_by_id.get(case["case_id"], {}).get("review_status") == "reviewing"
        and review_by_id.get(case["case_id"], {}).get("reviewer_1_approved") is not True
        for case in cases
    )
    return {
        "total": len(cases),
        "ready_for_human_approval": sum(
            case["draft_status"] == "ready_for_human_approval" for case in cases
        ),
        "edit_recommended": sum(
            case["draft_status"] == "edit_recommended" for case in cases
        ),
        "manual_review_required": sum(
            case["draft_status"] == "manual_review_required" for case in cases
        ),
        "approved": approved,
        "on_hold": on_hold,
    }


def copy_draft_values(draft: Mapping[str, Any]) -> dict[str, Any]:
    """Copy only reviewable fields; never copy draft state as human approval."""
    validate_case_draft(draft)
    fields = (
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
    return {field: copy.deepcopy(draft[field]) for field in fields}
