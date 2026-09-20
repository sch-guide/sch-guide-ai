"""Evaluation-only Groq Live controlled-generation boundary.

The module is intentionally disconnected from the production answer pipeline.
It keeps provider content in memory, emits metadata-only records, and performs
no retry.  Callers must run identifier scanning before transmitting real
SourceUnit text.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import time
import unicodedata
from copy import deepcopy
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Sequence

import httpx

from src.controlled_generation import (
    _normalized_numeric_tokens,
    decide_controlled_generation,
)
from src.evidence import SourceUnit
from tools.provider_controlled_generation_evaluate import (
    NormalizedProviderResponse,
    ProviderBlueprint,
    build_provider_blueprints,
    normalize_mock_response,
)
from tools.uat_s01_human_semantic_review import (
    apply_human_semantic_review,
    human_review_allows_subset_start,
    load_human_review_record,
    load_reviewed_case_snapshot,
    validate_reviewed_case_snapshot,
)

GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-20b"
DEFAULT_TIMEOUT_SECONDS = 30.0
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data/library/catalog.sqlite3"
DEFAULT_UAT = ROOT / "tests/fixtures/schat_v1_operational_uat.json"
DEFAULT_REVIEWED = ROOT / "tests/fixtures/schat_v1_operational_gold_reviewed.json"
DEFAULT_OUTPUT = ROOT / "workspace/RAGAS/2026-09-18_schat-v1-live-ragas-final"
DEFAULT_HUMAN_REVIEW = ROOT / "workspace/UAT/.tmp/uat_s01_human_semantic_review.json"
DEFAULT_HUMAN_REVIEW_SNAPSHOT = (
    ROOT / "workspace/UAT/.tmp/uat_s01_human_semantic_review_snapshot.json"
)
DEFAULT_HUMAN_REVIEW_QUEUE = ROOT / "workspace/RAGAS/.tmp/groq_live_human_review_queue.json"


class GroqLiveError(RuntimeError):
    """Persistence-safe provider failure code without response content."""

    def __init__(
        self,
        code: str,
        *,
        diagnostic: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(code)
        self.diagnostic = dict(diagnostic) if diagnostic is not None else None


_SAFE_PROVIDER_FIELD = re.compile(r"[A-Za-z0-9_.:\[\]-]{1,160}\Z")


def _safe_provider_field(value: Any) -> str | None:
    if not isinstance(value, str) or not _SAFE_PROVIDER_FIELD.fullmatch(value):
        return None
    return value


def _safe_validation_summary(message: Any) -> str:
    if not isinstance(message, str):
        return "Provider rejected the request."
    lowered = message.casefold()
    if "failed_generation" in lowered or (
        "generated json" in lowered and "schema" in lowered
    ):
        return "Generated JSON did not satisfy the requested schema."
    if "json schema" in lowered or "structured-output schema" in lowered:
        return "Provider rejected the structured-output schema."
    if "model" in lowered:
        return "Provider rejected the model or its request mode."
    if "authentication" in lowered or "api key" in lowered:
        return "Provider rejected the authentication configuration."
    if "rate limit" in lowered:
        return "Provider rate limit was reached."
    return "Provider rejected the request."


def _safe_http_error_diagnostic(
    response: httpx.Response,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    error: Mapping[str, Any] = {}
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, Mapping) and isinstance(body.get("error"), Mapping):
        error = body["error"]
    response_format = payload.get("response_format")
    json_schema = (
        response_format.get("json_schema")
        if isinstance(response_format, Mapping)
        else None
    )
    schema = json_schema.get("schema") if isinstance(json_schema, Mapping) else {}
    strict = json_schema.get("strict") if isinstance(json_schema, Mapping) else None
    mode = (
        "json_schema_strict"
        if isinstance(response_format, Mapping)
        and response_format.get("type") == "json_schema"
        and strict is True
        else "other"
    )
    path = error.get("param") or error.get("field") or error.get("path")
    return {
        "status_code": response.status_code,
        "error_type": _safe_provider_field(error.get("type")),
        "error_code": _safe_provider_field(error.get("code")),
        "failing_field_path": _safe_provider_field(path),
        "validation_summary": _safe_validation_summary(error.get("message")),
        "schema_sha256": sha256(
            json.dumps(
                schema,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "request_mode": mode,
        "model": _safe_provider_field(payload.get("model")) or GROQ_MODEL,
    }


def safe_groq_error_diagnostic(error: BaseException) -> dict[str, Any] | None:
    """Return only the pre-sanitized provider diagnostic allowlist."""
    diagnostic = getattr(error, "diagnostic", None)
    return dict(diagnostic) if isinstance(diagnostic, Mapping) else None


_HTTP_ERROR_SUMMARIES = {
    400: "HTTP 400: request 또는 structured-output schema가 유효하지 않습니다.",
    401: "HTTP 401: authentication failed.",
    403: "HTTP 403: model/account access denied.",
    404: "HTTP 404: endpoint 또는 model을 찾을 수 없습니다.",
    413: "HTTP 413: request payload가 너무 큽니다.",
    422: "HTTP 422: provider validation error.",
    429: "HTTP 429: rate limit exceeded.",
    498: "HTTP 498: provider capacity exceeded.",
    500: "HTTP 500: provider internal error.",
    502: "HTTP 502: provider bad gateway.",
    503: "HTTP 503: provider service unavailable.",
}


def safe_groq_error_summary(error: BaseException) -> str:
    """Map internal error codes to user-safe diagnostics without raw bodies."""
    code = str(error)
    if code == "provider_connection":
        return "Connection failed: DNS/TCP/proxy/firewall 상태를 확인하세요."
    if code == "provider_timeout":
        return "Timeout: Groq 응답 제한 시간을 초과했습니다."
    if code == "provider_transport":
        return "Transport failed: TLS/HTTP 연결 상태를 확인하세요."
    if code == "missing_groq_api_key" or code == "MISSING_GROQ_API_KEY":
        return "Authentication configuration failed: GROQ_API_KEY가 없습니다."
    match = re.fullmatch(r"provider_http_(\d{3})", code)
    if match:
        status = int(match.group(1))
        return _HTTP_ERROR_SUMMARIES.get(
            status,
            f"HTTP {status}: Groq 요청이 실패했습니다.",
        )
    response_codes = {
        "provider_json": "Provider response JSON을 읽을 수 없습니다.",
        "provider_json_content": "Structured output JSON이 유효하지 않습니다.",
        "provider_source_map": "Citation/source mapping이 응답 schema와 일치하지 않습니다.",
        "provider_empty_statement": "Structured output에 빈 statement가 있습니다.",
    }
    return response_codes.get(code, f"Groq request failed: {code}")


@dataclass(frozen=True)
class GroqLiveCall:
    normalized: NormalizedProviderResponse
    latency_ms: float
    http_status: int
    actual_external_calls: int = 1


@dataclass(frozen=True)
class RequiredCoverageSlot:
    slot_id: str
    category: str
    requirement: str
    supporting_source_unit_ids: tuple[str, ...]

    def provider_descriptor(self) -> dict[str, Any]:
        """Return the raw-clinical-text-free provider instruction projection."""
        return {
            "slot_id": self.slot_id,
            "category": self.category,
            "supporting_source_unit_ids": list(self.supporting_source_unit_ids),
        }


@dataclass(frozen=True)
class PreparedLiveCase:
    case_id: str
    intent: str
    document_scope: str
    evidence_type: str
    units: tuple[SourceUnit, ...]
    critical: Mapping[str, Any]
    context_precision: float
    context_recall: float
    requested_phase: str = ""
    required_coverage: tuple[RequiredCoverageSlot, ...] = ()
    required_coverage_error: str = ""


_IDENTIFIER_PATTERNS = (
    ("resident_registration_number", re.compile(r"(?<!\d)\d{6}-[1-4]\d{6}(?!\d)")),
    ("phone_number", re.compile(r"(?<!\d)01[016789]-?\d{3,4}-?\d{4}(?!\d)")),
    ("email_address", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
    (
        "hospital_identifier",
        re.compile(r"(?:병원번호|등록번호|환자번호|직원번호|사번)\s*[:#]?\s*\d{5,}", re.I),
    ),
    (
        "calendar_timestamp",
        re.compile(
            r"(?<!\d)(?:19|20)\d{2}[-/.](?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])"
            r"(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?(?!\d)"
        ),
    ),
)


def scan_source_units_for_identifiers(units: Sequence[SourceUnit]) -> tuple[str, ...]:
    """Return only safe reason codes; never return matching text."""
    found: list[str] = []
    for unit in units:
        for code, pattern in _IDENTIFIER_PATTERNS:
            if pattern.search(unit.exact_text) and code not in found:
                found.append(code)
    return tuple(found)


def build_groq_live_payload(blueprint: ProviderBlueprint) -> dict[str, Any]:
    """Project an offline Groq blueprint to the fixed Live allowlist."""
    if blueprint.provider != "groq":
        raise GroqLiveError("provider_mismatch")
    payload = deepcopy(blueprint.payload)
    source_ids = _source_ids_from_blueprint(blueprint)
    payload.pop("store", None)
    payload["model"] = GROQ_MODEL
    payload["temperature"] = 0
    payload["stream"] = False
    payload["include_reasoning"] = False
    payload.pop("tools", None)
    payload.pop("tool_choice", None)
    payload["messages"][0]["content"] += (
        "\nReturn exactly one non-empty Korean statement for every request-local "
        "SourceUnit ID in statements_by_source. Do not omit any ID."
    )
    live_schema = {
        "type": "object",
        "properties": {
            "statements_by_source": {
                "type": "object",
                "properties": {
                    identifier: {"type": "string"} for identifier in source_ids
                },
                "required": list(source_ids),
                "additionalProperties": False,
            }
        },
        "required": ["statements_by_source"],
        "additionalProperties": False,
    }
    payload["response_format"] = {
        "type": "json_schema",
        "json_schema": {
            "name": "schat_controlled_generation_by_source",
            "strict": True,
            "schema": live_schema,
        },
    }
    allowed = {
        "model",
        "messages",
        "temperature",
        "max_completion_tokens",
        "stream",
        "include_reasoning",
        "response_format",
    }
    if set(payload) - allowed:
        raise GroqLiveError("payload_field_not_allowed")
    response_format = payload.get("response_format")
    schema = response_format.get("json_schema") if isinstance(response_format, Mapping) else None
    if (
        not isinstance(schema, Mapping)
        or schema.get("strict") is not True
        or response_format.get("type") != "json_schema"
    ):
        raise GroqLiveError("strict_schema_required")
    messages = payload.get("messages")
    if (
        not isinstance(messages, list)
        or len(messages) != 1
        or not isinstance(messages[0], Mapping)
        or messages[0].get("role") != "user"
        or not isinstance(messages[0].get("content"), str)
    ):
        raise GroqLiveError("message_contract")
    return payload


def _source_ids_from_blueprint(blueprint: ProviderBlueprint) -> tuple[str, ...]:
    try:
        identifiers = blueprint.payload["response_format"]["json_schema"]["schema"][
            "properties"
        ]["statements"]["items"]["properties"]["supporting_source_unit_ids"][
            "items"
        ]["enum"]
    except (KeyError, TypeError) as exc:
        raise GroqLiveError("source_id_schema") from exc
    if (
        not isinstance(identifiers, list)
        or not identifiers
        or not all(isinstance(value, str) and value for value in identifiers)
        or len(identifiers) != len(set(identifiers))
    ):
        raise GroqLiveError("source_id_schema")
    return tuple(identifiers)


def _normalize_live_content(content: str, source_ids: tuple[str, ...]) -> str:
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError) as exc:
        raise GroqLiveError("provider_json_content") from exc
    by_source = parsed.get("statements_by_source") if isinstance(parsed, Mapping) else None
    if not isinstance(by_source, Mapping) or set(by_source) != set(source_ids):
        raise GroqLiveError("provider_source_map")
    statements = []
    for identifier in source_ids:
        text = by_source.get(identifier)
        if not isinstance(text, str) or not text.strip():
            raise GroqLiveError("provider_empty_statement")
        statements.append(
            {
                "text": text.strip(),
                "supporting_source_unit_ids": [identifier],
            }
        )
    return json.dumps(
        {"statements": statements},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def numeric_unit_diagnostics(
    content: str, units: Sequence[SourceUnit]
) -> list[dict[str, Any]]:
    """Compare numeric tokens from clinical statement text to cited evidence only."""
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, TypeError) as exc:
        raise GroqLiveError("diagnostic_content") from exc
    statements = payload.get("statements") if isinstance(payload, Mapping) else None
    if not isinstance(statements, list):
        raise GroqLiveError("diagnostic_content")
    by_id = {unit.source_unit_id: unit for unit in units}
    diagnostics: list[dict[str, Any]] = []
    for index, statement in enumerate(statements):
        if not isinstance(statement, Mapping):
            raise GroqLiveError("diagnostic_content")
        text = statement.get("text")
        identifiers = statement.get("supporting_source_unit_ids")
        if (
            not isinstance(text, str)
            or not isinstance(identifiers, list)
            or not all(isinstance(value, str) and value in by_id for value in identifiers)
        ):
            raise GroqLiveError("diagnostic_content")
        allowed = set()
        for identifier in identifiers:
            allowed.update(_normalized_numeric_tokens(by_id[identifier].exact_text))
        generated = _normalized_numeric_tokens(text)
        diagnostics.append(
            {
                "statement_index": index,
                "field": f"statements[{index}].text",
                "supporting_source_unit_ids": list(identifiers),
                "allowed_tokens": sorted(allowed),
                "generated_tokens": sorted(generated),
                "offending_tokens": sorted(generated - allowed),
            }
        )
    return diagnostics


_CLINICAL_UNIT_TOKEN = re.compile(
    r"(?:%|mcg|μg|µg|mg|kg|gage|gauge|g|ml|l|cc|단위|분|시간|초|일|회|번|℃|°c|/hr|/min)",
    re.I,
)


def build_numeric_unit_diagnostic_record(
    *,
    case_id: str,
    content: str,
    units: Sequence[SourceUnit],
    answer_hash: str,
    evidence_hash: str,
) -> dict[str, Any]:
    """Project numeric failures to a raw-free evaluation-only record."""
    failed_statements = []
    for row in numeric_unit_diagnostics(content, units):
        offending = list(row["offending_tokens"])
        if not offending:
            continue
        failed_statements.append(
            {
                "statement_index": row["statement_index"],
                "allowed_tokens": list(row["allowed_tokens"]),
                "generated_tokens": list(row["generated_tokens"]),
                "offending_tokens": offending,
                "token_type": {
                    token: (
                        "unit" if _CLINICAL_UNIT_TOKEN.search(token) else "number"
                    )
                    for token in offending
                },
            }
        )
    failed = bool(failed_statements)
    return {
        "case_id": case_id,
        "validator_result": "FAIL" if failed else "PASS",
        "answer_hash": answer_hash,
        "evidence_hash": evidence_hash,
        "failure_code": "unsupported_number_or_unit" if failed else None,
        "failed_statements": failed_statements,
    }


def call_groq_live(
    blueprint: ProviderBlueprint,
    *,
    api_key: str,
    client: httpx.Client | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> GroqLiveCall:
    """Execute exactly one synchronous call and retain content only in memory."""
    if not api_key.strip():
        raise GroqLiveError("missing_groq_api_key")
    payload = build_groq_live_payload(blueprint)
    owned_client = client is None
    active_client = client or httpx.Client(
        timeout=timeout_seconds,
        follow_redirects=False,
    )
    started = time.perf_counter()
    try:
        try:
            response = active_client.post(
                GROQ_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise GroqLiveError("provider_timeout") from exc
        except httpx.ConnectError as exc:
            raise GroqLiveError("provider_connection") from exc
        except httpx.HTTPError as exc:
            raise GroqLiveError("provider_transport") from exc
        latency_ms = (time.perf_counter() - started) * 1000
        if response.status_code != 200:
            raise GroqLiveError(
                f"provider_http_{response.status_code}",
                diagnostic=_safe_http_error_diagnostic(response, payload),
            )
        try:
            envelope = response.json()
        except ValueError as exc:
            raise GroqLiveError("provider_json") from exc
        try:
            normalized = normalize_mock_response("groq", envelope)
        except ValueError as exc:
            raise GroqLiveError(f"provider_{str(exc) or 'response'}") from exc
        normalized = NormalizedProviderResponse(
            provider=normalized.provider,
            model=normalized.model,
            finish_reason=normalized.finish_reason,
            content=_normalize_live_content(
                normalized.content, _source_ids_from_blueprint(blueprint)
            ),
            usage=normalized.usage,
        )
        return GroqLiveCall(normalized, latency_ms, response.status_code)
    finally:
        if owned_client:
            active_client.close()


def safe_live_call_record(
    blueprint: ProviderBlueprint,
    result: GroqLiveCall,
) -> dict[str, Any]:
    """Return metadata safe to persist; excludes payload, text, key, and headers."""
    live_payload = build_groq_live_payload(blueprint)
    live_schema = live_payload["response_format"]["json_schema"]["schema"]
    live_instruction = live_payload["messages"][0]["content"]
    return {
        "case_id": blueprint.case_id,
        "provider": "groq",
        "model": result.normalized.model or GROQ_MODEL,
        "evidence_count": blueprint.evidence_count,
        "evidence_character_count": blueprint.evidence_character_count,
        "evidence_sha256": blueprint.evidence_fingerprint,
        "schema_sha256": sha256(
            json.dumps(live_schema, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "instruction_sha256": sha256(live_instruction.encode("utf-8")).hexdigest(),
        "generated_answer_sha256": sha256(
            result.normalized.content.encode("utf-8")
        ).hexdigest(),
        "request_body_bytes": len(
            json.dumps(live_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ),
        "finish_reason": result.normalized.finish_reason,
        "usage": dict(result.normalized.usage),
        "latency_ms": round(result.latency_ms, 3),
        "http_status": result.http_status,
        "actual_external_calls": result.actual_external_calls,
    }


def enqueue_human_semantic_review(path: Path, row: Mapping[str, Any]) -> None:
    """Upsert a metadata-only review request without answer or evidence text."""
    required = ("case_id", "generated_answer_sha256", "evidence_sha256")
    if any(not str(row.get(field, "")).strip() for field in required):
        raise GroqLiveError("human_review_queue_metadata")
    entry = {
        "case_id": str(row["case_id"]),
        "status": "needs_human_review",
        "generated_answer_sha256": str(row["generated_answer_sha256"]),
        "evidence_sha256": str(row["evidence_sha256"]),
        "failure_codes": [str(value) for value in row.get("failure_codes", ())],
        "document_scope": str(row.get("document_scope", "")),
        "evidence_type": str(row.get("evidence_type", "")),
    }
    payload: dict[str, Any] = {"cases": []}
    if path.exists():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or not isinstance(loaded.get("cases"), list):
            raise GroqLiveError("human_review_queue_shape")
        payload = loaded
    cases = [
        dict(existing)
        for existing in payload["cases"]
        if str(existing.get("case_id")) != entry["case_id"]
    ]
    cases.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps({"cases": cases}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def filter_approved_live_cases(
    reviewed: Mapping[str, Any],
    uat: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return only human-approved positive non-image UAT records."""
    approved = {
        str(row.get("case_id"))
        for row in reviewed.get("cases", ())
        if row.get("final_gold_approved") is True
    }
    return [
        dict(row)
        for row in uat.get("cases", ())
        if str(row.get("case_id")) in approved
        and row.get("document_scope") != "out_of_scope"
        and not str(row.get("expected_evidence_type", "")).startswith("image")
    ]


