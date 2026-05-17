# Playwright E2E Tests

End-to-end tests for the Butlers dashboard frontend using Playwright.

## Running Locally

### One-time browser setup

```bash
cd frontend
npm run test:e2e:install
```

### Start the dev server (separate terminal)

```bash
cd frontend
npm run dev
```

### Run the tests

```bash
cd frontend
npm run test:e2e          # headless chromium
npm run test:e2e:headed   # headed (visible browser, good for debugging)
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

The Playwright suite is not yet wired into the CI workflow — that requires adding
a step to start the Vite dev server (or run `vite preview` over a build) before
running `npm run test:e2e`. Track this in the follow-up issue referenced in the
bootstrap PR.
