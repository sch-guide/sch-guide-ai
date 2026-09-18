# Brainstorm Skill

## Description
Collaborative design skill used before implementation. It turns ideas into practical designs by clarifying goals, comparing alternatives, and validating a chosen direction step by step. It can also accept a handoff from another active skill when consequential design decisions still need exploration. Use `explain-code` for neutral descriptions of current architecture, `architect` when explicitly invoked for implementation-ready structural design, and `technical-writing`, when available, to structure an agreed design.

## Usage Examples
- "Let’s brainstorm a caching strategy for our API."
- "Help me design a new notifications module."
- "Explore options for multi-tenant auth in this project."
- "Think through this feature architecture before we implement."

The skill writes an implementation-plan file only when the user requests an artifact and the host supports file editing. Otherwise, it returns an approved plan in the response.

After behavior and important constraints are approved, the user may explicitly invoke `architect`, when installed, to derive caller usage, data structures, types, signatures, and module boundaries. This handoff is optional and does not reopen the approved design.

## Origin
- Original upstream skill: `plugins/brainstorm/skills/brainstorm/SKILL.md`
- Upstream repository: [umputun/cc-thingz](https://github.com/umputun/cc-thingz)

## Credits
- Original author: **Umputun**
- Local adaptation/usage in this repo: `coding-harness`

## License

The upstream skill is distributed under the MIT License. See `LICENSE` for the retained notice.
