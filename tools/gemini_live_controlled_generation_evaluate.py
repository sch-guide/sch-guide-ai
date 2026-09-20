"""Evaluation-only Gemini controlled-generation pilot.

The module is intentionally disconnected from ``src.ai`` and the Streamlit
production entrypoint.  Raw requests and raw provider responses exist only in
memory.  Persistence is limited to the already ignored local review source and
raw-free execution metadata.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import httpx
from dotenv import dotenv_values

from src.controlled_generation import ControlledResponsePayload, decide_controlled_generation
from src.evidence import SourceUnit
from src.prompt_config import load_evaluation_prompt
from tools.controlled_generation_diagnostics import diagnose_controlled_candidate
from tools.controlled_generation_review import (
    build_review_source_case,
    load_review_source,
    write_review_source,
)
from tools.evaluation_action_strength import validate_action_strength
from tools.gemini_chromadb_only_evaluate import (
    _HOSPITAL_OR_INTERNAL_PATTERNS,
    _PATIENT_OR_STAFF_PATTERNS,
)
from tools.groq_live_ragas_evaluate import (
    GroqLiveCall,
    PreparedLiveCase,
    evaluate_live_safety,
    scan_source_units_for_identifiers,
)
from tools.provider_controlled_generation_evaluate import (
    NormalizedProviderResponse,
    ProviderBlueprint,
    build_provider_blueprints,
)

GEMINI_MODEL = "gemini-3.8-flash"
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"
MAX_OUTPUT_TOKENS = 2048
MAX_REQUEST_BYTES = 64 * 1024
LIVE_AUTHORIZATION_VALUE = "APPROVE_GEMINI_3_8_FLASH_CONTROLLED_6_CASES"
DEFAULT_ENV_PATH = Path(__file__).resolve().parents[1] / "src" / ".env"
PILOT_CASE_IDS = (
    "UAT-T01",
    "UAT-T02",
    "UAT-S04",
    "UAT-S08",
    "UAT-T12",
    "UAT-T11",
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_LEDGER_FIELDS = {
    "case_id",
    "status",
    "request_count",
    "retry_count",
    "input_tokens",
    "output_tokens",
    "thought_tokens",
    "latency_ms",
    "candidate_sha256",
    "validator_pass",
    "fallback_to_extractive",
}


class GeminiLiveError(RuntimeError):
    """Fixed, raw-free failure reason for the bounded live adapter."""


@dataclass(frozen=True)
class GeminiLiveResponse:
    normalized: NormalizedProviderResponse
    usage: dict[str, int]
    latency_ms: float
    http_status: int
    actual_external_calls: int = 1
    retry_count: int = 0


@dataclass(frozen=True)
class GeminiCandidateEvaluation:
    case_id: str
    validator_pass: bool
    fallback_to_extractive: bool
    publish_controlled: bool
    semantic_support_status: str
    retry_count: int
    review_candidate: str | None
    supporting_source_unit_ids: tuple[tuple[str, ...], ...]
    validation: dict[str, Any]


@dataclass(frozen=True)
class ReviewCandidateSnapshot:
    candidate: str
    supporting_source_unit_ids: tuple[tuple[str, ...], ...]
    candidate_status: str


def _extractive_review_snapshot(
    *,
    case: PreparedLiveCase,
    extractive_answer: str,
) -> ReviewCandidateSnapshot:
    paragraphs: list[str] = []
    citations: list[tuple[str, ...]] = []
    for unit in case.units:
        unit_paragraphs = tuple(
            value.strip()
            for value in re.split(r"\n\s*\n", unit.exact_text)
            if value.strip()
        )
        paragraphs.extend(unit_paragraphs)
        citations.extend((unit.source_unit_id,) for _ in unit_paragraphs)
    projected_answer = "\n\n".join(paragraphs)
    if not paragraphs or projected_answer != extractive_answer:
        raise ValueError("fallback_source_unit_mapping")
    return ReviewCandidateSnapshot(
        candidate=projected_answer,
        supporting_source_unit_ids=tuple(citations),
        candidate_status="extractive_fallback",
    )


def _review_candidate_snapshot(
    *,
    case: PreparedLiveCase,
    evaluated: GeminiCandidateEvaluation,
    extractive_answer: str,
) -> ReviewCandidateSnapshot:
    if evaluated.review_candidate is None:
        return _extractive_review_snapshot(
            case=case,
            extractive_answer=extractive_answer,
        )
    return ReviewCandidateSnapshot(
        candidate=evaluated.review_candidate,
        supporting_source_unit_ids=evaluated.supporting_source_unit_ids,
        candidate_status="validated_live",
    )


def require_live_authorization(value: str | None) -> None:
    if value != LIVE_AUTHORIZATION_VALUE:
        raise PermissionError("explicit live authorization is required")


def resolve_gemini_api_key(
    *,
    env_path: Path = DEFAULT_ENV_PATH,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Resolve an evaluation-only key without mutating Production settings."""
    import os

    process_values = os.environ if environ is None else environ
    file_values = dotenv_values(env_path)
    for name in ("GEMINI_API_KEY", "GUIDE_LLM_API_KEY"):
        value = process_values.get(name) or file_values.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise GeminiLiveError("missing_api_key")


