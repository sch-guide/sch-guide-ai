from __future__ import annotations

import importlib
import json
from pathlib import Path

import httpx
import numpy as np
import pytest


def evaluator_module():
    try:
        return importlib.import_module("tools.gemini_chromadb_only_evaluate")
    except ModuleNotFoundError:
        pytest.fail("Gemini ChromaDB-only evaluator is not implemented")


def test_preflight_is_raw_free_and_reports_the_complete_request_plan(tmp_path: Path) -> None:
    evaluator = evaluator_module()
    documents = [
        evaluator.EmbeddingInput(
            input_id="chunk-1",
            input_kind="document",
            document_id="doc-1",
            value="title: none | text: private hospital procedure",
        ),
        evaluator.EmbeddingInput(
            input_id="chunk-2",
            input_kind="document",
            document_id="doc-1",
            value="title: none | text: second private procedure",
        ),
    ]
    queries = [
        evaluator.EmbeddingInput(
            input_id="case-1",
            input_kind="query",
            document_id="doc-1",
            value="task: search result | query: private clinical question",
        )
    ]

    audit = evaluator.build_preflight_audit(documents, queries)
    evaluator.write_preflight_reports(audit, tmp_path)
    serialized = json.dumps(audit, ensure_ascii=False)

    assert audit["transmission_plan"] == {
        "document_chunk_count": 2,
        "query_count": 1,
        "planned_api_request_count": 3,
        "one_input_per_request": True,
    }
    assert audit["documents"] == [{"document_id": "doc-1", "chunk_count": 2}]
    assert audit["metadata_transmission"] == {
        "document_id": False,
        "chunk_id": False,
        "page": False,
        "section": False,
        "title": False,
    }
    assert audit["provider"]["endpoint"].endswith("models/gemini-embedding-2:embedContent")
    assert audit["provider"]["model"] == "gemini-embedding-2"
    assert audit["provider"]["output_dimensionality"] == 3072
    assert audit["storage_policy"]["request_payload_stored"] is False
    assert audit["storage_policy"]["provider_response_raw_stored"] is False
    assert audit["retry_policy"]["duplicate_transmission_possible"] is True
    assert "private hospital procedure" not in serialized
    assert "private clinical question" not in serialized
    assert (tmp_path / "preflight_audit.json").is_file()
    assert (tmp_path / "preflight_audit.html").is_file()
    assert "private" not in (tmp_path / "preflight_audit.html").read_text(encoding="utf-8")


def test_preflight_reports_only_identifier_categories_not_matched_values() -> None:
    evaluator = evaluator_module()
    raw_value = "환자번호 12345678, 직원번호 AB-1234, 예시병원 내부문서 HSP-2026-77"
    inputs = [
        evaluator.EmbeddingInput(
            input_id="chunk-1",
            input_kind="document",
            document_id="doc-1",
            value=raw_value,
        )
    ]

    audit = evaluator.build_preflight_audit(inputs, [])
    serialized = json.dumps(audit, ensure_ascii=False)

    assert audit["identifier_scan"]["patient_or_staff_identifier"]["flagged_input_count"] == 1
    assert audit["identifier_scan"]["hospital_or_internal_identifier"]["flagged_input_count"] == 1
    assert "12345678" not in serialized
    assert "AB-1234" not in serialized
    assert "예시병원" not in serialized