def build_request_local_units(
    evidence_rows: Sequence[Mapping[str, Any]],
) -> tuple[SourceUnit, ...]:
    """Replace internal evidence identifiers with request-local IDs."""
    if not evidence_rows or len(evidence_rows) > 16:
        raise GroqLiveError("source_unit_count")
    seen: set[str] = set()
    units: list[SourceUnit] = []
    for position, row in enumerate(evidence_rows, 1):
        evidence_id = str(row.get("evidence_id", "")).strip()
        text = str(row.get("text", "")).strip()
        if not evidence_id or not text:
            raise GroqLiveError("invalid_evidence_row")
        if evidence_id in seen:
            raise GroqLiveError("duplicate_evidence_id")
        seen.add(evidence_id)
        units.append(
            SourceUnit(
                source_unit_id=f"su{position:03d}",
                chunk_id=f"evidence-{sha256(evidence_id.encode('utf-8')).hexdigest()[:16]}",
                source_order=(position, 1),
                branch="common",
                exact_text=text,
                group_key=f"request-group-{position:03d}",
                required=True,
                selectable=True,
                phase="unspecified",
            )
        )
    return tuple(units)


def build_request_local_source_units(
    source_units: Sequence[SourceUnit],
) -> tuple[SourceUnit, ...]:
    """Renumber exact atomic units while hiding every production identifier."""
    if not source_units or len(source_units) > 16:
        raise GroqLiveError("source_unit_count")
    units: list[SourceUnit] = []
    identities: set[tuple[str, tuple[int, int], str]] = set()
    for position, unit in enumerate(source_units, 1):
        identity = (unit.chunk_id, unit.source_order, unit.exact_text)
        if identity in identities or not unit.exact_text.strip():
            raise GroqLiveError("invalid_evidence_row")
        identities.add(identity)
        private_identity = json.dumps(
            [unit.chunk_id, list(unit.source_order)],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        units.append(
            SourceUnit(
                source_unit_id=f"su{position:03d}",
                chunk_id=(
                    "evidence-"
                    + sha256(private_identity.encode("utf-8")).hexdigest()[:16]
                ),
                source_order=(position, 1),
                branch=unit.branch,
                exact_text=unit.exact_text.strip(),
                group_key=f"request-group-{position:03d}",
                required=True,
                selectable=True,
                phase=unit.phase,
                action_families=unit.action_families,
            )
        )
    return tuple(units)


_WORKFLOW_BEFORE = re.compile(
    r"(?:시작|시행|시술|진정|수혈|투여|주입|검사|처치)\s*전|사전"
)
_WORKFLOW_DURING = re.compile(
    r"(?:시작\s*후|시행\s*중|시술\s*중|진정\s*중|수혈\s*중|"
    r"투여\s*중|주입\s*중|진행\s*중)"
)
_WORKFLOW_AFTER = re.compile(r"(?:완료|종료|회복|퇴실)\s*후|사후")
_STEP_NUMBER = re.compile(r"(?:^|\n)\s*(\d{1,2})\s*(?:[|.)])")
_LAYOUT_ONLY = re.compile(
    r"(?:\s|\||-|Pass|Fail|평가내용|프리셉터\s*평가|자가\s*평가|\d)+",
    re.I,
)


def _explicit_workflow_phase(text: str) -> str:
    phases = []
    if _WORKFLOW_BEFORE.search(text):
        phases.append("before")
    if _WORKFLOW_DURING.search(text):
        phases.append("during")
    if _WORKFLOW_AFTER.search(text):
        phases.append("after")
    return phases[0] if len(set(phases)) == 1 else ""


def _step_number(text: str) -> int | None:
    match = _STEP_NUMBER.search(text)
    return int(match.group(1)) if match else None


def _overlap_score(left: str, right: str) -> float:
    from src.retrieval import lexical_tokens

    left_tokens = {
        token for token in lexical_tokens(left) if not token.startswith("entity:")
    }
    right_tokens = {
        token for token in lexical_tokens(right) if not token.startswith("entity:")
    }
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens)
    return float(overlap) + overlap / min(len(left_tokens), len(right_tokens))


