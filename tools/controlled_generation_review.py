"""Raw-free storage helpers for local controlled-generation review."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

SemanticEquivalence = Literal["동일", "경미한 변화", "의미 변경", "판단 어려움"]
PreferredAnswer = Literal["extractive", "controlled", "동률", "판단 어려움"]
Naturalness = Literal["좋음", "보통", "나쁨", "판단 어려움"]
QualityJudgment = Literal["적절", "부분적절", "부적절", "판단 어려움"]
CoverageJudgment = Literal[
    "의미 동일·표현 차이", "실제 의미 누락", "부분 포함", "판단 어려움"
]
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SECRET = re.compile(
    r"(?i)(authorization\s*[:=]|bearer\s+[a-z0-9._-]{12,}|"
    r"\b(?:gsk|sk)-[a-z0-9_-]{12,}|api[_-]?key\s*[:=])"
)
_SOURCE_UNIT_ID = re.compile(r"[A-Za-z0-9._:-]{1,160}")


class ReviewSourceCaseRecord(BaseModel):
    """One local-only comparison case containing display text."""

    model_config = ConfigDict(extra="forbid", strict=True)

    case_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._-]+$")
    prompt_version: str = Field(
        min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._-]+$"
    )
    model: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._-]+$")
    candidate_status: Literal[
        "deterministic_projection",
        "validated_live",
        "extractive_fallback",
        "diagnostic_rejected_live",
    ]
    extractive_answer: str = Field(min_length=1)
    controlled_candidate: str = Field(min_length=1)
    supporting_source_unit_ids: list[list[str]] = Field(min_length=1)
    source_unit_mapping_sha256: str
    extractive_answer_sha256: str
    controlled_candidate_sha256: str

    @field_validator(
        "extractive_answer_sha256",
        "controlled_candidate_sha256",
        "source_unit_mapping_sha256",
    )
    @classmethod
    def validate_answer_sha256(cls, value: str) -> str:
        if _SHA256.fullmatch(value) is None:
            raise ValueError("answer_sha256")
        return value

    @field_validator("supporting_source_unit_ids")
    @classmethod
    def validate_source_unit_ids(cls, value: list[list[str]]) -> list[list[str]]:
        if any(
            not row
            or any(
                not isinstance(identifier, str)
                or _SOURCE_UNIT_ID.fullmatch(identifier) is None
                for identifier in row
            )
            for row in value
        ):
            raise ValueError("source_unit_mapping")
        return value


class ReviewSourceDocument(BaseModel):
    """Closed schema for an ignored local review source file."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    cases: list[ReviewSourceCaseRecord] = Field(min_length=1)


class ReviewRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    case_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._-]+$")
    preferred_answer: PreferredAnswer
    naturalness: Naturalness
    accuracy: QualityJudgment
    completeness: QualityJudgment
    grounding: QualityJudgment
    semantic_equivalence: SemanticEquivalence
    note: str = Field(default="", max_length=500)
    prompt_version: str = Field(
        min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._-]+$"
    )
    model: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._-]+$")
    timestamp: str
    config_sha256: str
    extractive_sha256: str
    candidate_sha256: str

    @field_validator("config_sha256", "extractive_sha256", "candidate_sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if _SHA256.fullmatch(value) is None:
            raise ValueError("sha256")
        return value

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("timestamp") from exc
        if parsed.tzinfo is None:
            raise ValueError("timestamp_timezone")
        return value


class CoverageReviewSourceSlot(BaseModel):
    """Local-only coverage comparison content for one required slot."""

    model_config = ConfigDict(extra="forbid", strict=True)

    case_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._-]+$")
    slot_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._-]+$")
    supporting_source_unit_ids: list[str] = Field(min_length=1)
    source_text: str = Field(min_length=1)
    candidate_statement: str = Field(min_length=1)
    token_coverage: float = Field(ge=0.0, le=1.0)
    source_sha256: str
    candidate_sha256: str

    @field_validator("source_sha256", "candidate_sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if _SHA256.fullmatch(value) is None:
            raise ValueError("sha256")
        return value

    @field_validator("supporting_source_unit_ids")
    @classmethod
    def validate_source_unit_ids(cls, value: list[str]) -> list[str]:
        if any(_SOURCE_UNIT_ID.fullmatch(identifier) is None for identifier in value):
            raise ValueError("source_unit_ids")
        return value


class CoverageReviewSourceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    slots: list[CoverageReviewSourceSlot] = Field(min_length=1)


class CoverageReviewRecord(BaseModel):
    """Raw-free human judgment for one coverage slot."""

    model_config = ConfigDict(extra="forbid", strict=True)

    case_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._-]+$")
    slot_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._-]+$")
    source_sha256: str
    candidate_sha256: str
    judgment: CoverageJudgment
    timestamp: str

    @field_validator("source_sha256", "candidate_sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if _SHA256.fullmatch(value) is None:
            raise ValueError("sha256")
        return value

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("timestamp") from exc
        if parsed.tzinfo is None:
            raise ValueError("timestamp_timezone")
        return value

@dataclass(frozen=True)
class ReviewSession:
    case_id: str
    extractive_text: str
    controlled_text: str
    controlled_citation_ids: tuple[tuple[str, ...], ...]
    prompt_version: str
    config_sha256: str
    extractive_sha256: str
    candidate_sha256: str
    follow_up_enabled: bool = False


@dataclass(frozen=True)
class LoadedReviewCase:
    case_id: str
    question: str
    prompt_version: str
    model: str
    candidate_status: str
    extractive_answer: str
    controlled_candidate: str
    supporting_source_unit_ids: tuple[tuple[str, ...], ...]
    extractive_answer_sha256: str
    controlled_candidate_sha256: str


def candidate_status_notice(status: str) -> str:
    notices = {
        "validated_live": "기존 validator를 통과한 Gemini evaluation candidate입니다.",
        "extractive_fallback": "Gemini candidate가 검증을 통과하지 못해 extractive fallback을 표시합니다.",
        "diagnostic_rejected_live": (
            "최초 실패 진단을 위해 보존한 Gemini candidate입니다. "
            "검증을 통과하지 못했으며 Production에는 게시되지 않습니다."
        ),
        "deterministic_projection": (
            "외부 모델 호출 없이 만든 deterministic SourceUnit projection입니다."
        ),
    }
    try:
        return notices[status]
    except KeyError as exc:
        raise ValueError("candidate_status") from exc


def ordered_review_case_ids(
    catalog: Mapping[str, Any], *, live_only: bool
) -> tuple[str, ...]:
    """Put attempted Gemini cases first and optionally hide offline projections."""
    pilot_order = {
        case_id: index
        for index, case_id in enumerate(
            ("UAT-T01", "UAT-T02", "UAT-S04", "UAT-S08", "UAT-T12", "UAT-T11")
        )
    }
    live_values = [
        case_id
        for case_id, case in catalog.items()
        if case.candidate_status != "deterministic_projection"
    ]
    live_values.sort(key=lambda case_id: (pilot_order.get(case_id, 999), case_id))
    live = tuple(live_values)
    if live_only:
        return live
    offline = tuple(case_id for case_id in catalog if case_id not in set(live))
    return (*live, *offline)


def review_case_label(case: Any) -> str:
    """Return a non-clinical dropdown label that makes candidate origin explicit."""
    if case.candidate_status == "validated_live":
        return f"{case.case_id} · Gemini live · validated"
    if case.candidate_status == "extractive_fallback":
        return f"{case.case_id} · Gemini live · fallback"
    if case.candidate_status == "diagnostic_rejected_live":
        return f"{case.case_id} · Gemini live · diagnostic rejected"
    if case.candidate_status == "deterministic_projection":
        return f"{case.case_id} · deterministic"
    raise ValueError("candidate_status")


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _answer_paragraphs(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in re.split(r"\n\s*\n", value) if part.strip())


def _mapping_sha256(value: Sequence[Sequence[str]]) -> str:
    canonical = json.dumps(
        [list(row) for row in value],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _text_sha256(canonical)


def _validate_coverage_review_slot(record: CoverageReviewSourceSlot) -> None:
    if _text_sha256(record.source_text) != record.source_sha256:
        raise ValueError("source_sha256")
    if _text_sha256(record.candidate_statement) != record.candidate_sha256:
        raise ValueError("candidate_sha256")


def build_coverage_review_slot(
    *,
    case_id: str,
    slot_id: str,
    supporting_source_unit_ids: Sequence[str],
    source_text: str,
    candidate_statement: str,
    token_coverage: float,
) -> CoverageReviewSourceSlot:
    record = CoverageReviewSourceSlot.model_validate(
        {
            "case_id": case_id,
            "slot_id": slot_id,
            "supporting_source_unit_ids": list(supporting_source_unit_ids),
            "source_text": source_text,
            "candidate_statement": candidate_statement,
            "token_coverage": float(token_coverage),
            "source_sha256": _text_sha256(source_text),
            "candidate_sha256": _text_sha256(candidate_statement),
        }
    )
    _validate_coverage_review_slot(record)
    return record


def write_coverage_review_source(
    path: Path,
    slots: Sequence[Mapping[str, Any] | CoverageReviewSourceSlot],
    *,
    allowed_root: Path,
) -> None:
    root = allowed_root.resolve()
    destination = path.resolve()
    if destination == root or not destination.is_relative_to(root):
        raise ValueError("coverage_review_source_path")
    document = CoverageReviewSourceDocument.model_validate(
        {
            "schema_version": 1,
            "slots": [
                value.model_dump(mode="json")
                if isinstance(value, CoverageReviewSourceSlot)
                else dict(value)
                for value in slots
            ],
        }
    )
    keys = [(slot.case_id, slot.slot_id) for slot in document.slots]
    if len(keys) != len(set(keys)):
        raise ValueError("coverage_slot_key")
    for slot in document.slots:
        _validate_coverage_review_slot(slot)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )


def load_coverage_review_source(
    path: Path,
) -> dict[tuple[str, str], CoverageReviewSourceSlot]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("coverage_review_source_json") from exc
    document = CoverageReviewSourceDocument.model_validate(payload)
    catalog: dict[tuple[str, str], CoverageReviewSourceSlot] = {}
    for slot in document.slots:
        _validate_coverage_review_slot(slot)
        key = (slot.case_id, slot.slot_id)
        if key in catalog:
            raise ValueError("coverage_slot_key")
        catalog[key] = slot
    return catalog


def save_coverage_review_record(
    path: Path,
    value: Mapping[str, Any] | CoverageReviewRecord,
) -> CoverageReviewRecord:
    record = (
        value
        if isinstance(value, CoverageReviewRecord)
        else CoverageReviewRecord.model_validate(value)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(record.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(encoded + "\n")
    return record


def _validate_review_source_case(
    record: ReviewSourceCaseRecord,
    *,
    expected_prompt_version: str | None = None,
) -> None:
    if (
        expected_prompt_version is not None
        and record.prompt_version != expected_prompt_version
    ):
        raise ValueError("prompt_version")
    if _text_sha256(record.extractive_answer) != record.extractive_answer_sha256:
        raise ValueError("extractive_answer_sha256")
    if (
        _text_sha256(record.controlled_candidate)
        != record.controlled_candidate_sha256
    ):
        raise ValueError("controlled_candidate_sha256")
    if len(_answer_paragraphs(record.controlled_candidate)) != len(
        record.supporting_source_unit_ids
    ):
        raise ValueError("source_unit_mapping")
    if (
        _mapping_sha256(record.supporting_source_unit_ids)
        != record.source_unit_mapping_sha256
    ):
        raise ValueError("source_unit_mapping")


def _validate_review_source_document(
    document: ReviewSourceDocument,
    *,
    expected_prompt_version: str | None = None,
) -> None:
    case_ids = [record.case_id for record in document.cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("case_id")
    for record in document.cases:
        _validate_review_source_case(
            record,
            expected_prompt_version=expected_prompt_version,
        )


def build_review_source_case(
    *,
    case_id: str,
    prompt_version: str,
    model: str,
    candidate_status: str,
    extractive_answer: str,
    controlled_candidate: str,
    supporting_source_unit_ids: Sequence[Sequence[str]],
) -> ReviewSourceCaseRecord:
    """Build one validated source record without accepting provider envelopes."""
    record = ReviewSourceCaseRecord.model_validate(
        {
            "case_id": case_id,
            "prompt_version": prompt_version,
            "model": model,
            "candidate_status": candidate_status,
            "extractive_answer": extractive_answer,
            "controlled_candidate": controlled_candidate,
            "supporting_source_unit_ids": [
                list(row) for row in supporting_source_unit_ids
            ],
            "source_unit_mapping_sha256": _mapping_sha256(
                supporting_source_unit_ids
            ),
            "extractive_answer_sha256": _text_sha256(extractive_answer),
            "controlled_candidate_sha256": _text_sha256(controlled_candidate),
        }
    )
    _validate_review_source_case(record)
    return record


def write_review_source(
    path: Path,
    cases: Sequence[Mapping[str, Any] | ReviewSourceCaseRecord],
    *,
    allowed_root: Path,
) -> None:
    """Write validated display text only beneath an explicitly ignored local root."""
    root = allowed_root.resolve()
    destination = path.resolve()
    if destination == root or not destination.is_relative_to(root):
        raise ValueError("review_source_path")
    document = ReviewSourceDocument.model_validate(
        {
            "schema_version": 1,
            "cases": [
                value.model_dump(mode="json")
                if isinstance(value, ReviewSourceCaseRecord)
                else dict(value)
                for value in cases
            ],
        }
    )
    _validate_review_source_document(document)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )


def load_review_source(
    path: Path,
    *,
    question_fixture_path: Path,
    expected_prompt_version: str,
) -> dict[str, LoadedReviewCase]:
    """Load local display text and join questions without persisting them to reviews."""
    if not path.is_file():
        raise ValueError("review_source_missing")
    if not question_fixture_path.is_file():
        raise ValueError("question_fixture_missing")
    try:
        source_payload = json.loads(path.read_text(encoding="utf-8"))
        question_payload = json.loads(question_fixture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("review_source_json") from exc
    document = ReviewSourceDocument.model_validate(source_payload)
    _validate_review_source_document(
        document,
        expected_prompt_version=expected_prompt_version,
    )
    if not isinstance(question_payload, dict) or not isinstance(
        question_payload.get("cases"), list
    ):
        raise ValueError("question_fixture")
    questions: dict[str, str] = {}
    for value in question_payload["cases"]:
        if not isinstance(value, dict):
            raise ValueError("question_fixture")
        case_id = value.get("case_id")
        question = value.get("question")
        if (
            not isinstance(case_id, str)
            or not case_id
            or case_id in questions
            or not isinstance(question, str)
            or not question.strip()
        ):
            raise ValueError("question_fixture")
        questions[case_id] = question

    catalog: dict[str, LoadedReviewCase] = {}
    for record in document.cases:
        if record.case_id not in questions:
            raise ValueError("case_id")
        catalog[record.case_id] = LoadedReviewCase(
            case_id=record.case_id,
            question=questions[record.case_id],
            prompt_version=record.prompt_version,
            model=record.model,
            candidate_status=record.candidate_status,
            extractive_answer=record.extractive_answer,
            controlled_candidate=record.controlled_candidate,
            supporting_source_unit_ids=tuple(
                tuple(row) for row in record.supporting_source_unit_ids
            ),
            extractive_answer_sha256=record.extractive_answer_sha256,
            controlled_candidate_sha256=record.controlled_candidate_sha256,
        )
    return catalog


def _validate_review_note(
    note: str, *, forbidden_exact_texts: Sequence[str] = ()
) -> None:
    if _SECRET.search(note):
        raise ValueError("review_note_secret")
    normalized_note = " ".join(note.split())
    forbidden_segments: set[str] = set()
    for text in forbidden_exact_texts:
        for paragraph in re.split(r"\n\s*\n|\r?\n", text):
            normalized_paragraph = " ".join(paragraph.split())
            if normalized_paragraph:
                forbidden_segments.add(normalized_paragraph)
            for sentence in re.findall(r"[^.!?。！？]+[.!?。！？]?", paragraph):
                normalized_sentence = " ".join(sentence.split())
                if normalized_sentence:
                    forbidden_segments.add(normalized_sentence)
    if any(
        len(segment) >= 8 and segment in normalized_note
        for segment in forbidden_segments
    ):
        raise ValueError("review_note_raw_text")


def validate_review_record(value: Mapping[str, Any] | ReviewRecord) -> ReviewRecord:
    """Validate the closed review schema without accepting raw-content fields."""
    if isinstance(value, ReviewRecord):
        return value
    return ReviewRecord.model_validate(value)


def save_review_record(
    path: Path,
    value: Mapping[str, Any] | ReviewRecord,
    *,
    forbidden_exact_texts: Sequence[str] = (),
) -> ReviewRecord:
    """Append one validated raw-free record, creating the file only on save."""
    record = validate_review_record(value)
    _validate_review_note(record.note, forbidden_exact_texts=forbidden_exact_texts)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(record.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(encoded + "\n")
    return record


def build_review_session(
    *,
    case_id: str,
    extractive_text: str,
    controlled_text: str,
    controlled_citation_ids: Sequence[Sequence[str]],
    prompt_version: str,
    config_sha256: str,
    follow_up_enabled: bool,
) -> ReviewSession:
    """Build a local in-memory view model; no answer text is persisted here."""
    if follow_up_enabled:
        raise ValueError("follow_up_disabled")
    if not case_id or not extractive_text.strip() or not controlled_text.strip():
        raise ValueError("review_session_text")
    if _SHA256.fullmatch(config_sha256) is None:
        raise ValueError("config_sha256")
    citations = tuple(tuple(row) for row in controlled_citation_ids)
    if not citations or any(
        not row or any(not value for value in row) for row in citations
    ):
        raise ValueError("controlled_citation_ids")
    return ReviewSession(
        case_id=case_id,
        extractive_text=extractive_text,
        controlled_text=controlled_text,
        controlled_citation_ids=citations,
        prompt_version=prompt_version,
        config_sha256=config_sha256,
        extractive_sha256=_text_sha256(extractive_text),
        candidate_sha256=_text_sha256(controlled_text),
        follow_up_enabled=False,
    )
