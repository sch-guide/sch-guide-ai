import json

import httpx
import pytest

from mvp.evidence import SourceUnit
from tools.groq_live_ragas_evaluate import (
    GROQ_MODEL,
    GroqLiveCall,
    GroqLiveError,
    PreparedLiveCase,
    RequiredCoverageSlot,
    _execute_case,
    build_groq_live_payload,
    build_numeric_unit_diagnostic_record,
    build_request_local_source_units,
    build_request_local_units,
    build_required_coverage_slots,
    call_groq_live,
    enqueue_human_semantic_review,
    evaluate_live_safety,
    filter_approved_live_cases,
    numeric_unit_diagnostics,
    project_controlled_generation_units,
    reuse_human_reviewed_case,
    run_live_evaluation,
    safe_groq_error_diagnostic,
    safe_groq_error_summary,
    safe_live_call_record,
    scan_source_units_for_identifiers,
    select_representative_subset,
    synthetic_preflight_case,
)
from tools.provider_controlled_generation_evaluate import (
    NormalizedProviderResponse,
    build_provider_blueprints,
)
from tools.uat_s01_human_semantic_review import (
    build_human_review_record,
    build_reviewed_case_snapshot,
)


def _unit(text: str = "Synthetic guideline evidence.") -> SourceUnit:
    return SourceUnit(
        source_unit_id="su001",
        chunk_id="private-production-id",
        source_order=(1, 1),
        branch="common",
        exact_text=text,
        group_key="synthetic",
        required=True,
        selectable=True,
        phase="unspecified",
    )


def _blueprint(text: str = "Synthetic guideline evidence."):
    groq, _ = build_provider_blueprints(
        case_id="SYNTHETIC-001",
        intent="fact_specific",
        units=(_unit(text),),
        groq_model=GROQ_MODEL,
        gemini_model="unused",
    )
    return groq


def _workflow_unit(
    identifier: str,
    text: str,
    order: int,
    *,
    group: str = "workflow",
    phase: str = "unspecified",
    selectable: bool = True,
    chunk: str | None = None,
) -> SourceUnit:
    return SourceUnit(
        source_unit_id=identifier,
        chunk_id=chunk or f"private-{identifier}",
        source_order=(order, 1),
        branch="common",
        exact_text=text,
        group_key=group,
        required=True,
        selectable=selectable,
        phase=phase,
    )


def test_live_payload_is_strict_and_has_no_tools_or_reasoning():
    payload = build_groq_live_payload(_blueprint())

    assert payload["model"] == "openai/gpt-oss-20b"
    assert payload["temperature"] == 0
    assert payload["stream"] is False
    assert payload["include_reasoning"] is False
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert "tools" not in payload
    assert "tool_choice" not in payload
    assert "store" not in payload
    schema = payload["response_format"]["json_schema"]["schema"]
    assert schema["required"] == ["statements_by_source"]
    by_source = schema["properties"]["statements_by_source"]
    assert by_source["required"] == ["su001"]
    assert by_source["properties"] == {"su001": {"type": "string"}}
    assert "minItems" not in json.dumps(schema)
    assert "maxItems" not in json.dumps(schema)
    serialized = json.dumps(payload)
    assert "private-production-id" not in serialized
    assert "Do not write list numbers or step labels" in payload["messages"][0][
        "content"
    ]


@pytest.mark.parametrize(
    "text, expected_code",
    [
        ("환자 주민번호 900101-1234567", "resident_registration_number"),
        ("연락처 010-1234-5678", "phone_number"),
        ("병원번호 12345678", "hospital_identifier"),
        ("review@example.com", "email_address"),
    ],
)
def test_identifier_scan_blocks_sensitive_source_units(text, expected_code):
    assert expected_code in scan_source_units_for_identifiers((_unit(text),))


def test_identifier_scan_allows_clinical_duration_without_personal_identifier():
    assert scan_source_units_for_identifiers((_unit("15분 동안 관찰한다."),)) == ()


def test_live_call_uses_one_request_and_returns_normalized_response_without_logging_key():
    seen = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["calls"] += 1
        assert request.url == "https://api.groq.com/openai/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-secret-key"
        body = json.loads(request.content)
        assert body["include_reasoning"] is False
        return httpx.Response(
            200,
            json={
                "model": GROQ_MODEL,
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "statements_by_source": {
                                        "su001": "Synthetic guideline evidence."
                                    }
                                }
                            ),
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 8,
                    "total_tokens": 18,
                },
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = call_groq_live(
            _blueprint(),
            api_key="test-secret-key",
            client=client,
            timeout_seconds=5,
        )

    assert seen["calls"] == 1
    assert result.normalized.model == GROQ_MODEL
    assert result.normalized.usage["total_tokens"] == 18
    safe = safe_live_call_record(_blueprint(), result)
    serialized = json.dumps(safe)
    assert "test-secret-key" not in serialized
    assert "Synthetic guideline evidence." not in serialized
    assert "raw_response" not in serialized
    assert "messages" not in serialized


def test_live_call_fails_closed_without_retry_or_response_body_on_http_error():
    seen = {"calls": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        seen["calls"] += 1
        return httpx.Response(400, text="secret provider body")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GroqLiveError, match="provider_http_400") as error:
            call_groq_live(
                _blueprint(),
                api_key="test-secret-key",
                client=client,
                timeout_seconds=5,
            )

    assert seen["calls"] == 1
    assert "secret provider body" not in str(error.value)


