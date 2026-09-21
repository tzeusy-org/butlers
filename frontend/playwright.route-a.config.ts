import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.PLAYWRIGHT_BASE_URL;
const artifactDir = process.env.ROUTE_A_ARTIFACT_DIR;

if (process.env.ROUTE_A_EVIDENCE !== "1") {
  throw new Error("Route A browser evidence requires ROUTE_A_EVIDENCE=1.");
}

if (baseURL !== "http://frontend:4173") {
  throw new Error("Route A browser evidence only accepts the isolated frontend service.");
}

if (!artifactDir) {
  throw new Error("Route A browser evidence requires its disposable artifact mount.");
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
