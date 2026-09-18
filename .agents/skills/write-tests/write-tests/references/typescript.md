## TypeScript

Use the **typescript-coder** skill. This reference governs cross-runner TypeScript test authoring; matching runner references govern runner-specific APIs. Repository configuration, installed versions, CI commands, and established tests take precedence.

### Compose the applicable guidance

Before writing tests, identify the package boundary, runtime, module system, test ownership, and every runner used by the requested scope. Load this file plus each matching runner reference:

- `vitest.md`
- `jest.md`
- `node-test.md`
- `bun-test.md`
- `playwright.md`

A package may use multiple runners, such as Vitest for unit tests and Playwright for E2E tests. Apply each runner reference only to tests it owns. Never add, replace, or migrate a runner merely because another runner is preferred for greenfield work.

### Naming and structure

- Name tests as behavior: relevant precondition/action and observable outcome.
- Use `describe` only when it adds meaningful shared context; avoid deeply nested suites.
- Follow Arrange / Act / Assert with blank lines, not phase comments.
- Keep one behavioral reason to fail per test. Multiple assertions are appropriate when they prove one outcome.
- Keep fixtures close to the tests that own them. Extract builders or factories when setup is complex or genuinely reused.
- Avoid exporting production internals solely for testing. Test through the smallest stable public or package boundary.

### Assertions

- Use the owning runner's established assertion API and local conventions.
- Prefer specific observable assertions over generic truthiness or large snapshots.
- Assert rejected async work with the runner's rejection/throw helpers; avoid manual `try/catch` that can pass when no error is thrown.
- Add custom or third-party matchers only when they express recurring contracts more clearly than built-in assertions.
- For UI tests, assert accessible and user-visible state through the project's established DOM integration.

### Type safety

- Keep tests covered by a compiler or framework type check.
- Avoid `any`, broad assertions, and non-null assertions in fixtures and mocks. Prefer `satisfies`, typed builders, focused helper types, or implementations of the narrow required contract.
- Use `Partial<T>` only for values that are genuinely partial; do not cast an incomplete fake to a full production interface.
- Keep production types and runtime schemas authoritative. Do not duplicate interfaces in tests.
- Use `@ts-expect-error` with a reason for intentional compiler failures; do not use `@ts-ignore` as ordinary setup.

### Runtime tests versus type tests

- Use runtime tests for runtime behavior.
- Use the project's established type-test facility for exported inference, overload, generic, and declaration contracts.
- Keep positive and negative type assertions focused. A type assertion does not prove runtime validation or behavior.
- Run the real compiler/type-test command; transpilers and test runners commonly strip types without checking them.

### Async work and resources

- Await the operation under test and every asynchronous assertion. Do not leave floating promises.
- Cover rejection, timeout, cancellation, stale-result suppression, and cleanup when they are supported behavior.
- Avoid arbitrary sleeps. Synchronize on observable state, events, or a bounded polling helper.
- Use fake timers only when time is part of the contract. Flush pending work deliberately and restore real timers after each test.
- Restore mocks, spies, environment variables, globals, fetch replacements, and process listeners.
- Close servers, sockets, database clients, workers, child processes, watchers, temporary directories, terminal modes, and native resources.

### Mocking

- Mock external boundaries—network, clock, randomness, filesystem edge, process launch, or third-party service—not the internal implementation graph by default.
- Prefer dependency injection, framework test adapters, fake servers, or request interception over brittle module-level mocks.
- Keep a mock behaviorally smaller than the production dependency. If it reimplements production logic, prefer a real object or integration test.
- Preserve the actual module system. Module-mock hoisting and ESM behavior differ by runner; follow the matching runner reference.
- Assert calls only when the interaction itself is the contract, such as an emitted command, persisted event, audit entry, or external request.

### Application profiles

Use **typescript-coder** references for stack-specific scope:

- Frontend: semantic queries, keyboard/focus behavior, loading/error/permission states, and accessibility.
- Backend: boundary validation, authorization denial, serialization, persistence, idempotency, and shutdown.
- CLI/TUI: stdout/stderr, exit codes, signals, non-TTY behavior, terminal cleanup, and packed entry points.
- Desktop: IPC/RPC/command contracts, sender/origin checks, disposal, and packaged smoke tests.
- AWS CDK: focused `aws-cdk-lib/assertions`, IAM/security properties, and logical-ID stability.

Do not duplicate those application rules in runner references.

### Coverage and snapshots

- Follow repository thresholds. Treat coverage as a gap-finding tool, not a percentage to game.
- Prioritize business rules, trust boundaries, lifecycle cleanup, data integrity, and likely failure branches.
- Keep snapshots small, stable, and reviewed. Never use snapshots as the only proof of authorization, validation, accessibility, IAM, or complex behavior.

### Validation

Run the narrowest relevant runner command first, then the package type check, lint, formatter check, broader tests, and build as warranted. Validation must not update snapshots, rewrite tracked source, regenerate lockfiles, switch runners, or change infrastructure unless the task explicitly authorizes it.