def test_http_400_retains_only_allowlisted_provider_validation_diagnostics():
    raw_clinical_text = "PRIVATE HOSPITAL EVIDENCE 15 mL"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "type": "invalid_request_error",
                    "code": "json_validate_failed",
                    "param": "response_format.json_schema.schema",
                    "message": (
                        "Generated JSON does not match the expected schema. "
                        f"failed_generation={raw_clinical_text}"
                    ),
                    "failed_generation": raw_clinical_text,
                }
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GroqLiveError) as error:
            call_groq_live(
                _blueprint(),
                api_key="test-secret-key",
                client=client,
                timeout_seconds=5,
            )

    diagnostic = safe_groq_error_diagnostic(error.value)

    assert diagnostic == {
        "status_code": 400,
        "error_type": "invalid_request_error",
        "error_code": "json_validate_failed",
        "failing_field_path": "response_format.json_schema.schema",
        "validation_summary": "Generated JSON did not satisfy the requested schema.",
        "schema_sha256": diagnostic["schema_sha256"],
        "request_mode": "json_schema_strict",
        "model": "openai/gpt-oss-20b",
    }
    assert len(diagnostic["schema_sha256"]) == 64
    serialized = json.dumps(diagnostic)
    assert raw_clinical_text not in serialized
    assert "test-secret-key" not in serialized
    assert "failed_generation" not in serialized


def test_http_diagnostic_rejects_unsafe_provider_field_path_and_message():
    raw_clinical_text = "PRIVATE HOSPITAL EVIDENCE"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "type": "bad request with spaces",
                    "code": "bad/code",
                    "param": f"response_format {raw_clinical_text}",
                    "message": raw_clinical_text,
                }
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GroqLiveError) as error:
            call_groq_live(
                _blueprint(),
                api_key="test-secret-key",
                client=client,
                timeout_seconds=5,
            )

    diagnostic = safe_groq_error_diagnostic(error.value)

    assert diagnostic["error_type"] is None
    assert diagnostic["error_code"] is None
    assert diagnostic["failing_field_path"] is None
    assert diagnostic["validation_summary"] == "Provider rejected the request."
    assert raw_clinical_text not in json.dumps(diagnostic)


def test_live_call_classifies_connection_failure_without_exposing_details():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "sensitive local proxy detail",
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GroqLiveError, match="provider_connection") as error:
            call_groq_live(
                _blueprint(),
                api_key="test-secret-key",
                client=client,
                timeout_seconds=5,
            )

    assert safe_groq_error_summary(error.value) == (
        "Connection failed: DNS/TCP/proxy/firewall 상태를 확인하세요."
    )
    assert "sensitive local proxy detail" not in str(error.value)


@pytest.mark.parametrize(
    ("status", "summary"),
    [
        (400, "HTTP 400: request 또는 structured-output schema가 유효하지 않습니다."),
        (401, "HTTP 401: authentication failed."),
        (403, "HTTP 403: model/account access denied."),
        (404, "HTTP 404: endpoint 또는 model을 찾을 수 없습니다."),
        (422, "HTTP 422: provider validation error."),
        (429, "HTTP 429: rate limit exceeded."),
        (503, "HTTP 503: provider service unavailable."),
    ],
)
def test_http_errors_have_safe_user_facing_classification(status, summary):
    error = GroqLiveError(f"provider_http_{status}")

    assert safe_groq_error_summary(error) == summary


def test_filter_approved_live_cases_excludes_deferred_abstention_and_image():
    reviewed = {
        "cases": [
            {"case_id": "A", "final_gold_approved": True},
            {"case_id": "B", "final_gold_approved": False},
            {"case_id": "C", "final_gold_approved": True},
        ]
    }
    uat = {
        "cases": [
            {"case_id": "A", "document_scope": "sedation", "expected_evidence_type": "text"},
            {"case_id": "B", "document_scope": "transfusion", "expected_evidence_type": "text"},
            {"case_id": "C", "document_scope": "transfusion", "expected_evidence_type": "image"},
            {"case_id": "N", "document_scope": "out_of_scope", "expected_evidence_type": "pre_llm_block"},
        ]
    }

    assert [row["case_id"] for row in filter_approved_live_cases(reviewed, uat)] == ["A"]


def test_request_local_units_hide_original_evidence_ids_and_preserve_exact_text():
    units = build_request_local_units(
        [
            {"evidence_id": "production-chunk-a", "text": "First exact evidence."},
            {"evidence_id": "table-id:row-id", "text": "Header | exact table row."},
        ]
    )

    assert [unit.source_unit_id for unit in units] == ["su001", "su002"]
    assert [unit.exact_text for unit in units] == [
        "First exact evidence.",
        "Header | exact table row.",
    ]
    blueprint = build_provider_blueprints(
        case_id="CASE",
        intent="fact_specific",
        units=units,
        groq_model=GROQ_MODEL,
        gemini_model="unused",
    )[0]
    serialized = json.dumps(build_groq_live_payload(blueprint))
    assert "production-chunk-a" not in serialized
    assert "table-id:row-id" not in serialized


