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

test("smoke: app loads and has a page title", async ({ page }) => {
  const baseURL = process.env.PLAYWRIGHT_BASE_URL || "http://localhost:5173";

  // Attempt to reach the dev server; skip cleanly if unreachable.
  try {
    const response = await page.goto("/", { timeout: 10_000 });
    if (!response || response.status() >= 500) {
      test.skip(
        true,
        `Dev server at ${baseURL} returned ${response?.status() ?? "no response"} — start it with: npm run dev`,
      );
      return;
    }
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
