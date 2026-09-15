import { defineConfig } from "@playwright/test";

// Auth payloads must never enter trace/video/screenshots, even for synthetic runs.
export default defineConfig({
  testDir: "tests/owner-auth", timeout: 60_000, workers: 1, retries: 0,
  reporter: "list", use: { trace: "off", video: "off", screenshot: "off" },
});