def test_preparation_projection_keeps_ordered_pre_steps_and_condition_only():
    catalog = (
        _workflow_unit("original-1", "1 | 동의서를 확인한다.", 100),
        _workflow_unit(
            "original-2",
            "2 | 혈액 불출 요청서를 준비한다.",
            101,
            chunk="ordered-row-2",
        ),
        _workflow_unit(
            "original-2-qualifier",
            "(혈액이 병동에 도착한 즉시, 수혈 시작 전)",
            101,
            selectable=False,
            chunk="ordered-row-2",
        ),
        _workflow_unit("original-3", "3 | 대상자에게 주의사항을 교육한다.", 102),
        _workflow_unit(
            "condition",
            "응급인 경우 혈액 불출 요청서를 동의서로 대체할 수 있다.",
            68,
            group="condition-parent",
        ),
        _workflow_unit(
            "reissue-heading",
            "재불출 절차",
            98,
            group="unrelated",
            selectable=False,
        ),
        _workflow_unit(
            "during-4",
            "4 | 수혈 시작 후 15분간 관찰한다.",
            103,
            phase="during",
        ),
        _workflow_unit(
            "after-5",
            "5 | 수혈 간호기록을 입력한다.",
            104,
            phase="unspecified",
        ),
    )

    projected = project_controlled_generation_units(
        catalog,
        intent="preparation",
        requested_phase="before",
    )

    assert [unit.exact_text for unit in projected] == [
        "1 | 동의서를 확인한다.",
        "2 | 혈액 불출 요청서를 준비한다. "
        "(혈액이 병동에 도착한 즉시, 수혈 시작 전)",
        "응급인 경우 혈액 불출 요청서를 동의서로 대체할 수 있다.",
        "3 | 대상자에게 주의사항을 교육한다.",
    ]
    assert all(unit.phase == "before" for unit in projected)


def test_full_procedure_projection_keeps_all_workflow_phases():
    catalog = (
        _workflow_unit("before", "1 | 시행 전에 확인한다.", 1, phase="before"),
        _workflow_unit("during", "2 | 시행 중 관찰한다.", 2, phase="during"),
        _workflow_unit("after", "3 | 시행 후 기록한다.", 3, phase="after"),
    )

    projected = project_controlled_generation_units(
        catalog,
        intent="procedure",
        requested_phase="all",
    )

    assert [unit.exact_text for unit in projected] == [
        "1 | 시행 전에 확인한다.",
        "2 | 시행 중 관찰한다.",
        "3 | 시행 후 기록한다.",
    ]


def test_request_local_atomic_units_preserve_projection_order_and_hide_chunk_ids():
    catalog = (
        _workflow_unit("original-a", "1 | 먼저 확인한다.", 3, phase="before"),
        _workflow_unit("original-b", "2 | 다음으로 준비한다.", 7, phase="before"),
    )

    units = build_request_local_source_units(catalog)

    assert [unit.source_unit_id for unit in units] == ["su001", "su002"]
    assert [unit.source_order for unit in units] == [(1, 1), (2, 1)]
    assert [unit.phase for unit in units] == ["before", "before"]
    assert all("private-" not in unit.chunk_id for unit in units)


def test_representative_subset_includes_text_and_structured_evidence_without_case_hardcode():
    cases = [
        {"case_id": "S1", "document_scope": "sedation", "expected_evidence_type": "text", "expected_intent": "fact"},
        {"case_id": "S2", "document_scope": "sedation", "expected_evidence_type": "text", "expected_intent": "procedure"},
        {"case_id": "T1", "document_scope": "transfusion", "expected_evidence_type": "text", "expected_intent": "preparation"},
        {"case_id": "T2", "document_scope": "transfusion", "expected_evidence_type": "table", "expected_intent": "fact"},
        {"case_id": "T3", "document_scope": "transfusion", "expected_evidence_type": "mixed", "expected_intent": "summary"},
        {"case_id": "T4", "document_scope": "transfusion", "expected_evidence_type": "text", "expected_intent": "procedure"},
    ]

    selected = select_representative_subset(cases, limit=5)

    assert len(selected) == 5
    assert {row["document_scope"] for row in selected} == {"sedation", "transfusion"}
    assert any(row["expected_evidence_type"] in {"table", "mixed"} for row in selected)


def test_live_safety_requires_citations_and_preserves_gold_numeric_fields():
    units = (_unit("Observe for 15 minutes and use 10 mL."),)
    blueprint = build_provider_blueprints(
        case_id="SAFE-001",
        intent="fact_specific",
        units=units,
        groq_model=GROQ_MODEL,
        gemini_model="unused",
    )[0]
    content = json.dumps(
        {
            "statements": [
                {
                    "text": "Observe for 15 minutes and use 10 mL.",
                    "supporting_source_unit_ids": ["su001"],
                }
            ]
        }
    )
    normalized = type("Normalized", (), {
        "model": GROQ_MODEL,
        "finish_reason": "stop",
        "content": content,
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    })()
    call = type("Call", (), {
        "normalized": normalized,
        "latency_ms": 1.0,
        "http_status": 200,
        "actual_external_calls": 1,
    })()

    safety = evaluate_live_safety(
        blueprint,
        call,
        units=units,
        intent="fact_specific",
        critical={
            "critical_facts": ["Observe"],
            "critical_numbers": ["15", "10"],
            "critical_units": ["minutes", "mL"],
            "critical_times": ["15 minutes"],
            "critical_conditions": [],
            "critical_negations": [],
            "critical_steps": [],
        },
    )

    assert safety["schema_pass"] is True
    assert safety["citation_pass"] is True
    assert safety["critical_fact_preservation"] is True
    assert safety["number_pass"] is True
    assert safety["unit_pass"] is True
    assert safety["time_pass"] is True
    assert safety["critical_safety_error_count"] == 0