def execute_bounded_sequence(case_ids: Sequence[str], execute: Any) -> list[Any]:
    """Execute at most six unique cases and stop naturally on the first error."""
    if len(case_ids) > 6:
        raise ValueError("maximum_six_cases")
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("duplicate_case_id")
    return [execute(case_id) for case_id in case_ids]


def append_raw_free_ledger_record(path: Any, value: Mapping[str, Any]) -> None:
    """Append a closed metadata record without accepting provider content."""
    if set(value) != _LEDGER_FIELDS:
        raise ValueError("ledger_fields")
    if (
        not isinstance(value.get("case_id"), str)
        or not value["case_id"]
        or value.get("status")
        not in {"started", "completed", "failed", "review_source_updated"}
        or value.get("request_count") != 1
        or value.get("retry_count") != 0
        or not isinstance(value.get("latency_ms"), (int, float))
        or value["latency_ms"] < 0
        or _SHA256.fullmatch(str(value.get("candidate_sha256", ""))) is None
        or not isinstance(value.get("validator_pass"), bool)
        or value.get("fallback_to_extractive") is not True
    ):
        raise ValueError("ledger_values")
    for field in ("input_tokens", "output_tokens", "thought_tokens"):
        if not isinstance(value.get(field), int) or value[field] < 0:
            raise ValueError("ledger_values")
    destination = path
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(dict(value), ensure_ascii=False, sort_keys=True) + "\n")


def build_gemini_live_payload(
    blueprint: ProviderBlueprint,
    *,
    question: str,
) -> dict[str, Any]:
    """Project an offline blueprint to the current stateless Interactions API."""
    if blueprint.provider != "gemini" or blueprint.model != GEMINI_MODEL:
        raise GeminiLiveError("provider_or_model_mismatch")
    if not isinstance(question, str) or not question.strip():
        raise GeminiLiveError("question_missing")
    try:
        prompt = blueprint.payload["contents"][0]["parts"][0]["text"]
        schema = blueprint.payload["generationConfig"]["responseFormat"]["text"][
            "schema"
        ]
    except (KeyError, IndexError, TypeError) as exc:
        raise GeminiLiveError("offline_blueprint_contract") from exc
    if not isinstance(prompt, str) or not isinstance(schema, dict):
        raise GeminiLiveError("offline_blueprint_contract")
    live_input = (
        f"{prompt}\n"
        "CURRENT USER QUESTION (use only to select and organize the supplied "
        "evidence; never answer from the question itself):\n"
        f"{question.strip()}"
    )
    payload = {
        "model": GEMINI_MODEL,
        "input": live_input,
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": schema,
        },
        "generation_config": {
            "thinking_level": "low",
            "max_output_tokens": MAX_OUTPUT_TOKENS,
        },
        "stream": False,
        "store": False,
    }
    if len(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    ) > MAX_REQUEST_BYTES:
        raise GeminiLiveError("unexpected_payload_growth")
    return payload


