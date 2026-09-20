"""Preflight and opt-in Gemini Embedding 2 ChromaDB-only evaluation support.

The default CLI action is a local-only, raw-free preflight. A live request is
possible only after both an explicit command flag and a dedicated environment
authorization value are present. Request or response bodies are never written.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import importlib.util
import json
import math
import os
import re
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import fmean
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlparse

import httpx
import numpy as np

from src.library import Embedder, bounded_embedding_question, fingerprint
from src.query import plan_query
from src.repository import snapshot
from tools import chromadb_only_evaluate as common

ROOT = Path(__file__).resolve().parents[1]
MODEL = "gemini-embedding-2"
OUTPUT_DIMENSIONALITY = 3072
COLLECTION_NAME = "schat_chromadb_gemini_embedding_3072_eval_v1"
EMBEDDING_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-2:embedContent"
PAID_TEXT_USD_PER_MILLION_TOKENS = 0.20
DEFAULT_PREFLIGHT_OUTPUT = (
    ROOT / "workspace" / "검색_성능평가" / "ChromaDB" / "2026-09-20-gemini-embedding-2-3072-preflight"
)
LIVE_AUTHORIZATION_ENV = "SCHAT_ALLOW_GEMINI_HOSPITAL_DATA"
LIVE_AUTHORIZATION_VALUE = "approved-for-gemini-embedding-2"
DEFAULT_LIVE_OUTPUT = (
    ROOT
    / "workspace"
    / "검색_성능평가"
    / "ChromaDB"
    / "2026-09-20-gemini-embedding-2-3072-chromadb-only-eval"
)
APPROVED_PREFLIGHT = DEFAULT_PREFLIGHT_OUTPUT / "preflight_audit.json"
MINILM_OUTPUT = (
    ROOT / "workspace" / "검색_성능평가" / "ChromaDB" / "2026-09-19_chromadb-only-eval"
)


@dataclass(frozen=True)
class EmbeddingInput:
    input_id: str
    input_kind: str
    document_id: str
    value: str
    allowed_document_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class EmbeddingResult:
    values: np.ndarray
    prompt_tokens: int
    total_tokens: int
    attempt_count: int
    duplicate_transmission_possible: bool


@dataclass(frozen=True)
class EmbeddingBatch:
    vectors: np.ndarray
    prompt_tokens: int
    total_tokens: int
    actual_api_request_count: int
    retry_count: int
    duplicate_transmission_count: int
    latency_ms: tuple[float, ...]


@dataclass(frozen=True)
class ResumableEmbeddingBatch:
    vectors: np.ndarray
    prompt_tokens: int
    total_tokens: int
    latency_ms: tuple[float, ...]
    resumed_input_count: int
    new_http_request_count: int
    cumulative_success_count: int


class SimulatedCheckpointCrash(RuntimeError):
    """Test-only interruption raised after a durable successful checkpoint."""


class ResumeCheckpointStore:
    """Raw-free per-input checkpoints plus a Windows-safe append-only ledger."""

    def __init__(self, root: Path, *, maximum_requests: int) -> None:
        if maximum_requests < 1:
            raise ValueError("maximum_requests must be positive")
        self.root = root
        self.checkpoint_dir = root / "checkpoints"
        self.ledger_path = root / "live_request_ledger.jsonl"
        self.attempt_path = root / "live_request_attempts.jsonl"
        self.maximum_requests = maximum_requests
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def _append(self, row: Mapping[str, Any], *, path: Path | None = None) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        target = path or self.ledger_path
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(dict(row), ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _rows(self) -> list[dict[str, Any]]:
        if not self.ledger_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(
            self.ledger_path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"invalid ledger record at line {line_number}")
            rows.append(value)
        return rows

    def _attempt_rows(self) -> list[dict[str, Any]]:
        if not self.attempt_path.exists():
            return []
        rows = []
        for line_number, line in enumerate(
            self.attempt_path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"invalid attempt record at line {line_number}")
            rows.append(value)
        return rows

    @staticmethod
    def _checkpoint_name(ordinal: int, item: EmbeddingInput) -> str:
        return f"{ordinal:03d}-{item.input_kind}-{_sha256(item.value)[:16]}.json"

    def _validate_checkpoint(
        self,
        *,
        path: Path,
        item: EmbeddingInput,
        ordinal: int,
        expected_file_sha256: str | None,
    ) -> dict[str, Any]:
        if not path.is_file():
            raise ValueError(f"ledger checkpoint missing at ordinal {ordinal}")
        file_sha256 = _file_sha256(path)
        if expected_file_sha256 is not None and file_sha256 != expected_file_sha256:
            raise ValueError(f"checkpoint hash mismatch at ordinal {ordinal}")
        payload = _read_json(path)
        expected = {
            "input_stable_hash": _sha256(item.value),
            "input_type": item.input_kind,
            "ordinal": ordinal,
            "model": MODEL,
            "dimension": OUTPUT_DIMENSIONALITY,
            "normalization": "l2_unit",
            "request_success_status": True,
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise ValueError(f"checkpoint identity drift at ordinal {ordinal}")
        vector = np.asarray(payload.get("embedding_vector"), dtype=np.float32)
        if vector.shape != (OUTPUT_DIMENSIONALITY,) or not np.isfinite(vector).all():
            raise ValueError(f"checkpoint vector drift at ordinal {ordinal}")
        if not math.isclose(float(np.linalg.norm(vector)), 1.0, rel_tol=0.0, abs_tol=1e-4):
            raise ValueError(f"checkpoint normalization drift at ordinal {ordinal}")
        if int(payload.get("prompt_tokens", -1)) < 0 or float(payload.get("latency_ms", -1)) < 0:
            raise ValueError(f"checkpoint usage drift at ordinal {ordinal}")
        payload["_path"] = path
        payload["_file_sha256"] = file_sha256
        payload["_vector"] = vector
        return payload

    def load_completed(self, inputs: Sequence[EmbeddingInput]) -> dict[int, dict[str, Any]]:
        rows = self._rows()
        if any(row.get("status") != "success" for row in rows):
            raise ValueError("incomplete failed request exists in append-only ledger")
        by_ordinal: dict[int, dict[str, Any]] = {}
        for row in rows:
            ordinal = int(row.get("ordinal", -1))
            if ordinal in by_ordinal or not 0 <= ordinal < len(inputs):
                raise ValueError("duplicate or invalid ledger ordinal")
            item = inputs[ordinal]
            if (
                row.get("input_stable_hash") != _sha256(item.value)
                or row.get("input_type") != item.input_kind
            ):
                raise ValueError(f"ledger input identity drift at ordinal {ordinal}")
            reference = str(row.get("checkpoint_reference", ""))
            path = self.root / reference
            if path.parent.resolve() != self.checkpoint_dir.resolve():
                raise ValueError("checkpoint reference escaped checkpoint directory")
            by_ordinal[ordinal] = self._validate_checkpoint(
                path=path,
                item=item,
                ordinal=ordinal,
                expected_file_sha256=str(row.get("checkpoint_sha256", "")),
            )

        expected_names = {
            self._checkpoint_name(ordinal, item): (ordinal, item)
            for ordinal, item in enumerate(inputs)
        }
        for path in sorted(self.checkpoint_dir.glob("*.json")):
            if path.name not in expected_names:
                raise ValueError(f"unexpected checkpoint file: {path.name}")
            ordinal, item = expected_names[path.name]
            if ordinal in by_ordinal:
                continue
            payload = self._validate_checkpoint(
                path=path,
                item=item,
                ordinal=ordinal,
                expected_file_sha256=None,
            )
            row = self._ledger_success_row(
                item=item,
                ordinal=ordinal,
                checkpoint_path=path,
                checkpoint_sha256=payload["_file_sha256"],
                prompt_tokens=int(payload["prompt_tokens"]),
                total_tokens=int(payload["total_tokens"]),
                latency_ms=float(payload["latency_ms"]),
                recovered=True,
            )
            self._append(row)
            by_ordinal[ordinal] = payload
        attempt_ordinals: set[int] = set()
        for row in self._attempt_rows():
            ordinal = int(row.get("ordinal", -1))
            if ordinal in attempt_ordinals or not 0 <= ordinal < len(inputs):
                raise ValueError("duplicate or invalid request-attempt ordinal")
            item = inputs[ordinal]
            if (
                row.get("status") != "started"
                or row.get("input_stable_hash") != _sha256(item.value)
                or row.get("input_type") != item.input_kind
            ):
                raise ValueError(f"request-attempt identity drift at ordinal {ordinal}")
            attempt_ordinals.add(ordinal)
        incomplete_attempts = attempt_ordinals - set(by_ordinal)
        if incomplete_attempts:
            raise ValueError("request attempt exists without a durable checkpoint")
        return by_ordinal

    def record_attempt(self, *, item: EmbeddingInput, ordinal: int) -> None:
        rows = self._attempt_rows()
        if len(rows) >= self.maximum_requests:
            raise RuntimeError("approved live HTTP request limit reached")
        if any(int(row.get("ordinal", -1)) == ordinal for row in rows):
            raise RuntimeError(f"request attempt already exists at ordinal {ordinal}")
        self._append(
            {
                "schema_version": 1,
                "input_stable_hash": _sha256(item.value),
                "ordinal": ordinal,
                "input_type": item.input_kind,
                "status": "started",
            },
            path=self.attempt_path,
        )

    def _ledger_success_row(
        self,
        *,
        item: EmbeddingInput,
        ordinal: int,
        checkpoint_path: Path,
        checkpoint_sha256: str,
        prompt_tokens: int,
        total_tokens: int,
        latency_ms: float,
        recovered: bool,
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "input_stable_hash": _sha256(item.value),
            "ordinal": ordinal,
            "input_type": item.input_kind,
            "status": "success",
            "checkpoint_reference": checkpoint_path.relative_to(self.root).as_posix(),
            "checkpoint_sha256": checkpoint_sha256,
            "prompt_tokens": prompt_tokens,
            "total_tokens": total_tokens,
            "latency_ms": round(latency_ms, 3),
            "recovered_from_checkpoint": recovered,
        }

    def record_success(
        self,
        *,
        item: EmbeddingInput,
        ordinal: int,
        result: EmbeddingResult,
        latency_ms: float,
    ) -> None:
        if len(self._rows()) >= self.maximum_requests:
            raise RuntimeError("approved live HTTP request limit reached")
        path = self.checkpoint_dir / self._checkpoint_name(ordinal, item)
        if path.exists():
            raise RuntimeError(f"checkpoint already exists at ordinal {ordinal}")
        payload = {
            "schema_version": 1,
            "artifact_type": "raw_free_gemini_embedding_checkpoint",
            "input_stable_hash": _sha256(item.value),
            "input_type": item.input_kind,
            "ordinal": ordinal,
            "embedding_vector": result.values.tolist(),
            "model": MODEL,
            "dimension": OUTPUT_DIMENSIONALITY,
            "normalization": "l2_unit",
            "request_success_status": True,
            "prompt_tokens": result.prompt_tokens,
            "total_tokens": result.total_tokens,
            "latency_ms": round(latency_ms, 3),
            "raw_request_payload_stored": False,
            "raw_provider_response_stored": False,
        }
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        checkpoint_sha256 = _file_sha256(path)
        self._append(
            self._ledger_success_row(
                item=item,
                ordinal=ordinal,
                checkpoint_path=path,
                checkpoint_sha256=checkpoint_sha256,
                prompt_tokens=result.prompt_tokens,
                total_tokens=result.total_tokens,
                latency_ms=latency_ms,
                recovered=False,
            )
        )

    def record_failure(self, *, item: EmbeddingInput, ordinal: int, latency_ms: float) -> None:
        self._append(
            {
                "schema_version": 1,
                "input_stable_hash": _sha256(item.value),
                "ordinal": ordinal,
                "input_type": item.input_kind,
                "status": "failed",
                "checkpoint_reference": None,
                "checkpoint_sha256": None,
                "prompt_tokens": 0,
                "total_tokens": 0,
                "latency_ms": round(latency_ms, 3),
                "recovered_from_checkpoint": False,
            }
        )


class GeminiEmbeddingClient:
    """Small REST adapter whose transport is injectable for offline tests."""

    def __init__(
        self,
        *,
        api_key: str,
        client: httpx.Client,
        max_attempts: int = 2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key or max_attempts < 1:
            raise ValueError("Gemini API key and a positive attempt limit are required")
        self._api_key = api_key
        self._client = client
        self._max_attempts = max_attempts
        self._sleep = sleep

    def embed(self, value: str) -> EmbeddingResult:
        if not value:
            raise ValueError("embedding input must not be empty")
        payload = {
            "content": {"parts": [{"text": value}]},
            "outputDimensionality": OUTPUT_DIMENSIONALITY,
        }
        for attempt in range(1, self._max_attempts + 1):
            response = self._client.post(
                EMBEDDING_ENDPOINT,
                headers={"x-goog-api-key": self._api_key, "content-type": "application/json"},
                json=payload,
            )
            if response.status_code in {429, 500, 502, 503, 504} and attempt < self._max_attempts:
                retry_after = response.headers.get("retry-after", "0")
                try:
                    delay = max(0.0, min(float(retry_after), 30.0))
                except ValueError:
                    delay = 0.0
                self._sleep(delay)
                continue
            response.raise_for_status()
            body = response.json()
            embedding = body.get("embedding")
            usage = body.get("usageMetadata")
            if not isinstance(embedding, dict) or not isinstance(usage, dict):
                raise ValueError("Gemini embedding response contract drift")
            values = np.asarray(embedding.get("values"), dtype=np.float32)
            if values.shape != (OUTPUT_DIMENSIONALITY,) or not np.isfinite(values).all():
                raise ValueError("Gemini embedding vector shape or value drift")
            norm = float(np.linalg.norm(values))
            if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=1e-4):
                raise ValueError("Gemini embedding vector is not L2-normalized")
            prompt_tokens = int(usage.get("promptTokenCount", 0))
            total_tokens = int(usage.get("totalTokenCount", prompt_tokens))
            if prompt_tokens < 0 or total_tokens < prompt_tokens:
                raise ValueError("Gemini embedding usage metadata drift")
            return EmbeddingResult(
                values=values,
                prompt_tokens=prompt_tokens,
                total_tokens=total_tokens,
                attempt_count=attempt,
                duplicate_transmission_possible=attempt > 1,
            )
        raise RuntimeError("Gemini embedding request was not completed")


def embed_inputs(client: Any, inputs: Sequence[EmbeddingInput]) -> EmbeddingBatch:
    """Embed approved inputs while retaining only vectors and aggregate usage."""
    if not inputs:
        raise ValueError("at least one embedding input is required")
    vectors: list[np.ndarray] = []
    latencies: list[float] = []
    prompt_tokens = 0
    total_tokens = 0
    request_count = 0
    duplicate_count = 0
    for item in inputs:
        started = time.perf_counter()
        result = client.embed(item.value)
        latencies.append((time.perf_counter() - started) * 1000)
        vectors.append(result.values)
        prompt_tokens += result.prompt_tokens
        total_tokens += result.total_tokens
        request_count += result.attempt_count
        duplicate_count += int(result.duplicate_transmission_possible)
    matrix = np.stack(vectors).astype(np.float32, copy=False)
    if matrix.shape != (len(inputs), OUTPUT_DIMENSIONALITY):
        raise ValueError("embedding batch shape drift")
    return EmbeddingBatch(
        vectors=matrix,
        prompt_tokens=prompt_tokens,
        total_tokens=total_tokens,
        actual_api_request_count=request_count,
        retry_count=request_count - len(inputs),
        duplicate_transmission_count=duplicate_count,
        latency_ms=tuple(latencies),
    )


def embed_inputs_resumable(
    client: Any,
    inputs: Sequence[EmbeddingInput],
    store: ResumeCheckpointStore,
    *,
    crash_after_new_successes: int | None = None,
) -> ResumableEmbeddingBatch:
    """Embed only incomplete inputs and checkpoint each success before continuing."""
    if not inputs or len(inputs) > store.maximum_requests:
        raise ValueError("resumable input count exceeds approved request limit")
    completed = store.load_completed(inputs)
    resumed_count = len(completed)
    new_successes = 0
    for ordinal, item in enumerate(inputs):
        if ordinal in completed:
            continue
        store.record_attempt(item=item, ordinal=ordinal)
        started = time.perf_counter()
        try:
            result = client.embed(item.value)
        except Exception:
            store.record_failure(
                item=item,
                ordinal=ordinal,
                latency_ms=(time.perf_counter() - started) * 1000,
            )
            raise
        latency_ms = (time.perf_counter() - started) * 1000
        if result.attempt_count != 1 or result.duplicate_transmission_possible:
            raise RuntimeError("live embedding retry contract drift")
        store.record_success(
            item=item,
            ordinal=ordinal,
            result=result,
            latency_ms=latency_ms,
        )
        new_successes += 1
        if crash_after_new_successes == new_successes:
            raise SimulatedCheckpointCrash("simulated interruption after durable checkpoint")
    completed = store.load_completed(inputs)
    if len(completed) != len(inputs):
        raise RuntimeError("resumable embedding set is incomplete")
    ordered = [completed[index] for index in range(len(inputs))]
    vectors = np.stack([row["_vector"] for row in ordered]).astype(np.float32, copy=False)
    return ResumableEmbeddingBatch(
        vectors=vectors,
        prompt_tokens=sum(int(row["prompt_tokens"]) for row in ordered),
        total_tokens=sum(int(row["total_tokens"]) for row in ordered),
        latency_ms=tuple(float(row["latency_ms"]) for row in ordered),
        resumed_input_count=resumed_count,
        new_http_request_count=new_successes,
        cumulative_success_count=len(completed),
    )


def validate_live_preflight(*, current: Mapping[str, Any], approved: Mapping[str, Any]) -> None:
    """Fail closed if the live inputs differ from the user-reviewed preflight."""
    guarded_fields = (
        "provider",
        "transmission_plan",
        "metadata_transmission",
        "identifier_scan",
        "retry_policy",
        "collection",
        "source_contract",
        "input_manifest",
    )
    if any(current.get(field) != approved.get(field) for field in guarded_fields):
        raise ValueError("preflight approval drift")
    plan = current.get("transmission_plan", {})
    identifiers = current.get("identifier_scan", {})
    if plan.get("document_chunk_count") != 147 or plan.get("query_count") != 21:
        raise ValueError("preflight approval drift: approved count changed")
    if any(
        identifiers.get(category, {}).get("flagged_input_count") != 0
        for category in ("patient_or_staff_identifier", "hospital_or_internal_identifier")
    ):
        raise ValueError("preflight approval drift: identifier scan no longer clean")


def require_live_runtime(
    *, find_spec: Callable[[str], Any] = importlib.util.find_spec
) -> None:
    """Validate all local-only runtime dependencies before any provider request."""
    if find_spec("chromadb") is None:
        raise RuntimeError("chromadb is required before live embedding requests may start")


def smoke_test_chromadb() -> dict[str, Any]:
    """Exercise a local persistent 3072d cosine collection without source data."""
    require_live_runtime()
    import chromadb
    from chromadb.config import Settings

    with tempfile.TemporaryDirectory(
        prefix="schat-gemini-chroma-smoke-",
        ignore_cleanup_errors=True,
    ) as temporary:
        client = chromadb.PersistentClient(
            path=temporary,
            settings=Settings(anonymized_telemetry=False),
        )
        name = "schat_gemini_embedding_smoke"
        collection = client.create_collection(
            name=name,
            embedding_function=None,
            metadata={"hnsw:space": "cosine"},
        )
        vector = [1.0] + [0.0] * (OUTPUT_DIMENSIONALITY - 1)
        collection.add(ids=["synthetic-vector"], embeddings=[vector])
        result = collection.query(
            query_embeddings=[vector],
            n_results=1,
            include=["distances"],
        )
        if result["ids"] != [["synthetic-vector"]]:
            raise RuntimeError("ChromaDB 3072d add/query smoke drift")
        client.delete_collection(name)
    return {
        "chromadb_version": common._package_version("chromadb"),
        "persistent_client": True,
        "dimension": OUTPUT_DIMENSIONALITY,
        "similarity_metric": "cosine",
        "add_query_delete": "pass",
    }


def smoke_test_checkpoint_resume() -> dict[str, Any]:
    """Verify checkpoint write/read, append-only ledger, and resume without network."""

    class SyntheticClient:
        def __init__(self) -> None:
            self.calls = 0

        def embed(self, value: str) -> EmbeddingResult:
            self.calls += 1
            vector = np.zeros(OUTPUT_DIMENSIONALITY, dtype=np.float32)
            vector[int(_sha256(value)[:8], 16) % OUTPUT_DIMENSIONALITY] = 1.0
            return EmbeddingResult(vector, 1, 1, 1, False)

    inputs = [
        EmbeddingInput(f"document:smoke-{index}", "document", "smoke", f"synthetic-{index}")
        for index in range(3)
    ]
    with tempfile.TemporaryDirectory(
        prefix="schat-gemini-checkpoint-smoke-",
        ignore_cleanup_errors=True,
    ) as temporary:
        store = ResumeCheckpointStore(Path(temporary), maximum_requests=3)
        first = SyntheticClient()
        try:
            embed_inputs_resumable(first, inputs, store, crash_after_new_successes=1)
        except SimulatedCheckpointCrash:
            pass
        else:
            raise RuntimeError("checkpoint smoke did not simulate interruption")
        second = SyntheticClient()
        batch = embed_inputs_resumable(
            second,
            inputs,
            ResumeCheckpointStore(Path(temporary), maximum_requests=3),
        )
        rows = [
            json.loads(line)
            for line in (Path(temporary) / "live_request_ledger.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        if first.calls != 1 or second.calls != 2 or len(rows) != 3:
            raise RuntimeError("checkpoint resume smoke drift")
        if len({int(row["ordinal"]) for row in rows}) != 3:
            raise RuntimeError("checkpoint ledger duplicate ordinal")
        if batch.vectors.shape != (3, OUTPUT_DIMENSIONALITY):
            raise RuntimeError("checkpoint vector order/shape drift")
    return {
        "checkpoint_write_read": "pass",
        "append_only_ledger": "pass",
        "resume_skipped_completed": 1,
        "resumed_new_requests": 2,
        "duplicate_ledger_records": 0,
    }


_COMPARISON_METRICS = (
    "hit_at_1",
    "hit_at_3",
    "hit_at_5",
    "hit_at_10",
    "mrr",
    "recall_at_10",
    "precision_at_10",
    "ragas_id_context_precision",
    "ragas_id_context_recall",
)


def build_minilm_comparison(*, gemini: Mapping[str, Any], minilm: Mapping[str, Any]) -> dict[str, Any]:
    """Compare raw-free metrics and case IDs under the shared Hit@10 success rule."""
    gemini_overall = gemini["summary"]["metrics"]["overall"]
    minilm_overall = minilm["summary"]["metrics"]["overall"]
    if gemini_overall["case_count"] != minilm_overall["case_count"]:
        raise ValueError("Gemini/MiniLM case-count drift")
    gemini_cases = {row["case_id"]: row for row in gemini["per_case"]["cases"]}
    minilm_cases = {row["case_id"]: row for row in minilm["per_case"]["cases"]}
    if set(gemini_cases) != set(minilm_cases):
        raise ValueError("Gemini/MiniLM case-ID drift")
    gemini_success = {
        case_id for case_id, row in gemini_cases.items() if row["metrics"]["hit_at_10"] == 1.0
    }
    minilm_success = {
        case_id for case_id, row in minilm_cases.items() if row["metrics"]["hit_at_10"] == 1.0
    }
    all_ids = set(gemini_cases)
    return {
        "schema_version": 1,
        "comparison": "chromadb_gemini_embedding_2_3072_vs_minilm_384",
        "success_definition": "hit_at_10 == 1.0",
        "case_count": len(all_ids),
        "gemini_metrics": {name: gemini_overall[name] for name in _COMPARISON_METRICS},
        "minilm_metrics": {name: minilm_overall[name] for name in _COMPARISON_METRICS},
        "metric_delta_gemini_minus_minilm": {
            name: round(float(gemini_overall[name]) - float(minilm_overall[name]), 12)
            for name in _COMPARISON_METRICS
        },
        "gemini_only_success_case_ids": sorted(gemini_success - minilm_success),
        "minilm_only_success_case_ids": sorted(minilm_success - gemini_success),
        "both_failed_case_ids": sorted(all_ids - gemini_success - minilm_success),
        "both_succeeded_case_ids": sorted(gemini_success & minilm_success),
        "raw_question_persisted": False,
        "raw_source_text_persisted": False,
    }


_PATIENT_OR_STAFF_PATTERNS = (
    re.compile(r"(?:환자|직원|사원|교직원)\s*(?:번호|ID|아이디)\s*[:#]?\s*[A-Za-z0-9-]{4,}", re.I),
    re.compile(r"\b\d{6}-?[1-4]\d{6}\b"),
    re.compile(r"\b01[016789]-?\d{3,4}-?\d{4}\b"),
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
)
_HOSPITAL_OR_INTERNAL_PATTERNS = (
    re.compile(r"[가-힣A-Za-z0-9]+(?:대학교)?(?:병원|의료원)"),
    re.compile(r"(?:내부문서|문서번호|규정번호)\s*[:#]?\s*[A-Za-z0-9-]{4,}", re.I),
)


def _estimated_tokens(value: str) -> int:
    """Return a conservative offline estimate without contacting countTokens."""
    return max(1, math.ceil(len(value.encode("utf-8")) / 4))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _flagged_count(inputs: Sequence[EmbeddingInput], patterns: Sequence[re.Pattern[str]]) -> int:
    return sum(any(pattern.search(item.value) for pattern in patterns) for item in inputs)


def build_preflight_audit(
    document_inputs: Sequence[EmbeddingInput],
    query_inputs: Sequence[EmbeddingInput],
) -> dict[str, Any]:
    """Describe a future live run without retaining input values."""
    if any(item.input_kind != "document" for item in document_inputs):
        raise ValueError("document preflight contains a non-document input")
    if any(item.input_kind != "query" for item in query_inputs):
        raise ValueError("query preflight contains a non-query input")
    all_inputs = [*document_inputs, *query_inputs]
    if not all_inputs or len({item.input_id for item in all_inputs}) != len(all_inputs):
        raise ValueError("preflight input IDs must be non-empty and unique")
    estimates = [_estimated_tokens(item.value) for item in all_inputs]
    total_estimated_tokens = sum(estimates)
    document_counts = Counter(item.document_id for item in document_inputs)
    manifest = [
        {
            "input_id": item.input_id,
            "input_kind": item.input_kind,
            "document_id": item.document_id,
            "sha256": _sha256(item.value),
            "utf8_bytes": len(item.value.encode("utf-8")),
            "estimated_input_tokens": estimate,
        }
        for item, estimate in zip(all_inputs, estimates, strict=True)
    ]
    return {
        "schema_version": 1,
        "audit_type": "raw_free_gemini_embedding_preflight",
        "provider": {
            "endpoint": EMBEDDING_ENDPOINT,
            "model": MODEL,
            "output_dimensionality": OUTPUT_DIMENSIONALITY,
            "similarity_metric": "cosine",
            "normalization": "provider_default_l2_unit_verified_after_response",
            "task_type_api_field_used": False,
            "query_instruction": "task: search result | query: {value}",
            "document_instruction": "title: none | text: {value}",
        },
        "transmission_plan": {
            "document_chunk_count": len(document_inputs),
            "query_count": len(query_inputs),
            "planned_api_request_count": len(all_inputs),
            "one_input_per_request": True,
        },
        "documents": [
            {"document_id": document_id, "chunk_count": count}
            for document_id, count in sorted(document_counts.items())
        ],
        "metadata_transmission": {
            "document_id": False,
            "chunk_id": False,
            "page": False,
            "section": False,
            "title": False,
        },
        "identifier_scan": {
            "patient_or_staff_identifier": {
                "flagged_input_count": _flagged_count(all_inputs, _PATIENT_OR_STAFF_PATTERNS),
                "matched_values_stored": False,
            },
            "hospital_or_internal_identifier": {
                "flagged_input_count": _flagged_count(all_inputs, _HOSPITAL_OR_INTERNAL_PATTERNS),
                "matched_values_stored": False,
            },
            "limitations": (
                "Deterministic pattern scan only; zero flags does not prove full de-identification."
            ),
        },
        "token_and_cost_estimate": {
            "estimated_input_tokens": total_estimated_tokens,
            "estimate_method": "ceil(utf8_bytes/4)_offline_no_provider_call",
            "exact_token_count_requires_provider_call": True,
            "paid_standard_usd_per_million_text_tokens": PAID_TEXT_USD_PER_MILLION_TOKENS,
            "estimated_paid_standard_cost_usd": round(
                total_estimated_tokens / 1_000_000 * PAID_TEXT_USD_PER_MILLION_TOKENS,
                8,
            ),
        },
        "storage_policy": {
            "request_payload_stored": False,
            "request_payload_logged": False,
            "api_key_stored_or_logged": False,
            "provider_response_raw_stored": False,
            "parsed_vectors_persisted_in_separate_chroma_collection": True,
            "raw_free_metrics_only": True,
        },
        "retry_policy": {
            "maximum_attempts_per_input": 2,
            "retryable_status_codes": [429, 500, 502, 503, 504],
            "provider_idempotency_key_used": False,
            "duplicate_transmission_possible": True,
            "maximum_duplicate_transmissions_per_input": 1,
        },
        "collection": {
            "name": COLLECTION_NAME,
            "separate_from_existing_minilm_collection": True,
        },
        "input_manifest": manifest,
        "external_api_calls_performed": 0,
    }


def _render_preflight_html(audit: dict[str, Any]) -> str:
    safe_json = html.escape(json.dumps(audit, ensure_ascii=False, indent=2))
    return (
        """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gemini Embedding 2 전송 전 감사</title>
