import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright e2e configuration for the Butlers dashboard frontend.
 *
 * Run locally:
 *   npm run test:e2e          — headless chromium
 *   npm run test:e2e:headed   — headed chromium (good for debugging)
 *
 * Install browsers first:
 *   npm run test:e2e:install
 *
 * By default, Playwright will start a `vite preview` server automatically
 * (via the `webServer` config below). Set PLAYWRIGHT_BASE_URL to point at a
 * running instance instead:
 *   PLAYWRIGHT_BASE_URL=https://your-instance.example.com npm run test:e2e
 *
 * Local dev workflow — reuse an already-running preview server:
 *   npm run preview    (in a separate terminal, starts at :4173)
 *   npm run test:e2e   (Playwright detects the server and reuses it)
 *
 * To test against the Vite dev server instead, set PLAYWRIGHT_BASE_URL:
 *   PLAYWRIGHT_BASE_URL=http://localhost:5173 npm run test:e2e
 *
 * In CI, Playwright always starts a fresh `vite preview` server so each run
 * is reproducible and independent of any external process.
 */

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || "http://localhost:4173";

export default defineConfig({
  testDir: "tests/e2e",

  timeout: 30_000,

  retries: process.env.CI ? 2 : 0,

  workers: process.env.CI ? 1 : 4,

  reporter: process.env.CI ? "github" : "list",

  use: {
    baseURL: BASE_URL,
    screenshot: "only-on-failure",
    trace: "on-first-retry",
    video: "retain-on-failure",
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],

  /**
   * webServer: Playwright manages the preview server lifecycle.
   *
   * - Uses `vite preview` (port 4173) over a prior `vite build`, which is
   *   closer to production than `vite dev` and avoids HMR overhead in CI.
   * - `reuseExistingServer: !CI` lets local developers keep their own dev
   *   server running without Playwright trying to start a second one.
   *   In CI (where CI=true), Playwright always starts a fresh server.
   * - The build step is a separate CI job step; here we only start preview.
   */
  webServer: {
    command: "npm run preview",
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
    stdout: "pipe",
    stderr: "pipe",
  },
});
