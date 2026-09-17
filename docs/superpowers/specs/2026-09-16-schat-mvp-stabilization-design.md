# SCHAT MVP Stabilization Design

## Status and authorization

This design executes the user's pre-approved stabilization scope without an
intermediate approval checkpoint. It extends, rather than recreates, the
existing transfusion retrieval baseline. A phase stops only at one of the
explicit safety conditions in the request.

## Goals

1. Turn the 14 pending transfusion gold cases into an auditable review queue,
   approving only relationships that are unambiguous from page structure and
   extracted catalog metadata.
2. Add 30–50 independently authored paraphrases and compare BM25, ChromaDB,
   and evaluation-only RRF Hybrid under one frozen contract.
3. Select a retrieval strategy using measured retrieval quality, latency, and
   failure-category evidence instead of implementation novelty.
4. Add evaluation-only table and figure retrieval units without changing the
   105 production chunks or treating OCR/vision output as verified clinical
   facts.
5. Exercise the selected path with offline chat/UAT fixtures before any
   external generation provider is considered.

## Non-goals and invariants

- No Groq or Gemini generation call is authorized by this design.
- The production chunk catalog, chunk IDs, embedding model, generation
  provider, Facet-slot selection, selection limit, AnswerCoverage,
  `validate_answer()`, citation checks, parent atomicity, and Q006 zero-call
  boundary stay unchanged.
- No question-string exception or transfusion-specific production ontology is
  introduced.
- Artifacts contain IDs, page numbers, structural metadata, hashes, scores,
  ranks, metrics, counts, and review decisions, but not the source document or
  full extracted clinical text.

## Trust model for gold review

Each reviewed case has one of these states:

- `approved_text`: a substantive body chunk already supports the reference.
- `approved_structural_table`: row/column/header relationships and all relevant
  numbers/units are unambiguous in the rendered source page and catalog
  references.
- `approved_nearby_text`: nearby substantive text fully states the answer;
  the image is illustrative and not needed for the fact.
- `needs_human_review`: OCR/layout ambiguity, image interpretation, diagram
  relationship, or uncertain number/unit remains.

Automated review may promote a case only to the first three states. It may not
invent table cells, interpret a picture, or infer clinical relationships.
Every promotion records page, source shape, reference IDs, decision basis,
and a source fingerprint rather than source text.

## Expanded retrieval dataset

The frozen baseline questions remain unchanged. New question IDs are added as
paraphrase cases and point to references that were established independently
of retrieval. Variants cover natural phrasing, colloquial nursing searches,
blood-product abbreviations, time/speed/unit language, table expressions,
adverse reactions, follow-up, and negative/out-of-scope prompts.

Metrics use only positive cases whose review state is approved. Pending review
cases remain visible in per-case artifacts but do not enter aggregate IR or
RAGAS-ID means. Negative questions remain a separate diagnostic and never enter
positive Hit/Recall/Precision means.

## Retrieval comparison contract

BM25, ChromaDB, and RRF Hybrid share:

- the same 105 catalog chunks and stable IDs;
- the same question and gold fixture;
- Top-K 1, 3, 5, and 10;
- the existing multi-gold metric formulas;
- the same per-query latency boundary and result-row schema;
- the same positive/review/negative inclusion rules.

Standalone quality is compared first. Hybrid remains evaluation-only. BM25 is
the production-primary candidate only if the expanded approved set confirms it
is no worse on Hit@10 and Recall@10 and materially better in the aggregate or
in the exact-medical-term categories, without a paraphrase failure pattern
that Hybrid fixes. ChromaDB is not selected merely because it is vector based.

## Production-change gate

An evaluation winner does not automatically justify changing production.
Before any production edit, the current retrieval funnel is inspected to
determine whether it already provides BM25-first candidate generation. A
change is allowed only when it is both minimal and demonstrated by offline UAT
to improve the selected evidence while preserving every safety invariant. If
the existing production path already satisfies the chosen strategy, the result
is recorded as `production_change_not_required`.

## Table-aware retrieval

Table units are evaluation sidecars derived from PDF layout, not replacements
for production chunks. Deterministic IDs are derived from document identity,
page, table index, and unit coordinates. A unit records only:

- `table_id`, `page`, `caption`, `parent_table_id`;
- `unit_kind` (`whole_table`, `header_row`, or `row`);
- row/column/header coordinates;
- source chunk IDs and fingerprints;
- a local searchable representation held only during evaluation.

Evaluation artifacts omit full cell text. Table gold requires an approved
structural relationship. The table-aware track is reported separately from
the text-only baseline.

## Figure-aware retrieval

Figure units record `figure_id`, page, caption fingerprint, bounding box,
nearby-text chunk IDs, and confidence. Decorative images are excluded. The
`vision_description` field stays absent unless a human-approved description is
available. OCR output or spatial arrangement is never promoted directly to a
clinical fact.

If a figure question cannot be grounded without interpreting the image, its
gold stays pending and figure clinical evaluation stops at that boundary. This
does not invalidate completed text/table evaluation.

## Multimodal tracks

Three tracks remain separate:

1. `text_only`: the 105 catalog chunks.
2. `table_aware`: text chunks plus approved table sidecars.
3. `multimodal_aware`: table-aware plus approved figure/caption sidecars.

Each uses the same metric definitions and latency boundary. Results are not
merged when their approved case sets differ.

## Offline UAT and external-provider gate

After retrieval selection, offline UAT covers sedation and transfusion across
purpose, procedure, preparation, monitoring, adverse reaction, product,
branch, temporal, paraphrase, summary, comparison, table/image, and negative
questions. The test asserts evidence-stage behavior and safety without calling
a generation provider.

Groq/Gemini adapter comparison is a later gate. Hospital-data transmission to
Gemini (including a free tier) requires an explicit security/policy decision
and any required external-account authorization. Until that decision exists,
the workflow records the provider comparison as blocked and makes zero calls.

## Required outputs

- Updated retrieval fixture and audit metadata.
- Expanded retrieval artifacts and strategy comparison.
- Table/figure manifests and separate-track metrics where safely evaluable.
- Offline UAT results.
- `docs/rag/42_SCHAT_MVP_STABILIZATION_RESULT.md` with decisions, evidence,
  remaining limits, and the exact external-provider gate.
