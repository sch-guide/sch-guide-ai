# Code Review Skill

## Description
Finds actionable defects and risks across correctness, architecture, security, performance, testing, and documentation. Use `explain-code` instead for explanation-only requests.

## Usage Examples
- "Review this codebase for quality and maintainability."
- "Audit this service for security and performance issues."
- "Check code quality in `src/` and prioritize findings by severity."
- "Do a comprehensive review before we merge this branch."

## Boundaries

- `code-review` owns general defect and risk review.
- `blast-radius`, when explicitly invoked and available, owns cross-boundary consequence tracing and executable safety proof for a concrete change.
- `explain-code` owns explanation-only requests.

## Origin (Custom Command Conversion)
This local skill was created by converting a custom slash command from the upstream repository.
- Original command: `.claude/commands/dev/code-review.md`
- Upstream repository: [qdhenry/Claude-Command-Suite](https://github.com/qdhenry/Claude-Command-Suite)

## Credits
- Original command author: **Quintin Henry**
- Converted/adapted into local skill format for this repo: `coding-harness`

## License
- Upstream repository README marks license as **MIT**: [qdhenry/Claude-Command-Suite](https://github.com/qdhenry/Claude-Command-Suite)
