# UAT-T01 Phase-aware Controlled Generation Plan

## Scope

- Keep production retrieval, Gold, and validator thresholds unchanged.
- Change only the evaluation-only controlled-generation preparation and contract.
- Make exactly one UAT-T01 Groq call after local tests pass.

## Root cause

1. The emergency replacement condition is present in the raw Top-10 candidate set,
   but the non-procedure evidence admission gate drops it because the candidate does
   not repeat the generic question verb `확인`.
2. The Live adapter sends whole chunks and overwrites every phase with
   `unspecified`, so ordered checklist rows cannot be enforced independently.
3. The provider contract requires every selected chunk, including re-release and
   post-start content, to appear in a preparation answer.

## TDD steps

1. Add failing tests for phase projection, condition-candidate preservation,
   action order, condition omission, and broad-procedure preservation.
2. Build request-local SourceUnits from existing atomic evidence units while
   preserving order/phase metadata and hashing all production identifiers.
3. For a bounded preparation phase, retain exact pre-phase atomic evidence,
   stop at explicit later workflow phases, attach relevant same-parent condition
   evidence, and preserve numbered source order.
4. Extend the provider-neutral prompt with requested phase and source-order rules.
5. Make the Live safety result fail when required actions exist but appear out of
   order; do not weaken any existing invariant.
6. Run focused and safety regression tests, Ruff, and `git diff --check`.
7. Execute exactly one UAT-T01 Groq revalidation and report whether the five-case
   subset may resume. Do not run the subset in this task.