def test_mock_transport_receives_only_supported_embedding_2_fields() -> None:
    evaluator = evaluator_module()
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["payload"] = json.loads(request.content)
        values = [1.0] + [0.0] * 3071
        return httpx.Response(
            200,
            json={
                "embedding": {"values": values},
                "usageMetadata": {"promptTokenCount": 17, "totalTokenCount": 17},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = evaluator.GeminiEmbeddingClient(
            api_key="test-secret",
            client=client,
            sleep=lambda _seconds: None,
        ).embed("task: search result | query: synthetic input")

    assert seen["url"] == evaluator.EMBEDDING_ENDPOINT
    assert seen["payload"] == {
        "content": {"parts": [{"text": "task: search result | query: synthetic input"}]},
        "outputDimensionality": 3072,
    }
    assert "taskType" not in seen["payload"]
    assert seen["headers"]["x-goog-api-key"] == "test-secret"
    assert result.prompt_tokens == 17
    assert result.attempt_count == 1
    assert result.values.shape == (3072,)
    assert np.linalg.norm(result.values) == pytest.approx(1.0)


def test_retry_accounting_exposes_possible_duplicate_transmission() -> None:
    evaluator = evaluator_module()
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(
            200,
            json={
                "embedding": {"values": [1.0] + [0.0] * 3071},
                "usageMetadata": {"promptTokenCount": 5, "totalTokenCount": 5},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = evaluator.GeminiEmbeddingClient(
            api_key="test-secret",
            client=client,
            max_attempts=2,
            sleep=lambda _seconds: None,
        ).embed("synthetic")

    assert attempts == 2
    assert result.attempt_count == 2
    assert result.duplicate_transmission_possible is True


def test_live_execution_requires_a_separate_explicit_authorization() -> None:
    evaluator = evaluator_module()

    with pytest.raises(PermissionError, match="external hospital-data transmission"):
        evaluator.require_live_authorization(execute_live=True, authorization_value=None)
    with pytest.raises(PermissionError, match="external hospital-data transmission"):
        evaluator.require_live_authorization(execute_live=True, authorization_value="yes")

    evaluator.require_live_authorization(
        execute_live=True,
        authorization_value=evaluator.LIVE_AUTHORIZATION_VALUE,
    )
    evaluator.require_live_authorization(execute_live=False, authorization_value=None)


def test_repository_preflight_loads_the_frozen_evaluation_set_without_external_calls(
    tmp_path: Path,
) -> None:
    evaluator = evaluator_module()

    audit = evaluator.build_repository_preflight()
    evaluator.write_preflight_reports(audit, tmp_path)

    assert audit["transmission_plan"] == {
        "document_chunk_count": 147,
        "query_count": 21,
        "planned_api_request_count": 168,
        "one_input_per_request": True,
    }
    assert sum(row["chunk_count"] for row in audit["documents"]) == 147
    assert len(audit["documents"]) == 2
    assert audit["external_api_calls_performed"] == 0
    assert audit["source_contract"]["approved_positive_case_count"] == 21
    assert audit["source_contract"]["production_changed"] is False
    assert audit["source_contract"]["gold_or_fixture_changed"] is False


def test_embedding_batch_keeps_only_vectors_and_usage_metadata() -> None:
    evaluator = evaluator_module()
    inputs = [
        evaluator.EmbeddingInput("document:c1", "document", "doc-1", "private source one"),
        evaluator.EmbeddingInput("query:q1", "query", "scope-1", "private question one"),
    ]
    seen: list[str] = []

    class FakeClient:
        def embed(self, value: str):
            seen.append(value)
            return evaluator.EmbeddingResult(
                values=np.asarray([1.0] + [0.0] * 3071, dtype=np.float32),
                prompt_tokens=7,
                total_tokens=7,
                attempt_count=1,
                duplicate_transmission_possible=False,
            )

    batch = evaluator.embed_inputs(FakeClient(), inputs)

    assert seen == ["private source one", "private question one"]
    assert batch.vectors.shape == (2, 3072)
    assert batch.prompt_tokens == 14
    assert batch.total_tokens == 14
    assert batch.actual_api_request_count == 2
    assert batch.retry_count == 0
    assert len(batch.latency_ms) == 2
    assert not hasattr(batch, "request_payloads")
    assert not hasattr(batch, "provider_responses")


def test_minilm_comparison_is_raw_free_and_uses_hit_at_10_success() -> None:
    evaluator = evaluator_module()

    def report(hit_by_case: dict[str, float], hit_at_10: float) -> dict:
        return {
            "summary": {
                "metrics": {
                    "overall": {
                        "case_count": 2,
                        "hit_at_1": 0.0,
                        "hit_at_3": 0.0,
                        "hit_at_5": 0.0,
                        "hit_at_10": hit_at_10,
                        "mrr": 0.1,
                        "recall_at_10": 0.2,
                        "precision_at_10": 0.05,
                        "ragas_id_context_precision": 0.05,
                        "ragas_id_context_recall": 0.2,
                    }
                }
            },
            "per_case": {
                "cases": [
                    {"case_id": case_id, "metrics": {"hit_at_10": hit}}
                    for case_id, hit in hit_by_case.items()
                ]
            },
        }

    comparison = evaluator.build_minilm_comparison(
        gemini=report({"C1": 1.0, "C2": 0.0}, 0.5),
        minilm=report({"C1": 0.0, "C2": 1.0}, 0.5),
    )

    assert comparison["success_definition"] == "hit_at_10 == 1.0"
    assert comparison["gemini_only_success_case_ids"] == ["C1"]
    assert comparison["minilm_only_success_case_ids"] == ["C2"]
    assert comparison["both_failed_case_ids"] == []
    assert comparison["metric_delta_gemini_minus_minilm"]["hit_at_10"] == 0.0
    assert "private clinical question" not in json.dumps(comparison).casefold()


def test_live_preflight_guard_rejects_any_input_manifest_drift() -> None:
    evaluator = evaluator_module()
    approved = {
        "transmission_plan": {"document_chunk_count": 147, "query_count": 21},
        "identifier_scan": {
            "patient_or_staff_identifier": {"flagged_input_count": 0},
            "hospital_or_internal_identifier": {"flagged_input_count": 0},
        },
        "source_contract": {"catalog_sha256": "a", "uat_fixture_sha256": "b"},
        "input_manifest": [{"input_id": "document:c1", "sha256": "abc"}],
    }
    current = json.loads(json.dumps(approved))
    evaluator.validate_live_preflight(current=current, approved=approved)
    current["input_manifest"][0]["sha256"] = "changed"

    with pytest.raises(ValueError, match="preflight approval drift"):
        evaluator.validate_live_preflight(current=current, approved=approved)


def test_live_runtime_dependency_check_can_fail_before_any_embedding_call() -> None:
    evaluator = evaluator_module()

    with pytest.raises(RuntimeError, match="chromadb"):
        evaluator.require_live_runtime(find_spec=lambda _name: None)


def test_checkpoint_resume_skips_28_successes_and_finishes_168_without_duplicates(
    tmp_path: Path,
) -> None:
    evaluator = evaluator_module()
    inputs = [
        evaluator.EmbeddingInput(
            input_id=f"document:c{index}",
            input_kind="document" if index < 147 else "query",
            document_id="scope",
            value=f"private-input-{index}",
        )
        for index in range(168)
    ]

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def embed(self, value: str):
            self.calls.append(value)
            vector = np.zeros(3072, dtype=np.float32)
            vector[int(evaluator._sha256(value)[:8], 16) % 3072] = 1.0
            return evaluator.EmbeddingResult(
                values=vector,
                prompt_tokens=1,
                total_tokens=1,
                attempt_count=1,
                duplicate_transmission_possible=False,
            )

    store = evaluator.ResumeCheckpointStore(tmp_path, maximum_requests=168)
    first = FakeClient()
    with pytest.raises(evaluator.SimulatedCheckpointCrash):
        evaluator.embed_inputs_resumable(
            first,
            inputs,
            store,
            crash_after_new_successes=28,
        )
    assert len(first.calls) == 28

    resumed_store = evaluator.ResumeCheckpointStore(tmp_path, maximum_requests=168)
    second = FakeClient()
    batch = evaluator.embed_inputs_resumable(second, inputs, resumed_store)

    assert len(second.calls) == 140
    assert batch.vectors.shape == (168, 3072)
    assert batch.resumed_input_count == 28
    assert batch.new_http_request_count == 140
    for index, item in enumerate(inputs):
        assert int(np.argmax(batch.vectors[index])) == int(
            evaluator._sha256(item.value)[:8], 16
        ) % 3072
    ledger_rows = [
        json.loads(line)
        for line in (tmp_path / "live_request_ledger.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(ledger_rows) == 168
    assert len({row["ordinal"] for row in ledger_rows}) == 168
    assert all(row["status"] == "success" for row in ledger_rows)
    serialized = json.dumps(ledger_rows)
    assert "private-input" not in serialized
    assert len(list((tmp_path / "checkpoints").glob("*.json"))) == 168
    attempt_rows = [
        json.loads(line)
        for line in (tmp_path / "live_request_attempts.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(attempt_rows) == 168
    assert len({row["ordinal"] for row in attempt_rows}) == 168
    assert all(row["status"] == "started" for row in attempt_rows)
    assert "private-input" not in json.dumps(attempt_rows)