def validate_live_projection(
    payload: Mapping[str, Any],
    *,
    question: str,
    units: Sequence[SourceUnit],
    forbidden_identifiers: Sequence[str] = (),
) -> None:
    """Fail closed before transmission without logging any matched text."""
    if set(payload) != {
        "model",
        "input",
        "response_format",
        "generation_config",
        "stream",
        "store",
    }:
        raise GeminiLiveError("payload_allowlist")
    if (
        payload.get("model") != GEMINI_MODEL
        or payload.get("stream") is not False
        or payload.get("store") is not False
        or payload.get("generation_config")
        != {"thinking_level": "low", "max_output_tokens": MAX_OUTPUT_TOKENS}
    ):
        raise GeminiLiveError("payload_configuration")
    live_input = payload.get("input")
    if not isinstance(live_input, str) or question.strip() not in live_input:
        raise GeminiLiveError("question_projection")
    if not units or scan_source_units_for_identifiers(tuple(units)):
        raise GeminiLiveError("identifier_detected")
    if any(
        pattern.search(question) or any(pattern.search(unit.exact_text) for unit in units)
        for pattern in (*_PATIENT_OR_STAFF_PATTERNS, *_HOSPITAL_OR_INTERNAL_PATTERNS)
    ):
        raise GeminiLiveError("identifier_detected")
    for unit in units:
        if unit.source_unit_id not in live_input or unit.exact_text not in live_input:
            raise GeminiLiveError("source_unit_projection")
        if unit.chunk_id and unit.chunk_id in live_input:
            raise GeminiLiveError("internal_identifier_transmitted")
    if any(value and value in live_input for value in forbidden_identifiers):
        raise GeminiLiveError("internal_identifier_transmitted")
    response_format = payload.get("response_format")
    if not isinstance(response_format, Mapping) or response_format.get("type") != "text":
        raise GeminiLiveError("structured_output_contract")
    if response_format.get("mime_type") != "application/json":
        raise GeminiLiveError("structured_output_contract")
    schema = response_format.get("schema")
    if not isinstance(schema, Mapping) or schema.get("additionalProperties") is not False:
        raise GeminiLiveError("structured_output_contract")


