## Bun Test

Load this reference when tests import `bun:test`, scripts use `bun test`, or the package deliberately runs tests under Bun. Also load `typescript.md` for TypeScript-wide rules. Test with the repository's selected Bun version; Bun compatibility and runner behavior evolve independently of Node and Jest.

### Runtime and configuration

- Import test APIs from `bun:test` unless the repository intentionally enables another supported style.
- Preserve `bunfig.toml`, preload scripts, environment handling, discovery patterns, timeouts, concurrency, and package scripts.
- Bun executes TypeScript but does not replace the repository's full type check. Run `tsc --noEmit`, the workspace build, or the established compiler command separately.
- Passing under Bun proves Bun behavior. It does not prove Node.js compatibility; run Node-owned tests under Node when the package claims both runtimes.
- Jest-like syntax does not guarantee complete Jest compatibility. Verify APIs against Bun's current compatibility documentation before copying Jest-specific helpers, environments, transformers, or plugins.

### Assertions and lifecycle

- Use `test`, `describe`, lifecycle hooks, and `expect` from `bun:test` according to repository conventions.
- Await promise assertions and asynchronous hooks.
- Keep `beforeAll` resources suite-owned and close them in `afterAll`; prefer per-test ownership when isolation matters.
- Use conditional and expected-failure APIs only when the test contract truly requires them. Do not leave focused tests committed.
- Keep snapshots small and review updates explicitly.

### Mocks and spies

- Use `mock`/`jest.fn` compatibility and `spyOn` only where interaction is the contract.
- Restore mocks and spies after each test or use the repository's deliberate global cleanup configuration.
- Bun's current `mock.restore()` does not reset modules replaced through `mock.module()`. Those replacements can persist in the process; keep incompatible module-mock variants in preload-controlled or dedicated test files/processes instead of assuming ordinary restoration isolates them.
- Module mocks can be affected by import timing and Bun's preload/hoisting behavior. Configure required early mocks through the documented preload mechanism rather than depending on accidental file order.
- A module already imported and cached may retain effects even if later mocking changes exports; design dependencies for replacement instead of relying on cache tricks.
- Prefer narrow fakes, dependency injection, or request interception to broad module mocking.

### Timers, processes, and resources

- Use fake timers only if supported by the selected Bun version and already appropriate for the project; verify current semantics instead of assuming Jest parity.
- Do not use arbitrary sleeps to coordinate tests. Wait for observable state or events.
- Close Bun servers, sockets, workers, subprocesses, file handles, watchers, and temporary resources.
- Restore environment variables, globals, fetch stubs, listeners, working directory, and process state.
- Account for Bun-specific APIs and runtime behavior when creating test doubles; a Node-only fake can hide Bun integration defects.

### Focused validation

Prefer repository scripts. Typical direct forms include:

- `bun test ./path/to/file.test.ts` for an exact relative file path; the leading `./` distinguishes a path from a bare substring filter.
- `bun test <substring>` for Bun's filename substring filtering; do not assume shell-style glob support.
- `bun test -t <name-pattern>` for matching test names.
- Explicit preload/configuration flags only when they match the repository setup.

Use non-watch mode in CI. Do not update snapshots, repeat tests until they happen to pass, or commit `.only` merely to validate.

### Common mistakes

- Treating Bun's Jest compatibility as complete Jest identity.
- Running a Bun-native suite under Node or vice versa without adapting runtime APIs.
- Forgetting a separate TypeScript check.
- Depending on module cache/import order for mocks.
- Leaving Bun servers, workers, timers, or subprocesses active.
- Hiding runtime incompatibility behind broad mocks.

### Official sources

- [Bun test runner](https://bun.sh/docs/test)
- [Writing tests](https://bun.sh/docs/test/writing-tests)
- [Mocks](https://bun.sh/docs/test/mocks)
- [Lifecycle hooks](https://bun.sh/docs/test/lifecycle)
- [Test configuration](https://bun.sh/docs/test/configuration)
- [Runtime behavior](https://bun.sh/docs/test/runtime-behavior)
- [Bun Node.js compatibility](https://bun.com/docs/runtime/nodejs-compat)
