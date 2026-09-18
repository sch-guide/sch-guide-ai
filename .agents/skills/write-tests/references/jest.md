## Jest

Load this reference when the relevant package uses `jest`, Jest configuration, `@jest/globals`, Jest globals, or a Jest transform such as `ts-jest`. Also load `typescript.md` for TypeScript-wide rules. Preserve the installed transformer, module system, and Jest version.

### Configuration and TypeScript

- Use the repository's Jest config, setup files, test environment, resolver, and package scripts. Do not create parallel configuration or migrate to Vitest incidentally.
- Determine how TypeScript is transformed: `ts-jest`, Babel, SWC, another transformer, precompiled output, or native-compatible syntax. These choices have different ESM and type-check behavior.
- Do not assume a Jest transform performs the repository's full TypeScript check. Run the authoritative compiler/framework command separately.
- Preserve the established globals versus explicit `@jest/globals` import style.
- Keep `setupFiles` and `setupFilesAfterEnv` responsibilities distinct and minimal; broad setup state makes isolation harder.

### ESM and CommonJS

- Determine whether Jest is executing the package as CommonJS or ESM before copying mocking examples.
- Follow the installed Jest ESM documentation and transformer settings. ESM support and required Node flags can vary by Jest/Node generation.
- In ESM tests, import the `jest` object from `@jest/globals` or use the installed documented equivalent.
- Static ESM imports are evaluated before ordinary code. ESM module mocking uses the installed Jest ESM API, commonly `jest.unstable_mockModule`, followed by a dynamic import; do not assume CommonJS `jest.mock` hoisting works identically.

### Assertions

- Use Jest `expect` and existing matcher integrations.
- Await `.resolves` and `.rejects`, or return the assertion promise when the project convention permits it.
- Use assertion-count checks only when they protect callback/error paths that could otherwise finish without asserting.
- Prefer exact behavioral assertions over broad snapshots or implementation call sequences.

### Mocks and spies

- Use `jest.fn` for owned callbacks and `jest.spyOn` for observable method interactions.
- CommonJS-style `jest.mock` calls are hoisted by supported transforms; factories must not depend on unavailable initialization order.
- Prefer dependency boundaries or request interception to deep module mocking.
- Know the cleanup operations:
  - clear: remove call/result history while retaining implementation;
  - reset: clear history and reset mock implementation;
  - restore: return spies/replaced properties to their original implementation.
- `restoreAllMocks` can restore spies and replaced properties; ordinary standalone mock functions still need appropriate reset/ownership.
- Choose `clearMocks`, `resetMocks`, or `restoreMocks` deliberately. Enabling all of them without understanding repository behavior can erase intended setup.

### Timers and async behavior

- Use modern or legacy fake timers according to the installed configuration; do not mix APIs from different Jest generations.
- Fake timers can replace dates, timers, and selected platform APIs. Advance only what the behavior requires and flush promise work deliberately.
- Restore real timers in cleanup.
- Avoid arbitrary delays and callback-style `done` when promises or async functions express completion more safely. Never combine `done` with a returned promise.

### Focused validation

Prefer repository scripts. The bare `jest` forms below assume a locally resolvable binary; outside scripts, invoke it through the detected package manager (`npx`, `pnpm exec`, or the repository equivalent). Typical forms include:

- `jest --runTestsByPath <file>` for exact files.
- `jest -t <name-pattern>` for test names.
- `jest --findRelatedTests <source-files>` for changed production files.
- `--runInBand` only when isolation/resource constraints justify serial execution, not as a default fix for flaky tests.

Do not use `-u`/`--updateSnapshot`, watch mode, or committed `.only` merely to validate.

### Common mistakes

- Copying CommonJS mocking recipes into ESM tests.
- Replacing the configured transformer without checking framework behavior.
- Assuming fake timers also flush every promise or external I/O operation.
- Overusing module mocks until tests reproduce the implementation graph.
- Forgetting to restore globals, environment changes, spies, timers, and open handles.
- Treating `--forceExit` or open-handle suppression as a cleanup fix.

### Official sources

- [Jest configuration](https://jestjs.io/docs/configuration)
- [ECMAScript modules](https://jestjs.io/docs/ecmascript-modules)
- [Mock functions](https://jestjs.io/docs/mock-function-api)
- [Timer mocks](https://jestjs.io/docs/timer-mocks)
- [Asynchronous testing](https://jestjs.io/docs/asynchronous)
- [CLI options](https://jestjs.io/docs/cli)
