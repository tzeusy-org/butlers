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

test("smoke: /health route renders without crashing", async ({ page }) => {
  // The route may fail API requests (no backend in e2e preview), but the
  // React tree must mount cleanly — no JS error, no blank white screen.
  await page.goto("/health", { timeout: 10_000 });

  // Root element must be attached.
  await expect(page.locator("#root")).toBeAttached();

  // The health overview page container must be present.
  // data-testid="health-overview-page" is set on the page root div.
  await expect(page.locator('[data-testid="health-overview-page"]')).toBeAttached({
    timeout: 5_000,
  });
});