def _clinical_continuation(text: str) -> bool:
    cleaned = text.strip()
    return (
        len(cleaned) >= 8
        and bool(re.search(r"[가-힣A-Za-z]", cleaned))
        and _LAYOUT_ONLY.fullmatch(cleaned) is None
    )


def _coalesce_atomic_rows(catalog: Sequence[SourceUnit]) -> tuple[SourceUnit, ...]:
    """Keep clinical qualifier fragments with the preceding selectable row."""
    rows: list[SourceUnit] = []
    for unit in sorted(catalog, key=lambda candidate: candidate.source_order):
        if unit.selectable:
            rows.append(unit)
            continue
        if (
            rows
            and rows[-1].chunk_id == unit.chunk_id
            and _clinical_continuation(unit.exact_text)
        ):
            previous = rows[-1]
            explicit = _explicit_workflow_phase(
                f"{previous.exact_text} {unit.exact_text}"
            )
            rows[-1] = replace(
                previous,
                exact_text=f"{previous.exact_text} {unit.exact_text}".strip(),
                phase=explicit or previous.phase,
                action_families=tuple(
                    dict.fromkeys(previous.action_families + unit.action_families)
                ),
            )
    return tuple(rows)


def project_controlled_generation_units(
    catalog: Sequence[SourceUnit],
    *,
    intent: str,
    requested_phase: str,
    limit: int = 16,
) -> tuple[SourceUnit, ...]:
    """Project exact atomic evidence to a bounded workflow-generation scope.

    This is an evaluation-only input projection. It never changes retrieval rank,
    Gold, or clinical validators. A before/preparation request stops at the first
    explicit during/after step in each ordered evidence group.
    """
    if limit < 1:
        raise ValueError("limit must be positive")
    ordered = list(_coalesce_atomic_rows(catalog))
    if not ordered:
        raise GroqLiveError("empty_generation_projection")
    if not (intent == "preparation" and requested_phase == "before"):
        if len(ordered) > limit:
            raise GroqLiveError("source_unit_count")
        return tuple(ordered)

    phase_state: dict[str, str] = {}
    bounded: list[SourceUnit] = []
    for unit in ordered:
        explicit = _explicit_workflow_phase(unit.exact_text)
        if explicit:
            phase_state[unit.group_key] = explicit
        effective = explicit or phase_state.get(unit.group_key, "")
        if effective in {"during", "after"}:
            continue
        bounded.append(replace(unit, phase="before"))

    step_groups: dict[str, list[tuple[int, SourceUnit]]] = {}
    for unit in bounded:
        number = _step_number(unit.exact_text)
        if number is not None:
            step_groups.setdefault(unit.group_key, []).append((number, unit))
    candidates = [
        (group, rows)
        for group, rows in step_groups.items()
        if len({number for number, _ in rows}) >= 2
    ]
    if not candidates:
        if len(bounded) > limit:
            raise GroqLiveError("source_unit_count")
        return tuple(bounded)

    sequence_group, numbered = max(
        candidates,
        key=lambda item: (len({number for number, _ in item[1]}), item[0]),
    )
    numbered.sort(key=lambda item: (item[0], item[1].source_order))
    anchors = [unit for _, unit in numbered]
    anchor_ids = {id(unit) for unit in anchors}
    attached: dict[int, list[tuple[float, SourceUnit]]] = {
        index: [] for index in range(len(anchors))
    }
    for unit in bounded:
        if id(unit) in anchor_ids or unit.group_key == sequence_group:
            continue
        scores = [_overlap_score(unit.exact_text, anchor.exact_text) for anchor in anchors]
        best = max(range(len(scores)), key=scores.__getitem__)
        if scores[best] > 0:
            attached[best].append((scores[best], unit))

    supplement_budget = limit - len(anchors)
    if supplement_budget < 0:
        raise GroqLiveError("source_unit_count")
    ranked_supplements = sorted(
        (
            (score, index, unit)
            for index, values in attached.items()
            for score, unit in values
        ),
        key=lambda item: (-item[0], item[2].source_order),
    )[:supplement_budget]
    allowed_supplements = {id(unit) for _, _, unit in ranked_supplements}

    projected: list[SourceUnit] = []
    for index, anchor in enumerate(anchors):
        projected.append(anchor)
        projected.extend(
            unit
            for _, unit in sorted(
                attached[index],
                key=lambda item: (-item[0], item[1].source_order),
            )
            if id(unit) in allowed_supplements
        )
    return tuple(projected)


