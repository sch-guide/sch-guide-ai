"""Groq strict Structured Outputs 계약을 실제 외부 호출 없이 검증합니다."""

import json

import httpx
import pytest

from mvp.ai import (
    AI_VERSION,
    GROQ_REQUEST_TOKEN_BUDGET,
    OUTPUT_LIMIT,
    RESPONSE_SELECTION_SCHEMA_VERSION,
    BranchAwareSelectionContract,
    Quota,
    SelectionGroupSlot,
    answer_text,
    generate,
    groq_answer_json_schema,
    validate_answer,
)
from mvp.library import NO_GUIDELINE, Chunk, Hit
from mvp.settings import GuideError, Settings


def source():
    return Chunk(
        "chunk-1", "doc-1", "가상교육실.pdf", 3, "가상 교육실 안내", "교육실 사용 전",
        None, "교육실 사용 전 예약 확인표를 확인합니다.", 0,
    )


def groq_settings():
    return Settings(
        llm_provider="groq_free",
        llm_key="test-only-key",
        llm_model="openai/gpt-oss-20b",
        llm_approved=True,
        groq_free_confirmed=True,
    )


def strict_content(chunk, *, quote=None):
    return json.dumps({
        "answerable": True,
        "statements": [{
            "text": chunk.text,
            "evidence": [{"chunk_id": chunk.id, "quote": quote or chunk.text}],
            "label": "",
        }],
        "format": "paragraph",
        "conflict": False,
    }, ensure_ascii=False)


def contract():
    return BranchAwareSelectionContract((SelectionGroupSlot(
        prompt_group_id="g1",
        group_key="doc-1:chunk-1",
        branch="common",
        required=True,
        selectable_source_unit_ids=("su001",),
        source_order=0,
    ),))


def selection_content(*ids):
    return json.dumps({
        "group_selections": {"g1": list(ids)},
    })


def assert_strict_objects(node):
    if isinstance(node, dict):
        if node.get("type") == "object":
            assert set(node.get("required", [])) == set(node.get("properties", {}))
            assert node.get("additionalProperties") is False
        assert "default" not in node
        for value in node.values():
            assert_strict_objects(value)
    elif isinstance(node, list):
        for value in node:
            assert_strict_objects(value)


def test_groq_schema_is_derived_closed_required_and_does_not_change_runtime_defaults():
    schema = groq_answer_json_schema(contract())

    assert_strict_objects(schema)
    groups = schema["properties"]["group_selections"]
    assert set(groups["properties"]) == {"g1"}
    assert groups["properties"]["g1"]["items"]["enum"] == ["su001"]
    assert set(groups["properties"]["g1"]) == {"type", "items"}
    schema["properties"]["group_selections"]["type"] = "string"
    fresh = groq_answer_json_schema(contract())
    assert fresh["properties"]["group_selections"]["type"] == "object"
    assert set(fresh["properties"]) == {"group_selections"}


def test_strict_valid_response_passes_and_traces_safe_metadata(tmp_path):
    chunk = source()
    trace = {}

    def handle(request):
        payload = json.loads(request.content)
        response_format = payload["response_format"]
        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["name"] == "schat_group_source_unit_selection"
        assert response_format["json_schema"]["strict"] is True
        assert_strict_objects(response_format["json_schema"]["schema"])
        assert payload["reasoning_effort"] == "low"
        assert payload["max_completion_tokens"] == 2048
        assert "reasoning_format" not in payload
        assert "tools" not in payload and "tool_choice" not in payload and "stream" not in payload
        envelope = json.loads(
            payload["messages"][1]["content"].split("Evidence groups (JSON):\n", 1)[1]
        )
        group = envelope["groups"][0]
        unit = next(
            unit for source in group["sources"] for unit in source["units"]
            if unit["selectable"]
        )
        content = json.dumps({
            "group_selections": {group["group_id"]: [unit["id"]]},
        })
        return httpx.Response(200, json={
            "model": "openai/gpt-oss-20b",
            "choices": [{"finish_reason": "stop", "message": {"content": content}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50},
        })

    answer, _ = generate(
        groq_settings(), "교육실 사용 전 확인 사항", [Hit(chunk, .9)], "employee",
        Quota(tmp_path / "quota.db"), httpx.MockTransport(handle), trace=trace,
    )

    assert answer.answerable
    assert trace["response_http_status"] == 200
    assert trace["response_model"] == "openai/gpt-oss-20b"
    assert trace["response_usage"] == {
        "prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50,
    }
    assert trace["finish_reason"] == "stop"
    assert trace["stage"] == "complete"
    assert "response_content" not in trace and "raw_response" not in trace


def test_finish_reason_length_is_ai_incomplete_before_validation(tmp_path):
    chunk = source()
    trace = {}
    payload = {
        "model": "openai/gpt-oss-20b",
        "choices": [{"finish_reason": "length", "message": {"content": selection_content("su001")}}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 2048, "total_tokens": 2068},
    }

    with pytest.raises(GuideError, match="AI_INCOMPLETE"):
        generate(
            groq_settings(), "교육실 사용 전 확인 사항", [Hit(chunk, .9)], "employee",
            Quota(tmp_path / "quota.db"), httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
            trace=trace,
        )

    assert trace["finish_reason"] == "length"
    assert trace["llm_error_code"] == "AI_INCOMPLETE"
    assert trace["stage"] == "llm_request"


def test_output_and_request_admission_limits_are_the_approved_values():
    assert OUTPUT_LIMIT == 2048
    assert GROQ_REQUEST_TOKEN_BUDGET == 5120
    assert AI_VERSION == 19
    assert RESPONSE_SELECTION_SCHEMA_VERSION == 5


@pytest.mark.parametrize("response", [
    httpx.Response(200, text="not-json"),
    httpx.Response(200, json={"model": "openai/gpt-oss-20b", "choices": []}),
])
def test_malformed_response_is_ai_response(tmp_path, response):
    chunk = source()
    trace = {}

    with pytest.raises(GuideError, match="AI_RESPONSE"):
        generate(
            groq_settings(), "교육실 사용 전 확인 사항", [Hit(chunk, .9)], "employee",
            Quota(tmp_path / "quota.db"), httpx.MockTransport(lambda _: response), trace=trace,
        )

    assert trace["llm_error_code"] == "AI_RESPONSE"
    assert "response_content" not in trace and "raw_response" not in trace


def test_schema_valid_wrong_citation_keeps_existing_safe_block(tmp_path):
    chunk = source()
    trace = {}

    with pytest.raises(GuideError, match="AI_EVIDENCE"):
        validate_answer(
            strict_content(chunk, quote="원문에 없는 잘못된 인용입니다."),
            [Hit(chunk, .9)], trace=trace,
        )

    assert trace["validation_reason"] == "citation"


def test_q006_keeps_zero_transport_calls():
    calls = 0

    def reject(_):
        nonlocal calls
        calls += 1
        pytest.fail("Q006 must not call Groq")

    answer, _ = generate(
        groq_settings(), "화성 우주선의 궤도 계산 공식은?", [], "employee",
        transport=httpx.MockTransport(reject),
    )

    assert calls == 0
    assert answer_text(answer) == NO_GUIDELINE
