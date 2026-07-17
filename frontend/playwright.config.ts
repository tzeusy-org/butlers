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
 * By default, Playwright starts a strict local API mock and a `vite preview`
 * server automatically (via the `webServer` config below). Set
 * PLAYWRIGHT_BASE_URL to point at a running instance instead:
 *   PLAYWRIGHT_BASE_URL=https://your-instance.example.com npm run test:e2e
 *
 * Local isolated workflow — Playwright starts both test processes:
 *   npm run build
 *   npm run test:e2e
 *
 * To test against the Vite dev server instead, set PLAYWRIGHT_BASE_URL:
 *   PLAYWRIGHT_BASE_URL=http://localhost:5173 npm run test:e2e
 *
 * In CI, Playwright always starts fresh local processes so each run is
 * reproducible and independent of any external process.
 */

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || "http://localhost:4173";
const API_MOCK_PORT = 4174;
const API_MOCK_URL = `http://127.0.0.1:${API_MOCK_PORT}`;

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
   * webServer: Playwright manages the test-only API mock and preview server
   * lifecycle. The mock answers only /api/health; every other API route gets
   * an explicit 404 error envelope unless its test registers page.route().
   *
   * - Uses `vite preview` (port 4173) over a prior `vite build`, which is
   *   closer to production than `vite dev` and avoids HMR overhead in CI.
   * - The preview process proxies /api to the API mock's dedicated port, so
   *   ready preview HTML cannot be mistaken for ready API coverage.
   * - `reuseExistingServer: !CI` permits local iteration against an existing
   *   preview/mock pair. In CI, both are always fresh.
   * - The build step is a separate CI job step; here we only start preview.
   */
  webServer: process.env.PLAYWRIGHT_BASE_URL
    ? undefined
    : [
        {
          command: `E2E_API_MOCK_PORT=${API_MOCK_PORT} node scripts/e2e-api-mock.mjs`,
          url: `${API_MOCK_URL}/api/health`,
          reuseExistingServer: !process.env.CI,
          timeout: 60_000,
          stdout: "pipe",
          stderr: "pipe",
        },
        {
          command: `VITE_PROXY_TARGET=${API_MOCK_URL} npm run preview`,
          url: BASE_URL,
          reuseExistingServer: !process.env.CI,
          timeout: 60_000,
          stdout: "pipe",
          stderr: "pipe",
        },
      ],
});
