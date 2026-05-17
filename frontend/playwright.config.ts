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
 * The dev server must be running before the tests execute. Start it with:
 *   npm run dev
 *
 * Or point at a deployed instance:
 *   PLAYWRIGHT_BASE_URL=https://your-instance.example.com npm run test:e2e
 */
export default defineConfig({
  testDir: "tests/e2e",

  timeout: 30_000,

  retries: process.env.CI ? 2 : 0,

  workers: process.env.CI ? 1 : 4,

  reporter: process.env.CI ? "github" : "list",

  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || "http://localhost:5173",
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
});
