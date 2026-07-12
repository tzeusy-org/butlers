/**
 * Playwright smoke test — /entities/circles (bu-hmdqz.5)
 *
 * CirclesPage.tsx's unit tests fully mock `useGroups`, so they never exercise
 * the actual query string sent to `GET /api/relationship/groups`. That let a
 * `FETCH_LIMIT=500` client constant drift silently past the backend's
 * `limit` ceiling (`le=200`, roster/relationship/api/router.py:555) and 422
 * the route on every real load — invisible to unit tests, only caught by a
 * live audit. This test intercepts the real route shape and simulates the
 * backend's own `limit<=200` validation, so a regression back to a
 * too-large client limit fails the test the same way it fails in prod.
 *
 * DESIGN:
 * - All API calls are intercepted via page.route() — no real backend
 *   required — but the groups route stub enforces the same `le=200`
 *   constraint the FastAPI router enforces, returning 422 if violated.
 * - Run: cd frontend && npm run test:e2e -- circles.spec.ts
 */

import { test, expect, type Page } from "@playwright/test";

const TIMEOUT_MS = 10_000;

const FAMILY_GROUP = {
  id: "group-family",
  name: "Family",
  description: "Immediate family",
  member_count: 3,
  labels: [],
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

/**
 * Stub GET /api/relationship/groups the way the real backend behaves:
 * `limit` must satisfy `1 <= limit <= 200` (router.py:555) or the request
 * 422s. This is the load-bearing assertion — CirclesPage must never send a
 * `limit` outside that range.
 */
async function installGroupsStub(page: Page) {
  await page.route("**/api/relationship/groups?**", (route) => {
    const url = new URL(route.request().url());
    const limit = Number(url.searchParams.get("limit") ?? "50");
    if (!Number.isFinite(limit) || limit < 1 || limit > 200) {
      route.fulfill({
        status: 422,
        contentType: "application/json",
        body: JSON.stringify({
          detail: [
            {
              loc: ["query", "limit"],
              msg: "ensure this value is less than or equal to 200",
              type: "value_error.number.not_le",
            },
          ],
        }),
      });
      return;
    }
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ groups: [FAMILY_GROUP], total: 1 }),
    });
  });
  await page.route("**/api/relationship/labels", (route) => {
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
}

test.describe("circles: /entities/circles", () => {
  test("loads circle rows without a 422 against the real backend limit constraint", async ({
    page,
  }) => {
    await installGroupsStub(page);
    await page.goto("/entities/circles", { timeout: TIMEOUT_MS });

    // A regression to a client limit >200 would 422 and render the Page
    // error region instead of the circle row — this is the assertion that
    // catches the bug class.
    await expect(page.getByText("Family")).toBeVisible({ timeout: TIMEOUT_MS });
    await expect(page.getByText("3 members")).toBeVisible();
    await expect(page.getByText("Something went wrong")).not.toBeVisible();
  });
});
