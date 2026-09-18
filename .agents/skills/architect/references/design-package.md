# Design package

Use this structure for a substantial implementation-ready design. It is a selection guide, not a required template. Omit irrelevant sections, combine short ones, and do not invent alternatives, risks, or open questions to fill headings.

## Problem and constraints

State what must change, who needs it, and which observed mechanics, documented contracts, compatibility requirements, or approved decisions constrain the shape. Separate facts from assumptions and unknowns.

## Caller usage

Write this before the types. Show realistic imports or dependencies, inputs, calls, results, and failures from the caller's view. Include distinct call patterns that materially affect the design; do not invent consumers.

The caller examples and the proposed surface must agree. When they diverge, either revise the shape or explain why the usage assumption was wrong.

## Data and interface shape

Define the core data structures, public types, signatures, and meaningful return or error forms. Trace dominant access patterns through the structures. State which complexity the public surface hides and why it is no larger than current callers need.

Use target-language sketches when useful, but label pseudocode and unimplemented bodies. Do not imply that a sketch compiles or has been executed.

## Module ownership and flow

Map responsibilities to files, modules, components, or processes at the level the implementation needs. Trace one representative operation from input through state changes and side effects to output. Name the owner of shared state, resources, and cleanup.

## Invariants and boundaries

State the load-bearing invariants and where each is encoded or validated. Cover trust boundaries, errors, concurrency, cancellation, idempotency, persistence, compatibility, and lifecycle only when relevant.

## Synthesis decision

Include this section only when several candidates were compared. Name the selected base, the criteria that decided it, compatible ideas adapted from other candidates, meaningful rejections, and any convergence or framing failure. Keep one coherent mental model.

## Trade-offs accepted

Record choices a future implementer might otherwise mistake for oversights. A useful form is: “We accept X in exchange for Y.” Include only material trade-offs supported by the design.

## Alternatives considered

Describe the natural number of credible whole-shape alternatives and why they lost against the established criteria. If constraints leave one viable shape, say so and identify the eliminating constraints. Do not list cosmetic variants.

## Open questions and risks

List only decisions that still require an answer and credible risks that could change implementation or verification. Name the owner or evidence needed when known. Do not convert an unknown into an assertion.

## Implementation sequence

Order the smallest implementation slices by dependency and observable result. Identify behavior that must remain unchanged. Separate required work from optional follow-ups, and avoid scaffolding that exists only to support a test.

## Verification boundary

State what focused checks can establish, which integration or runtime boundaries remain unverified, and what evidence would be needed before broader claims are justified.
