# Playwright E2E Tests

End-to-end tests for the Butlers dashboard frontend using Playwright.

## Running Locally

### One-time browser setup

```bash
cd frontend
npm run test:e2e:install
```

### Run the tests

```bash
cd frontend
npm run test:e2e          # headless chromium
npm run test:e2e:headed   # headed (visible browser, good for debugging)
```

Playwright starts its own test-only API mock and Vite preview process. Build
first if `dist/` is absent or stale:

```bash
npm run build
```

From the repo root:

```bash
make test-e2e-frontend
```

## Running Against a Non-Local Instance

```bash
PLAYWRIGHT_BASE_URL=https://your-instance.example.com npm run test:e2e
```

## Adding New Tests

1. Create a file under `frontend/tests/e2e/` with the `.spec.ts` suffix.
2. Import `test` and `expect` from `@playwright/test`.
3. Use `page.goto("/your-route")` to navigate, then assert with `expect()`.
4. Run your test with `npm run test:e2e -- --grep "your test name"` to iterate quickly.

## Skipping When Dev Server is Absent

The smoke test detects an unreachable dev server and calls `test.skip()` with a
clear message rather than failing. New tests that require a live server should
follow the same pattern if they are likely to run in contexts without a server.

## CI Integration

The Playwright suite runs in the `frontend-e2e` job in `.github/workflows/ci.yml`.

CI flow:
1. `npm ci` — install dependencies
2. `npm run test:e2e:install` — install Playwright browsers (chromium + deps)
3. `npm run build` — produce the production build
4. `npm run test:e2e` — Playwright starts a strict local API mock and `vite
   preview` automatically (via the `webServer` block in
   `playwright.config.ts`) and runs the tests

The `webServer` config uses `vite preview` (port 4173) over the built output,
which is closer to production than `vite dev`. Its `/api` proxy is pointed at
the test-only mock on port 4174; that mock only returns `200` for
`GET /api/health`. All other `/api/*` requests return an explicit `404` error
envelope unless the individual test mocks them with `page.route()`. This keeps
missing fixtures visible rather than converting them into generic proxy
failures. To test against a dev server at `:5173`, set
`PLAYWRIGHT_BASE_URL=http://localhost:5173`; Playwright will then start no
local server or mock.

Playwright reports (screenshots, traces, videos on failure) are uploaded as the
`playwright-report` artifact and retained for 7 days.
