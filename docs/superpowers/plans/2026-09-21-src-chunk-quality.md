# SRC Chunk Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve current `src` chunk contents while adding automatic chunk-quality checks and a plain-Korean administrator summary.

**Architecture:** Put pure quality calculations in `src/chunking.py`, call them from `src.library.make_chunks`, persist the summary with document metadata, and render it in `src.admin_ui`. Existing version-4 evaluation fixtures remain reproducible through an explicit private compatibility argument.

**Tech Stack:** Python, pytest, Streamlit, PostgreSQL/Supabase migration SQL

**Spec:** `docs/superpowers/specs/2026-09-21-src-chunk-quality-design.md`

## Global Constraints

- Do not change produced chunk text, order, size, or overlap behavior.
- Do not automatically reindex existing documents.
- Fail registration before persistence only for empty or over-110-token chunks.
- Keep repeated labels and heading markers as informational counts.
- Do not send source documents to an external provider.

---

### Task 1: Pure quality checks

**Files:**
- Create: `src/chunking.py`
- Create: `tests/test_chunking_v5.py`

**Interfaces:**
- Produces: `repeated_edge_labels(pages)`, `repeated_edge_label_count(pages, labels)`, and `validate_chunk_texts(texts, count_tokens, max_tokens=110)`.

- [x] Write tests for repeated edge labels, table/sentence exclusions, empty chunks, oversized chunks, and duplicates.
- [x] Run `pytest tests/test_chunking_v5.py -q` and confirm import failure.
- [x] Add the minimal pure implementation.
- [x] Run the focused tests and confirm they pass.

### Task 2: Integrate checks without changing chunks

**Files:**
- Modify: `src/library.py`
- Modify: `src/structure.py` only if the existing public heading detector is unavailable.
- Modify: `tests/test_chunking_v5.py`

**Interfaces:**
- Consumes: Task 1 quality functions and `src.structure.looks_like_heading`.
- Produces: `make_chunks(..., _chunk_version=CHUNK_VERSION)` metadata containing `chunk_quality` for version 5.

- [x] Add tests for version-5 metadata, the quality gate, heading counts, and version-4 fixture compatibility.
- [x] Run the focused tests and confirm the new assertions fail.
- [x] Set `CHUNK_VERSION = 5` and integrate the audit after chunk creation.
- [x] Compare version-4 and version-5 chunk text lists in a regression test.
- [x] Run focused chunking and existing library tests.

### Task 3: Persist and explain results

**Files:**
- Modify: `src/admin_ui.py`
- Modify: `src/migrations/20260910_operational_pgvector.sql`
- Create: `tests/test_admin_chunk_quality.py`

**Interfaces:**
- Consumes: document metadata field `chunk_quality`.
- Produces: `chunk_quality_caption(document)` and a nullable JSONB storage column.

- [x] Add failing tests for the easy Korean caption and legacy-document message.
- [x] Add the caption helper and display it in document management.
- [x] Add `chunk_quality jsonb` to the operational schema and publish/reindex functions.
- [x] Run focused administrator, repository, and schema tests.

### Task 4: Compatibility, documentation, and verification

**Files:**
- Modify: evaluation tools that intentionally consume frozen version-4 fixtures.
- Create: `docs/07_쉬운_작업일지/2026-09-21_청킹_자동검사.md`
- Create: `workspace/과거작업/문서이력/superpowers/progress/2026-09-21-src-chunk-quality.md`

**Interfaces:**
- Consumes: `make_chunks(..., _chunk_version=4)` for frozen evaluation fixtures.
- Produces: passing focused tests, full tests, Ruff, docs view, and operational UAT evidence.

- [x] Update only frozen evaluation paths to request version 4 explicitly.
- [x] Add a nontechnical work log and progress record.
- [x] Run focused tests, full `pytest`, Ruff, `git diff --check`, docs-view build, secret audit, and the operational UAT.
- [x] Review the final diff to confirm no automatic reindex or chunk-content rewrite was introduced.
