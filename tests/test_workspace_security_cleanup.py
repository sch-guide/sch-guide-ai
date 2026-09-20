from __future__ import annotations

import csv
import importlib
import json
from pathlib import Path

import pytest


def cleanup_module():
    try:
        return importlib.import_module("tools.workspace_security_cleanup")
    except ModuleNotFoundError:
        pytest.fail("workspace security cleanup module is not implemented")


def test_gitignore_blocks_local_review_temporary_and_historical_workspaces():
    root = Path(__file__).resolve().parents[1]
    rules = (root / ".gitignore").read_text(encoding="utf-8")

    assert "/workspace/데이터_검수/" in rules
    assert "/workspace/임시작업/" in rules
    assert "/workspace/과거작업/" in rules
    assert "/workspace/**/chroma_index/" in rules


def test_json_sanitizer_hashes_questions_and_preserves_non_sensitive_contract(tmp_path: Path):
    cleanup = cleanup_module()
    source = tmp_path / "source.json"
    target = tmp_path / "target.json"
    raw_question = "private clinical question"
    payload = {
        "evaluation_contract": {"version": "v1", "metric": "hit_at_10"},
        "cases": [
            {"case_id": "C001", "question": raw_question, "rank": 2, "score": 0.75},
            {"case_id": "C002", "question": "second question", "rank": 1, "score": 1.0},
        ],
        "metrics": {"hit_at_10": 1.0},
        "status": "complete",
    }
    source.write_text(json.dumps(payload), encoding="utf-8")

    summary = cleanup.sanitize_file(source, target)
    sanitized_text = target.read_text(encoding="utf-8")
    sanitized = json.loads(sanitized_text)

    assert raw_question not in sanitized_text
    assert "question" not in sanitized["cases"][0]
    assert sanitized["cases"][0]["question_sha256"] == cleanup.stable_identifier(raw_question)
    assert sanitized["metrics"] == payload["metrics"]
    assert sanitized["cases"][0]["rank"] == 2
    assert sanitized["evaluation_contract"] == payload["evaluation_contract"]
    assert summary.question_count == 2
    assert cleanup.verify_sanitized_file(source, target).verified is True


def test_csv_sanitizer_hashes_questions_and_preserves_rows_metrics_and_ranks(tmp_path: Path):
    cleanup = cleanup_module()
    source = tmp_path / "source.csv"
    target = tmp_path / "target.csv"
    with source.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["case_id", "question", "rank", "metric"])
        writer.writeheader()
        writer.writerow({"case_id": "C001", "question": "private question", "rank": "1", "metric": "0.5"})
        writer.writerow({"case_id": "C002", "question": "other question", "rank": "2", "metric": "0.25"})

    summary = cleanup.sanitize_file(source, target)
    with target.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert list(rows[0]) == ["case_id", "question_sha256", "rank", "metric"]
    assert [row["rank"] for row in rows] == ["1", "2"]
    assert [row["metric"] for row in rows] == ["0.5", "0.25"]
    assert summary.case_count == 2
    assert summary.question_count == 2
    assert cleanup.verify_sanitized_file(source, target).verified is True


def test_sanitizer_fails_closed_for_unapproved_raw_fields(tmp_path: Path):
    cleanup = cleanup_module()
    source = tmp_path / "source.json"
    target = tmp_path / "target.json"
    source.write_text(json.dumps({"case_id": "C001", "question": "q", "source_text": "raw"}), encoding="utf-8")

    with pytest.raises(cleanup.UnsafeArtifactFieldError):
        cleanup.sanitize_file(source, target)

    assert not target.exists()


def test_q002_compatibility_projection_keeps_only_loader_contract(tmp_path: Path):
    cleanup = cleanup_module()
    source = tmp_path / "source.json"
    target = tmp_path / "target.json"
    required_row = {
        "chunk_id": "chunk-002",
        "semantic_score": 0.91,
        "bm25_score": 3.25,
        "rrf_score": 0.031,
        "rerank_score": 0.84,
        "context_only": False,
        "context_complete": True,
    }
    source.write_text(
        json.dumps(
            {
                "question": "private question",
                "funnel": {
                    "improved_evidence": [
                        {
                            **required_row,
                            "source_text": "private source",
                            "evidence": "private evidence",
                        }
                    ]
                },
                "prompt": "private prompt",
            }
        ),
        encoding="utf-8",
    )

    summary = cleanup.create_q002_compatibility_projection(source, target)
    projection = json.loads(target.read_text(encoding="utf-8"))
    serialized = target.read_text(encoding="utf-8")

    assert projection == {
        "artifact_type": "raw_free_compatibility_projection",
        "projection_version": "q002_phase1_hits_v1",
        "status": "compatible",
        "funnel": {"improved_evidence": [required_row]},
    }
    assert summary.case_count == 1
    assert cleanup.verify_q002_compatibility_projection(source, target).verified
    assert "private question" not in serialized
    assert "private source" not in serialized
    assert "private evidence" not in serialized
    assert "private prompt" not in serialized
    assert not ({"question", "evidence", "source_text", "prompt"} & set(serialized.split('"')))


def test_q002_compatibility_projection_fails_closed_on_missing_loader_field(
    tmp_path: Path,
):
    cleanup = cleanup_module()
    source = tmp_path / "source.json"
    target = tmp_path / "target.json"
    source.write_text(
        json.dumps(
            {
                "funnel": {
                    "improved_evidence": [
                        {
                            "chunk_id": "chunk-002",
                            "semantic_score": 0.91,
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(cleanup.UnsafeArtifactFieldError):
        cleanup.create_q002_compatibility_projection(source, target)

    assert not target.exists()


def test_repository_q002_compatibility_projection_is_raw_free():
    cleanup = cleanup_module()
    root = Path(__file__).resolve().parents[1]
    target = (
        root
        / "workspace"
        / "RAG_실험"
        / "2026-09-13_rag-phase1-retrieval"
        / "q002_retrieval_funnel.json"
    )
    payload = json.loads(target.read_text(encoding="utf-8"))

    assert payload["artifact_type"] == "raw_free_compatibility_projection"
    assert set(payload) == {
        "artifact_type",
        "projection_version",
        "status",
        "funnel",
    }
    assert set(payload["funnel"]) == {"improved_evidence"}
    assert payload["funnel"]["improved_evidence"]
    assert all(
        tuple(row) == cleanup.Q002_LOADER_FIELDS
        for row in payload["funnel"]["improved_evidence"]
    )
    assert not (
        cleanup.UNAPPROVED_RAW_FIELDS
        & {key.casefold() for key in _all_mapping_keys(payload)}
    )


def _all_mapping_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _all_mapping_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _all_mapping_keys(item)
