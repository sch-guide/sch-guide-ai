from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

import tools.gemini_live_controlled_generation_evaluate as gemini_live
from src.evidence import SourceUnit
from tools.controlled_generation_review import (
    build_review_source_case,
    candidate_status_notice,
    load_review_source,
    review_case_label,
    write_review_source,
)
from tools.gemini_live_controlled_generation_evaluate import (
    GEMINI_ENDPOINT,
    GEMINI_MODEL,
    LIVE_AUTHORIZATION_VALUE,
    PILOT_CASE_IDS,
    GeminiCandidateEvaluation,
    GeminiGenerationClient,
    GeminiLiveError,
    append_raw_free_ledger_record,
    build_gemini_live_payload,
    evaluate_gemini_candidate,
    execute_bounded_sequence,
    require_live_authorization,
    resolve_gemini_api_key,
    run_live_pilot,
    run_single_case_diagnostic,
    validate_live_projection,
)
from tools.groq_live_ragas_evaluate import PreparedLiveCase
from tools.provider_controlled_generation_evaluate import build_provider_blueprints


def _unit(identifier: str, text: str, *, chunk_id: str = "internal-chunk-001") -> SourceUnit:
    return SourceUnit(
        source_unit_id=identifier,
        chunk_id=chunk_id,
        source_order=(1, 0),
        branch="common",
        exact_text=text,
        group_key="internal-group-001",
        required=True,
        selectable=True,
    )


def _case(text: str = "15분 동안 확인한다.") -> PreparedLiveCase:
    return PreparedLiveCase(
        case_id="CASE-001",
        intent="procedure",
        document_scope="internal-document-scope",
        evidence_type="text",
        units=(_unit("su001", text),),
        critical={
            "critical_facts": (),
            "critical_numbers": ("15",),
            "critical_units": ("분",),
            "critical_times": ("15분",),
            "critical_conditions": (),
            "critical_negations": (),
            "critical_steps": (),
        },
        context_precision=1.0,
        context_recall=1.0,
    )


def _blueprint(case: PreparedLiveCase):
    _, gemini = build_provider_blueprints(
        case_id=case.case_id,
        intent=case.intent,
        units=case.units,
        groq_model="unused",
        gemini_model=GEMINI_MODEL,
        preserve_source_order=True,
    )
    return gemini


def _interaction(content: dict, *, input_tokens: int = 30, output_tokens: int = 12):
    return {
        "id": "interaction-not-persisted",
        "model": GEMINI_MODEL,
        "status": "completed",
        "steps": [
            {
                "type": "model_output",
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(content, ensure_ascii=False),
                    }
                ],
            }
        ],
        "usage": {
            "total_input_tokens": input_tokens,
            "total_output_tokens": output_tokens,
            "total_thought_tokens": 3,
            "total_tokens": input_tokens + output_tokens + 3,
        },
    }


def test_live_projection_contains_only_question_selected_evidence_and_local_ids() -> None:
    case = _case()
    payload = build_gemini_live_payload(
        _blueprint(case),
        question="현재 절차는 무엇인가요?",
    )

    serialized = json.dumps(payload, ensure_ascii=False)
    assert payload == {
        "model": GEMINI_MODEL,
        "input": payload["input"],
        "response_format": payload["response_format"],
        "generation_config": {
            "thinking_level": "low",
            "max_output_tokens": 2048,
        },
        "stream": False,
        "store": False,
    }
    assert "현재 절차는 무엇인가요?" in payload["input"]
    assert "15분 동안 확인한다." in payload["input"]
    assert "su001" in serialized
    assert "internal-chunk-001" not in serialized
    assert "internal-document-scope" not in serialized
    assert "gold answer must never be sent" not in serialized
    assert "previous_interaction_id" not in payload
    assert "tools" not in payload
    assert "follow_up" not in serialized.casefold()
    assert payload["response_format"]["type"] == "text"
    assert payload["response_format"]["mime_type"] == "application/json"
    assert payload["response_format"]["schema"]["additionalProperties"] is False

    validate_live_projection(
        payload,
        question="현재 절차는 무엇인가요?",
        units=case.units,
        forbidden_identifiers=(case.document_scope,),
    )


