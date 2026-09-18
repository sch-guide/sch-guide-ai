# Design review

Use this checklist to pressure-test a non-trivial structural design before implementation. A match is a reason to investigate, not an automatic rejection. Keep legitimate domain complexity and prefer a direct function when no deeper boundary is needed.

## Interface depth

Compare the capability and policy hidden by a public surface with the size and complexity of that surface.

Look for:

- callers coordinating several methods to complete one operation;
- public options that expose internal stages or implementation choices;
- an interface that requires callers to understand the implementation;
- a broad API whose implementation merely forwards requests elsewhere.

A deep module concentrates useful capability behind a small surface. It is not the same as a deep call chain, which spreads one operation across layers.

## Information leakage

Check whether multiple modules depend on the same internal representation, policy, protocol, or framework decision.

Common signs include:

- transport or wire types crossing into domain callers;
- storage schemas or framework objects appearing in public signatures;
- the same parsing, normalization, ordering, or retry rule repeated in several modules;
- one internal change requiring coordinated edits across unrelated owners.

Parse or adapt external representations at their boundary when the domain can own a stable internal form. Do not hide a detail that callers genuinely need to control.

## Temporal decomposition

Check whether modules are divided by execution order rather than by the knowledge and invariants they own. Separate load, validate, transform, and save stages can scatter one representation and its rules across several boundaries.

Keep operations together when they protect the same domain decision, even if their methods execute at different times. Retain a pipeline when its stages are independently meaningful, replaceable, or observable contracts rather than arbitrary time slices.

## Pass-through layers

Find methods or modules that forward the same arguments and result without adding policy, adaptation, ownership, or a distinct abstraction.

Remove the layer or move responsibility to the component that can complete the operation. Keep a forwarding boundary when it enforces authorization, translation, transaction ownership, compatibility, observability, or another real policy.

## Data and access patterns

Trace each dominant operation through the proposed structures:

- lookup and membership;
- ordered iteration;
- insertion, update, and deletion;
- grouping, aggregation, and merge;
- persistence and serialization.

If a required index, cache, or parallel structure is deferred without an ownership or consistency story, reconsider the primary shape. Do not optimize an access pattern that is not demonstrated or consequential.

## Sources of truth and invariants

For each important invariant, ask:

- Which module owns it?
- Which representation is authoritative?
- Can another value be derived instead of synchronized?
- Which boundary validates it?
- What prevents an invalid transition or partial update?

Duplicated mutable representations need an explicit synchronization contract. Prefer one source of truth when derivation is practical.

## State, concurrency, and lifecycle

Identify every writer and resource owner. If more than one actor can write, define ordering, conflict, retry, and failure semantics rather than assuming coordination will work.

Check creation, cancellation, shutdown, cleanup, and partial failure for stateful or resource-owning components. Separation and merge may be clearer than shared mutation when the domain permits it, but do not force that pattern when the state is inherently shared.

## Reader coordination cost

Trace one representative operation end to end. Reconsider the shape when understanding it requires reconstructing one decision across many files, pass-through calls, or duplicated configuration points.

Short call chains are not a numeric rule. Add a boundary when it hides meaningful complexity; remove one when it only relocates navigation.

## Speculative structure

Challenge:

- extension points with no current extension;
- generic services with one consumer;
- protocols introduced for hypothetical substitution;
- configuration for choices the product does not support;
- new lifecycle or ownership machinery created only for tests.

Keep an abstraction when several current consumers need it or an established contract requires it. Otherwise prefer the local type, function, or module that solves the demonstrated problem.
