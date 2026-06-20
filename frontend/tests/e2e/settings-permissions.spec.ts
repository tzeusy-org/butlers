/**
 * Settings Permissions Page — /settings/permissions
 *
 * Three test scenarios (all API calls mocked via Playwright route handlers;
 * no real server or DB required):
 *
 *   1. Matrix flip with reason   — open page, click a cell, fill reason, submit,
 *                                   assert PUT was called and matrix re-rendered.
 *   2. Wipe panel disabled       — assert the wipe panel renders as permanently
 *                                   disabled (no phrase input, button disabled),
 *                                   and DELETE /api/data/wipe is never called.
 *   3. Webhook test action       — mock webhook list + test endpoint, click the
 *                                   test-webhook button, assert the last-tested
 *                                   cell updates with a success indicator.
 *
 * The preview server is managed by playwright.config.ts `webServer`; tests
 * rely on it being available and will fail hard (not skip) if it is not.
 *
 * Prerequisites:
 *   npm run build && npm run preview  (or Playwright starts preview automatically)
 *   npm run test:e2e:install (once per machine to install Chromium)
 */

import { test, expect, Page } from "@playwright/test";

// ---------------------------------------------------------------------------
// Shared fixture data
// ---------------------------------------------------------------------------

const WEBHOOK_ID = "wh-001";

const MOCK_MATRIX = {
  data: {
    butlers: ["inbox"],
    permissions: ["email.read"],
    cells: {
      inbox: {
        "email.read": {
          granted: false,
          reason: null,
          updated_at: null,
          inherited: false,
        },
      },
    },
  },
  meta: {},
};

const MOCK_WEBHOOKS = {
  data: [
    {
      id: WEBHOOK_ID,
      endpoint: "https://example.com/hook",
      events: ["permission.set"],
      enabled: true,
      last_test_at: null,
      last_test_ok: null,
      retry_policy: { max_attempts: 3, backoff_seconds: 5 },
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
  ],
  meta: {},
};

const MOCK_AUDIT_LOG = { data: [], meta: {} };

// ---------------------------------------------------------------------------
// Helper: install all baseline API mocks
// ---------------------------------------------------------------------------

async function installBaseMocks(page: Page) {
  // Permissions matrix
  await page.route("**/api/permissions", (route) => {
    if (route.request().method() === "GET") {
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_MATRIX) });
    } else {
      route.continue();
    }
  });

  // Audit log
  await page.route("**/api/audit-log**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_AUDIT_LOG) }),
  );

  // Webhooks list
  await page.route("**/api/webhooks", (route) => {
    if (route.request().method() === "GET") {
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_WEBHOOKS) });
    } else {
      route.continue();
    }
  });
}

// ---------------------------------------------------------------------------
// Test 1: Matrix flip with reason
// ---------------------------------------------------------------------------

test("permissions: matrix cell flip requires reason and calls PUT", async ({ page }) => {
  await installBaseMocks(page);

  // Track PUT call to assert it was made
  let putCalled = false;
  let putBody: Record<string, unknown> | null = null;

  await page.route("**/api/permissions/**", async (route) => {
    if (route.request().method() === "PUT") {
      putCalled = true;
      putBody = JSON.parse(route.request().postData() ?? "{}");

      // After the flip, return updated matrix with the cell now granted
      const updatedMatrix = {
        data: {
          ...MOCK_MATRIX.data,
          cells: {
            inbox: {
              "email.read": {
                granted: true,
                reason: "testing grant",
                updated_at: new Date().toISOString(),
                inherited: false,
              },
            },
          },
        },
        meta: {},
      };

      // Intercept the subsequent GET /api/permissions after PUT to return updated data
      await page.route("**/api/permissions", (r) => {
        if (r.request().method() === "GET") {
          r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(updatedMatrix) });
        } else {
          r.continue();
        }
      });

      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: null, meta: {} }) });
    } else {
      route.continue();
    }
  });

  await page.goto("/settings/permissions", { timeout: 10_000 });

  // Wait for the matrix to render
  const cellButton = page.getByTestId("perm-cell-inbox-email.read");
  await expect(cellButton).toBeVisible();
  // Dispatch: denied state is a hollow "○" glyph; assert the semantic aria-label
  // rather than the word-badge text the old design used.
  await expect(cellButton).toHaveAttribute("aria-label", /denied/);

  // Click the cell — the flip modal should open
  await cellButton.click();

  // Modal should appear with "Grant permission" title
  const modalTitle = page.getByRole("heading", { name: /grant permission/i });
  await expect(modalTitle).toBeVisible();

  // Submit button should be disabled (blank reason)
  const submitBtn = page.getByRole("button", { name: /^grant$/i });
  await expect(submitBtn).toBeDisabled();

  // Fill in a reason
  const reasonInput = page.locator("#flip-reason");
  await reasonInput.fill("testing grant");

  // Submit button should now be enabled
  await expect(submitBtn).toBeEnabled();

  // Confirm
  await submitBtn.click();

  // Modal should close
  await expect(modalTitle).not.toBeVisible();

  // PUT must have been called with the right payload
  expect(putCalled).toBe(true);
  expect(putBody).toMatchObject({ granted: true, reason: "testing grant" });

  // Matrix re-renders — cell should now show the granted "●" glyph
  await expect(page.getByTestId("perm-cell-inbox-email.read")).toHaveAttribute(
    "aria-label",
    /granted/,
  );
});

