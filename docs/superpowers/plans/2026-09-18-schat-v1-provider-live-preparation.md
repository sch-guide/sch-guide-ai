# SCHAT v1.0 Provider Live Preparation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development to implement this plan task-by-task. Do not use subagents for this workspace task.

**Goal:** Build an offline-only Groq/Gemini controlled-generation comparison harness, TF027 human-review checklist, operational UAT fixture, and v1.0 completion contract without any provider call or production retrieval change.

**Architecture:** A provider-neutral case is converted into two in-memory wire-format blueprints. Mock envelopes are normalized to the existing controlled-generation validator, while persisted reports contain hashes and counts only. Human-review and UAT contracts remain fixtures and documents outside production routing.

**Tech Stack:** Python 3.12, Pydantic, pytest, JSON/HTML artifacts; no new dependency or SDK.

**Spec:** `docs/superpowers/specs/2026-09-18-schat-v1-provider-live-preparation-design.md`

## Global Constraints

- Actual Groq, Gemini and vision API calls: 0.
- Do not add API key handling, HTTP transport or a live execution flag.
- Do not change production retrieval, embedding, Facet-slot or validators.
- Persist no raw prompt, raw response or SourceUnit text.
- Keep TF027 `needs_human_review=true` until a human approves it.
- Preserve annotated tag `v0.9`; do not commit, tag or push without a separate request.

---

### Task 1: Offline provider comparison harness

**Files:**
- Create: `tools/provider_controlled_generation_evaluate.py`
- Test: `tests/test_provider_controlled_generation_harness.py`

**Interfaces:**
- Consumes: `SourceUnit`, `build_controlled_generation_prompt()`, `build_controlled_generation_schema()`, `decide_controlled_generation()`
- Produces: `ProviderBlueprint`, `build_provider_blueprints()`, `normalize_mock_response()`, `evaluate_mock_providers()`, `safe_comparison_report()`

- [x] Write failing tests proving identical evidence/schema fingerprints, provider-specific JSON schema placement, absence of network/API-key fields, envelope normalization, fail-closed Mock validation, and raw-free reports.
- [x] Run `pytest tests/test_provider_controlled_generation_harness.py -q` and confirm collection/import failure for the missing harness.
- [x] Implement immutable blueprint/result types and pure request builders without importing `httpx`, `requests` or provider SDKs.
- [x] Implement provider envelope parsers and route both through the existing controlled-generation decision.
- [x] Implement a raw-free report that exposes hashes, counts and validation outcomes only.
- [x] Run the focused tests and confirm all pass.

### Task 2: TF027 and operational UAT contracts

**Files:**
- Create: `tests/fixtures/tf027_image_human_review_checklist.json`
- Create: `tests/fixtures/schat_v1_operational_uat.json`
- Create: `tests/test_schat_v1_readiness_contracts.py`
- Create: `docs/rag/61_TF027_IMAGE_HUMAN_REVIEW_CHECKLIST.md`
- Create: `docs/rag/62_SCHAT_V1_COMPLETION_CRITERIA.md`

**Interfaces:**
- Consumes: current text/table/image/mixed routing names and abstention contract
- Produces: machine-checkable review/UAT fixtures and human-facing completion criteria

- [x] Write failing fixture-contract tests for TF027 pending state, required human checks, UAT type coverage, provider-call policy and v1.0 gates.
- [x] Run the fixture tests and confirm failure because the files do not exist.
- [x] Add a raw-free TF027 checklist with no inferred workflow values.
- [x] Add 36 operational UAT cases spanning sedation, transfusion, table, image-pending and out-of-scope behavior.
- [x] Document human review and v1.0 completion gates.
- [x] Run the fixture tests and confirm all pass.

### Task 3: Offline Mock evaluation artifacts

**Files:**
- Create: `tools/provider_controlled_generation_evaluate.py` CLI entry point
- Create: `artifacts/2026-09-18_schat-v1-provider-preparation/`
- Test: `tests/test_provider_controlled_generation_harness.py`

**Interfaces:**
- Consumes: synthetic SourceUnits and synthetic provider envelopes only
- Produces: `comparison_manifest.json`, `mock_results.json`, `security_audit.json`, `review.html`

- [x] Add a test that runs the evaluator against synthetic evidence and asserts network calls remain zero.
- [x] Confirm the new test fails before adding the CLI entry point.
- [x] Add a deterministic offline evaluator and generate raw-free artifacts.
- [x] Audit artifacts against forbidden text fields, secret markers and synthetic raw evidence.
- [x] Run focused evaluator tests and inspect the HTML report.

### Task 4: Current-source documents and verification

**Files:**
- Create: `docs/rag/63_PROVIDER_LIVE_CONTROLLED_GENERATION_PREPARATION_RESULT.md`
- Create: `docs/superpowers/progress/2026-09-18-schat-v1-provider-live-preparation.md`
- Modify: `docs/현재정본/00_현재상태.md`
- Modify: `docs/현재정본/01_요구사항.md`
- Modify: `docs/현재정본/02_전체구조.md`
- Modify: `docs/현재정본/04_답변생성.md`
- Modify: `docs/현재정본/05_표와이미지.md`
- Modify: `docs/현재정본/06_테스트현황.md`
- Modify: `변경이력.md`
- Modify: `복구기준점.md`
- Modify: `artifacts/프로젝트현황/index.html`

**Interfaces:**
- Consumes: verified focused/full test results and artifact audit
- Produces: current-source documentation that keeps v0.9 official and records a v1.0 preparation candidate

- [x] Run focused provider/readiness tests.
- [x] Run full pytest, Ruff and `git diff --check`.
- [x] Review the complete diff for production changes, unsafe persistence and accidental network paths.
- [x] Re-run artifact security audit.
- [x] Update current-source, history, recovery and dashboard documents with measured results only.
- [x] Re-run final focused tests and static checks after documentation updates.