def test_mock_transport_makes_one_request_without_retry_or_secret_persistence(
    tmp_path: Path,
) -> None:
    case = _case()
    payload = build_gemini_live_payload(_blueprint(case), question="질문")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=_interaction(
                {
                    "statements": [
                        {
                            "text": "15분 동안 확인한다.",
                            "supporting_source_unit_ids": ["su001"],
                        }
                    ]
                }
            ),
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = GeminiGenerationClient(
            api_key="secret-must-not-survive",
            client=http_client,
        ).generate(payload)

    assert len(seen) == 1
    assert str(seen[0].url) == GEMINI_ENDPOINT
    assert seen[0].headers["x-goog-api-key"] == "secret-must-not-survive"
    assert result.actual_external_calls == 1
    assert result.retry_count == 0
    assert result.usage == {
        "input_tokens": 30,
        "output_tokens": 12,
        "thought_tokens": 3,
        "total_tokens": 45,
    }
    assert not hasattr(result, "raw_response")
    assert "secret-must-not-survive" not in repr(result)
    assert list(tmp_path.iterdir()) == []


def test_validation_failure_discards_candidate_and_uses_extractive_fallback() -> None:
    case = _case()
    blueprint = _blueprint(case)
    payload = build_gemini_live_payload(blueprint, question="질문")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_interaction(
                {
                    "statements": [
                        {
                            "text": "30분 동안 확인한다.",
                            "supporting_source_unit_ids": ["su001"],
                        }
                    ]
                }
            ),
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        response = GeminiGenerationClient(api_key="test", client=http_client).generate(
            payload
        )

    evaluated = evaluate_gemini_candidate(
        case=case,
        blueprint=blueprint,
        response=response,
        extractive_answer="기존 답변",
    )

    assert evaluated.validator_pass is False
    assert evaluated.fallback_to_extractive is True
    assert evaluated.review_candidate is None
    assert evaluated.publish_controlled is False
    assert evaluated.semantic_support_status == "pending"


def test_evaluation_action_strength_rejection_adds_only_a_stricter_fallback() -> None:
    case = _case("즉시 투여를 중단한다.")
    blueprint = _blueprint(case)
    payload = build_gemini_live_payload(blueprint, question="질문")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_interaction(
                {
                    "statements": [
                        {
                            "text": "투여 중단을 고려한다.",
                            "supporting_source_unit_ids": ["su001"],
                        }
                    ]
                }
            ),
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        response = GeminiGenerationClient(api_key="test", client=http_client).generate(
            payload
        )

    evaluated = evaluate_gemini_candidate(
        case=case,
        blueprint=blueprint,
        response=response,
        extractive_answer="즉시 투여를 중단한다.",
    )

    assert evaluated.validator_pass is False
    assert evaluated.fallback_to_extractive is True
    assert evaluated.review_candidate is None
    assert evaluated.retry_count == 0
    assert evaluated.validation["evaluation_action_strength_pass"] is False
    assert "evaluation_action_strength_changed" in evaluated.validation["failure_codes"]


def test_valid_candidate_remains_evaluation_only_and_keeps_citations() -> None:
    case = _case()
    blueprint = _blueprint(case)
    payload = build_gemini_live_payload(blueprint, question="질문")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_interaction(
                {
                    "statements": [
                        {
                            "text": "15분 동안 확인한다.",
                            "supporting_source_unit_ids": ["su001"],
                        }
                    ]
                }
            ),
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        response = GeminiGenerationClient(api_key="test", client=http_client).generate(
            payload
        )

    evaluated = evaluate_gemini_candidate(
        case=case,
        blueprint=blueprint,
        response=response,
        extractive_answer="기존 답변",
    )

    assert evaluated.validator_pass is True
    assert evaluated.review_candidate == "15분 동안 확인한다."
    assert evaluated.supporting_source_unit_ids == (("su001",),)
    assert evaluated.publish_controlled is False
    assert evaluated.fallback_to_extractive is True
    assert evaluated.retry_count == 0
    assert evaluated.semantic_support_status == "pending"


def test_adapter_is_not_imported_by_production_generation() -> None:
    production = Path("src/ai.py").read_text(encoding="utf-8")
    entrypoint = Path("streamlit_app.py").read_text(encoding="utf-8")

    assert "gemini_live_controlled_generation_evaluate" not in production
    assert "gemini_live_controlled_generation_evaluate" not in entrypoint