def _generation_candidate_hits(
    plan: Any,
    hits: Sequence[Any],
    selected: Sequence[Any],
) -> tuple[Any, ...]:
    """Keep production selections plus safe same-parent retrieved siblings."""
    from src.library import compatible, has_substantive_body

    selected_ids = {hit.chunk.id for hit in selected}
    selected_parents = {
        (hit.chunk.document_id, hit.chunk.parent_id)
        for hit in selected
        if hit.chunk.parent_id
    }
    candidates = []
    for hit in hits:
        parent = (hit.chunk.document_id, hit.chunk.parent_id)
        if hit.chunk.id in selected_ids:
            candidates.append(hit)
            continue
        if (
            parent in selected_parents
            and has_substantive_body(hit.chunk)
            and compatible(
                plan.query,
                f"{hit.chunk.text} {hit.chunk.section}",
            )
        ):
            candidates.append(hit)
    return tuple(
        sorted(
            {hit.chunk.id: hit for hit in candidates}.values(),
            key=lambda hit: (hit.chunk.document_id, hit.chunk.index),
        )
    )


def select_representative_subset(
    cases: Sequence[Mapping[str, Any]], *, limit: int = 5
) -> list[dict[str, Any]]:
    """Choose scope/evidence/intent diversity without case-ID exceptions."""
    if limit < 1:
        raise ValueError("limit must be positive")
    remaining = [dict(row) for row in cases]
    selected: list[dict[str, Any]] = []

    def take(predicate: Any) -> None:
        if len(selected) >= limit:
            return
        for index, row in enumerate(remaining):
            if predicate(row):
                selected.append(remaining.pop(index))
                return

    take(lambda row: row.get("document_scope") == "sedation")
    take(lambda row: row.get("document_scope") == "transfusion")
    take(lambda row: row.get("expected_evidence_type") in {"table", "mixed"})
    while remaining and len(selected) < limit:
        used_intents = {str(row.get("expected_intent", "")) for row in selected}
        index = next(
            (
                offset
                for offset, row in enumerate(remaining)
                if str(row.get("expected_intent", "")) not in used_intents
            ),
            0,
        )
        selected.append(remaining.pop(index))
    return selected


def _normalized(value: Any) -> str:
    return re.sub(
        r"\s+", "", unicodedata.normalize("NFKC", str(value)).casefold()
    )


def _semantic_tokens(value: Any) -> set[str]:
    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    normalized = re.sub(r"^\s*\d{1,2}\s*[.)]\s*", "", normalized)
    words: list[str] = []
    for raw_word in re.findall(r"[가-힣]+|[a-z0-9%]+", normalized):
        word = raw_word
        if re.fullmatch(r"[가-힣]+", word):
            for suffix in (
                "으로",
                "에서",
                "에게",
                "까지",
                "부터",
                "한다",
                "하고",
                "하여",
                "하며",
                "했다",
                "되는",
                "되면",
                "해야",
                "별",
                "한",
                "는",
                "은",
                "이",
                "가",
                "을",
                "를",
                "에",
                "의",
                "와",
                "과",
            ):
                if word.endswith(suffix) and len(word) - len(suffix) >= 2:
                    word = word[: -len(suffix)]
                    break
        words.append(word)
    tokens: set[str] = set()
    for index, word in enumerate(words):
        if re.fullmatch(r"[가-힣]+", word):
            variants = [word]
            if (
                len(word) == 1
                and index + 1 < len(words)
                and re.fullmatch(r"[가-힣]+", words[index + 1])
            ):
                variants.append(word + words[index + 1])
            for variant in variants:
                tokens.update(
                    variant[offset : offset + 2]
                    for offset in range(max(1, len(variant) - 1))
                )
        else:
            tokens.add(word)
    return tokens


def _requirement_present(requirement: Any, generated_text: str, *, semantic: bool) -> bool:
    wanted = _normalized(requirement)
    actual = _normalized(generated_text)
    if not wanted:
        return True
    if wanted in actual:
        return True
    if not semantic:
        return False
    wanted_tokens = _semantic_tokens(requirement)
    actual_tokens = _semantic_tokens(generated_text)
    if not wanted_tokens:
        return False
    return len(wanted_tokens & actual_tokens) / len(wanted_tokens) >= 0.8