def test_live_safety_fails_when_required_number_is_missing():
    units = (_unit("Observe for 15 minutes."),)
    blueprint = build_provider_blueprints(
        case_id="UNSAFE-001",
        intent="fact_specific",
        units=units,
        groq_model=GROQ_MODEL,
        gemini_model="unused",
    )[0]
    content = json.dumps(
        {
            "statements": [
                {
                    "text": "Observe the patient.",
                    "supporting_source_unit_ids": ["su001"],
                }
            ]
        }
    )
    normalized = type("Normalized", (), {
        "model": GROQ_MODEL,
        "finish_reason": "stop",
        "content": content,
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    })()
    call = type("Call", (), {
        "normalized": normalized,
        "latency_ms": 1.0,
        "http_status": 200,
        "actual_external_calls": 1,
    })()

    safety = evaluate_live_safety(
        blueprint,
        call,
        units=units,
        intent="fact_specific",
        critical={
            "critical_facts": [],
            "critical_numbers": ["15"],
            "critical_units": [],
            "critical_times": [],
            "critical_conditions": [],
            "critical_negations": [],
            "critical_steps": [],
        },
    )

    assert safety["number_pass"] is False
    assert safety["critical_safety_error_count"] == 1


def test_live_safety_rejects_present_actions_when_generated_out_of_order():
    units = (
        _workflow_unit("su001", "먼저 동의서를 확인한다.", 1),
        _workflow_unit("su002", "다음으로 물품을 준비한다.", 2),
    )
    blueprint = build_provider_blueprints(
        case_id="ORDER-001",
        intent="preparation",
        units=units,
        groq_model=GROQ_MODEL,
        gemini_model="unused",
    )[0]
    content = json.dumps(
        {
            "statements": [
                {
                    "text": "다음으로 물품을 준비한다.",
                    "supporting_source_unit_ids": ["su002"],
                },
                {
                    "text": "먼저 동의서를 확인한다.",
                    "supporting_source_unit_ids": ["su001"],
                },
            ]
        },
        ensure_ascii=False,
    )
    normalized = type(
        "Normalized",
        (),
        {
            "model": GROQ_MODEL,
            "finish_reason": "stop",
            "content": content,
            "usage": {},
        },
    )()
    call = type(
        "Call",
        (),
        {
            "normalized": normalized,
            "latency_ms": 1.0,
            "http_status": 200,
            "actual_external_calls": 1,
        },
    )()

    safety = evaluate_live_safety(
        blueprint,
        call,
        units=units,
        intent="preparation",
        critical={
            "critical_facts": [],
            "critical_numbers": [],
            "critical_units": [],
            "critical_times": [],
            "critical_conditions": [],
            "critical_negations": [],
            "critical_steps": [
                "먼저 동의서를 확인한다.",
                "다음으로 물품을 준비한다.",
            ],
        },
    )

    assert safety["action_order_pass"] is False
    assert safety["action_pass"] is False
    assert "action_pass" in safety["failure_codes"]


def test_live_safety_rejects_omitted_required_condition_without_relaxation():
    units = (_unit("응급인 경우 요청서를 동의서로 대체할 수 있다."),)
    blueprint = build_provider_blueprints(
        case_id="CONDITION-001",
        intent="preparation",
        units=units,
        groq_model=GROQ_MODEL,
        gemini_model="unused",
    )[0]
    content = json.dumps(
        {
            "statements": [
                {
                    "text": "요청서를 준비한다.",
                    "supporting_source_unit_ids": ["su001"],
                }
            ]
        },
        ensure_ascii=False,
    )
    normalized = type(
        "Normalized",
        (),
        {
            "model": GROQ_MODEL,
            "finish_reason": "stop",
            "content": content,
            "usage": {},
        },
    )()
    call = type(
        "Call",
        (),
        {
            "normalized": normalized,
            "latency_ms": 1.0,
            "http_status": 200,
            "actual_external_calls": 1,
        },
    )()

    safety = evaluate_live_safety(
        blueprint,
        call,
        units=units,
        intent="preparation",
        critical={
            "critical_facts": [],
            "critical_numbers": [],
            "critical_units": [],
            "critical_times": [],
            "critical_conditions": [
                "응급인 경우 요청서를 동의서로 대체할 수 있다."
            ],
            "critical_negations": [],
            "critical_steps": [],
        },
    )

    assert safety["condition_pass"] is False


