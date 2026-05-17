/**
 * Smoke test — verifies the Playwright pipeline works end-to-end.
 *
 * This test loads the app root and asserts a non-empty page title.
 * It skips gracefully if the dev server is not reachable, to keep
 * the test suite from failing in environments where only unit tests run.
 *
 * Prerequisites:
 *   npm run dev          (in a separate terminal)
 *   npm run test:e2e:install (once per machine)
 */

import { test, expect } from "@playwright/test";

test("smoke: app loads and has a page title", async ({ page, baseURL }) => {
  // Attempt to reach the dev server; skip cleanly only when truly unreachable
  // (network-level failure). HTTP error responses (4xx/5xx) must NOT be
  // skipped — they indicate the server is up but the app is broken, and
  // masking that would let regressions land.
  try {
    await page.goto("/", { timeout: 10_000 });
  } catch {
    test.skip(
      true,
      `Dev server not reachable at ${baseURL} — start it with: npm run dev`,
    );
    return;
  }

  // The app must have a non-empty document title.
  const title = await page.title();
  expect(title.length).toBeGreaterThan(0);

  // The root element must be present.
  await expect(page.locator("#root")).toBeAttached();
});