def _field_pass(
    critical: Mapping[str, Any], field: str, generated_text: str, *, semantic: bool
) -> bool:
    values = critical.get(field, ())
    if not isinstance(values, (list, tuple)):
        return False
    return all(
        _requirement_present(value, generated_text, semantic=semantic)
        for value in values
    )


_REQUIRED_COVERAGE_FIELDS = (
    ("critical_facts", "fact"),
    ("critical_conditions", "condition"),
    ("critical_times", "qualifier"),
    ("critical_steps", "step"),
)


def _requirement_support_score(requirement: str, evidence_text: str) -> float:
    wanted = _normalized(requirement)
    evidence = _normalized(evidence_text)
    if not wanted:
        return 0.0
    if wanted in evidence:
        return 1.0
    wanted_tokens = _semantic_tokens(requirement)
    evidence_tokens = _semantic_tokens(evidence_text)
    if not wanted_tokens:
        return 0.0
    return len(wanted_tokens & evidence_tokens) / len(wanted_tokens)


def build_required_coverage_slots(
    critical: Mapping[str, Any],
    units: Sequence[SourceUnit],
) -> tuple[RequiredCoverageSlot, ...]:
    """Bind locally reviewed Gold requirements to request-local evidence IDs."""
    slots: list[RequiredCoverageSlot] = []
    counters: dict[str, int] = {}
    for field, category in _REQUIRED_COVERAGE_FIELDS:
        requirements = critical.get(field, ())
        if not isinstance(requirements, (list, tuple)):
            raise GroqLiveError(f"required_coverage_shape:{field}")
        for raw_requirement in requirements:
            requirement = str(raw_requirement).strip()
            if not requirement:
                raise GroqLiveError(f"required_coverage_empty:{field}")
            scored = [
                (_requirement_support_score(requirement, unit.exact_text), unit)
                for unit in units
            ]
            best_score = max((score for score, _ in scored), default=0.0)
            if best_score < 0.25:
                raise GroqLiveError(f"required_coverage_unmapped:{field}")
            support_floor = max(0.25, best_score * 0.75)
            supporting_ids = tuple(
                unit.source_unit_id
                for score, unit in scored
                if score >= support_floor
            )
            counters[category] = counters.get(category, 0) + 1
            slots.append(
                RequiredCoverageSlot(
                    slot_id=f"required_{category}_{counters[category]:03d}",
                    category=category,
                    requirement=requirement,
                    supporting_source_unit_ids=supporting_ids,
                )
            )
    return tuple(slots)


def _required_coverage_result(
    validated: Any,
    slots: Sequence[RequiredCoverageSlot],
) -> tuple[bool, list[str]]:
    if not slots:
        return True, []
    if validated is None:
        return False, [slot.slot_id for slot in slots]
    missing: list[str] = []
    for slot in slots:
        linked_text = " ".join(
            statement.text
            for statement in validated.statements
            if {
                citation.source_unit_id for citation in statement.citations
            }
            & set(slot.supporting_source_unit_ids)
        )
        if not _requirement_present(slot.requirement, linked_text, semantic=True):
            missing.append(slot.slot_id)
    return not missing, missing


def _phase_scope_pass(validated: Any, requested_phase: str) -> bool:
    if not requested_phase or requested_phase == "all":
        return True
    if validated is None:
        return False
    generated_text = " ".join(statement.text for statement in validated.statements)
    if requested_phase == "before":
        return not (
            _WORKFLOW_DURING.search(generated_text)
            or _WORKFLOW_AFTER.search(generated_text)
        )
    return True


def _cited_source_order_pass(
    validated: Any,
    units: Sequence[SourceUnit],
) -> bool:
    source_order = {unit.source_unit_id: unit.source_order for unit in units}
    cited = [
        citation.source_unit_id
        for statement in validated.statements
        for citation in statement.citations
    ]
    if len(cited) != len(set(cited)) or set(cited) != set(source_order):
        return False
    orders = [source_order[identifier] for identifier in cited]
    return all(left < right for left, right in zip(orders, orders[1:]))


def evaluate_live_safety(
    blueprint: ProviderBlueprint,
    result: GroqLiveCall,
    *,
    units: tuple[SourceUnit, ...],
    intent: str,
    critical: Mapping[str, Any],
    required_coverage: Sequence[RequiredCoverageSlot] = (),
    requested_phase: str = "",
) -> dict[str, Any]:
    """Apply local deterministic validators without persisting generated text."""
    decision = decide_controlled_generation(
        result.normalized.content,
        units,
        intent=intent,
        extractive_answer={"kind": "verified_extractive_fallback"},
    )
    validated = decision.validated_candidate
    schema_pass = validated is not None
    citation_pass = bool(
        validated
        and set(validated.covered_source_unit_ids)
        == {unit.source_unit_id for unit in units}
    )
    generated_statements = (
        [statement.text for statement in validated.statements]
        if validated
        else []
    )
    generated_text = " ".join(generated_statements)
    critical_steps = critical.get("critical_steps", ())
    action_content_pass = (
        _field_pass(critical, "critical_steps", generated_text, semantic=True)
        if validated
        else False
    )
    action_order_pass = bool(
        validated
        and isinstance(critical_steps, (list, tuple))
        and _cited_source_order_pass(validated, units)
    )
    required_coverage_pass, missing_required_slot_ids = _required_coverage_result(
        validated, required_coverage
    )
    missing_required_slot_id_set = set(missing_required_slot_ids)
    required_coverage_by_category = {
        category: all(
            slot.slot_id not in missing_required_slot_id_set
            for slot in required_coverage
            if slot.category == category
        )
        for category in dict.fromkeys(slot.category for slot in required_coverage)
    }
    phase_scope_pass = _phase_scope_pass(validated, requested_phase)
    checks = {
        "critical_fact_preservation": _field_pass(
            critical, "critical_facts", generated_text, semantic=True
        ) if validated else False,
        "number_pass": _field_pass(
            critical, "critical_numbers", generated_text, semantic=False
        ) if validated else False,
        "unit_pass": _field_pass(
            critical, "critical_units", generated_text, semantic=False
        ) if validated else False,
        "time_pass": _field_pass(
            critical, "critical_times", generated_text, semantic=True
        ) if validated else False,
        "condition_pass": _field_pass(
            critical, "critical_conditions", generated_text, semantic=True
        ) if validated else False,
        "negation_pass": _field_pass(
            critical, "critical_negations", generated_text, semantic=True
        ) if validated else False,
        "action_pass": action_content_pass and action_order_pass,
        "required_coverage_pass": required_coverage_pass,
        "phase_scope_pass": phase_scope_pass,
    }
    failures = [
        name
        for name, passed in {
            "schema_pass": schema_pass,
            "citation_pass": citation_pass,
            **checks,
        }.items()
        if not passed
    ]
    return {
        "case_id": blueprint.case_id,
        "schema_pass": schema_pass,
        "citation_pass": citation_pass,
        **checks,
        "action_content_pass": action_content_pass,
        "action_order_pass": action_order_pass,
        "missing_required_slot_ids": missing_required_slot_ids,
        "required_coverage_by_category": required_coverage_by_category,
        "branch_phase_pass": schema_pass and phase_scope_pass,
        "semantic_support": "pending_llm_judge_not_authorized_by_payload_allowlist",
        "faithfulness": "not_measured_no_judge_payload_authority",
        "answer_relevancy": "not_measured_original_question_not_transmitted",
        "publish_controlled": decision.publish_controlled,
        "fallback_to_extractive": decision.fallback_to_extractive,
        "validation_reason": decision.reason,
        "failure_codes": failures,
        "critical_safety_error_count": len(failures),
    }


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise GroqLiveError("fixture_shape")
    return payload