def test_required_coverage_slots_bind_gold_requirements_to_request_local_sources():
    units = (
        _workflow_unit(
            "su001",
            "수혈 동의서를 확인하고 환자에게 주의사항을 교육한다.",
            1,
        ),
        _workflow_unit(
            "su002",
            "응급 수혈의 경우 요청서를 동의서로 대체할 수 있다.",
            2,
        ),
        _workflow_unit(
            "su003",
            "혈액이 병동에 도착한 즉시 수혈 시작 전 상태를 확인한다.",
            3,
        ),
        _workflow_unit(
            "su004",
            "손위생을 시행하고 수혈 물품을 준비하여 연결한다.",
            4,
        ),
    )

    slots = build_required_coverage_slots(
        {
            "critical_facts": [
                "수혈 동의서를 확인하고 환자에게 주의사항을 교육한다."
            ],
            "critical_conditions": [
                "응급 수혈의 경우 요청서를 동의서로 대체할 수 있다."
            ],
            "critical_times": ["혈액이 병동에 도착한 즉시", "수혈 시작 전"],
            "critical_steps": ["손위생 및 수혈 물품 준비"],
        },
        units,
    )

    assert [slot.category for slot in slots] == [
        "fact",
        "condition",
        "qualifier",
        "qualifier",
        "step",
    ]
    assert all(slot.supporting_source_unit_ids for slot in slots)
    assert slots[0].supporting_source_unit_ids == ("su001",)
    assert slots[1].supporting_source_unit_ids == ("su002",)
    assert slots[2].supporting_source_unit_ids == ("su003",)
    assert slots[3].supporting_source_unit_ids == ("su003",)
    assert slots[4].supporting_source_unit_ids == ("su004",)


def test_required_coverage_slots_fail_closed_when_no_source_can_support_requirement():
    with pytest.raises(GroqLiveError, match="required_coverage_unmapped"):
        build_required_coverage_slots(
            {"critical_conditions": ["근거에 없는 완전히 다른 조건"]},
            (_unit("동의서를 확인한다."),),
        )


def test_execute_case_blocks_unmapped_required_coverage_before_provider_call(monkeypatch):
    case = PreparedLiveCase(
        case_id="COVERAGE-BLOCKED",
        intent="preparation",
        document_scope="transfusion",
        evidence_type="text",
        units=(_unit("동의서를 확인한다."),),
        critical={"critical_facts": ["근거에 없는 사실"]},
        context_precision=0.0,
        context_recall=0.0,
        requested_phase="before",
        required_coverage_error="required_coverage_unmapped:critical_facts",
    )

    def unexpected_call(*args, **kwargs):
        raise AssertionError("provider must not be called")

    monkeypatch.setattr(
        "tools.groq_live_ragas_evaluate.call_groq_live", unexpected_call
    )

    with pytest.raises(GroqLiveError, match="required_coverage_unmapped"):
        _execute_case(case, api_key="not-used")


def _evaluate_required_slot(
    *,
    category: str,
    requirement: str,
    evidence: str,
    generated: str,
    requested_phase: str = "before",
):
    units = (_unit(evidence),)
    coverage = (
        RequiredCoverageSlot(
            slot_id=f"required_{category}_001",
            category=category,
            requirement=requirement,
            supporting_source_unit_ids=("su001",),
        ),
    )
    blueprint = build_provider_blueprints(
        case_id="COVERAGE-001",
        intent="preparation",
        units=units,
        groq_model=GROQ_MODEL,
        gemini_model="unused",
        requested_phase=requested_phase,
        preserve_source_order=True,
        required_coverage=(coverage[0].provider_descriptor(),),
    )[0]
    content = json.dumps(
        {
            "statements": [
                {
                    "text": generated,
                    "supporting_source_unit_ids": ["su001"],
                }
            ]
        },
        ensure_ascii=False,
    )
    normalized = type(
        "Normalized",
        (),
        {
            "model": GROQ_MODEL,
            "finish_reason": "stop",
            "content": content,
            "usage": {},
        },
    )()
    call = type(
        "Call",
        (),
        {
            "normalized": normalized,
            "latency_ms": 1.0,
            "http_status": 200,
            "actual_external_calls": 1,
        },
    )()
    return evaluate_live_safety(
        blueprint,
        call,
        units=units,
        intent="preparation",
        critical={
            "critical_facts": [],
            "critical_numbers": [],
            "critical_units": [],
            "critical_times": [],
            "critical_conditions": [],
            "critical_negations": [],
            "critical_steps": [],
        },
        required_coverage=coverage,
        requested_phase=requested_phase,
    )


@pytest.mark.parametrize(
    ("category", "requirement", "evidence", "generated"),
    [
        (
            "fact",
            "수혈 동의서를 확인하고 환자에게 주의사항을 교육한다.",
            "수혈 동의서를 확인하고 환자에게 주의사항을 교육한다.",
            "수혈 동의서를 확인한다.",
        ),
        (
            "condition",
            "응급 수혈의 경우 요청서를 동의서로 대체할 수 있다.",
            "응급 수혈의 경우 요청서를 동의서로 대체할 수 있다.",
            "응급 수혈의 경우 요청서를 준비한다.",
        ),
        (
            "qualifier",
            "혈액이 병동에 도착한 즉시 수혈 시작 전",
            "혈액이 병동에 도착한 즉시 수혈 시작 전 상태를 확인한다.",
            "수혈 전 상태를 확인한다.",
        ),
        (
            "step",
            "손위생을 시행하고 수혈 물품을 준비하여 연결한다.",
            "손위생을 시행하고 수혈 물품을 준비하여 연결한다.",
            "수혈 물품을 준비한다.",
        ),
    ],
)
def test_live_safety_rejects_each_missing_required_coverage_slot(
    category, requirement, evidence, generated
):
    safety = _evaluate_required_slot(
        category=category,
        requirement=requirement,
        evidence=evidence,
        generated=generated,
    )

    assert safety["required_coverage_pass"] is False
    assert safety["missing_required_slot_ids"] == [f"required_{category}_001"]
    assert safety["required_coverage_by_category"][category] is False
    assert "required_coverage_pass" in safety["failure_codes"]


