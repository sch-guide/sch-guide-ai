import hashlib
import json

import httpx
import pytest

from tools.rag_procedure_live_evaluate import OneCallTransport


def _request(source_units):
    envelope = {"schema_version": 6, "source_units": source_units}
    payload = {
        "model": "openai/gpt-oss-20b",
        "messages": [
            {"role": "system", "content": "fixture"},
            {
                "role": "user",
                "content": (
                    "Question:\nfixture\nEvidence groups (JSON):\n"
                    + json.dumps(envelope, ensure_ascii=False)
                ),
            },
        ],
    }
    return httpx.Request(
        "POST", "https://api.groq.com/openai/v1/chat/completions", json=payload
    )


def test_facet_live_transport_validates_source_hashes_before_single_call():
    source_units = [{"id": "su001", "text": "검증용 원문 문장이다."}]
    expected = {
        "su001": hashlib.sha256(source_units[0]["text"].encode()).hexdigest()
    }
    inner_calls = []

    def handle(request):
        inner_calls.append(request)
        return httpx.Response(200, request=request, json={"ok": True})

    transport = OneCallTransport(
        "openai/gpt-oss-20b",
        {"chunk-allowed"},
        expected_source_unit_fingerprints=expected,
        inner=httpx.MockTransport(handle),
    )

    response = transport.handle_request(_request(source_units))

    assert response.status_code == 200
    assert transport.calls == 1
    assert len(inner_calls) == 1


def test_facet_live_transport_rejects_unapproved_text_before_external_call():
    approved = "승인된 원문 문장이다."
    expected = {"su001": hashlib.sha256(approved.encode()).hexdigest()}
    inner_calls = []
    transport = OneCallTransport(
        "openai/gpt-oss-20b",
        {"chunk-allowed"},
        expected_source_unit_fingerprints=expected,
        inner=httpx.MockTransport(lambda request: inner_calls.append(request)),
    )

    with pytest.raises(RuntimeError, match="source units differ"):
        transport.handle_request(_request([{"id": "su001", "text": "다른 원문"}]))

    assert transport.calls == 0
    assert inner_calls == []


def test_facet_live_transport_rejects_second_request_without_forwarding():
    source_units = [{"id": "su001", "text": "검증용 원문 문장이다."}]
    expected = {
        "su001": hashlib.sha256(source_units[0]["text"].encode()).hexdigest()
    }
    inner_calls = []

    def handle(request):
        inner_calls.append(request)
        return httpx.Response(200, request=request, json={"ok": True})

    transport = OneCallTransport(
        "openai/gpt-oss-20b",
        {"chunk-allowed"},
        expected_source_unit_fingerprints=expected,
        inner=httpx.MockTransport(handle),
    )
    request = _request(source_units)

    transport.handle_request(request)
    with pytest.raises(RuntimeError, match="second request"):
        transport.handle_request(request)

    assert transport.calls == 1
    assert len(inner_calls) == 1
