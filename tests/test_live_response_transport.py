"""실제 API 호출 없이 live response transport와 Groq envelope 경계를 재현합니다."""

from __future__ import annotations

import copy
import gzip
import json
import zlib
from pathlib import Path

import httpx
import pytest

from mvp.ai import Quota, generate
from mvp.library import Chunk, Hit
from mvp.settings import GuideError, Settings
from tools.rag_groq_evaluate import SingleCallTransport

FIXTURE = Path(__file__).parent / "fixtures" / "groq_strict_chat_completion.json"


def official_envelope():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def source():
    return Chunk(
        "chunk-1",
        "doc-1",
        "진정간호.pdf",
        3,
        "진정간호",
        "목적",
        None,
        "진정간호의 목적은 환자의 불안을 감소시키는 것입니다.",
        0,
    )


def settings():
    return Settings(
        llm_provider="groq_free",
        llm_key="test-only-key",
        llm_model="openai/gpt-oss-20b",
        llm_approved=True,
        groq_free_confirmed=True,
    )


def generate_with(transport, tmp_path, trace):
    chunk = source()
    return generate(
        settings(),
        "진정간호 목적은?",
        [Hit(chunk, 0.9)],
        "employee",
        Quota(tmp_path / "quota.db"),
        transport,
        trace=trace,
    )


def test_official_groq_chat_completion_fixture_passes_current_parser(tmp_path):
    envelope = official_envelope()
    trace = {}
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=envelope))

    answer, _ = generate_with(transport, tmp_path, trace)

    assert answer.answerable
    assert len(answer.statements) == 1
    assert trace["response_http_status"] == 200
    assert trace["response_model"] == "openai/gpt-oss-20b"
    assert trace["response_usage"] == {
        "prompt_tokens": 100,
        "completion_tokens": 40,
        "total_tokens": 140,
    }
    assert trace["finish_reason"] == "stop"
    assert trace["citation_assessment"] == "supported"
    assert trace["response_parse_stage"] == "complete"
    assert trace["response_json_succeeded"] is True
    assert trace["response_top_level_type"] == "object"
    assert trace["response_choices_present"] is True
    assert trace["response_choices_count"] == 1
    assert trace["response_choice0_type"] == "object"
    assert trace["response_finish_reason_present"] is True
    assert trace["response_message_present"] is True
    assert trace["response_message_type"] == "object"
    assert trace["response_content_present"] is True
    assert trace["response_content_type"] == "string"
    assert trace["response_content_char_count"] > 0
    assert trace["response_refusal_present"] is True
    assert trace["response_refusal_non_null"] is False


def test_completion_limit_with_length_finish_reason_is_ai_incomplete(tmp_path):
    envelope = copy.deepcopy(official_envelope())
    envelope["choices"][0]["finish_reason"] = "length"
    envelope["usage"]["completion_tokens"] = 2048
    envelope["usage"]["total_tokens"] = envelope["usage"]["prompt_tokens"] + 2048
    trace = {}
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=envelope))

    with pytest.raises(GuideError, match="AI_INCOMPLETE"):
        generate_with(transport, tmp_path, trace)

    assert trace["response_http_status"] == 200
    assert trace["finish_reason"] == "length"
    assert trace["response_usage"]["completion_tokens"] == 2048
    assert trace["llm_error_code"] == "AI_INCOMPLETE"
    assert trace["response_parse_stage"] == "finish"


def test_reading_compressed_response_consumes_and_decodes_body():
    encoded = json.dumps(official_envelope()).encode()
    response = httpx.Response(
        200,
        headers={"content-encoding": "gzip", "content-type": "application/json"},
        content=gzip.compress(encoded),
    )

    decoded = response.read()

    assert response.is_stream_consumed
    assert decoded == encoded
    assert response.content == encoded
    assert response.headers["content-encoding"] == "gzip"


def encoded_transport(encoding, selected_ids):
    envelope = official_envelope()
    encoded = json.dumps(envelope).encode()
    headers = {"content-type": "application/json"}
    body = encoded
    if encoding == "gzip":
        headers["content-encoding"] = "gzip"
        body = gzip.compress(encoded)
    elif encoding == "deflate":
        headers["content-encoding"] = "deflate"
        body = zlib.compress(encoded)
    inner = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers=headers,
            stream=httpx.ByteStream(body),
            request=request,
        )
    )
    return SingleCallTransport("openai/gpt-oss-20b", selected_ids, inner=inner)


@pytest.mark.parametrize("encoding", [None, "gzip", "deflate"])
def test_evaluation_transport_preserves_response_stream_for_generate(
    encoding,
    tmp_path,
):
    transport = encoded_transport(encoding, {"chunk-1"})
    trace = {}

    answer, _ = generate_with(transport, tmp_path, trace)

    assert answer.answerable
    assert transport.calls == 1
    assert transport.status_code == 200
    assert trace["response_parse_stage"] == "complete"
    assert trace["response_json_succeeded"] is True
    assert trace["finish_reason"] == "stop"


def malformed_envelope(case):
    envelope = copy.deepcopy(official_envelope())
    choice = envelope["choices"][0]
    message = choice["message"]
    if case == "choices_missing":
        del envelope["choices"]
    elif case == "choices_empty":
        envelope["choices"] = []
    elif case == "choices_type":
        envelope["choices"] = {}
    elif case == "choice_type":
        envelope["choices"][0] = []
    elif case == "finish_missing":
        del choice["finish_reason"]
    elif case == "message_missing":
        del choice["message"]
    elif case == "message_type":
        choice["message"] = []
    elif case == "content_missing":
        del message["content"]
    elif case == "content_type":
        message["content"] = []
    elif case == "refusal":
        message["refusal"] = "fixture refusal"
    return envelope


@pytest.mark.parametrize(
    ("case", "detail", "stage"),
    [
        ("choices_missing", "choices_missing", "choices"),
        ("choices_empty", "choices_empty", "choices"),
        ("choices_type", "choices_type", "choices"),
        ("choice_type", "choice_type", "choice"),
        ("finish_missing", "finish_missing", "finish"),
        ("message_missing", "message_missing", "message"),
        ("message_type", "message_type", "message"),
        ("content_missing", "content_missing", "content"),
        ("content_type", "content_type", "content"),
        ("refusal", "refusal", "message"),
    ],
)
def test_malformed_envelope_has_safe_failure_detail(case, detail, stage, tmp_path):
    envelope = malformed_envelope(case)
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=envelope))
    trace = {}

    with pytest.raises(GuideError, match="AI_RESPONSE"):
        generate_with(transport, tmp_path, trace)

    assert trace["llm_error_code"] == "AI_RESPONSE"
    assert trace["response_failure_detail"] == detail
    assert trace["response_parse_stage"] == stage
    assert not any(
        value == official_envelope()["choices"][0]["message"]["content"]
        for value in trace.values()
    )


@pytest.mark.parametrize(
    ("response", "json_succeeded", "top_type", "detail"),
    [
        (httpx.Response(200, text="not-json"), False, None, "json_decode"),
        (httpx.Response(200, json=[]), True, "array", "top_level_type"),
    ],
)
def test_json_and_top_level_failures_have_safe_shape(
    response,
    json_succeeded,
    top_type,
    detail,
    tmp_path,
):
    trace = {}
    transport = httpx.MockTransport(lambda _: response)

    with pytest.raises(GuideError, match="AI_RESPONSE"):
        generate_with(transport, tmp_path, trace)

    assert trace["response_json_succeeded"] is json_succeeded
    assert trace.get("response_top_level_type") == top_type
    assert trace["response_failure_detail"] == detail
