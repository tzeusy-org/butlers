import { expect, test } from "@playwright/test";

test.describe("local Playwright API harness", () => {
  test.skip(
    Boolean(process.env.PLAYWRIGHT_BASE_URL),
    "the local mock API is only started for Playwright-managed preview runs",
  );

  test("proxies a ready API and rejects an unmocked route explicitly", async ({ request }) => {
    const health = await request.get("/api/health");

    expect(health.status()).toBe(200);
    await expect(health.json()).resolves.toEqual({ status: "ok" });

    const unmocked = await request.get("/api/__playwright_harness_unmocked__");

    expect(unmocked.status()).toBe(404);
    await expect(unmocked.json()).resolves.toEqual({
      error: {
        code: "E2E_UNMOCKED_API_ROUTE",
        message: "No Playwright API mock is registered for GET /api/__playwright_harness_unmocked__.",
        butler: null,
        details: null,
      },
    });
  });
});
