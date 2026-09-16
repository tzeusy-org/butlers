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

test("smoke: stored dark theme paints the pre-hydration document frame dark", async ({ page }) => {
  const entryModule = /\/assets\/index-[^/]+\.js(?:\?.*)?$/
  let releaseEntryModule!: () => void
  const entryModuleGate = new Promise<void>((resolve) => {
    releaseEntryModule = resolve
  })

  // Keep React from mounting while the browser parses the document head and
  // loads the production stylesheet. This makes the assertion below observe
  // the actual first frame rather than a post-hydration state.
  await page.route(entryModule, async (route) => {
    await entryModuleGate
    await route.continue()
  })
  await page.addInitScript(() => window.localStorage.setItem("theme", "dark"));

  try {
    await page.goto("/", { timeout: 10_000, waitUntil: "commit" })
    await page.waitForFunction(() => document.documentElement.classList.contains("dark"))

    const firstFrame = await page.locator("html").evaluate((node) => {
      const style = getComputedStyle(node)
      return {
        backgroundColor: style.backgroundColor,
        backgroundToken: style.getPropertyValue("--bg").trim(),
        colorScheme: style.colorScheme,
        appChildren: document.querySelector("#root")?.childElementCount,
      }
    })

    expect(firstFrame.appChildren).toBe(0)
    expect(firstFrame.colorScheme).toBe("dark")
    expect(firstFrame.backgroundColor).toMatch(/^oklch\((?:0\.145|14\.5%) 0 0\)$/)
    expect(firstFrame.backgroundToken).toMatch(/^oklch\((?:0\.145|14\.5%) 0 0\)$/)
  } finally {
    releaseEntryModule()
  }
});

test("smoke: /health route renders without crashing", async ({ page }) => {
  // Domain API requests without page.route() fixtures receive an explicit 404
  // from the local test harness, but the React tree must still mount cleanly.
  await page.goto("/health", { timeout: 10_000 });

  // Root element must be attached.
  await expect(page.locator("#root")).toBeAttached();

  // The health overview page container must be present.
  // data-testid="health-overview-page" is set on the page root div.
  await expect(page.locator('[data-testid="health-overview-page"]')).toBeAttached({
    timeout: 5_000,
  });
});
