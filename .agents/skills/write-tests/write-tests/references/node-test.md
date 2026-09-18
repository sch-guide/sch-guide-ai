## Node Test Runner

Load this reference when tests import `node:test`, scripts invoke `node --test`, or the relevant package otherwise uses Node's built-in runner. Also load `typescript.md` for TypeScript-wide rules. Check the selected Node release because runner, mock, timer, snapshot, and coverage APIs evolve with Node.

### Execution model

- Import from `node:test` and use `node:assert`/`node:assert/strict` or the repository's established assertion layer consistently.
- Use the repository's pinned Node version in local validation and CI. Do not write against APIs available only in a newer local Node release.
- Node normally discovers supported test file patterns and can run files in isolated child processes. Preserve configured isolation, concurrency, sharding, reporters, and loaders.
- Test concurrency is explicit and can expose shared global, port, filesystem, environment, or database state. Make resources unique per test/worker rather than forcing serial execution globally.
- Direct Node TypeScript execution and loader-based execution have version-specific constraints. A passing `node --test` run is not a replacement for the package type check.

### Assertions

- Prefer strict assertion APIs and specific methods such as equality, matching, throws, and rejects.
- Await `assert.rejects` and other asynchronous completion.
- Use partial/deep assertions only to the level required by the public behavior; avoid asserting unstable incidental fields.
- If the project adds a matcher library, preserve it rather than mixing assertion styles inside one suite.

### Test context, hooks, and cleanup

- Prefer the test context's diagnostics, subtests, abort signal, and mock tracker where supported by the installed Node version.
- Await subtests and asynchronous hooks. A parent finishing before an unawaited subtest can cancel or misreport work.
- Keep `before`, `beforeEach`, `afterEach`, and `after` ownership local to the suite that needs them.
- Register cleanup immediately after acquiring servers, sockets, temporary directories, workers, or subprocesses. Do not rely on process exit to hide open handles.
- Avoid mutating process-wide environment, working directory, globals, or listeners in concurrent tests. Restore unavoidable changes in `finally`/cleanup hooks.

### Mocks and timers

- Prefer context-owned mocks such as `t.mock` where available so lifecycle follows the test.
- Use mock functions/methods only for real interaction contracts. Prefer a real value or narrow fake for internal collaborators.
- Node mock and mock-timer APIs are version-sensitive. Check the pinned Node documentation before using timer/date support or options copied from a newer release.
- Restore globally owned mocks manually; do not assume context cleanup covers mocks created outside the test context.
- Fake timers do not simulate network, child-process, filesystem, or arbitrary promise completion.

### Focused validation

Prefer repository scripts. Typical direct forms include:

- `node --test <file-or-pattern>` for a focused file set.
- `node --test --test-name-pattern=<pattern>` for matching test names.
- Repository-configured reporters, loaders, coverage, or TypeScript execution flags must be preserved.

Use `test.only` only for temporary local diagnosis and only with the runner options that honor it; never commit it. Do not disable isolation or force serial execution merely to mask shared-state defects.

### Common mistakes

- Using APIs absent from the project's supported Node release.
- Leaving subtests, promises, servers, timers, subprocesses, or listeners unawaited/unclosed.
- Sharing fixed ports or filesystem paths across concurrent test files.
- Mutating global state while file/process isolation is disabled.
- Assuming native TypeScript stripping performs type checking or supports every TypeScript syntax form.
- Treating a custom loader as interchangeable with emitted JavaScript or native stripping.

### Official sources

- [Node.js test runner](https://nodejs.org/api/test.html)
- [Node.js assertions](https://nodejs.org/api/assert.html)
- [Node.js TypeScript modules](https://nodejs.org/api/typescript.html)
- [Node.js command-line options](https://nodejs.org/api/cli.html)
