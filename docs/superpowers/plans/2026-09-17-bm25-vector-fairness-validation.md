# BM25–Vector Fairness Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 동일 수혈 corpus/gold에서 BM25 tuning ablation과 MiniLM vector preprocessing/representation 실험을 실행해 기존 성능 차이의 원인을 판정한다.

**Architecture:** 새 evaluation-only runner가 existing catalog/fixture/metric helpers를 읽고 config별 Top-10을 in-memory로 계산한다. Production module은 import해 현재 계약을 재사용하지만 수정하지 않으며, raw-free JSON/CSV/HTML만 새 artifact에 기록한다.

**Tech Stack:** Python 3.12, NumPy, FastEmbed 0.8.0, existing SCHAT BM25/query helpers, pytest, Ruff

**Spec:** `docs/superpowers/specs/2026-09-17-bm25-vector-fairness-validation-design.md`

## Global Constraints

- Production retrieval, embedding, RRF, reranker, Facet-slot, validator를 변경하지 않는다.
- Groq/Gemini 및 외부 embedding API 호출은 0회다.
- 동일 105 chunks, 90 questions, approved gold, Top-K와 metric을 사용한다.
- Source text와 질문 원문을 artifact에 저장하지 않는다.
- 새 embedding model을 다운로드하거나 production dependency를 추가하지 않는다.
- Git commit/push를 자동 수행하지 않는다.

---

### Task 1: Fairness contract tests

**Files:**
- Create: `tests/test_bm25_vector_fairness.py`
- Create: `tools/bm25_vector_fairness_evaluate.py`

**Interfaces:**
- Consumes: `CatalogChunk`, fixture cases, existing metric helpers
- Produces: `common_query(str) -> str`, BM25/vector config result rows, safe artifact validator

- [ ] Write failing tests for common query normalization, BM25 minimal/current distinction, normalized vector exact ranking, deterministic ranking, current baseline reproduction, verdict rules, and raw-free artifacts.
- [ ] Run the focused test and confirm failure because the evaluation module does not exist.
- [ ] Add the smallest evaluation helpers needed to pass unit tests.
- [ ] Re-run focused tests and keep all assertions at observable result boundaries.

### Task 2: BM25 audit and ablation

**Files:**
- Modify: `tools/bm25_vector_fairness_evaluate.py`
- Modify: `tests/test_bm25_vector_fairness.py`

**Interfaces:**
- Produces: `bm25_minimal_raw`, `bm25_current_baseline`, `bm25_current_canonical`, `bm25_production_query`

- [ ] Add a failing integration test asserting 105 chunk identity and prior current-BM25 Top-10/overall metric reproduction.
- [ ] Implement minimal/raw BM25 and current adapters without changing `mvp/`.
- [ ] Implement common canonical and production-query variants using existing public/current helpers.
- [ ] Record one-axis ablation deltas and latency mean/p95.
- [ ] Run the focused integration tests.

### Task 3: Vector sanity and representation experiment

**Files:**
- Modify: `tools/bm25_vector_fairness_evaluate.py`
- Modify: `tests/test_bm25_vector_fairness.py`

**Interfaces:**
- Produces: current/body and metadata-enriched MiniLM vector result rows

- [ ] Add failing tests for L2 normalization, cosine direction, unique count/dimension, deterministic ranking, token-safe representation, and Chroma artifact agreement audit.
- [ ] Implement exact cosine current vector evaluation from catalog vectors.
- [ ] Implement in-memory MiniLM embedding for body/title-section/document-title representations.
- [ ] Audit local model cache and record alternate embedding as skipped when download is required.
- [ ] Run focused tests and the 90-case experiment.

### Task 4: Metrics, verdict, artifacts, and review

**Files:**
- Modify: `tools/bm25_vector_fairness_evaluate.py`
- Create: `artifacts/2026-09-17_bm25-vector-fairness-validation/*`
- Create: `docs/rag/44_BM25_VECTOR_FAIRNESS_VALIDATION_RESULT.md`

**Interfaces:**
- Produces: required JSON/CSV/HTML, overall/type/axis metrics, A/B/C/D verdict

- [ ] Aggregate approved positive metrics and separate negative diagnostics.
- [ ] Calculate mean and p95 latency for every config.
- [ ] Apply the frozen verdict rule and generate raw-free review HTML.
- [ ] Run artifact audit against all 105 exact source texts and forbidden fields/secrets.
- [ ] Write the result document with audit table, overall/type metrics, limitations, and production non-change.

### Task 5: Verification and progress

**Files:**
- Create: `docs/superpowers/progress/2026-09-17-bm25-vector-fairness-validation-final.md`
- Modify: `artifacts/2026-09-17_bm25-vector-fairness-validation/test_results.json`

- [ ] Run focused evaluation tests.
- [ ] Run existing retrieval regression tests.
- [ ] Run full pytest.
- [ ] Run Ruff and `git diff --check`.
- [ ] Review the complete evaluation diff and artifact security boundary.
- [ ] Record commands/results, pending alternate-model boundary, and next step in progress/result documents.
