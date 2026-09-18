# Architect skill

Produces an implementation-ready, caller-first structural design for a sufficiently understood engineering problem.

## Activation

Manual only. Use the host's explicit skill-invocation mechanism. In Pi:

```text
/skill:architect Architect the retry scheduler before implementation.
```

Other example requests:

- "Architect the types, signatures, and ownership for this approved caching design."
- "Define the caller API and module boundaries for the import pipeline."
- "Architect and implement this change, stopping if the agreed shape is disproven."

The first two requests authorize design only. The third explicitly includes implementation. Requesting a design file authorizes that artifact, not production changes.

## Behavior

- Grounds the design in current callers, ownership, flow, contracts, and relevant lifecycle behavior.
- Writes realistic caller usage before deriving data structures, types, signatures, and module boundaries.
- Traces dominant access patterns and assigns invariants, state, and cleanup to clear owners.
- Pressure-tests interface depth, information leakage, temporal decomposition, pass-through layers, duplicated truth, coordination cost, and speculative abstractions.
- Uses several candidates only when credible structural alternatives or consequential uncertainty justify them.
- Returns the design in the response unless the user requests a persistent destination.
- Begins implementation only after explicit authorization and treats repeated same-cause deviations as evidence that the architecture needs revision.

For one clearly supported shape, the skill does not invent alternatives. When several candidates are warranted but `arena` is unavailable, it performs a disclosed sequential fallback with weaker independence.

## Optional composition

When installed and loadable, `architect` can use:

- `explain-code` for current mechanics;
- `clarify` for expectation mismatches or historical rationale;
- `brainstorm` for unresolved future behavior or ownership;
- `arena` for independent whole-shape candidates and coherent synthesis;
- project coding skills for implementation conventions;
- `technical-writing` for a substantial persistent design artifact;
- `unslop` for the final prose pass.

These integrations are optional. The core includes local fallbacks and does not require a named model, provider, tool, runtime, or agent harness.

## Boundaries

- `brainstorm` explores unresolved future behavior, ownership, and substantive alternatives.
- `architect` turns an understood problem into an implementation-ready structural design.
- `arena` generates and synthesizes several attempts at the same artifact.
- `explain-code` describes current mechanics without redesigning them.
- `code-review` finds defects and risks in existing code or diffs.
- Project coding skills govern language, framework, build, test, and implementation conventions.

## Repository and privacy behavior

The skill keeps tracked files unchanged by default, preserves pre-existing work, and does not commit or push without a separate request. Evidence remains local unless remote access is requested and permitted. Secrets, sensitive personal data, and unrelated proprietary content are not reproduced.

## Structure

- `SKILL.md`: self-contained grounding, caller-first design, proportional exploration, review, output, and implementation-follow-up workflow.
- `references/design-review.md`: diagnostic review of interfaces, ownership, data, state, and speculative structure.
- `references/design-package.md`: adaptable shape for a substantial implementation-ready design.
- `LICENSE`: retained upstream MIT notice.

## Origin and license

This local, vendor-neutral adaptation is inspired by [Cursor pstack `architect`](https://github.com/cursor/plugins/blob/46125561306434d8a1d7745d540d8932ab0cd2a2/pstack/skills/architect/SKILL.md), written by Lauren Tan.

The upstream skill is distributed under the MIT License. See `LICENSE` for the retained notice.
