## Vitest

Load this reference when the relevant package uses `vitest`, `vitest.config.*`, imports from `vitest`, or Vitest scripts. Also load `typescript.md` for TypeScript-wide rules. Repository configuration and the installed Vitest version take precedence.

### Configuration and execution

- Use the repository script and existing Vite/Vitest configuration. Do not create a second config or migrate from Jest incidentally.
- Preserve the established import style. Vitest globals are disabled by default; explicit imports from `vitest` are the portable baseline unless the project enables globals and their types deliberately.
- The default environment is Node. Use the configured `jsdom`, `happy-dom`, edge environment, or Browser Mode only for tests that require it; do not put all tests in a browser-like environment by habit.
- Use Vitest projects when packages or environments need distinct configuration. Keep setup files and global state minimal because they run broadly and can couple tests.
- A normal Vitest run transpiles TypeScript but does not replace the package's compiler check. Run the authoritative type-check command separately.

### Assertions and type contracts

- Use Vitest `expect` and existing matcher integrations.
- Await `.resolves`, `.rejects`, polling, and asynchronous custom matchers.
- Use `expectTypeOf` or `assertType` only for type contracts. Keep runtime assertions in runtime tests.
- If the project uses Vitest type checking, remember that typecheck tests and runtime tests are separate modes and may use different include patterns.
- Keep snapshots focused. Review snapshot updates explicitly; never use update mode merely to make CI pass.

### Mocks and spies

- Use `vi.fn` for owned callbacks and `vi.spyOn` when the real object/method boundary is the contract.
- `vi.mock` is hoisted. Its factory cannot safely close over ordinary top-level variables created later; use `vi.hoisted` only when a hoisted value is genuinely required.
- Use `vi.doMock` only when non-hoisted, subsequent-import behavior is intentional. Static imports already evaluated before it will not be replaced.
- Prefer dependency injection, request interception, or a real adapter over module mocks for internal code.
- Use `vi.mocked` for TypeScript assistance only; it does not change runtime behavior.
- Understand cleanup distinctions: clearing removes call history, resetting also replaces/reset implementations, and restoring returns spies/replaced properties to originals. Follow repository hooks or config rather than applying all three indiscriminately.
- Restore global stubs, environment stubs, spies, and replaced properties after each test. Avoid relying on suite execution order.

### Timers and async behavior

- Use `vi.useFakeTimers` only when time drives the behavior.
- Advance or run the specific timers required by the contract and account for microtasks/promises separately.
- Restore real timers in cleanup even when a test fails.
- Do not combine arbitrary sleeps with fake timers. Synchronize through observable state or Vitest's retry/polling facilities where appropriate.

### Focused validation

Prefer repository scripts. The bare `vitest` forms below assume a locally resolvable binary; outside scripts, invoke it through the detected package manager (`npx`, `pnpm exec`, or the repository equivalent). Typical forms are:

- `vitest run <file-or-filter>` for a non-watch run.
- `vitest run -t <name-pattern>` for a focused test name.
- Supported versions can filter a file and line; verify the installed CLI before relying on it.

Do not commit `.only`, use watch mode in CI, or enable snapshot updates as validation.

### Common mistakes

- Importing a module in setup before attempting to mock it later.
- Assuming Jest module-mocking behavior is identical.
- Using a DOM emulator when real browser behavior is the risk.
- Forgetting that Vite aliases/plugins and environment configuration affect module loading.
- Leaving workers, servers, listeners, fake timers, or globals active after a test.
- Treating a passing Vitest transpile as a passing TypeScript check.

### Official sources

- [Vitest guide](https://vitest.dev/guide/)
- [Mocking](https://vitest.dev/guide/mocking.html)
- [`vi` API](https://vitest.dev/api/vi.html)
- [Test filtering](https://vitest.dev/guide/filtering)
- [Testing types](https://vitest.dev/guide/testing-types)
- [Environment configuration](https://vitest.dev/config/environment)
- [Test projects](https://vitest.dev/guide/projects)
