/**
 * Playwright smoke test — §2.1 + §2.10 ingestion sub-route 301 redirects.
 *
 * Verifies that legacy ?tab= query-param URLs redirect to the dedicated
 * sub-routes when the INGESTION_DISPATCH_CONSOLE feature flag is enabled.
 *
 * The four redirects under test:
 *   /ingestion?tab=connectors → /ingestion/connectors
 *   /ingestion?tab=filters    → /ingestion/filters
 *   /ingestion?tab=history    → /ingestion (Timeline; spec says history SHALL NOT remain a fourth redesigned tab)
 *   /ingestion                → /ingestion (no redirect — stays on timeline)
 *
 * The preview server is managed by playwright.config.ts `webServer`; tests
 * rely on it being available and will fail hard (not skip) if it is not.
 *
 * Note: Requires VITE_INGESTION_DISPATCH_CONSOLE=true at build time
 * (set automatically in CI) for the redirect logic to be active.
 *
 * Prerequisites:
 *   npm run build && npm run preview  (or Playwright starts preview automatically)
 *   npm run test:e2e:install (once per machine for browser binaries)
 */

import { test, expect } from "@playwright/test";

const TIMEOUT_MS = 10_000;

test.describe("ingestion sub-route redirects", () => {
  test("smoke: /ingestion loads without error", async ({ page }) => {
    await page.goto("/ingestion", { timeout: TIMEOUT_MS });
    // Final URL should be /ingestion (no redirect when no ?tab= param)
    expect(page.url()).toMatch(/\/ingestion$/);
  });

  test("?tab=connectors redirects to /ingestion/connectors", async ({ page }) => {
    await page.goto("/ingestion?tab=connectors", { timeout: TIMEOUT_MS });
    // After redirect, URL must end with /ingestion/connectors
    await page.waitForURL(/\/ingestion\/connectors/, { timeout: TIMEOUT_MS });
    expect(page.url()).toMatch(/\/ingestion\/connectors/);
    // The ?tab= param must be stripped from the final URL
    expect(page.url()).not.toContain("tab=");
  });

  test("?tab=filters redirects to /ingestion/filters", async ({ page }) => {
    await page.goto("/ingestion?tab=filters", { timeout: TIMEOUT_MS });
    await page.waitForURL(/\/ingestion\/filters/, { timeout: TIMEOUT_MS });
    expect(page.url()).toMatch(/\/ingestion\/filters/);
    expect(page.url()).not.toContain("tab=");
  });

  test("?tab=history redirects to /ingestion (Timeline)", async ({ page }) => {
    // Spec (complete-ingestion-redesign-parity §2.10): "history SHALL map to the Timeline
    // route … it SHALL NOT remain a fourth redesigned tab." No primary /ingestion/history
    // route in dispatch mode — /ingestion/history itself also redirects to /ingestion.
    await page.goto("/ingestion?tab=history", { timeout: TIMEOUT_MS });
    // After redirect, URL must be exactly /ingestion — NOT /ingestion/history
    await page.waitForURL(/\/ingestion$/, { timeout: TIMEOUT_MS });
    expect(page.url()).toMatch(/\/ingestion$/);
    expect(page.url()).not.toContain("/ingestion/history");
    expect(page.url()).not.toContain("tab=");
  });

  test("?tab=* filter params are preserved during redirect", async ({ page }) => {
    await page.goto("/ingestion?tab=connectors&period=7d&channel=gmail", { timeout: TIMEOUT_MS });
    await page.waitForURL(/\/ingestion\/connectors/, { timeout: TIMEOUT_MS });
    // Filter params period and channel must survive the redirect
    expect(page.url()).toContain("period=7d");
    expect(page.url()).toContain("channel=gmail");
    // But ?tab= itself must be stripped
    expect(page.url()).not.toContain("tab=");
  });
});
