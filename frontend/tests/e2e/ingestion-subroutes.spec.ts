/**
 * Playwright smoke test — §2.1 + §2.10 ingestion sub-route 301 redirects.
 *
 * Verifies that legacy ?tab= query-param URLs redirect to the dedicated
 * sub-routes when the INGESTION_DISPATCH_CONSOLE feature flag is enabled.
 *
 * The four redirects under test:
 *   /ingestion?tab=connectors → /ingestion/connectors
 *   /ingestion?tab=filters    → /ingestion/filters
 *   /ingestion?tab=history    → /ingestion/history
 *   /ingestion                → /ingestion (no redirect — stays on timeline)
 *
 * This test skips gracefully when the dev/preview server is not reachable,
 * following the same convention as smoke.spec.ts.
 *
 * Prerequisites:
 *   npm run dev (or npm run preview in a separate terminal)
 *   npm run test:e2e:install (once per machine for browser binaries)
 */

import { test, expect } from "@playwright/test";

const TIMEOUT_MS = 10_000;

/**
 * Attempt to load the app root; skip if the server is unreachable.
 * HTTP error responses (4xx/5xx) are NOT skipped — they signal a broken app.
 */
async function tryNavigate(page: Parameters<typeof test>[1] extends (...args: infer P) => unknown ? P[0] : never, url: string) {
  try {
    await page.goto(url, { timeout: TIMEOUT_MS });
    return true;
  } catch {
    return false;
  }
}

test.describe("ingestion sub-route redirects", () => {
  test("smoke: /ingestion loads without error", async ({ page, baseURL }) => {
    const ok = await tryNavigate(page, "/ingestion");
    if (!ok) {
      test.skip(
        true,
        `Dev server not reachable at ${baseURL} — start it with: npm run dev`,
      );
      return;
    }
    // Final URL should be /ingestion (no redirect when no ?tab= param)
    expect(page.url()).toMatch(/\/ingestion$/);
  });

  test("?tab=connectors redirects to /ingestion/connectors", async ({ page, baseURL }) => {
    const ok = await tryNavigate(page, "/ingestion?tab=connectors");
    if (!ok) {
      test.skip(
        true,
        `Dev server not reachable at ${baseURL} — start it with: npm run dev`,
      );
      return;
    }
    // After redirect, URL must end with /ingestion/connectors
    await page.waitForURL(/\/ingestion\/connectors/, { timeout: TIMEOUT_MS });
    expect(page.url()).toMatch(/\/ingestion\/connectors/);
    // The ?tab= param must be stripped from the final URL
    expect(page.url()).not.toContain("tab=");
  });

  test("?tab=filters redirects to /ingestion/filters", async ({ page, baseURL }) => {
    const ok = await tryNavigate(page, "/ingestion?tab=filters");
    if (!ok) {
      test.skip(
        true,
        `Dev server not reachable at ${baseURL} — start it with: npm run dev`,
      );
      return;
    }
    await page.waitForURL(/\/ingestion\/filters/, { timeout: TIMEOUT_MS });
    expect(page.url()).toMatch(/\/ingestion\/filters/);
    expect(page.url()).not.toContain("tab=");
  });

  test("?tab=history redirects to /ingestion/history", async ({ page, baseURL }) => {
    const ok = await tryNavigate(page, "/ingestion?tab=history");
    if (!ok) {
      test.skip(
        true,
        `Dev server not reachable at ${baseURL} — start it with: npm run dev`,
      );
      return;
    }
    await page.waitForURL(/\/ingestion\/history/, { timeout: TIMEOUT_MS });
    expect(page.url()).toMatch(/\/ingestion\/history/);
    expect(page.url()).not.toContain("tab=");
  });

  test("?tab= filter params are preserved during redirect", async ({ page, baseURL }) => {
    const ok = await tryNavigate(page, "/ingestion?tab=connectors&period=7d&channel=gmail");
    if (!ok) {
      test.skip(
        true,
        `Dev server not reachable at ${baseURL} — start it with: npm run dev`,
      );
      return;
    }
    await page.waitForURL(/\/ingestion\/connectors/, { timeout: TIMEOUT_MS });
    // Filter params period and channel must survive the redirect
    expect(page.url()).toContain("period=7d");
    expect(page.url()).toContain("channel=gmail");
    // But ?tab= itself must be stripped
    expect(page.url()).not.toContain("tab=");
  });
});
