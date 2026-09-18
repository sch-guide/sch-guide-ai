---
name: brainstorm
description: Explore unresolved future behavior, architecture, ownership, features, or substantive alternatives through collaborative dialogue before implementation. Use when the user explicitly asks to brainstorm, explore options, or think through a future change, or when another active skill identifies consequential decisions that still need exploration. Use explain-code for neutral current-code walkthroughs, architect when explicitly invoked for implementation-ready structural design, and technical-writing when available to structure an agreed design.
license: See LICENSE
---

# Brainstorm

Turn ideas into designs through collaborative dialogue before implementation.

## Process

### Phase 1: Understand the Idea

Check project context first, then ask questions one at a time:

1. **Gather context** - check files, docs, recent commits relevant to the idea
2. **Ask questions one at a time** - prefer multiple choice when possible
3. **Focus on**: purpose, constraints, success criteria, integration points

Do not overwhelm with multiple questions. One question per message. If a topic needs more exploration, break it into multiple questions.

### Phase 2: Explore Approaches

Once the problem is understood:

1. **Compare 2-3 approaches when several are credible** and explain their trade-offs
2. **When one approach is clearly supported**, explain why alternatives do not fit instead of inventing options
3. **Lead with the recommendation** and present it conversationally, not as a formal document

When comparing approaches, put each option label on its own plain-text line in the form `Option A: Name (recommended)` or `Option B: Name`, followed by bullets for how it works and its trade-offs. Do not prefix option labels with Markdown heading markers, bullets, numbering, backticks, or emphasis markers, and do not wrap the response in a code fence. The labels must remain readable without Markdown rendering.

### Phase 3: Present Design

After approach is selected:

1. **Break design into sections** of 200-300 words each
2. **Ask after each section** whether it looks right
3. **Cover relevant concerns** - for example, architecture, components, data flow, failure handling, and validation
4. **Be ready to backtrack** if something doesn't make sense

Do not present entire design at once. Incremental validation catches misunderstandings early.

### Phase 4: Next steps

Create an implementation plan only when the user requested one or approves it after the design. When the host can write files and the user wants an artifact, use `YYYY-MM-DD-<topic>.md`; otherwise return the plan in the response.

When `architect` is installed and the user explicitly invokes it, hand off the approved problem and constraints for caller-first types, signatures, data structures, and module boundaries. Do not make this handoff mandatory or reopen approved choices.

When `technical-writing` is installed and loadable, apply it to a substantial plan without reopening approved design decisions.

## Key Principles

- **One question at a time** - do not overwhelm with multiple questions
- **Multiple choice preferred** - easier to answer than open-ended when possible
- **YAGNI ruthlessly** - remove unnecessary features from all designs, keep scope minimal
- **Explore real alternatives** - compare credible options without inventing choices to fill a quota
- **Incremental validation** - present design in sections, validate each
- **Be flexible** - go back and clarify when something doesn't make sense
- **Lead with recommendation** - have an opinion, explain why, but let user decide
- **Duplication vs abstraction** - discuss the trade-off only when both options are credible; prefer the simplest option that meets current needs

