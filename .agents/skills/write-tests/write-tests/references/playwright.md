## Playwright Test

Load this reference when the relevant package uses `@playwright/test`, `playwright.config.*`, or Playwright test scripts. Also load `typescript.md` for TypeScript-wide rules. Treat Playwright as browser/system testing unless the repository explicitly uses its component-testing facilities.

### Scope and configuration

- Use the repository's Playwright config, projects, base URLs, web-server ownership, fixtures, reporters, retries, and CI scripts.
- Test user-observable behavior across real browser contexts. Keep lower-level business logic in faster unit/integration tests.
- Preserve the supported browser/project matrix. Do not silently reduce coverage to the locally installed browser.
- Keep test data and external dependencies controlled. Do not test third-party sites that the application does not own.
- Use TypeScript checking separately; Playwright transformation/execution is not the project's complete compiler contract.

### Isolation and fixtures

- Rely on Playwright's isolated browser context per test. Do not share mutable pages, contexts, storage, or accounts accidentally.
- Use built-in fixtures and extend them for reusable setup with clear test/worker scope. Prefer fixtures to deeply nested `beforeEach` setup.
- A worker-scoped fixture must be safe for every test in that worker and isolated from other workers.
- Give parallel workers unique users, database records, ports, and output paths when tests mutate server-side state.
- Use serial mode only for an inherently ordered workflow that cannot be redesigned; it should not mask shared-state coupling.

### Locators and assertions

- Prefer user-facing locators such as `getByRole` with its `name` option, `getByLabel`, `getByPlaceholder`, `getByText`, `getByAltText`, and `getByTitle`. Use `getByTestId` when user-facing semantics cannot identify the element.
- Use stable explicit test IDs when needed. Avoid CSS/XPath tied to DOM structure.
- Use web-first Playwright assertions such as `expect(locator).toBeVisible()` for built-in retry behavior. Locator actions separately perform actionability checks and automatic waiting.
- Avoid extracting a value and asserting it later when a locator assertion can observe the changing UI directly.
- Keep locator strictness: if multiple elements match unexpectedly, fix the product/test contract rather than selecting the first arbitrarily.

### Waiting and actions

- Use locator actions for actionability checks and automatic waiting, and web-first assertions for retrying observable expectations.
- Do not use `waitForTimeout` or arbitrary sleeps for product synchronization.
- Wait for a meaningful UI, network, URL, event, or application state when automatic waiting is insufficient.
- Avoid `force` unless the test intentionally verifies behavior that bypasses normal actionability; forced clicks can hide real usability defects.
- Model popups, downloads, dialogs, navigation, and events by starting the wait before the triggering action where the API requires it.

### Authentication and secrets

- Reuse authenticated storage state only when tests can safely share one account without conflicting server-side mutations.
- Use one account/state per parallel worker when tests modify shared server state.
- Keep authentication state files out of version control; they can contain cookies and headers capable of impersonation.
- Store credentials through the repository's protected test-secret mechanism. Never print them in traces, screenshots, attachments, or logs.
- Test authorization separately from merely starting authenticated.

### Retries and diagnostics

- Retries can reveal flakes but must not redefine a flaky test as healthy. Investigate retry-only passes.
- Configure traces, screenshots, and video as diagnostics according to repository policy; traces are usually most useful on the first retry or failure.
- Use Playwright Trace Viewer to inspect actions, DOM snapshots, console, network, and errors instead of adding sleeps or excessive logging.
- Keep attachments bounded and secret-safe.

### Focused validation

Prefer repository scripts. The bare `playwright` forms below assume a locally resolvable binary; outside scripts, invoke it through the detected package manager (`npx`, `pnpm exec`, or the repository equivalent). Typical forms include:

- `playwright test <file-or-pattern>` for focused files.
- `playwright test -g <name-pattern>` for test names.
- `playwright test --project=<project>` for one configured browser/device project.
- `playwright test --trace on` only for focused diagnosis when normal trace policy is insufficient.

Do not use snapshot-update mode, UI/watch mode, committed `.only`, or a reduced browser matrix as final validation unless the task authorizes that change.

### Common mistakes

- CSS/XPath selectors coupled to implementation markup.
- Arbitrary sleeps and manual polling instead of locators/assertions/events.
- Shared accounts or records across parallel workers.
- Page objects that hide assertions, duplicate the product, or become a second application framework.
- Testing every edge case through E2E instead of using lower test layers.
- Committing authentication state, traces, screenshots, or videos containing secrets.
- Enabling retries without tracking and fixing flakes.

### Official sources

- [Playwright best practices](https://playwright.dev/docs/best-practices)
- [Locators](https://playwright.dev/docs/locators)
- [Assertions](https://playwright.dev/docs/test-assertions)
- [Fixtures](https://playwright.dev/docs/test-fixtures)
- [Authentication](https://playwright.dev/docs/auth)
- [Parallelism](https://playwright.dev/docs/test-parallel)
- [Retries](https://playwright.dev/docs/test-retries)
- [Trace Viewer](https://playwright.dev/docs/trace-viewer)
- [CLI](https://playwright.dev/docs/test-cli)