// ---------------------------------------------------------------------------
// Test 2: Wipe panel is permanently disabled — no phrase input, no endpoint call
// ---------------------------------------------------------------------------

test("permissions: wipe panel is permanently disabled — no phrase input, DELETE never called", async ({ page }) => {
  await installBaseMocks(page);

  // Guard: DELETE /api/data/wipe must never be called from the UI
  let wipeCalled = false;
  await page.route("**/api/data/wipe", (route) => {
    wipeCalled = true;
    route.fulfill({ status: 503, contentType: "application/json", body: '{"detail": {"error": "wipe_disabled"}}' });
  });

  await page.goto("/settings/permissions", { timeout: 10_000 });

  // The disabled wipe panel must render
  const wipePanel = page.getByTestId("wipe-panel-disabled");
  await expect(wipePanel).toBeVisible();

  // The "Wipe everything" button must be permanently disabled
  const wipeButton = page.getByRole("button", { name: /wipe everything/i });
  await expect(wipeButton).toBeDisabled();

  // No phrase input should exist
  const phraseInput = page.locator("#wipe-phrase");
  await expect(phraseInput).not.toBeVisible();

  // Verify DELETE was never called
  expect(wipeCalled).toBe(false);
});

// ---------------------------------------------------------------------------
// Test 3: Webhook test action updates last-tested cell
// ---------------------------------------------------------------------------

test("permissions: webhook test action updates last-tested indicator", async ({ page }) => {
  await installBaseMocks(page);

  const testedAt = "2026-05-17T12:00:00.000Z";

  // State flag: set to true when the POST /test fires so subsequent GET /webhooks
  // returns the updated data. Using a flag (rather than a request counter) is robust
  // to React StrictMode double-invoking effects during the initial page load.
  let testTriggered = false;

  // Mock the test endpoint
  await page.route(`**/api/webhooks/${WEBHOOK_ID}/test`, (route) => {
    if (route.request().method() === "POST") {
      testTriggered = true;
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { ok: true, status_code: 200, latency_ms: 42 }, meta: {} }),
      });
    } else {
      route.continue();
    }
  });

  // After the test, the reload() call fetches webhooks again with updated last_test_at.
  // Only return the updated webhook data after testTriggered is set by the POST above.
  await page.route("**/api/webhooks", (route) => {
    if (route.request().method() === "GET") {
      if (testTriggered) {
        // Post-test fetch: return webhook with last_test_at set
        const updated = {
          data: [
            {
              ...MOCK_WEBHOOKS.data[0],
              last_test_at: testedAt,
              last_test_ok: true,
            },
          ],
          meta: {},
        };
        route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(updated) });
      } else {
        route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_WEBHOOKS) });
      }
    } else {
      route.continue();
    }
  });

  await page.goto("/settings/permissions", { timeout: 10_000 });

  // Wait for webhook row to render
  const webhookRow = page.getByTestId(`webhook-row-${WEBHOOK_ID}`);
  await expect(webhookRow).toBeVisible();

  // Before test: last-tested cell shows dash
  const lastTestCell = page.getByTestId(`webhook-last-test-${WEBHOOK_ID}`);
  await expect(lastTestCell).toContainText("—");

  // Click the test button
  const testBtn = page.getByTestId(`webhook-test-${WEBHOOK_ID}`);
  await expect(testBtn).toBeVisible();
  await testBtn.click();

  // After the test completes and reload() runs, the cell should show a success indicator
  // The CheckCircle icon has data-testid="webhook-test-ok"
  await expect(lastTestCell.getByTestId("webhook-test-ok")).toBeVisible();

  // The timestamp should be updated (contains the date string)
  await expect(lastTestCell).not.toContainText("—");
});
