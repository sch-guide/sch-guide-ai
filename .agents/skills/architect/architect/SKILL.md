---
name: architect
description: Produce an implementation-ready, caller-first structural design for a sufficiently understood engineering problem. Manual invocation only; use when the user explicitly asks to architect a change or define its types, signatures, data structures, and module boundaries. Use brainstorm for unresolved future behavior and explain-code for a neutral current-code walkthrough.
disable-model-invocation: true
license: See LICENSE
---

# Architect

Turn an understood engineering problem into a structural design that an implementer can follow without rediscovering its boundaries.

## Invocation and boundary

Run only after explicit invocation. Do not activate merely because a task is non-trivial.

Accept a problem whose intended behavior and important constraints are sufficiently understood. When consequential requirements, product behavior, ownership, or alternatives are still unresolved, use `brainstorm` if it is installed and loadable. Otherwise ask one focused question at a time and establish those decisions locally before continuing.

The default deliverable is a design, not production code. Begin implementation only when the original request clearly includes it or the user approves it after reviewing the design. A request for a persistent design artifact authorizes only that artifact, not implementation.

## Preserve the repository and access boundary

Inspect local evidence before designing. When a repository is present, record its initial state before running commands that may generate files.

- Keep tracked files unchanged unless the user authorized a persistent artifact or implementation.
- Never reset, clean, overwrite, stage, or delete pre-existing work.
- Remove only temporary resources created by this workflow.
- Keep evidence local by default. Use remote sources or environments only when the user requested access and the privacy boundary permits it.
- Do not reproduce credentials, tokens, private keys, cookies, sensitive personal data, or unrelated proprietary content. Use a redacted placeholder and report the exposure.
- Do not commit or push unless separately requested.

Keep the design in the response unless the user requests a destination.

## Compose with optional skills

When installed and loadable:

- use `explain-code` to establish current mechanics and flow;
- use `clarify` for expectation mismatches or historical rationale;
- use `arena` for credible, consequential structural alternatives;
- use applicable project coding skills for language, framework, build, test, and implementation conventions;
- use `technical-writing` for a substantial persistent design artifact;
- use `unslop` for the final prose pass.

These skills are optional. When one is unavailable, perform the smallest equivalent local investigation, comparison, or prose check here. Do not claim that an unavailable skill ran, repeat evidence already established, or require a named model, provider, tool, command, or agent harness.

## Ground the system

Build the smallest accurate model of the systems the design will touch. Naming files is not grounding.

Trace relevant:

- callers and entry points;
- current ownership and module boundaries;
- data and control flow;
- state, lifecycle, concurrency, and cleanup;
- configuration, persistence, and external contracts;
- tests and documented compatibility constraints.

Distinguish observed mechanics, documented contracts, historical rationale, and unknowns. Treat existing structure as a constraint only when evidence establishes that it must remain. For genuinely greenfield work, state the available constraints and skip repository tracing that has no target.

## Design from caller usage

Write realistic caller usage before defining types. Show what a caller imports or receives, what it supplies, what it invokes, and what returns or fails. Use two or three examples when materially different call patterns exist; do not invent consumers to fill a quota.

Derive the shape from that usage:

1. Choose core data structures and trace every dominant lookup, update, iteration, and merge through them.
2. Define the smallest public types and signatures that serve the callers.
3. Assign each invariant and source of truth to one owning module.
4. Keep transport, storage, framework, and representation details behind their boundaries unless callers genuinely own them.
5. Make validation and error behavior explicit at trust and type boundaries.
6. Address state ownership, concurrent writers, cancellation, idempotency, partial failure, persistence, compatibility, and cleanup only where the problem makes them relevant.
7. Encode invariants structurally when the target language permits it; use runtime validation when types cannot establish them.

Prefer a small interface that hides meaningful policy and coordination. Do not replace a direct function with a service, framework, extension point, or generic protocol without multiple current consumers or an existing contract that requires it.

## Explore proportionally

Use `arena` only when several credible whole shapes exist, an ownership decision is consequential, uncertainty makes the first shape risky, or the user explicitly requests it. Give every candidate the same grounding and require complete caller usage, data shape, interfaces, module ownership, and rationale.

When `arena` is unavailable but comparison is warranted, produce at least two structurally independent sketches from the same frame, compare them against explicit criteria, and disclose that sequential parent-only attempts have weaker independence. Do not let one sketch become an incremental edit of the other.

When one shape is clearly supported, present it and explain why apparent alternatives do not fit. Do not manufacture alternatives or invoke a multi-candidate workflow to satisfy a quota.

## Pressure-test the shape

For non-trivial architecture, read [references/design-review.md](references/design-review.md). Revise or reject a shape when evidence shows that it:

- exposes more interface than the complexity it hides;
- leaks internal representation or policy across modules;
- scatters one invariant across execution stages;
- adds pass-through layers without policy or adaptation;
- mismatches dominant data access patterns;
- duplicates sources of truth;
- leaves shared state or lifecycle ownership unclear;
- creates long coordination chains;
- introduces speculative abstractions without current consumers.

These are diagnostic signals, not automatic failures. Preserve legitimate domain complexity, and keep a direct function when it is the clearest sufficient boundary.

## Return the design

For a substantial result, read [references/design-package.md](references/design-package.md). Adapt the package to the task and omit empty sections.

A useful design normally establishes:

- the problem and evidence-backed constraints;
- caller usage;
- data and interface shape;
- module ownership and end-to-end flow;
- relevant invariants, validation, errors, state, and lifecycle behavior;
- the synthesis decision when multiple candidates were compared;
- accepted trade-offs and real rejected alternatives;
- open questions and risks;
- the smallest dependency-ordered implementation sequence;
- the verification boundary for implementation.

Label pseudocode and unimplemented bodies clearly. Do not present a sketch as compiled or executed code. Keep genuine unknowns visible rather than filling them with a plausible design assumption.

## Follow through after implementation approval

After explicit implementation approval, follow repository instructions and load applicable project coding guidance when available. Implement the smallest agreed slice, then compare the result with the design.

Treat deviations as evidence. For an isolated mismatch, determine whether a requirement was missed, the design was wrong, or the implementation is overreaching, then make the narrowest correction. Do not silently widen interfaces.

Re-ground and redesign only when repeated deviations share the same cause, for example:

- recurring escape-hatch types, casts, or meaningless optional fields;
- unrelated edge cases requiring the same special branch;
- repeated ownership or synchronization workarounds;
- callers repeatedly learning internal rules;
- several implementation steps needing the same unplanned parameter or layer.

A hard case or one correction does not condemn the architecture. When the shape is disproven, remove the failed approach instead of preserving compatibility scaffolding for it, incorporate the implementation evidence, subtract unnecessary structure, and design again.

## Final check

Before returning a design or approved implementation, verify:

- real callers can use the proposed surface without learning hidden internals;
- data structures support the dominant access patterns;
- every important invariant and mutable state has an owner;
- complexity is hidden where justified rather than moved into extra layers;
- trade-offs, unknowns, and evidence limits are explicit;
- no speculative mechanism or unused extension point remains;
- persistence, repository, privacy, and implementation-approval boundaries were respected.