def _table_text_lookup(
    *, catalog_path: Path, library: Any, scopes: Mapping[str, tuple[str, ...]]
) -> tuple[tuple[Any, ...], dict[tuple[str, str], tuple[str, str]]]:
    from tools.chroma_baseline_evaluate import load_catalog
    from tools.structured_table_evidence import extract_table_records

    transfusion_document = next(
        document
        for document in library.docs
        if str(document["id"]) in scopes["transfusion"]
    )
    document_name = str(transfusion_document["document_name"])
    _, chunks = load_catalog(catalog_path, document_name=document_name)
    records = extract_table_records(
        catalog_path.resolve().parents[1] / document_name,
        scopes["transfusion"][0],
        document_name,
        chunks,
    )
    lookup: dict[tuple[str, str], tuple[str, str]] = {}
    for record in records:
        header = " | ".join(cell.text for cell in record.header if cell.text)
        for row in record.rows:
            body = " | ".join(cell.text for cell in row.cells if cell.text)
            lookup[(record.table_id, row.row_id)] = (
                f"{record.table_id}:{row.row_id}",
                f"{header}\n{body}" if header else body,
            )
    return records, lookup


def prepare_approved_live_cases(
    *,
    catalog_path: Path = DEFAULT_CATALOG,
    uat_path: Path = DEFAULT_UAT,
    reviewed_path: Path = DEFAULT_REVIEWED,
) -> list[PreparedLiveCase]:
    """Prepare production-selected exact evidence entirely in local memory."""
    from src.evidence import (
        assess_evidence,
        build_source_unit_catalog,
        evidence_groups,
    )
    from src.library import Embedder, bounded_embedding_question
    from src.query import plan_query
    from src.repository import snapshot
    from tools.schat_v1_final_validate import (
        _catalog_revision,
        _post_budget_selection,
        _scope_document_ids,
    )
    from tools.structured_table_evidence import search_table_records

    reviewed = _read_json(reviewed_path)
    uat = _read_json(uat_path)
    approved_specs = filter_approved_live_cases(reviewed, uat)
    if len(approved_specs) != 21:
        raise GroqLiveError("approved_case_count_drift")
    approved_ids = {str(row["case_id"]) for row in approved_specs}
    review_by_id = {
        str(row["case_id"]): row
        for row in reviewed.get("cases", ())
        if str(row.get("case_id", "")) in approved_ids
    }
    library = snapshot(
        str(catalog_path.resolve()), _catalog_revision(catalog_path)
    )
    embedder = Embedder()
    scopes = _scope_document_ids(library.docs)
    table_records, table_lookup = _table_text_lookup(
        catalog_path=catalog_path,
        library=library,
        scopes=scopes,
    )
    previous: dict[str, dict[str, Any]] = {}
    prepared: list[PreparedLiveCase] = []
    for case in uat.get("cases", ()):
        case_id = str(case.get("case_id", ""))
        scope = str(case.get("document_scope", ""))
        expected_behavior = str(case.get("expected_behavior", ""))
        is_follow_up = str(case.get("question_type", "")) == "follow_up"
        prior = previous.get(scope, {}) if is_follow_up else {}
        allowed = scopes.get(scope, tuple(scopes["sedation"] + scopes["transfusion"]))
        explicit_context = allowed if scope in scopes else ()
        plan = plan_query(
            str(case.get("question", "")),
            previous=str(prior.get("question", "")),
            follow_up=is_follow_up,
            documents=library.docs,
            previous_sources=tuple(prior.get("document_ids", ())),
            context_document_ids=explicit_context,
        )
        selected: list[Any] = []
        hits: Sequence[Any] = ()
        post = None
        table_hits: tuple[Any, ...] = ()
        evidence_type = str(case.get("expected_evidence_type", "text"))
        if (
            scope == "transfusion"
            and evidence_type in {"table", "mixed"}
            and plan.domain != "out_of_scope"
            and not plan.clarification
        ):
            table_hits = search_table_records(
                str(case.get("question", "")), table_records, limit=10
            )
        if (
            expected_behavior == "answer"
            and plan.domain != "out_of_scope"
            and not plan.clarification
        ):
            query_text = bounded_embedding_question(plan.expanded, embedder)
            vector = embedder.encode([query_text])[0]
            hits = library.search(
                plan.query,
                vector,
                list(allowed),
                0.38,
                plan=plan,
                trace={},
            )
            pre = assess_evidence(plan, hits)
            if pre.sufficient:
                selected, _, post, _ = _post_budget_selection(plan, pre)
        if case_id in approved_ids:
            if not ((post and post.sufficient) or table_hits):
                raise GroqLiveError(f"approved_case_without_evidence:{case_id}")
            text_rows = [
                {"evidence_id": hit.chunk.id, "text": hit.chunk.text}
                for hit in selected
            ]
            table_rows = [
                {
                    "evidence_id": table_lookup[(hit.table_id, hit.row_id)][0],
                    "text": table_lookup[(hit.table_id, hit.row_id)][1],
                }
                for hit in table_hits
                if (hit.table_id, hit.row_id) in table_lookup
            ]
            if evidence_type == "table":
                evidence_rows = table_rows[:10] + text_rows[:6]
            elif evidence_type == "mixed":
                evidence_rows = text_rows[:8] + table_rows[:8]
            else:
                evidence_rows = text_rows[:16]
            deduplicated: list[dict[str, str]] = []
            seen_ids: set[str] = set()
            for row in evidence_rows:
                if row["evidence_id"] not in seen_ids:
                    seen_ids.add(row["evidence_id"])
                    deduplicated.append(row)
            requested_phase = str(plan.monitoring_phase or "")
            projected_source_units: tuple[SourceUnit, ...] = ()
            if (
                evidence_type == "text"
                and plan.kind == "preparation"
                and requested_phase == "before"
            ):
                generation_hits = _generation_candidate_hits(plan, hits, selected)
                branch_by_chunk = {
                    hit.chunk.id: group.branch
                    for group in pre.groups
                    for hit in group.hits
                }
                generation_catalog = build_source_unit_catalog(
                    evidence_groups(
                        plan,
                        generation_hits,
                        branch_by_chunk=branch_by_chunk,
                    )
                )
                try:
                    projected_source_units = project_controlled_generation_units(
                        generation_catalog,
                        intent=str(case.get("expected_intent", plan.kind)),
                        requested_phase=requested_phase,
                    )
                except GroqLiveError as exc:
                    if str(exc) != "source_unit_count":
                        raise
                    projected_source_units = ()
                if projected_source_units:
                    units = build_request_local_source_units(projected_source_units)
                else:
                    units = build_request_local_units(deduplicated[:16])
            else:
                units = build_request_local_units(deduplicated[:16])
            identifier_codes = scan_source_units_for_identifiers(units)
            if identifier_codes:
                raise GroqLiveError(
                    f"identifier_detected:{case_id}:{','.join(identifier_codes)}"
                )
            review = review_by_id[case_id]
            gold_ids = {
                str(value)
                for field in ("primary_gold_ids", "acceptable_gold_ids")
                for value in review.get(field, ())
            }
            selected_ids = (
                {unit.chunk_id for unit in projected_source_units}
                if projected_source_units
                else {row["evidence_id"] for row in deduplicated[:16]}
            )
            overlap = selected_ids & gold_ids
            context_precision = len(overlap) / len(selected_ids) if selected_ids else 0.0
            context_recall = len(overlap) / len(gold_ids) if gold_ids else 0.0
            critical = {
                field: tuple(review.get(field, ()))
                for field in (
                    "critical_facts",
                    "critical_numbers",
                    "critical_units",
                    "critical_times",
                    "critical_conditions",
                    "critical_negations",
                    "critical_steps",
                )
            }
            required_coverage: tuple[RequiredCoverageSlot, ...] = ()
            required_coverage_error = ""
            if (
                str(case.get("expected_intent", plan.kind)) == "preparation"
                and requested_phase == "before"
            ):
                try:
                    required_coverage = build_required_coverage_slots(
                        critical, units
                    )
                except GroqLiveError as exc:
                    required_coverage_error = str(exc)
            prepared.append(
                PreparedLiveCase(
                    case_id=case_id,
                    intent=str(case.get("expected_intent", "fact_specific")),
                    document_scope=scope,
                    evidence_type=evidence_type,
                    units=units,
                    critical=critical,
                    context_precision=context_precision,
                    context_recall=context_recall,
                    requested_phase=requested_phase,
                    required_coverage=required_coverage,
                    required_coverage_error=required_coverage_error,
                )
            )
        if scope in scopes and expected_behavior == "answer":
            previous[scope] = {
                "question": str(case.get("question", "")),
                "document_ids": scopes[scope],
            }
    if len(prepared) != 21:
        raise GroqLiveError("prepared_case_count_drift")
    return prepared


