import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.PLAYWRIGHT_BASE_URL;
const artifactDir = process.env.ROUTE_A_ARTIFACT_DIR;
const populatedTitle = process.env.ROUTE_A_POPULATED_EVENT_TITLE;
const emptyTitle = process.env.ROUTE_A_EMPTY_EVENT_TITLE;
const populatedEventId = process.env.ROUTE_A_POPULATED_EVENT_ID;
const emptyEventId = process.env.ROUTE_A_EMPTY_EVENT_ID;

if (process.env.ROUTE_A_EVIDENCE !== "1") {
  throw new Error("Route A browser evidence requires ROUTE_A_EVIDENCE=1.");
}

if (baseURL !== "http://frontend:4173") {
  throw new Error("Route A browser evidence only accepts the isolated frontend service.");
}

if (!artifactDir || !populatedTitle || !emptyTitle || !populatedEventId || !emptyEventId) {
  throw new Error("Route A browser evidence requires all isolated fixture and artifact variables.");
}

export default defineConfig({
  testDir: "tests/e2e",
  testMatch: "meeting-prep-route-a.evidence.spec.ts",
  forbidOnly: true,
  retries: 0,
  workers: 1,
  outputDir: artifactDir,
  reporter: [
    ["list"],
    ["junit", { outputFile: `${artifactDir}/junit.xml` }],
  ],
  use: {
    ...devices["Desktop Chrome"],
    baseURL,
    screenshot: "on",
    trace: "on",
    video: "on",
  },
});