def _nonnegative_int(value: Any, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise GeminiLiveError(f"usage_{field}")
    return value


def _normalize_interaction(body: Any) -> tuple[NormalizedProviderResponse, dict[str, int]]:
    if not isinstance(body, Mapping) or body.get("status") != "completed":
        raise GeminiLiveError("provider_status")
    if body.get("model") != GEMINI_MODEL:
        raise GeminiLiveError("provider_model")
    steps = body.get("steps")
    if not isinstance(steps, list):
        raise GeminiLiveError("provider_steps")
    text_outputs: list[str] = []
    for step in steps:
        if not isinstance(step, Mapping) or step.get("type") != "model_output":
            continue
        content = step.get("content")
        if not isinstance(content, list):
            raise GeminiLiveError("provider_content")
        for item in content:
            if isinstance(item, Mapping) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    text_outputs.append(text)
    if len(text_outputs) != 1:
        raise GeminiLiveError("provider_text_output")
    usage_raw = body.get("usage")
    if not isinstance(usage_raw, Mapping):
        raise GeminiLiveError("provider_usage")
    usage = {
        "input_tokens": _nonnegative_int(
            usage_raw.get("total_input_tokens"), field="input_tokens"
        ),
        "output_tokens": _nonnegative_int(
            usage_raw.get("total_output_tokens"), field="output_tokens"
        ),
        "thought_tokens": _nonnegative_int(
            usage_raw.get("total_thought_tokens", 0), field="thought_tokens"
        ),
        "total_tokens": _nonnegative_int(
            usage_raw.get("total_tokens"), field="total_tokens"
        ),
    }
    if usage["total_tokens"] < usage["input_tokens"] + usage["output_tokens"]:
        raise GeminiLiveError("usage_total_tokens")
    return (
        NormalizedProviderResponse(
            provider="gemini",
            model=GEMINI_MODEL,
            finish_reason="completed",
            content=text_outputs[0],
            usage=usage,
        ),
        usage,
    )


class GeminiGenerationClient:
    """Single-attempt REST client with an injectable transport for TDD."""

    def __init__(self, *, api_key: str, client: httpx.Client) -> None:
        if not api_key.strip():
            raise GeminiLiveError("missing_api_key")
        self._api_key = api_key
        self._client = client

    def generate(self, payload: Mapping[str, Any]) -> GeminiLiveResponse:
        started = time.perf_counter()
        try:
            response = self._client.post(
                GEMINI_ENDPOINT,
                headers={
                    "x-goog-api-key": self._api_key,
                    "content-type": "application/json",
                },
                json=dict(payload),
            )
        except httpx.HTTPError as exc:
            raise GeminiLiveError("provider_transport") from exc
        latency_ms = (time.perf_counter() - started) * 1000
        if response.status_code != 200:
            raise GeminiLiveError(f"provider_http_{response.status_code}")
        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise GeminiLiveError("provider_json") from exc
        normalized, usage = _normalize_interaction(body)
        return GeminiLiveResponse(
            normalized=normalized,
            usage=usage,
            latency_ms=latency_ms,
            http_status=response.status_code,
        )


def evaluate_gemini_candidate(
    *,
    case: PreparedLiveCase,
    blueprint: ProviderBlueprint,
    response: GeminiLiveResponse,
    extractive_answer: str,
) -> GeminiCandidateEvaluation:
    """Apply the unchanged local validator stack and retain no failed text."""
    compatibility_call = GroqLiveCall(
        normalized=response.normalized,
        latency_ms=response.latency_ms,
        http_status=response.http_status,
        actual_external_calls=response.actual_external_calls,
    )
    validation = evaluate_live_safety(
        blueprint,
        compatibility_call,
        units=case.units,
        intent=case.intent,
        critical=case.critical,
        required_coverage=case.required_coverage,
        requested_phase=case.requested_phase,
    )
    validation = dict(validation)
    action_strength_pass = True
    action_strength_error: str | None = None
    try:
        parsed = ControlledResponsePayload.model_validate_json(
            response.normalized.content
        )
    except (TypeError, ValueError):
        # Schema failures remain owned by the existing validator stack.
        parsed = None
    if parsed is not None:
        by_id = {unit.source_unit_id: unit for unit in case.units}
        for statement in parsed.statements:
            if any(
                identifier not in by_id
                for identifier in statement.supporting_source_unit_ids
            ):
                # Unknown IDs remain owned by the existing citation validator.
                continue
            result = validate_action_strength(
                statement_text=statement.text,
                cited_sources={
                    identifier: by_id[identifier].exact_text
                    for identifier in statement.supporting_source_unit_ids
                },
            )
            if not result.passed:
                action_strength_pass = False
                action_strength_error = result.error_code
                break
    validation["evaluation_action_strength_pass"] = action_strength_pass
    if not action_strength_pass and action_strength_error:
        validation["failure_codes"] = [
            *validation.get("failure_codes", ()),
            action_strength_error,
        ]
        validation["critical_safety_error_count"] = len(
            validation["failure_codes"]
        )
    decision = decide_controlled_generation(
        response.normalized.content,
        case.units,
        intent=case.intent,
        extractive_answer=extractive_answer,
    )
    validator_pass = (
        not validation["failure_codes"]
        and action_strength_pass
        and decision.validated_candidate is not None
    )
    candidate: str | None = None
    citation_rows: tuple[tuple[str, ...], ...] = ()
    if validator_pass and decision.validated_candidate is not None:
        candidate = "\n\n".join(
            statement.text for statement in decision.validated_candidate.statements
        )
        citation_rows = tuple(
            tuple(citation.source_unit_id for citation in statement.citations)
            for statement in decision.validated_candidate.statements
        )
    return GeminiCandidateEvaluation(
        case_id=case.case_id,
        validator_pass=validator_pass,
        fallback_to_extractive=True,
        publish_controlled=False,
        semantic_support_status="pending",
        retry_count=0,
        review_candidate=candidate,
        supporting_source_unit_ids=citation_rows,
        validation=validation,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _ledger_value(
    *,
    case_id: str,
    status: str,
    usage: Mapping[str, int] | None = None,
    latency_ms: float = 0.0,
    candidate_sha256: str | None = None,
    validator_pass: bool = False,
) -> dict[str, Any]:
    current_usage = usage or {}
    return {
        "case_id": case_id,
        "status": status,
        "request_count": 1,
        "retry_count": 0,
        "input_tokens": int(current_usage.get("input_tokens", 0)),
        "output_tokens": int(current_usage.get("output_tokens", 0)),
        "thought_tokens": int(current_usage.get("thought_tokens", 0)),
        "latency_ms": round(float(latency_ms), 3),
        "candidate_sha256": candidate_sha256
        or _sha256_text(f"pending:{case_id}"),
        "validator_pass": validator_pass,
        "fallback_to_extractive": True,
    }


def _write_raw_free_summary(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_single_case_diagnostic(
    *,
    api_key: str,
    source_root: Path,
    source_path: Path,
    question_fixture_path: Path,
    diagnostic_path: Path,
    prepared_cases: Sequence[PreparedLiveCase],
    case_id: str,
    client: httpx.Client,
) -> dict[str, Any]:
    """Call one approved case once and retain only parsed candidate statements."""
    root = source_root.resolve()
    for path in (source_path, diagnostic_path):
        resolved = path.resolve()
        if resolved == root or not resolved.is_relative_to(root):
            raise ValueError("local_only_path")
    if diagnostic_path.exists():
        raise ValueError("existing_diagnostic")

    config = load_evaluation_prompt()
    catalog = load_review_source(
        source_path,
        question_fixture_path=question_fixture_path,
        expected_prompt_version=config.prompt_version,
    )
    by_case = {case.case_id: case for case in prepared_cases}
    if case_id not in by_case or case_id not in catalog:
        raise ValueError("diagnostic_case_missing")
    case = by_case[case_id]
    if case.required_coverage_error:
        raise GeminiLiveError(f"required_coverage_error:{case_id}")

    _, blueprint = build_provider_blueprints(
        case_id=case.case_id,
        intent=case.intent,
        units=case.units,
        groq_model="unused-evaluation-only",
        gemini_model=GEMINI_MODEL,
        requested_phase=case.requested_phase,
        preserve_source_order=bool(case.requested_phase),
        required_coverage=tuple(
            slot.provider_descriptor() for slot in case.required_coverage
        ),
    )
    baseline = catalog[case_id]
    payload = build_gemini_live_payload(blueprint, question=baseline.question)
    validate_live_projection(payload, question=baseline.question, units=case.units)

    response = GeminiGenerationClient(api_key=api_key, client=client).generate(payload)
    diagnostic = diagnose_controlled_candidate(
        case=case,
        content=response.normalized.content,
        http_response_received=True,
    )
    evaluated = evaluate_gemini_candidate(
        case=case,
        blueprint=blueprint,
        response=response,
        extractive_answer=baseline.extractive_answer,
    )
    if evaluated.validator_pass != diagnostic.final_validator_pass:
        raise GeminiLiveError("diagnostic_validator_mismatch")

    records = {
        value_case_id: build_review_source_case(
            case_id=value.case_id,
            prompt_version=value.prompt_version,
            model=value.model,
            candidate_status=value.candidate_status,
            extractive_answer=value.extractive_answer,
            controlled_candidate=value.controlled_candidate,
            supporting_source_unit_ids=value.supporting_source_unit_ids,
        )
        for value_case_id, value in catalog.items()
    }
    if diagnostic.statements:
        display_candidate = "\n\n".join(
            statement.text for statement in diagnostic.statements
        )
        display_citations = tuple(
            statement.supporting_source_unit_ids
            for statement in diagnostic.statements
        )
        records[case_id] = build_review_source_case(
            case_id=case_id,
            prompt_version=config.prompt_version,
            model=GEMINI_MODEL,
            candidate_status=(
                "validated_live"
                if diagnostic.final_validator_pass
                else "diagnostic_rejected_live"
            ),
            extractive_answer=baseline.extractive_answer,
            controlled_candidate=display_candidate,
            supporting_source_unit_ids=display_citations,
        )
        write_review_source(
            source_path,
            [records[value] for value in catalog],
            allowed_root=source_root,
        )
    else:
        display_candidate = baseline.extractive_answer

    result = {
        "case_id": case_id,
        "model": GEMINI_MODEL,
        "prompt_version": config.prompt_version,
        "http_success": response.http_status == 200,
        "http_status": response.http_status,
        "actual_http_requests": response.actual_external_calls,
        "retry_count": response.retry_count,
        "usage": dict(response.usage),
        "latency_ms": round(response.latency_ms, 3),
        "candidate_sha256": diagnostic.candidate_sha256,
        "statement_count": diagnostic.statement_count,
        "statements": [asdict(statement) for statement in diagnostic.statements],
        "stages": [asdict(stage) for stage in diagnostic.stages],
        "first_failure_stage": diagnostic.first_failure_stage,
        "first_failure_validator": diagnostic.first_failure_validator,
        "failure_statement_index": diagnostic.failure_statement_index,
        "related_source_unit_ids": list(diagnostic.related_source_unit_ids),
        "error_code": diagnostic.error_code,
        "final_validator_pass": diagnostic.final_validator_pass,
        "fallback_to_extractive": diagnostic.fallback_to_extractive,
        "publish_controlled": diagnostic.publish_controlled,
        "semantic_support_status": "pending",
        "candidate_changed": display_candidate != baseline.extractive_answer,
    }
    _write_raw_free_summary(diagnostic_path, result)
    return result


def run_live_pilot(
    *,
    api_key: str,
    source_root: Path,
    source_path: Path,
    question_fixture_path: Path,
    ledger_path: Path,
    summary_path: Path,
    prepared_cases: Sequence[PreparedLiveCase],
    case_ids: Sequence[str] = PILOT_CASE_IDS,
    client: httpx.Client,
) -> dict[str, Any]:
    """Run the bounded pilot once; an existing ledger blocks retransmission."""
    root = source_root.resolve()
    for path in (source_path, ledger_path, summary_path):
        resolved = path.resolve()
        if resolved == root or not resolved.is_relative_to(root):
            raise ValueError("local_only_path")
    if ledger_path.exists():
        raise ValueError("existing_live_ledger")
    if tuple(case_ids) and (len(case_ids) > 6 or len(set(case_ids)) != len(case_ids)):
        raise ValueError("maximum_six_cases")

    config = load_evaluation_prompt()
    catalog = load_review_source(
        source_path,
        question_fixture_path=question_fixture_path,
        expected_prompt_version=config.prompt_version,
    )
    by_case = {case.case_id: case for case in prepared_cases}
    if set(case_ids) - set(by_case) or set(case_ids) - set(catalog):
        raise ValueError("pilot_case_missing")

    records = {
        case_id: build_review_source_case(
            case_id=value.case_id,
            prompt_version=value.prompt_version,
            model=value.model,
            candidate_status=value.candidate_status,
            extractive_answer=value.extractive_answer,
            controlled_candidate=value.controlled_candidate,
            supporting_source_unit_ids=value.supporting_source_unit_ids,
        )
        for case_id, value in catalog.items()
    }

    requests: dict[str, tuple[PreparedLiveCase, ProviderBlueprint, dict[str, Any]]] = {}
    for case_id in case_ids:
        case = by_case[case_id]
        if case.required_coverage_error:
            raise GeminiLiveError(f"required_coverage_error:{case_id}")
        _, blueprint = build_provider_blueprints(
            case_id=case.case_id,
            intent=case.intent,
            units=case.units,
            groq_model="unused-evaluation-only",
            gemini_model=GEMINI_MODEL,
            requested_phase=case.requested_phase,
            preserve_source_order=bool(case.requested_phase),
            required_coverage=tuple(
                slot.provider_descriptor() for slot in case.required_coverage
            ),
        )
        payload = build_gemini_live_payload(
            blueprint,
            question=catalog[case_id].question,
        )
        validate_live_projection(
            payload,
            question=catalog[case_id].question,
            units=case.units,
        )
        requests[case_id] = (case, blueprint, payload)

    active_client = GeminiGenerationClient(api_key=api_key, client=client)
    case_results: list[dict[str, Any]] = []

    def execute(case_id: str) -> dict[str, Any]:
        case, blueprint, payload = requests[case_id]
        baseline = catalog[case_id]
        append_raw_free_ledger_record(
            ledger_path,
            _ledger_value(case_id=case_id, status="started"),
        )
        try:
            response = active_client.generate(payload)
            evaluated = evaluate_gemini_candidate(
                case=case,
                blueprint=blueprint,
                response=response,
                extractive_answer=baseline.extractive_answer,
            )
        except Exception:
            append_raw_free_ledger_record(
                ledger_path,
                _ledger_value(case_id=case_id, status="failed"),
            )
            raise

        provider_candidate_sha256 = _sha256_text(response.normalized.content)
        append_raw_free_ledger_record(
            ledger_path,
            _ledger_value(
                case_id=case_id,
                status="completed",
                usage=response.usage,
                latency_ms=response.latency_ms,
                candidate_sha256=provider_candidate_sha256,
                validator_pass=evaluated.validator_pass,
            ),
        )
        snapshot = _review_candidate_snapshot(
            case=case,
            evaluated=evaluated,
            extractive_answer=baseline.extractive_answer,
        )
        records[case_id] = build_review_source_case(
            case_id=case_id,
            prompt_version=config.prompt_version,
            model=GEMINI_MODEL,
            candidate_status=snapshot.candidate_status,
            extractive_answer=baseline.extractive_answer,
            controlled_candidate=snapshot.candidate,
            supporting_source_unit_ids=snapshot.supporting_source_unit_ids,
        )
        write_review_source(
            source_path,
            [records[value] for value in catalog],
            allowed_root=source_root,
        )
        append_raw_free_ledger_record(
            ledger_path,
            _ledger_value(
                case_id=case_id,
                status="review_source_updated",
                usage=response.usage,
                latency_ms=response.latency_ms,
                candidate_sha256=provider_candidate_sha256,
                validator_pass=evaluated.validator_pass,
            ),
        )
        validation = evaluated.validation
        result = {
            "case_id": case_id,
            "generation_success": True,
            "validator_pass": evaluated.validator_pass,
            "fallback_to_extractive": evaluated.fallback_to_extractive,
            "candidate_changed": bool(
                evaluated.review_candidate is not None
                and evaluated.review_candidate != baseline.extractive_answer
            ),
            "candidate_sha256": provider_candidate_sha256,
            "http_status": response.http_status,
            "actual_http_requests": response.actual_external_calls,
            "retry_count": response.retry_count,
            "latency_ms": round(response.latency_ms, 3),
            "usage": dict(response.usage),
            "validation": {
                name: bool(validation.get(name, False))
                for name in (
                    "schema_pass",
                    "citation_pass",
                    "evaluation_action_strength_pass",
                    "critical_fact_preservation",
                    "number_pass",
                    "unit_pass",
                    "time_pass",
                    "condition_pass",
                    "negation_pass",
                    "action_pass",
                    "branch_phase_pass",
                    "required_coverage_pass",
                    "phase_scope_pass",
                )
            },
            "failure_codes": list(validation.get("failure_codes", ())),
            "publish_controlled": False,
            "semantic_support_status": "pending",
        }
        case_results.append(result)
        return result

    execute_bounded_sequence(tuple(case_ids), execute)
    usage = {
        field: sum(row["usage"][field] for row in case_results)
        for field in ("input_tokens", "output_tokens", "thought_tokens", "total_tokens")
    }
    billable_output = usage["output_tokens"] + usage["thought_tokens"]
    summary = {
        "status": "completed",
        "provider": "gemini",
        "model": GEMINI_MODEL,
        "thinking_level": "low",
        "case_count": len(case_results),
        "actual_http_requests": sum(
            row["actual_http_requests"] for row in case_results
        ),
        "retry_count": sum(row["retry_count"] for row in case_results),
        "usage": usage,
        "estimated_cost_usd": round(
            usage["input_tokens"] * 0.75 / 1_000_000
            + billable_output * 3.75 / 1_000_000,
            8,
        ),
        "candidate_changed_count": sum(
            bool(row["candidate_changed"]) for row in case_results
        ),
        "validator_pass_count": sum(
            bool(row["validator_pass"]) for row in case_results
        ),
        "publish_controlled": False,
        "semantic_support_status": "pending",
        "case_results": case_results,
    }
    _write_raw_free_summary(summary_path, summary)
    return summary