def test_live_execution_requires_exact_bounded_authorization() -> None:
    with pytest.raises(PermissionError, match="explicit live authorization"):
        require_live_authorization(None)
    with pytest.raises(PermissionError, match="explicit live authorization"):
        require_live_authorization("yes")

    require_live_authorization(LIVE_AUTHORIZATION_VALUE)


def test_evaluation_key_resolution_prefers_gemini_then_guide_fallback(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "GEMINI_API_KEY=primary-secret\nGUIDE_LLM_API_KEY=fallback-secret\n",
        encoding="utf-8",
    )
    assert (
        resolve_gemini_api_key(env_path=env_path, environ={}) == "primary-secret"
    )

    env_path.write_text("GUIDE_LLM_API_KEY=fallback-secret\n", encoding="utf-8")
    assert (
        resolve_gemini_api_key(env_path=env_path, environ={}) == "fallback-secret"
    )


def test_evaluation_key_resolution_fails_closed_without_supported_key(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("GUIDE_LLM_MODEL=production-model\n", encoding="utf-8")

    with pytest.raises(GeminiLiveError, match="missing_api_key"):
        resolve_gemini_api_key(env_path=env_path, environ={})


def test_bounded_sequence_stops_on_first_failure_and_never_exceeds_six() -> None:
    seen: list[str] = []

    def execute(case_id: str) -> str:
        seen.append(case_id)
        if case_id == PILOT_CASE_IDS[1]:
            raise RuntimeError("stop")
        return case_id

    with pytest.raises(RuntimeError, match="stop"):
        execute_bounded_sequence(PILOT_CASE_IDS, execute)

    assert seen == list(PILOT_CASE_IDS[:2])

    with pytest.raises(ValueError, match="maximum_six_cases"):
        execute_bounded_sequence((*PILOT_CASE_IDS, "CASE-007"), lambda value: value)


def test_ledger_is_append_only_and_rejects_raw_or_secret_fields(tmp_path: Path) -> None:
    ledger = tmp_path / "live_ledger.jsonl"
    safe = {
        "case_id": "CASE-001",
        "status": "completed",
        "request_count": 1,
        "retry_count": 0,
        "input_tokens": 30,
        "output_tokens": 12,
        "thought_tokens": 3,
        "latency_ms": 25.5,
        "candidate_sha256": "a" * 64,
        "validator_pass": True,
        "fallback_to_extractive": True,
    }

    append_raw_free_ledger_record(ledger, safe)
    append_raw_free_ledger_record(ledger, {**safe, "status": "review_source_updated"})

    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert [row["status"] for row in rows] == ["completed", "review_source_updated"]
    assert all("question" not in row and "response" not in row for row in rows)

    with pytest.raises(ValueError, match="ledger_fields"):
        append_raw_free_ledger_record(ledger, {**safe, "raw_response": "forbidden"})


def test_mock_pilot_updates_ignored_review_source_and_writes_only_raw_free_metadata(
    tmp_path: Path,
) -> None:
    case = _case()
    source_root = tmp_path / "local-only"
    source_path = source_root / "review_source.json"
    question_path = tmp_path / "questions.json"
    ledger_path = source_root / "live_ledger.jsonl"
    summary_path = source_root / "live_summary.json"
    question_path.write_text(
        json.dumps(
            {"cases": [{"case_id": case.case_id, "question": "현재 절차는?"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    write_review_source(
        source_path,
        [
            build_review_source_case(
                case_id=case.case_id,
                prompt_version="v1.0-natural-grounded",
                model="deterministic-source-unit-projection",
                candidate_status="deterministic_projection",
                extractive_answer="15분 동안 확인한다.",
                controlled_candidate="15분 동안 확인한다.",
                supporting_source_unit_ids=(("su001",),),
            )
        ],
        allowed_root=source_root,
    )
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=_interaction(
                {
                    "statements": [
                        {
                            "text": "15분  동안 확인한다.",
                            "supporting_source_unit_ids": ["su001"],
                        }
                    ]
                }
            ),
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        summary = run_live_pilot(
            api_key="test-key",
            source_root=source_root,
            source_path=source_path,
            question_fixture_path=question_path,
            ledger_path=ledger_path,
            summary_path=summary_path,
            prepared_cases=(case,),
            case_ids=(case.case_id,),
            client=http_client,
        )

    assert calls == 1
    assert summary["actual_http_requests"] == 1
    assert summary["retry_count"] == 0
    assert summary["usage"]["input_tokens"] == 30
    assert summary["case_results"][0]["validator_pass"] is True
    assert summary["case_results"][0]["candidate_changed"] is True
    catalog = load_review_source(
        source_path,
        question_fixture_path=question_path,
        expected_prompt_version="v1.0-natural-grounded",
    )
    assert catalog[case.case_id].model == GEMINI_MODEL
    assert catalog[case.case_id].candidate_status == "validated_live"

    safe_files = ledger_path.read_text(encoding="utf-8") + summary_path.read_text(
        encoding="utf-8"
    )
    assert catalog[case.case_id].question not in safe_files
    assert catalog[case.case_id].extractive_answer not in safe_files
    assert catalog[case.case_id].controlled_candidate not in safe_files
    assert "test-key" not in safe_files

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(ValueError, match="existing_live_ledger"):
            run_live_pilot(
                api_key="test-key",
                source_root=source_root,
                source_path=source_path,
                question_fixture_path=question_path,
                ledger_path=ledger_path,
                summary_path=summary_path,
                prepared_cases=(case,),
                case_ids=(case.case_id,),
                client=http_client,
            )
    assert calls == 1


def _fallback_case(paragraph_count: int) -> PreparedLiveCase:
    units = tuple(
        SourceUnit(
            source_unit_id=f"su{index:03d}",
            chunk_id=f"chunk-{index:03d}",
            source_order=(index, 0),
            branch="common",
            exact_text=f"fallback paragraph {index}",
            group_key=f"group-{index:03d}",
            required=True,
            selectable=True,
        )
        for index in range(1, paragraph_count + 1)
    )
    return PreparedLiveCase(
        case_id="CASE-FALLBACK",
        intent="summary",
        document_scope="internal-document-scope",
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


def _run_fallback_pilot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    unit_count: int,
    state: dict[str, object] | None = None,
) -> tuple[Path, Path, Path, bytes]:
    case = _fallback_case(unit_count)
    source_root = tmp_path / "local-only"
    source_path = source_root / "review_source.json"
    question_path = tmp_path / "questions.json"
    ledger_path = source_root / "live_ledger.jsonl"
    summary_path = source_root / "live_summary.json"
    extractive_answer = "\n\n".join(
        f"fallback paragraph {index}" for index in range(1, 9)
    )
    controlled_candidate = "\n\n".join(
        f"controlled paragraph {index}" for index in range(1, 8)
    )
    question_path.write_text(
        json.dumps(
            {"cases": [{"case_id": case.case_id, "question": "safe question"}]}
        ),
        encoding="utf-8",
    )
    write_review_source(
        source_path,
        [
            build_review_source_case(
                case_id=case.case_id,
                prompt_version="v1.0-natural-grounded",
                model="previous-model",
                candidate_status="validated_live",
                extractive_answer=extractive_answer,
                controlled_candidate=controlled_candidate,
                supporting_source_unit_ids=tuple(
                    (f"su{index:03d}",) for index in range(1, 8)
                ),
            )
        ],
        allowed_root=source_root,
    )
    original = source_path.read_bytes()
    if state is not None:
        state.update(
            source_path=source_path,
            ledger_path=ledger_path,
            original=original,
        )

    monkeypatch.setattr(
        gemini_live,
        "evaluate_gemini_candidate",
        lambda **_kwargs: GeminiCandidateEvaluation(
            case_id=case.case_id,
            validator_pass=False,
            fallback_to_extractive=True,
            publish_controlled=False,
            semantic_support_status="pending",
            retry_count=0,
            review_candidate=None,
            supporting_source_unit_ids=(("su001",),),
            validation={"failure_codes": ("mock_rejection",)},
        ),
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_interaction(
                {
                    "statements": [
                        {
                            "text": "rejected candidate",
                            "supporting_source_unit_ids": ["su001"],
                        }
                    ]
                }
            ),
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        gemini_live.run_live_pilot(
            api_key="secret-must-not-survive",
            source_root=source_root,
            source_path=source_path,
            question_fixture_path=question_path,
            ledger_path=ledger_path,
            summary_path=summary_path,
            prepared_cases=(case,),
            case_ids=(case.case_id,),
            client=http_client,
        )
    return source_path, ledger_path, summary_path, original


def test_fallback_snapshot_uses_its_own_eight_citation_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path, ledger_path, summary_path, _ = _run_fallback_pilot(
        tmp_path,
        monkeypatch,
        unit_count=8,
    )

    payload = json.loads(source_path.read_text(encoding="utf-8"))
    record = payload["cases"][0]
    assert record["candidate_status"] == "extractive_fallback"
    assert len(record["controlled_candidate"].split("\n\n")) == 8
    assert record["supporting_source_unit_ids"] == [
        [f"su{index:03d}"] for index in range(1, 9)
    ]
    persisted_metadata = ledger_path.read_text(
        encoding="utf-8"
    ) + summary_path.read_text(encoding="utf-8")
    assert "fallback paragraph" not in persisted_metadata
    assert "rejected candidate" not in persisted_metadata
    assert "secret-must-not-survive" not in persisted_metadata


def test_fallback_mapping_mismatch_preserves_existing_review_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state: dict[str, object] = {}
    with pytest.raises(ValueError, match="fallback_source_unit_mapping"):
        _run_fallback_pilot(
            tmp_path,
            monkeypatch,
            unit_count=7,
            state=state,
        )

    source_path = state["source_path"]
    assert isinstance(source_path, Path)
    assert source_path.read_bytes() == state["original"]
    ledger_path = state["ledger_path"]
    assert isinstance(ledger_path, Path)
    persisted_metadata = ledger_path.read_text(encoding="utf-8")
    assert "fallback paragraph" not in persisted_metadata
    assert "rejected candidate" not in persisted_metadata
    assert "secret-must-not-survive" not in persisted_metadata


def test_single_case_diagnostic_keeps_rejected_statements_for_local_review_only(
    tmp_path: Path,
) -> None:
    case = _case()
    source_root = tmp_path / "local-only"
    source_path = source_root / "review_source.json"
    question_path = tmp_path / "questions.json"
    diagnostic_path = source_root / "uat_t01_diagnostic.json"
    question_path.write_text(
        json.dumps(
            {"cases": [{"case_id": case.case_id, "question": "현재 절차는?"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    write_review_source(
        source_path,
        [
            build_review_source_case(
                case_id=case.case_id,
                prompt_version="v1.0-natural-grounded",
                model="deterministic-source-unit-projection",
                candidate_status="deterministic_projection",
                extractive_answer="15분 동안 확인한다.",
                controlled_candidate="15분 동안 확인한다.",
                supporting_source_unit_ids=(("su001",),),
            )
        ],
        allowed_root=source_root,
    )
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=_interaction(
                {
                    "statements": [
                        {
                            "text": "30분 동안 확인한다.",
                            "supporting_source_unit_ids": ["su001"],
                        }
                    ]
                }
            ),
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = run_single_case_diagnostic(
            api_key="secret-must-not-survive",
            source_root=source_root,
            source_path=source_path,
            question_fixture_path=question_path,
            diagnostic_path=diagnostic_path,
            prepared_cases=(case,),
            case_id=case.case_id,
            client=http_client,
        )

    assert calls == 1
    assert result["actual_http_requests"] == 1
    assert result["retry_count"] == 0
    assert result["first_failure_stage"] == "number_validation"
    assert result["failure_statement_index"] == 0
    assert result["fallback_to_extractive"] is True
    catalog = load_review_source(
        source_path,
        question_fixture_path=question_path,
        expected_prompt_version="v1.0-natural-grounded",
    )
    assert catalog[case.case_id].controlled_candidate == "30분 동안 확인한다."
    assert catalog[case.case_id].candidate_status == "diagnostic_rejected_live"
    assert "진단" in candidate_status_notice(catalog[case.case_id].candidate_status)
    assert "diagnostic rejected" in review_case_label(catalog[case.case_id])
    persisted = diagnostic_path.read_text(encoding="utf-8")
    assert "30분 동안 확인한다." in persisted
    assert "secret-must-not-survive" not in persisted
    assert "raw_response" not in persisted
    assert "provider metadata" not in persisted

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(ValueError, match="existing_diagnostic"):
            run_single_case_diagnostic(
                api_key="secret-must-not-survive",
                source_root=source_root,
                source_path=source_path,
                question_fixture_path=question_path,
                diagnostic_path=diagnostic_path,
                prepared_cases=(case,),
                case_id=case.case_id,
                client=http_client,
            )
    assert calls == 1
