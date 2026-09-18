---
name: code-review
description: Find actionable defects and risks through a focused, evidence-based review of correctness, regressions, security, performance, architecture, and testing. Use for requests to review, audit, critique, or identify improvements. Use blast-radius, when explicitly invoked and available, for cross-boundary consequence tracing and executable safety proof of a concrete change. Use explain-code when the user only wants to understand current behavior or architecture.
---

# Evidence-Based Code Review

Review the requested code or diff in repository context. Load applicable language/framework skills and follow repository instructions before judging conventions.

## Scope

- Review the user-specified target. For uncommitted work, inspect the complete diff and relevant call paths rather than auditing unrelated code.
- Distinguish regressions introduced by the change from pre-existing issues.
- Use `blast-radius`, when explicitly invoked and available, when the primary task is to trace what a concrete change could break beyond the obvious diff and prove its decision-critical safety assumptions.
- Do not broaden a focused review into an architecture, dependency, formatting, or security program without approval.

## Finding Standard

Report an issue only when you can explain:

1. The concrete failure or maintainability cost.
2. The reachable code path or credible conditions that trigger it.
3. The actual impact and proportionate severity.
4. The smallest practical correction.

Use file paths and line numbers. Do not inflate severity, list theoretical possibilities as defects, or manufacture findings to appear comprehensive. If no actionable issues exist, say so.

## Review Priorities

1. Correctness, data integrity, and user-visible regressions.
2. Cancellation, concurrency, cleanup, and error propagation on realistic paths.
3. Security at actual trust and privilege boundaries.
4. Performance on demonstrated hot paths or where complexity clearly creates material cost.
5. Tests for likely behavior and important boundaries.
6. Readability and maintainability of the changed design.

## Complexity and Security Guardrails

- Flag overengineering when a direct fix became a framework, protocol, ownership system, stack of limits, or abstraction with one production consumer.
- Prefer existing primitives and local fixes. Do not recommend design patterns, generalized services, scalability work, or dependencies for hypothetical future use.
- Security findings must identify the producer, trust boundary, attacker capability, and plausible impact. Do not import public-server or multi-tenant assumptions into a different deployment model.
- Do not recommend arbitrary quotas, timeouts, sanitizers, or deny-by-default behavior that rejects valid inputs without a contract or credible risk.
- Tests prove only the boundary they execute. Do not treat mocks, source assertions, or unit tests as proof of native, packaged, external-service, or end-to-end behavior.
- Do not request production hooks solely to test implausible failures.

## Regression and Diff Audit

Check whether the change:

- Preserves known-good behavior outside the requested fix.
- Adds unrelated scope, dead helpers, duplicate lifecycle paths, speculative limits, unused dependencies, or compatibility scaffolding for failed approaches.
- Reimplements behavior already owned by a mature library.
- Claims integration behavior that was not exercised at the relevant boundary.
- Has a simpler rollback or local alternative.

## Output

List actionable findings first, ordered by severity. Keep summaries concise. Separate residual/manual validation risks from code defects. Avoid praise-heavy filler, exhaustive low-value suggestions, and implementation plans the user did not request.
