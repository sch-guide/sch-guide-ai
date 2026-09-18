---
name: write-tests
description: Write unit, integration, component, and end-to-end tests for code. Use when the user asks to "write tests", "add tests", "add test coverage", or "create test cases" for functions, services, components, CLIs, or user flows.
allowed-tools: Read, Grep, Glob, Bash, Write, Edit
---

# Write Tests

Write the appropriate unit, integration, component, and end-to-end tests: **$ARGUMENTS**

## Process

Follow these steps in order:

1. **Understand the project's test conventions**
   - Identify the language, runtime, package boundary, and every test runner relevant to the requested tests
   - Inspect dependencies, imports, runner configuration, package scripts, setup files, CI commands, and representative existing tests
   - Load every matching language and runner reference that exists:
      - `references/junit.md`
      - `references/typescript.md`
      - `references/vitest.md`
      - `references/jest.md`
      - `references/node-test.md`
      - `references/bun-test.md`
      - `references/playwright.md`
   - In monorepos and migrations, determine runner ownership per package and test file; do not assume one runner governs the repository
   - Repository conventions and installed-version behavior override greenfield defaults

2. **Analyse the code under test**
   - Identify the public interface and critical business logic
   - Map dependencies and external interactions
   - Note error conditions, edge cases, and validation rules
   - Follow repository coverage thresholds and use coverage to identify consequential gaps rather than gaming a universal percentage

3. **Plan the test strategy**
   - Decide which levels are needed: unit, integration, end-to-end
   - List what to mock or stub, and why
   - Prioritise business-critical paths first

4. **Write unit tests**
   - Test cohesive units through the smallest stable public or package boundary; tests do not need to map one-to-one to functions or methods
   - Cover relevant happy paths, edge cases, boundary conditions, and error/exception paths
   - Follow the AAA pattern (Arrange / Act / Assert) — use blank lines to separate phases, never write `// Arrange` comments
   - Use descriptive test names that explain the scenario being tested
   - Keep each test focused on one behaviour; avoid assertions on unrelated state

5. **Write integration tests**
   - Test real interactions among application-owned components and controlled dependencies such as in-process APIs, test databases, sandboxes, or contract servers
   - Do not contact production or uncontrolled live third-party services by default
   - Verify data flow across meaningful layer boundaries
   - Cover failure modes and partial failures, not just the happy path

6. **Write component or end-to-end tests when needed**
   - Exercise observable user behavior through framework-supported test APIs
   - Keep critical cross-boundary flows in E2E tests and lower-level edge cases in faster tests
   - Preserve accessibility, isolation, cleanup, and supported runtime/browser coverage

7. **Mocking and test data**
   - Mock external dependencies to keep unit tests fast and deterministic
   - Avoid over-mocking — if a mock replicates production logic, use a real object instead
   - Use factories or builders for complex test data; keep setup out of assertion code
   - Ensure tests are isolated — no shared mutable state between cases

8. **Apply applicable specialist concerns**
   - **Async code**: use the framework's async test utilities; test both resolution and rejection
   - **Security**: cover auth checks, input validation, and access control boundaries
   - **UI**: test rendered output, keyboard navigation, and ARIA semantics where relevant
