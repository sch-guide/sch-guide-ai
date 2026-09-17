# Multilingual E5 Large Fairness Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 동일한 수혈 105 chunks와 90문항 gold 계약에서 `intfloat/multilingual-e5-large`를 evaluation-only로 평가해 BM25 우세가 MiniLM 선택에 의존하는지 판정한다.

**Architecture:** 기존 fairness artifact의 BM25 minimal/current 및 current MiniLM 결과를 고정 baseline으로 읽고, 새 runner가 catalog body에 공식 `passage:` prefix와 query에 `query:` prefix를 붙여 FastEmbed E5 vector를 생성한다. Exact normalized cosine Top-10을 동일 metric helper로 평가하고 raw-free JSON/CSV/HTML만 별도 artifact에 저장한다.

**Tech Stack:** Python 3.12, FastEmbed 0.8.0, ONNX Runtime 1.30.0, NumPy, pytest, Ruff

**Spec:** `docs/superpowers/specs/2026-09-17-bm25-vector-fairness-validation-design.md`

## Global Constraints

- Production retrieval, embedding, dependencies, requirements와 catalog를 변경하지 않는다.
- 동일 문서, 105 chunk IDs, 90문항, approved gold와 Top-K 1/3/5/10을 사용한다.
- E5 query/document prefix는 각각 `query: `와 `passage: `로 고정한다.
- 병원 원문, 질문, raw vector와 provider response를 artifact에 저장하지 않는다.
- 외부 generation/embedding API를 호출하지 않는다.
- Git commit/push를 수행하지 않는다.

---

### Task 1: Model cache preparation

**Files:**
- Download only: `data/models/models--qdrant--multilingual-e5-large-onnx/`

- [ ] FastEmbed official registry에서 model ID, prefix, dimension, size를 확인한다.
- [ ] 저장공간과 existing dependency 호환성을 확인한다.
- [ ] 승인된 network access로 model을 `data/models` local cache에 다운로드한다.
- [ ] neutral public probe만 encode하여 1024 dimensions와 local load를 확인한다.

### Task 2: Evaluation contract through TDD

**Files:**
- Create: `tests/test_multilingual_e5_fairness.py`
- Create: `tools/multilingual_e5_fairness_evaluate.py`

- [ ] Prefix, baseline contract, exact cosine, verdict A/B/C와 raw-free artifact를 요구하는 실패 테스트를 작성한다.
- [ ] 테스트가 module/behavior 부재로 실패하는지 확인한다.
- [ ] E5 adapter와 동일 dataset/gold metric aggregation을 최소 구현한다.
- [ ] 집중 테스트를 통과시킨다.

### Task 3: Full 90-case evaluation

**Files:**
- Create: `artifacts/2026-09-17_multilingual-e5-large-fairness-validation/*`

- [ ] 105 passage vectors를 local inference로 구축한다.
- [ ] 90 query vectors와 exact cosine Top-10을 계산한다.
- [ ] Positive 78개 aggregate, negative diagnostic와 question type breakdown을 계산한다.
- [ ] BM25 minimal/current/current MiniLM과 E5를 비교해 A/B/C 판정을 생성한다.
- [ ] source/question/secret 없는 artifacts를 생성하고 검사한다.

### Task 4: Verification and report

**Files:**
- Create: `docs/rag/45_MULTILINGUAL_E5_LARGE_FAIRNESS_RESULT.md`
- Create: `docs/superpowers/progress/2026-09-17-multilingual-e5-large-fairness-final.md`

- [ ] 집중 및 retrieval regression을 실행한다.
- [ ] 전체 pytest, Ruff와 `git diff --check`를 실행한다.
- [ ] evaluation diff와 artifact security boundary를 code-review한다.
- [ ] metric, 유형별 결과, latency, 판정, production 비변경을 문서화한다.