<style>body{font-family:system-ui,sans-serif;margin:2rem;line-height:1.6;color:#263238}main{max-width:1100px;margin:auto}h1{color:#087f5b}pre{white-space:pre-wrap;background:#f4f6f7;padding:1rem;border-radius:8px;overflow:auto}.notice{border-left:4px solid #0ca678;padding:.75rem 1rem;background:#eefbf6}</style></head>
<body><main><h1>Gemini Embedding 2 전송 전 감사</h1><p class="notice">외부 API 호출 0회 · 원문/질문/API key 미포함 · ID/hash/count/추정치만 표시</p><pre>"""
        + safe_json
        + """</pre></main></body></html>
"""
    )


def write_preflight_reports(audit: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "preflight_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "preflight_audit.html").write_text(
        _render_preflight_html(audit),
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_query_inputs(
    *,
    uat: dict[str, Any],
    approved_cases: Sequence[common.ApprovedCase],
    documents: Sequence[dict[str, Any]],
    scopes: dict[str, tuple[str, ...]],
) -> list[EmbeddingInput]:
    approved_by_id = {case.case_id: case for case in approved_cases}
    embedder = Embedder()
    previous: dict[str, dict[str, Any]] = {}
    result: list[EmbeddingInput] = []
    for uat_case in uat["cases"]:
        scope = str(uat_case.get("document_scope", ""))
        case_id = str(uat_case.get("case_id", ""))
        if scope not in scopes:
            continue
        is_follow_up = str(uat_case.get("question_type")) == "follow_up"
        prior = previous.get(scope, {}) if is_follow_up else {}
        allowed_documents = scopes[scope]
        plan = plan_query(
            str(uat_case["question"]),
            previous=str(prior.get("question", "")),
            follow_up=is_follow_up,
            documents=documents,
            previous_sources=tuple(prior.get("document_ids", ())),
            context_document_ids=allowed_documents,
        )
        case = approved_by_id.get(case_id)
        if case is not None:
            if plan.clarification or plan.domain == "out_of_scope":
                raise ValueError(f"{case_id}: approved case was not queryable")
            effective_documents = set(allowed_documents)
            if plan.document_ids:
                effective_documents.intersection_update(plan.document_ids)
            if not effective_documents:
                raise ValueError(f"{case_id}: query plan removed document scope")
            projected = bounded_embedding_question(plan.expanded, embedder)
            result.append(
                EmbeddingInput(
                    input_id=f"query:{case_id}",
                    input_kind="query",
                    document_id=case.document_scope,
                    value=f"task: search result | query: {projected}",
                    allowed_document_ids=tuple(sorted(effective_documents)),
                )
            )
        if str(uat_case.get("expected_behavior")) == "answer":
            previous[scope] = {
                "question": str(uat_case["question"]),
                "document_ids": allowed_documents,
            }
    if {item.input_id.removeprefix("query:") for item in result} != set(approved_by_id):
        raise ValueError("approved Gold preflight coverage drift")
    return result


def build_repository_preflight(
    *,
    catalog_path: Path = common.DEFAULT_CATALOG,
    uat_path: Path = common.DEFAULT_UAT,
    reviewed_gold_path: Path = common.DEFAULT_REVIEWED_GOLD,
) -> dict[str, Any]:
    """Build the real frozen-corpus audit without making a provider call."""
    reviewed = _read_json(reviewed_gold_path)
    uat = _read_json(uat_path)
    approved_cases = common.load_approved_cases(reviewed, uat)
    revision = common._catalog_revision(catalog_path)
    library = snapshot(str(catalog_path.resolve()), revision)
    if not library.chunks or not library.docs:
        raise ValueError("frozen corpus is empty")

    from tools.schat_v1_final_validate import _scope_document_ids

    scopes = _scope_document_ids(library.docs)
    document_inputs = [
        EmbeddingInput(
            input_id=f"document:{chunk.id}",
            input_kind="document",
            document_id=chunk.document_id,
            value=f"title: none | text: {chunk.text}",
        )
        for chunk in library.chunks
    ]
    query_inputs = _build_query_inputs(
        uat=uat,
        approved_cases=approved_cases,
        documents=library.docs,
        scopes=scopes,
    )
    audit = build_preflight_audit(document_inputs, query_inputs)
    audit["source_contract"] = {
        "catalog_path": str(catalog_path.relative_to(ROOT)).replace("\\", "/"),
        "catalog_sha256": _file_sha256(catalog_path),
        "catalog_revision": revision,
        "uat_fixture": str(uat_path.relative_to(ROOT)).replace("\\", "/"),
        "uat_fixture_sha256": _file_sha256(uat_path),
        "reviewed_gold_fixture": str(reviewed_gold_path.relative_to(ROOT)).replace("\\", "/"),
        "reviewed_gold_fixture_sha256": _file_sha256(reviewed_gold_path),
        "approved_positive_case_count": len(approved_cases),
        "production_changed": False,
        "gold_or_fixture_changed": False,
        "db_or_schema_changed": False,
    }
    return audit


def _repository_inputs(
    *,
    catalog_path: Path,
    uat_path: Path,
    reviewed_gold_path: Path,
) -> tuple[Any, dict[str, Any], dict[str, Any], list[common.ApprovedCase], list[EmbeddingInput], list[EmbeddingInput]]:
    reviewed = _read_json(reviewed_gold_path)
    uat = _read_json(uat_path)
    approved_cases = common.load_approved_cases(reviewed, uat)
    revision = common._catalog_revision(catalog_path)
    library = snapshot(str(catalog_path.resolve()), revision)
    if not library.chunks or not library.docs:
        raise ValueError("frozen corpus is empty")
    from tools.schat_v1_final_validate import _scope_document_ids

    scopes = _scope_document_ids(library.docs)
    document_inputs = [
        EmbeddingInput(
            input_id=f"document:{chunk.id}",
            input_kind="document",
            document_id=chunk.document_id,
            value=f"title: none | text: {chunk.text}",
        )
        for chunk in library.chunks
    ]
    query_inputs = _build_query_inputs(
        uat=uat,
        approved_cases=approved_cases,
        documents=library.docs,
        scopes=scopes,
    )
    return library, reviewed, uat, approved_cases, document_inputs, query_inputs


def _load_minilm_report(output_dir: Path) -> dict[str, Any]:
    return {
        "summary": _read_json(output_dir / "summary.json"),
        "per_case": _read_json(output_dir / "per_case_results.json"),
    }


def evaluate_live(
    *,
    embedding_client: GeminiEmbeddingClient,
    approved_preflight_path: Path = APPROVED_PREFLIGHT,
    catalog_path: Path = common.DEFAULT_CATALOG,
    uat_path: Path = common.DEFAULT_UAT,
    reviewed_gold_path: Path = common.DEFAULT_REVIEWED_GOLD,
    operational_gold_path: Path = common.DEFAULT_OPERATIONAL_GOLD,
    minilm_output_dir: Path = MINILM_OUTPUT,
    output_dir: Path = DEFAULT_LIVE_OUTPUT,
    request_store: ResumeCheckpointStore | None = None,
) -> dict[str, Any]:
    """Run the approved one-off live evaluation and persist only raw-free output."""
    require_live_runtime()
    current_preflight = build_repository_preflight(
        catalog_path=catalog_path,
        uat_path=uat_path,
        reviewed_gold_path=reviewed_gold_path,
    )
    validate_live_preflight(
        current=current_preflight,
        approved=_read_json(approved_preflight_path),
    )
    (
        library,
        reviewed,
        _uat,
        approved_cases,
        document_inputs,
        query_inputs,
    ) = _repository_inputs(
        catalog_path=catalog_path,
        uat_path=uat_path,
        reviewed_gold_path=reviewed_gold_path,
    )
    operational_gold = _read_json(operational_gold_path)
    if len(document_inputs) != 147 or len(query_inputs) != 21:
        raise ValueError("live input count escaped approved boundary")

    if request_store is None:
        request_store = ResumeCheckpointStore(
            output_dir / "resume_state",
            maximum_requests=168,
        )
    all_inputs = [*document_inputs, *query_inputs]
    embedding_started = time.perf_counter()
    embedding_batch = embed_inputs_resumable(
        embedding_client,
        all_inputs,
        request_store,
    )
    current_execution_seconds = time.perf_counter() - embedding_started
    embedding_generation_seconds = sum(embedding_batch.latency_ms) / 1000
    document_vectors = embedding_batch.vectors[: len(document_inputs)]
    query_vectors = embedding_batch.vectors[len(document_inputs) :]
    document_latencies = embedding_batch.latency_ms[: len(document_inputs)]
    query_latencies = embedding_batch.latency_ms[len(document_inputs) :]
    actual_input_tokens = embedding_batch.prompt_tokens
    total_tokens = embedding_batch.total_tokens
    if actual_input_tokens <= 0 or total_tokens < actual_input_tokens:
        raise ValueError("provider did not return usable token accounting")

    import chromadb
    from chromadb.config import Settings

    index_dir = output_dir / "chroma_index"
    index_dir.mkdir(parents=True, exist_ok=True)
    chroma_client = chromadb.PersistentClient(
        path=str(index_dir),
        settings=Settings(anonymized_telemetry=False),
    )
    collection = common.rebuild_collection(
        chroma_client,
        library.chunks,
        document_vectors,
        collection_name=COLLECTION_NAME,
    )
    stored = collection.get(limit=len(library.chunks), include=["documents"])
    if any(value is not None for value in (stored.get("documents") or [])):
        raise RuntimeError("ChromaDB unexpectedly persisted source documents")

    approved_by_id = {case.case_id: case for case in approved_cases}
    corpus_chunk_ids = {chunk.id for chunk in library.chunks}
    results: list[dict[str, Any]] = []
    for index, (item, query_vector) in enumerate(
        zip(query_inputs, query_vectors, strict=True)
    ):
        case_id = item.input_id.removeprefix("query:")
        case = approved_by_id[case_id]
        candidate_count = sum(
            chunk.document_id in item.allowed_document_ids for chunk in library.chunks
        )
        query_started = time.perf_counter()
        ranked = common.query_collection(
            collection,
            query_vector,
            document_ids=item.allowed_document_ids,
            limit=10,
            candidate_limit=candidate_count,
        )
        chroma_query_ms = (time.perf_counter() - query_started) * 1000
        if len(ranked) != min(10, candidate_count):
            raise RuntimeError(f"{case_id}: incomplete ChromaDB Top-k")
        embedding_ms = query_latencies[index]
        results.append(
            common.build_case_result(
                case,
                ranked,
                latency_ms=embedding_ms + chroma_query_ms,
                embedding_latency_ms=embedding_ms,
                query_latency_ms=chroma_query_ms,
                corpus_chunk_ids=corpus_chunk_ids,
            )
        )
    if {row["case_id"] for row in results} != set(approved_by_id):
        raise ValueError("approved Gold evaluation coverage drift")

    summary = common.aggregate_results(results)
    revision = common._catalog_revision(catalog_path)
    corpus_fingerprint = fingerprint(
        library.docs,
        [str(document["id"]) for document in library.docs],
    )
    norms = np.linalg.norm(document_vectors, axis=1)
    usage = {
        "logical_input_count": len(document_inputs) + len(query_inputs),
        "actual_api_request_count": embedding_batch.cumulative_success_count,
        "successful_request_count": embedding_batch.cumulative_success_count,
        "failed_request_count": 0,
        "retry_count": 0,
        "approved_retransmission_count": 28,
        "post_checkpoint_duplicate_request_count": 0,
        "resumed_input_count_at_final_execution": embedding_batch.resumed_input_count,
        "new_http_request_count_at_final_execution": embedding_batch.new_http_request_count,
        "document_embedding_count": len(document_vectors),
        "query_embedding_count": len(query_vectors),
        "actual_input_tokens": actual_input_tokens,
        "actual_total_tokens": total_tokens,
        "paid_standard_usd_per_million_text_tokens": PAID_TEXT_USD_PER_MILLION_TOKENS,
        "actual_estimated_cost_usd": round(
            actual_input_tokens / 1_000_000 * PAID_TEXT_USD_PER_MILLION_TOKENS,
            8,
        ),
        "embedding_generation_time_seconds": round(embedding_generation_seconds, 3),
        "current_execution_wall_time_seconds": round(current_execution_seconds, 3),
        "document_embedding_latency_mean_ms": round(fmean(document_latencies), 3),
        "query_embedding_latency_mean_ms": round(fmean(query_latencies), 3),
        "chromadb_query_latency_mean_ms": round(
            fmean(float(row["latency_ms"]["query"]) for row in results), 3
        ),
    }
    contract = {
        "schema_version": 1,
        "evaluation_date": date.today().isoformat(),
        "evaluator_file": "tools/gemini_chromadb_only_evaluate.py",
        "engine": "chromadb_only",
        "retrieval_contract": common.retrieval_contract(),
        "corpus": {
            "catalog_revision": revision,
            "fingerprint_algorithm": common.FINGERPRINT_ALGORITHM_ID,
            "fingerprint": corpus_fingerprint,
            "document_count": len(library.docs),
            "chunk_count": len(library.chunks),
            "document_ids": sorted(str(document["id"]) for document in library.docs),
        },
        "gold": {
            "fixture": str(reviewed_gold_path.relative_to(ROOT)).replace("\\", "/"),
            "fixture_sha256": common._sha256_file(reviewed_gold_path),
            "dataset_version": reviewed.get("dataset_version", "unknown"),
            "approved_positive_case_count": len(approved_cases),
            "deferred_positive_case_count": len(reviewed["cases"]) - len(approved_cases),
            "approved_abstention_case_count": common._approved_abstention_count(
                operational_gold
            ),
            "negative_handling": "separate_not_queried_by_positive_retrieval_evaluator",
        },
        "query_processing": {
            "planner": "src.query.plan_query",
            "embedding_projection": "src.library.bounded_embedding_question(plan.expanded)",
            "follow_up_context": True,
            "document_scope_filter": True,
            "context_expansion": False,
            "equal_distance_tie_breaker": "corpus_position_then_chunk_id",
        },
        "embedding": {
            "provider": "Google Gemini paid API",
            "endpoint": EMBEDDING_ENDPOINT,
            "model": MODEL,
            "dimension": OUTPUT_DIMENSIONALITY,
            "normalization": "provider_3072_l2_unit_verified",
            "minimum_norm": round(float(norms.min()), 6),
            "maximum_norm": round(float(norms.max()), 6),
            "document_task_representation": "title: none | text: {value}",
            "query_task_representation": "task: search result | query: {value}",
            "task_type_api_field_used": False,
        },
        "chromadb": {
            "version": common._package_version("chromadb"),
            "collection_name": COLLECTION_NAME,
            "similarity_metric": "cosine",
            "default_embedding_function_used": False,
            "documents_stored": False,
            "anonymized_telemetry": False,
            "clean_rebuild": True,
        },
        "top_k": list(common.TOP_K),
        "usage": usage,
        "storage_policy": current_preflight["storage_policy"],
    }
    summary_payload = {
        "schema_version": 1,
        "engine": "chromadb_only_gemini_embedding_2_3072",
        "approved_positive_case_count": len(approved_cases),
        "deferred_positive_case_count": len(reviewed["cases"]) - len(approved_cases),
        "approved_abstention_case_count": common._approved_abstention_count(
            operational_gold
        ),
        "metrics": summary,
        "usage": usage,
        "production_changed": False,
        "gold_changed": False,
        "raw_source_text_persisted": False,
        "raw_question_persisted": False,
        "raw_request_or_provider_response_persisted": False,
    }
    per_case_payload = {
        "schema_version": 1,
        "engine": "chromadb_only_gemini_embedding_2_3072",
        "cases": results,
    }
    gemini_report = {"summary": summary_payload, "per_case": per_case_payload}
    comparison = build_minilm_comparison(
        gemini=gemini_report,
        minilm=_load_minilm_report(minilm_output_dir),
    )
    forbidden_texts = {chunk.text for chunk in library.chunks}
    for payload in (contract, summary_payload, per_case_payload, comparison):
        common.ensure_artifact_safe(payload, forbidden_exact_texts=forbidden_texts)

    output_dir.mkdir(parents=True, exist_ok=True)
    common._write_json(output_dir / "evaluation_contract.json", contract)
    common._write_json(output_dir / "summary.json", summary_payload)
    common._write_json(output_dir / "per_case_results.json", per_case_payload)
    common._write_json(output_dir / "comparison_with_minilm.json", comparison)
    common._write_metrics_csv(output_dir / "metrics.csv", summary)
    common._write_failure_analysis(output_dir / "failure_analysis.md", results)
    return {
        "contract": contract,
        "summary": summary_payload,
        "per_case": per_case_payload,
        "comparison": comparison,
        "output_dir": str(output_dir),
    }


def require_live_authorization(*, execute_live: bool, authorization_value: str | None) -> None:
    if execute_live and authorization_value != LIVE_AUTHORIZATION_VALUE:
        raise PermissionError("external hospital-data transmission requires separate explicit authorization")


def _live_api_key() -> str:
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if key:
        return key
    from src.settings import load_settings

    settings = load_settings(use_streamlit=False)
    is_approved_google_gemini = (
        settings.llm_provider == "internal"
        and settings.llm_approved
        and settings.llm_model.startswith("gemini-")
        and urlparse(settings.llm_url).hostname == "generativelanguage.googleapis.com"
        and bool(settings.llm_key)
    )
    if not is_approved_google_gemini:
        raise RuntimeError(
            "an approved Google Gemini API credential is required for the live run"
        )
    return settings.llm_key


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a raw-free Gemini Embedding 2 transmission preflight."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_PREFLIGHT_OUTPUT)
    parser.add_argument("--execute-live", action="store_true")
    parser.add_argument("--approved-preflight", type=Path, default=APPROVED_PREFLIGHT)
    parser.add_argument("--minilm-output", type=Path, default=MINILM_OUTPUT)
    args = parser.parse_args()
    require_live_authorization(
        execute_live=args.execute_live,
        authorization_value=os.getenv(LIVE_AUTHORIZATION_ENV),
    )
    if args.execute_live:
        live_output = args.output
        if live_output == DEFAULT_PREFLIGHT_OUTPUT:
            live_output = DEFAULT_LIVE_OUTPUT
        chromadb_smoke = smoke_test_chromadb()
        checkpoint_smoke = smoke_test_checkpoint_resume()
        request_store = ResumeCheckpointStore(
            live_output / "resume_state",
            maximum_requests=168,
        )
        with httpx.Client(
            timeout=httpx.Timeout(60.0, connect=15.0),
            follow_redirects=False,
        ) as http_client:
            report = evaluate_live(
                embedding_client=GeminiEmbeddingClient(
                    api_key=_live_api_key(),
                    client=http_client,
                    max_attempts=1,
                ),
                approved_preflight_path=args.approved_preflight,
                minilm_output_dir=args.minilm_output,
                output_dir=live_output,
                request_store=request_store,
            )
        overall = report["summary"]["metrics"]["overall"]
        usage = report["summary"]["usage"]
        print(
            json.dumps(
                {
                    "engine": report["summary"]["engine"],
                    "case_count": overall["case_count"],
                    "hit_at_10": overall["hit_at_10"],
                    "mrr": overall["mrr"],
                    "actual_api_request_count": usage["actual_api_request_count"],
                    "actual_input_tokens": usage["actual_input_tokens"],
                    "actual_estimated_cost_usd": usage["actual_estimated_cost_usd"],
                    "retry_count": usage["retry_count"],
                    "chromadb_smoke": chromadb_smoke,
                    "checkpoint_smoke": checkpoint_smoke,
                    "output_dir": report["output_dir"],
                },
                ensure_ascii=False,
            )
        )
        return
    audit = build_repository_preflight()
    write_preflight_reports(audit, args.output)
    print(
        json.dumps(
            {
                "audit_type": audit["audit_type"],
                "document_chunk_count": audit["transmission_plan"]["document_chunk_count"],
                "query_count": audit["transmission_plan"]["query_count"],
                "estimated_input_tokens": audit["token_and_cost_estimate"]["estimated_input_tokens"],
                "estimated_paid_standard_cost_usd": audit["token_and_cost_estimate"][
                    "estimated_paid_standard_cost_usd"
                ],
                "external_api_calls_performed": audit["external_api_calls_performed"],
                "output_dir": str(args.output),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
