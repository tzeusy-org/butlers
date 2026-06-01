/**
 * Playwright smoke test — Filters Pipeline at /ingestion/filters.
 *
 * Mounts the route and asserts the five-gate diagram appears.
 *
 * PR #1942 lesson: Playwright page.route() handlers are LIFO — register
 * catch-alls FIRST, specific routes LAST. All mocks must be installed
 * BEFORE any page.goto().
 *
 * The preview server is managed by playwright.config.ts `webServer`; tests
 * rely on it being available and will fail hard (not skip) if it is not.
 *
 * Note: Requires VITE_INGESTION_DISPATCH_CONSOLE=true at build time
 * (set automatically in CI) for the /ingestion/filters route to be active.
 */

import { test, expect } from "@playwright/test";

const TIMEOUT_MS = 10_000;

test.describe("ingestion filters pipeline", () => {
  test("smoke: /ingestion/filters loads without error", async ({
    page,
  }) => {
    // --- Catch-all API mock (register FIRST per LIFO rule) ---
    await page.route("**/api/**", async (route) => {
      const url = route.request().url();

      // Pipeline stats
      if (url.includes("/ingestion/pipeline")) {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            window: "24h",
            aggregates_available: true,
            ingested: 1000,
            filtered: 200,
            errored: 10,
            routed_by_butler: { general: 700, health: 250 },
            spark24h: Array(24).fill(40),
            rate1h: 12,
            routed_pct: 95,
            filtered24h: 200,
          }),
        });
        return;
      }

      // Ingestion rules
      if (url.includes("/switchboard/ingestion-rules")) {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: [
              {
                id: "rule-001",
                scope: "email",
                rule_type: "filter",
                condition: { source_channel: "gmail" },
                action: "drop",
                priority: 10,
                enabled: true,
                name: "Drop spam",
                description: "Drop known spam patterns",
                created_by: "owner",
                created_at: "2026-01-01T00:00:00Z",
                updated_at: "2026-01-02T00:00:00Z",
                deleted_at: null,
              },
            ],
            meta: { total: 1 },
          }),
        });
        return;
      }

      // Default: pass through all other API calls
      await route.continue();
    });

    await page.goto("/ingestion/filters", { timeout: TIMEOUT_MS });

    // The five-gate diagram should be present
    const diagram = page.locator('[data-testid="pipeline-gate-diagram"]');
    await expect(diagram).toBeVisible({ timeout: TIMEOUT_MS });

    // All five gate labels must appear
    for (const gate of ["accept", "dedupe", "tier", "route", "execute"]) {
      const node = page.locator(`[data-testid="gate-node-${gate}"]`);
      await expect(node).toBeVisible({ timeout: TIMEOUT_MS });
    }

    // Funnel bar must be present
    const funnel = page.locator('[data-testid="funnel-bar"]');
    await expect(funnel).toBeVisible({ timeout: TIMEOUT_MS });
  });

  test("filters pipeline shows gate sections", async ({ page }) => {
    // Catch-all mock (FIRST)
    await page.route("**/api/**", async (route) => {
      if (route.request().url().includes("/ingestion/pipeline")) {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            window: "24h",
            aggregates_available: false,
            ingested: 0,
            filtered: 0,
            errored: 0,
            routed_by_butler: {},
            spark24h: [],
            rate1h: 0,
            routed_pct: 0,
            filtered24h: 0,
          }),
        });
        return;
      }
      if (route.request().url().includes("/switchboard/ingestion-rules")) {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: [], meta: { total: 0 } }),
        });
        return;
      }
      await route.continue();
    });

    await page.goto("/ingestion/filters", { timeout: TIMEOUT_MS });

    // Gate sections must render
    for (const gate of ["accept", "dedupe", "tier", "route", "execute"]) {
      const section = page.locator(`[data-testid="gate-section-${gate}"]`);
      await expect(section).toBeVisible({ timeout: TIMEOUT_MS });
    }
  });
});