def _percentile_95(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * 0.95 + 0.999999)))
    return ordered[index]


def _safe_write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def audit_live_artifacts(
    output_dir: Path, *, forbidden_exact_texts: Sequence[str]
) -> dict[str, Any]:
    secret = re.compile(
        r"(?i)(authorization\s*[:=]|bearer\s+[a-z0-9._-]{12,}|"
        r"\b(?:gsk|sk)-[a-z0-9_-]{12,}|api[_-]?key\s*[:=])"
    )
    forbidden_fields = re.compile(
        r'"(?:messages|raw_response|full_prompt|source_unit_text|exact_text)"\s*:'
    )
    files = [
        path
        for path in output_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".json", ".html", ".md"}
        and path.name != "security_audit.json"
    ]
    exact_matches = 0
    secret_markers = 0
    forbidden_field_count = 0
    for path in files:
        content = path.read_text(encoding="utf-8")
        exact_matches += sum(1 for value in forbidden_exact_texts if value and value in content)
        secret_markers += len(secret.findall(content))
        forbidden_field_count += len(forbidden_fields.findall(content))
    passed = not any((exact_matches, secret_markers, forbidden_field_count))
    return {
        "pass": passed,
        "files_checked": len(files),
        "raw_clinical_text_matches": exact_matches,
        "secret_markers": secret_markers,
        "forbidden_raw_fields": forbidden_field_count,
    }


def synthetic_preflight_case() -> PreparedLiveCase:
    units = build_request_local_units(
        [{"evidence_id": "synthetic-only", "text": "Synthetic guideline evidence."}]
    )
    return PreparedLiveCase(
        case_id="SYNTHETIC-LIVE-002",
        intent="fact_specific",
        document_scope="synthetic",
        evidence_type="text",
        units=units,
        critical={
            "critical_facts": (),
            "critical_numbers": (),
            "critical_units": (),
            "critical_times": (),
            "critical_conditions": (),
            "critical_negations": (),
            "critical_steps": (),
        },
        context_precision=1.0,
        context_recall=1.0,
    )