def test_live_safety_rejects_during_or_post_content_for_before_phase():
    safety = _evaluate_required_slot(
        category="fact",
        requirement="수혈 전 물품을 준비한다.",
        evidence="수혈 전 물품을 준비한다.",
        generated="수혈 시작 후 물품을 준비한다.",
    )

    assert safety["phase_scope_pass"] is False
    assert "phase_scope_pass" in safety["failure_codes"]


def test_live_safety_passes_when_all_required_slots_and_phase_are_preserved():
    statement = "응급 수혈의 경우 수혈 시작 전 요청서를 동의서로 대체할 수 있다."
    safety = _evaluate_required_slot(
        category="condition",
        requirement=statement,
        evidence=statement,
        generated=statement,
    )

    assert safety["required_coverage_pass"] is True
    assert safety["missing_required_slot_ids"] == []
    assert safety["required_coverage_by_category"]["condition"] is True
    assert safety["phase_scope_pass"] is True
    assert safety["branch_phase_pass"] is True
    assert safety["critical_safety_error_count"] == 0


def test_synthetic_preflight_has_no_clinical_gold_fact_requirement():
    case = synthetic_preflight_case()

    assert case.document_scope == "synthetic"
    assert case.critical["critical_facts"] == ()


def test_execute_case_accepts_exact_hash_matched_human_semantic_review(monkeypatch):
    units = (_unit("승인 근거 문장입니다."),)
    case = PreparedLiveCase(
        case_id="UAT-S01",
        intent="fact_specific",
        document_scope="sedation",
        evidence_type="text",
        units=units,
        critical={
            "critical_facts": ["원문과 다른 표현의 의미 보존 사실"],
            "critical_numbers": [],
            "critical_units": [],
            "critical_times": [],
            "critical_conditions": [],
            "critical_negations": [],
            "critical_steps": [],
        },
        context_precision=1.0,
        context_recall=1.0,
    )
    content = json.dumps(
        {
            "statements": [
                {
                    "text": "승인 근거 문장입니다.",
                    "supporting_source_unit_ids": ["su001"],
                }
            ]
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    blueprint = build_provider_blueprints(
        case_id=case.case_id,
        intent=case.intent,
        units=case.units,
        groq_model=GROQ_MODEL,
        gemini_model="unused",
    )[0]
    review = build_human_review_record(
        case_id="UAT-S01",
        decision="PASS",
        reviewer="reviewer_1",
        generated_content=content,
        evidence_sha256=blueprint.evidence_fingerprint,
    )
    live = GroqLiveCall(
        NormalizedProviderResponse(
            provider="groq",
            model=GROQ_MODEL,
            finish_reason="stop",
            content=content,
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        ),
        latency_ms=1.0,
        http_status=200,
    )
    monkeypatch.setattr(
        "tools.groq_live_ragas_evaluate.call_groq_live",
        lambda *_args, **_kwargs: live,
    )

    row = _execute_case(case, api_key="test-key", human_review=review)

    assert row["critical_fact_preservation"] is True
    assert row["human_semantic_support_approved"] is True
    assert row["critical_safety_error_count"] == 0


def test_live_runner_makes_no_real_call_before_pass_review(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(
        "tools.groq_live_ragas_evaluate._execute_case",
        lambda *_args, **_kwargs: {
            "case_id": "SYNTHETIC-LIVE-002",
            "critical_safety_error_count": 0,
        },
    )

    def fail_if_prepared(**_kwargs):
        pytest.fail("real approved cases must not be prepared before PASS review")

    monkeypatch.setattr(
        "tools.groq_live_ragas_evaluate.prepare_approved_live_cases",
        fail_if_prepared,
    )

    summary = run_live_evaluation(
        output_dir=tmp_path / "output",
        human_review_path=tmp_path / "missing-review.json",
    )

    assert summary["status"] == "blocked_by_uat_s01_human_review"
    assert summary["synthetic_actual_calls"] == 1
    assert summary["real_actual_calls"] == 0


def test_live_runner_does_not_regenerate_reviewed_case_without_safe_snapshot(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(
        "tools.groq_live_ragas_evaluate._execute_case",
        lambda *_args, **_kwargs: {
            "case_id": "SYNTHETIC-LIVE-002",
            "critical_safety_error_count": 0,
        },
    )
    review_path = tmp_path / "review.json"
    review = build_human_review_record(
        case_id="UAT-S01",
        decision="PASS",
        reviewer="reviewer_1",
        generated_content=json.dumps(
            {
                "statements": [
                    {
                        "text": "Reviewed statement.",
                        "supporting_source_unit_ids": ["su001"],
                    }
                ]
            }
        ),
        evidence_sha256="e" * 64,
    )
    review_path.write_text(json.dumps(review), encoding="utf-8")

    def fail_if_prepared(**_kwargs):
        pytest.fail("UAT-S01 must not be regenerated without a safe snapshot")

    monkeypatch.setattr(
        "tools.groq_live_ragas_evaluate.prepare_approved_live_cases",
        fail_if_prepared,
    )

    summary = run_live_evaluation(
        output_dir=tmp_path / "output",
        human_review_path=review_path,
        human_review_snapshot_path=tmp_path / "missing-snapshot.json",
    )

    assert summary["status"] == "blocked_by_uat_s01_review_snapshot"
    assert summary["synthetic_actual_calls"] == 1
    assert summary["real_actual_calls"] == 0


def test_human_review_queue_persists_only_safe_hash_metadata(tmp_path):
    queue_path = tmp_path / "review-queue.json"
    row = {
        "case_id": "UAT-T01",
        "generated_answer_sha256": "a" * 64,
        "evidence_sha256": "b" * 64,
        "failure_codes": ["critical_fact_preservation"],
        "document_scope": "transfusion",
        "evidence_type": "text",
        "raw_answer": "must not persist",
        "raw_evidence": "must not persist",
    }

    enqueue_human_semantic_review(queue_path, row)

    saved = json.loads(queue_path.read_text(encoding="utf-8"))
    assert saved == {
        "cases": [
            {
                "case_id": "UAT-T01",
                "status": "needs_human_review",
                "generated_answer_sha256": "a" * 64,
                "evidence_sha256": "b" * 64,
                "failure_codes": ["critical_fact_preservation"],
                "document_scope": "transfusion",
                "evidence_type": "text",
            }
        ]
    }
    serialized = queue_path.read_text(encoding="utf-8")
    assert "must not persist" not in serialized


def test_human_review_queue_updates_case_without_erasing_other_cases(tmp_path):
    queue_path = tmp_path / "review-queue.json"
    enqueue_human_semantic_review(
        queue_path,
        {
            "case_id": "UAT-S03",
            "generated_answer_sha256": "1" * 64,
            "evidence_sha256": "2" * 64,
            "failure_codes": ["critical_fact_preservation"],
            "document_scope": "sedation",
            "evidence_type": "text",
        },
    )
    enqueue_human_semantic_review(
        queue_path,
        {
            "case_id": "UAT-T02",
            "generated_answer_sha256": "3" * 64,
            "evidence_sha256": "4" * 64,
            "failure_codes": ["critical_fact_preservation"],
            "document_scope": "transfusion",
            "evidence_type": "table",
        },
    )

    saved = json.loads(queue_path.read_text(encoding="utf-8"))
    assert [case["case_id"] for case in saved["cases"]] == ["UAT-S03", "UAT-T02"]


def test_numeric_unit_diagnostics_projects_only_clinical_statement_text():
    units = (
        _unit("Observe continuously."),
        SourceUnit(
            source_unit_id="su002",
            chunk_id="private-production-id-2",
            source_order=(2, 1),
            branch="common",
            exact_text="Record the result.",
            group_key="synthetic-2",
            required=True,
            selectable=True,
            phase="unspecified",
        ),
    )
    content = json.dumps(
        {
            "statements": [
                {
                    "text": "Observe continuously.",
                    "supporting_source_unit_ids": ["su001"],
                },
                {
                    "text": "Record the result.",
                    "supporting_source_unit_ids": ["su002"],
                },
            ]
        }
    )

    diagnostics = numeric_unit_diagnostics(content, units)

    assert [row["field"] for row in diagnostics] == [
        "statements[0].text",
        "statements[1].text",
    ]
    assert all(row["generated_tokens"] == [] for row in diagnostics)
    assert all(row["offending_tokens"] == [] for row in diagnostics)


def test_numeric_unit_diagnostics_ignores_only_leading_order_markers():
    units = (_unit("Prepare the verified equipment."),)
    content = json.dumps(
        {
            "statements": [
                {
                    "text": "3. Prepare the verified equipment.",
                    "supporting_source_unit_ids": ["su001"],
                },
                {
                    "text": "4. Prepare 50 mL/hr.",
                    "supporting_source_unit_ids": ["su001"],
                },
            ]
        }
    )

    diagnostics = numeric_unit_diagnostics(content, units)

    assert diagnostics[0]["generated_tokens"] == []
    assert diagnostics[0]["offending_tokens"] == []
    assert diagnostics[1]["generated_tokens"] == ["50ml/hr"]
    assert diagnostics[1]["offending_tokens"] == ["50ml/hr"]


def test_numeric_unit_diagnostics_reports_only_unsupported_clinical_tokens():
    units = (_unit("Observe the patient for 10 minutes."),)
    content = json.dumps(
        {
            "statements": [
                {
                    "text": "Observe the patient for 15 minutes at 50 mL/hr.",
                    "supporting_source_unit_ids": ["su001"],
                }
            ]
        }
    )

    diagnostics = numeric_unit_diagnostics(content, units)

    assert diagnostics == [
        {
            "statement_index": 0,
            "field": "statements[0].text",
            "supporting_source_unit_ids": ["su001"],
            "allowed_tokens": ["10"],
            "generated_tokens": ["15", "50ml/hr"],
            "offending_tokens": ["15", "50ml/hr"],
        }
    ]


def test_numeric_unit_diagnostic_record_captures_unsupported_number_only():
    units = (_unit("의료인 2인이 확인한다."),)
    content = json.dumps(
        {
            "statements": [
                {
                    "text": "의료인 3인이 확인한다.",
                    "supporting_source_unit_ids": ["su001"],
                }
            ]
        },
        ensure_ascii=False,
    )

    record = build_numeric_unit_diagnostic_record(
        case_id="TEST-NUMBER",
        content=content,
        units=units,
        answer_hash="a" * 64,
        evidence_hash="b" * 64,
    )

    assert record["validator_result"] == "FAIL"
    assert record["failure_code"] == "unsupported_number_or_unit"
    assert record["failed_statements"] == [
        {
            "statement_index": 0,
            "allowed_tokens": ["2"],
            "generated_tokens": ["3"],
            "offending_tokens": ["3"],
            "token_type": {"3": "number"},
        }
    ]


def test_numeric_unit_diagnostic_record_captures_unsupported_unit_expression():
    units = (_unit("50 mL/min으로 투여한다."),)
    content = json.dumps(
        {
            "statements": [
                {
                    "text": "50 mL/hr로 투여한다.",
                    "supporting_source_unit_ids": ["su001"],
                }
            ]
        },
        ensure_ascii=False,
    )

    record = build_numeric_unit_diagnostic_record(
        case_id="TEST-UNIT",
        content=content,
        units=units,
        answer_hash="c" * 64,
        evidence_hash="d" * 64,
    )

    assert record["validator_result"] == "FAIL"
    assert record["failed_statements"][0]["offending_tokens"] == ["50ml/hr"]
    assert record["failed_statements"][0]["token_type"] == {"50ml/hr": "unit"}


@pytest.mark.parametrize(
    "text",
    (
        "3. 수혈 전 검사",
        "3) 수혈 전 검사",
        "(3) 수혈 전 검사",
        "3단계: 수혈 전 검사",
        "Step 3: 수혈 전 검사",
        "단계 3: 수혈 전 검사",
    ),
)
def test_numeric_unit_diagnostic_record_excludes_presentation_order_markers(text):
    units = (_unit("수혈 전 검사"),)
    content = json.dumps(
        {
            "statements": [
                {
                    "text": text,
                    "supporting_source_unit_ids": ["su001"],
                }
            ]
        },
        ensure_ascii=False,
    )

    record = build_numeric_unit_diagnostic_record(
        case_id="TEST-ORDER",
        content=content,
        units=units,
        answer_hash="e" * 64,
        evidence_hash="f" * 64,
    )

    assert record["validator_result"] == "PASS"
    assert record["failed_statements"] == []


def test_numeric_unit_diagnostic_record_never_contains_raw_text_or_payload_fields():
    evidence = "PRIVATE_EVIDENCE 의료인 2인이 확인한다."
    generated = "PRIVATE_GENERATED 의료인 3인이 확인한다."
    units = (_unit(evidence),)
    content = json.dumps(
        {
            "statements": [
                {
                    "text": generated,
                    "supporting_source_unit_ids": ["su001"],
                }
            ]
        },
        ensure_ascii=False,
    )

    record = build_numeric_unit_diagnostic_record(
        case_id="TEST-SECURITY",
        content=content,
        units=units,
        answer_hash="1" * 64,
        evidence_hash="2" * 64,
    )
    serialized = json.dumps(record, ensure_ascii=False)

    assert evidence not in serialized
    assert generated not in serialized
    assert "PRIVATE_EVIDENCE" not in serialized
    assert "PRIVATE_GENERATED" not in serialized
    for forbidden in (
        "exact_text",
        "messages",
        "prompt",
        "raw_response",
        "Authorization",
        "api_key",
    ):
        assert forbidden not in serialized


def test_human_reviewed_case_is_reused_without_provider_call():
    case = PreparedLiveCase(
        case_id="UAT-S01",
        intent="fact_specific",
        document_scope="sedation",
        evidence_type="text",
        units=(_unit("Synthetic evidence."),),
        critical={"critical_facts": ["Synthetic fact."]},
        context_precision=1.0,
        context_recall=1.0,
    )
    blueprint = build_provider_blueprints(
        case_id=case.case_id,
        intent=case.intent,
        units=case.units,
        groq_model=GROQ_MODEL,
        gemini_model="unused",
    )[0]
    content = json.dumps(
        {
            "statements": [
                {
                    "text": "Synthetic reviewed statement.",
                    "supporting_source_unit_ids": ["su001"],
                }
            ]
        }
    )
    review = build_human_review_record(
        case_id="UAT-S01",
        decision="PASS",
        reviewer="reviewer_1",
        generated_content=content,
        evidence_sha256=blueprint.evidence_fingerprint,
    )
    snapshot = build_reviewed_case_snapshot(
        review=review,
        call_record={
            "case_id": "UAT-S01",
            "provider": "groq",
            "model": GROQ_MODEL,
            "generated_answer_sha256": review["generated_answer_sha256"],
            "evidence_sha256": blueprint.evidence_fingerprint,
            "latency_ms": 12.3,
            "http_status": 200,
        },
        safety={
            "schema_pass": True,
            "citation_pass": True,
            "critical_fact_preservation": False,
            "number_pass": True,
            "unit_pass": True,
            "time_pass": True,
            "condition_pass": True,
            "negation_pass": True,
            "action_pass": True,
            "validation_reason": "semantic_support_pending",
            "failure_codes": ["critical_fact_preservation"],
        },
    )

    row = reuse_human_reviewed_case(case, review=review, snapshot=snapshot)

    assert row["case_id"] == "UAT-S01"
    assert row["actual_external_calls"] == 0
    assert row["reviewed_provider_result_reused"] is True
    assert row["critical_fact_preservation"] is True
    assert row["deterministic_critical_fact_preservation"] is False
    assert row["human_semantic_support_approved"] is True
    assert row["failure_codes"] == []
    assert row["critical_safety_error_count"] == 0
