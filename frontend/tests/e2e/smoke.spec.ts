/**
 * Smoke test — verifies the Playwright pipeline works end-to-end.
 *
 * This test loads the app root and asserts a non-empty page title.
 * The preview server is managed by playwright.config.ts `webServer`; tests
 * rely on it being available and will fail hard (not skip) if it is not.
 *
 * Prerequisites:
 *   npm run build && npm run preview  (or Playwright starts preview automatically)
 *   npm run test:e2e:install (once per machine)
 */

import { test, expect } from "@playwright/test";

test("smoke: app loads and has a page title", async ({ page }) => {
  await page.goto("/", { timeout: 10_000 });

  // The app must have a non-empty document title.
  const title = await page.title();
  expect(title.length).toBeGreaterThan(0);

  // The root element must be present.
  await expect(page.locator("#root")).toBeAttached();
});