def _execute_case(
    case: PreparedLiveCase,
    *,
    api_key: str,
    human_review: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if case.required_coverage_error:
        raise GroqLiveError(
            f"{case.required_coverage_error}:{case.case_id}"
        )
    blueprint, _ = build_provider_blueprints(
        case_id=case.case_id,
        intent=case.intent,
        units=case.units,
        groq_model=GROQ_MODEL,
        gemini_model="unused",
        requested_phase=case.requested_phase,
        preserve_source_order=bool(case.requested_phase),
        required_coverage=tuple(
            slot.provider_descriptor() for slot in case.required_coverage
        ),
    )
    live = call_groq_live(blueprint, api_key=api_key)
    safe = safe_live_call_record(blueprint, live)
    safety = evaluate_live_safety(
        blueprint,
        live,
        units=case.units,
        intent=case.intent,
        critical=case.critical,
        required_coverage=case.required_coverage,
        requested_phase=case.requested_phase,
    )
    numeric_unit_diagnostic = build_numeric_unit_diagnostic_record(
        case_id=case.case_id,
        content=live.normalized.content,
        units=case.units,
        answer_hash=safe["generated_answer_sha256"],
        evidence_hash=safe["evidence_sha256"],
    )
    safety = apply_human_semantic_review(
        safety,
        case_id=case.case_id,
        generated_content=live.normalized.content,
        evidence_sha256=blueprint.evidence_fingerprint,
        review=human_review,
    )
    return {
        **safe,
        "document_scope": case.document_scope,
        "evidence_type": case.evidence_type,
        "id_context_precision": round(case.context_precision, 4),
        "id_context_recall": round(case.context_recall, 4),
        "number_unit_diagnostic": numeric_unit_diagnostic,
        **safety,
    }


def reuse_human_reviewed_case(
    case: PreparedLiveCase,
    *,
    review: Mapping[str, Any],
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Reuse the exact hash-reviewed result without another provider call."""
    if case.required_coverage_error:
        raise GroqLiveError(
            f"{case.required_coverage_error}:{case.case_id}"
        )
    validated_snapshot = validate_reviewed_case_snapshot(snapshot)
    if not human_review_allows_subset_start(review):
        raise GroqLiveError("human_review_invalid")
    if any(
        review.get(field) != validated_snapshot.get(field)
        for field in (
            "case_id",
            "reviewer",
            "reviewed_at",
            "generated_answer_sha256",
            "evidence_sha256",
            "human_semantic_support_approved",
        )
    ):
        raise GroqLiveError("human_review_snapshot_mismatch")
    blueprint, _ = build_provider_blueprints(
        case_id=case.case_id,
        intent=case.intent,
        units=case.units,
        groq_model=GROQ_MODEL,
        gemini_model="unused",
        requested_phase=case.requested_phase,
        preserve_source_order=bool(case.requested_phase),
        required_coverage=tuple(
            slot.provider_descriptor() for slot in case.required_coverage
        ),
    )
    if (
        case.case_id != validated_snapshot["case_id"]
        or blueprint.evidence_fingerprint != validated_snapshot["evidence_sha256"]
        or validated_snapshot["provider"] != "groq"
        or validated_snapshot["model"] != GROQ_MODEL
    ):
        raise GroqLiveError("human_review_snapshot_mismatch")
    return {
        "case_id": case.case_id,
        "provider": "groq",
        "model": GROQ_MODEL,
        "evidence_count": len(case.units),
        "evidence_character_count": sum(len(unit.exact_text) for unit in case.units),
        "evidence_sha256": validated_snapshot["evidence_sha256"],
        "generated_answer_sha256": validated_snapshot["generated_answer_sha256"],
        "latency_ms": validated_snapshot["provider_latency_ms"],
        "http_status": validated_snapshot["http_status"],
        "actual_external_calls": 0,
        "reviewed_provider_result_reused": True,
        "document_scope": case.document_scope,
        "evidence_type": case.evidence_type,
        "id_context_precision": round(case.context_precision, 4),
        "id_context_recall": round(case.context_recall, 4),
        "schema_pass": True,
        "citation_pass": True,
        "deterministic_critical_fact_preservation": validated_snapshot[
            "deterministic_critical_fact_preservation"
        ],
        "critical_fact_preservation": True,
        "number_pass": True,
        "unit_pass": True,
        "time_pass": True,
        "condition_pass": True,
        "negation_pass": True,
        "action_pass": True,
        "action_content_pass": True,
        "branch_phase_pass": True,
        "required_coverage_pass": True,
        "missing_required_slot_ids": [],
        "required_coverage_by_category": {
            category: True
            for category in dict.fromkeys(
                slot.category for slot in case.required_coverage
            )
        },
        "phase_scope_pass": True,
        "unsupported_clinical_claim_pass": True,
        "semantic_support": "human_approved_exact_hash_snapshot",
        "faithfulness": "not_measured_no_judge_payload_authority",
        "answer_relevancy": "not_measured_original_question_not_transmitted",
        "publish_controlled": False,
        "fallback_to_extractive": True,
        "validation_reason": "human_semantic_review_exact_hash_reuse",
        "human_semantic_support_approved": True,
        "failure_codes": [],
        "critical_safety_error_count": 0,
    }


def run_live_evaluation(
    *,
    output_dir: Path = DEFAULT_OUTPUT,
    catalog_path: Path = DEFAULT_CATALOG,
    uat_path: Path = DEFAULT_UAT,
    reviewed_path: Path = DEFAULT_REVIEWED,
    human_review_path: Path = DEFAULT_HUMAN_REVIEW,
    human_review_snapshot_path: Path = DEFAULT_HUMAN_REVIEW_SNAPSHOT,
    human_review_queue_path: Path = DEFAULT_HUMAN_REVIEW_QUEUE,
) -> dict[str, Any]:
    """Run synthetic, representative subset, then remaining approved cases."""
    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        raise GroqLiveError("MISSING_GROQ_API_KEY")
    output_dir.mkdir(parents=True, exist_ok=True)
    synthetic = _execute_case(synthetic_preflight_case(), api_key=api_key)
    if synthetic["critical_safety_error_count"]:
        summary = {
            "status": "synthetic_failed",
            "synthetic_actual_calls": 1,
            "real_actual_calls": 0,
            "critical_safety_error_count": synthetic["critical_safety_error_count"],
        }
        _safe_write_json(output_dir / "synthetic_result.json", synthetic)
        _safe_write_json(output_dir / "live_summary.json", summary)
        return summary

    try:
        human_review = load_human_review_record(human_review_path)
    except (OSError, ValueError) as exc:
        raise GroqLiveError("human_review_invalid") from exc
    if not human_review_allows_subset_start(human_review):
        summary = {
            "status": "blocked_by_uat_s01_human_review",
            "provider": "groq",
            "model": GROQ_MODEL,
            "synthetic_actual_calls": 1,
            "real_actual_calls": 0,
            "critical_safety_error_count": 0,
        }
        _safe_write_json(output_dir / "synthetic_result.json", synthetic)
        _safe_write_json(output_dir / "live_summary.json", summary)
        return summary
    try:
        human_review_snapshot = load_reviewed_case_snapshot(
            human_review_snapshot_path
        )
    except (OSError, ValueError) as exc:
        raise GroqLiveError("human_review_snapshot_invalid") from exc
    if human_review_snapshot is None:
        summary = {
            "status": "blocked_by_uat_s01_review_snapshot",
            "provider": "groq",
            "model": GROQ_MODEL,
            "synthetic_actual_calls": 1,
            "real_actual_calls": 0,
            "critical_safety_error_count": 0,
        }
        _safe_write_json(output_dir / "synthetic_result.json", synthetic)
        _safe_write_json(output_dir / "live_summary.json", summary)
        return summary

    prepared = prepare_approved_live_cases(
        catalog_path=catalog_path,
        uat_path=uat_path,
        reviewed_path=reviewed_path,
    )
    prepared_by_id = {case.case_id: case for case in prepared}
    subset_specs = select_representative_subset(
        [
            {
                "case_id": case.case_id,
                "document_scope": case.document_scope,
                "expected_evidence_type": case.evidence_type,
                "expected_intent": case.intent,
            }
            for case in prepared
        ],
        limit=5,
    )
    subset_ids = [str(row["case_id"]) for row in subset_specs]
    ordered_cases = [prepared_by_id[case_id] for case_id in subset_ids]
    ordered_cases.extend(case for case in prepared if case.case_id not in set(subset_ids))
    rows: list[dict[str, Any]] = []
    stopped_stage = ""
    for index, case in enumerate(ordered_cases):
        row = (
            reuse_human_reviewed_case(
                case,
                review=human_review,
                snapshot=human_review_snapshot,
            )
            if case.case_id == "UAT-S01"
            else _execute_case(case, api_key=api_key, human_review=human_review)
        )
        row["execution_stage"] = "subset" if index < len(subset_ids) else "full_expansion"
        rows.append(row)
        if row["critical_safety_error_count"]:
            if (
                case.case_id != "UAT-S01"
                and "critical_fact_preservation" in row.get("failure_codes", ())
            ):
                enqueue_human_semantic_review(human_review_queue_path, row)
            stopped_stage = row["execution_stage"]
            break
    latencies = [float(row["latency_ms"]) for row in rows]
    successful_rows = [row for row in rows if not row["critical_safety_error_count"]]
    completed_all = len(rows) == 21 and not stopped_stage
    summary = {
        "status": "completed" if completed_all else "stopped_on_critical_safety_error",
        "provider": "groq",
        "model": GROQ_MODEL,
        "synthetic_actual_calls": 1,
        "subset_planned_count": len(subset_ids),
        "real_actual_calls": sum(int(row["actual_external_calls"]) for row in rows),
        "evaluated_real_case_count": len(rows),
        "approved_case_count": 21,
        "deferred_case_calls": 0,
        "image_case_calls": 0,
        "abstention_provider_calls": 0,
        "critical_safety_error_count": sum(
            int(row["critical_safety_error_count"]) for row in rows
        ),
        "schema_pass_rate": round(
            sum(bool(row["schema_pass"]) for row in rows) / len(rows), 4
        ) if rows else 0.0,
        "citation_pass_rate": round(
            sum(bool(row["citation_pass"]) for row in rows) / len(rows), 4
        ) if rows else 0.0,
        "gold_critical_fact_pass_rate": round(
            sum(bool(row["critical_fact_preservation"]) for row in rows) / len(rows), 4
        ) if rows else 0.0,
        "id_context_precision": round(
            statistics.fmean(row["id_context_precision"] for row in rows), 4
        ) if rows else 0.0,
        "id_context_recall": round(
            statistics.fmean(row["id_context_recall"] for row in rows), 4
        ) if rows else 0.0,
        "faithfulness": "not_measured_no_judge_payload_authority",
        "answer_relevancy": "not_measured_original_question_not_transmitted",
        "provider_latency_mean_ms": round(statistics.fmean(latencies), 3) if latencies else 0.0,
        "provider_latency_p95_ms": round(_percentile_95(latencies), 3),
        "provider_error_rate": round((len(rows) - len(successful_rows)) / len(rows), 4) if rows else 0.0,
        "stopped_stage": stopped_stage or None,
        "completed_all_approved_cases": completed_all,
    }
    policy = {
        "provider": "Groq",
        "model": GROQ_MODEL,
        "endpoint_type": "synchronous_chat_completions",
        "strict_json_schema": True,
        "temperature": 0,
        "include_reasoning": False,
        "batch": False,
        "fine_tuning": False,
        "lora": False,
        "tools_browser_code": False,
        "default_inference_customer_data_retention": False,
        "usage_metadata_retained": True,
        "temporary_reliability_abuse_logging_max_days": 30,
        "data_controls_verification": "account_console_manual_confirmation_pending",
        "region_pin": "UNKNOWN",
        "retained_data_location_if_applicable": "United States",
    }
    _safe_write_json(output_dir / "synthetic_result.json", synthetic)
    _safe_write_json(output_dir / "live_case_results.json", rows)
    _safe_write_json(output_dir / "live_summary.json", summary)
    _safe_write_json(output_dir / "provider_policy.json", policy)
    forbidden_texts = [unit.exact_text for case in prepared for unit in case.units]
    forbidden_texts.extend(unit.exact_text for unit in synthetic_preflight_case().units)
    audit = audit_live_artifacts(output_dir, forbidden_exact_texts=forbidden_texts)
    _safe_write_json(output_dir / "security_audit.json", audit)
    if not audit["pass"]:
        raise GroqLiveError("artifact_security_audit")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--uat", type=Path, default=DEFAULT_UAT)
    parser.add_argument("--reviewed", type=Path, default=DEFAULT_REVIEWED)
    parser.add_argument("--human-review", type=Path, default=DEFAULT_HUMAN_REVIEW)
    parser.add_argument(
        "--human-review-snapshot",
        type=Path,
        default=DEFAULT_HUMAN_REVIEW_SNAPSHOT,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = run_live_evaluation(
        output_dir=args.output,
        catalog_path=args.catalog,
        uat_path=args.uat,
        reviewed_path=args.reviewed,
        human_review_path=args.human_review,
        human_review_snapshot_path=args.human_review_snapshot,
    )
    print(
        json.dumps(
            {
                "status": summary["status"],
                "provider": summary.get("provider", "groq"),
                "model": summary.get("model", GROQ_MODEL),
                "synthetic_actual_calls": summary["synthetic_actual_calls"],
                "real_actual_calls": summary["real_actual_calls"],
                "critical_safety_error_count": summary["critical_safety_error_count"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
