/**
 * Settings Spend Page - /settings/spend  [bu-8dk6b section 5.6]
 *
 * Three test scenarios (all API calls mocked via Playwright route handlers;
 * no real server or DB required):
 *
 *   1. Happy path      - open the page, verify it renders without crashing,
 *                        verify spend summary KPIs load (MTD, projected EOM,
 *                        ceiling, days-in-month cells all present).
 *   2. Chart render    - assert the SVG forecast chart is visible and
 *                        contains actual + projected polyline segments.
 *   3. Ceiling-update  - click "Set ceiling", enter a value, submit, assert
 *                        PUT was called with the right payload, mock the
 *                        re-fetch, assert KPI strip re-renders with new ceiling.
 *
 * Prerequisites:
 *   npm run dev          (in a separate terminal), or point PLAYWRIGHT_BASE_URL
 *   npm run test:e2e:install (once per machine to install Chromium)
 *
 * Mocking strategy:
 *   - HTTP routes: page.route() intercepts GET /api/spend/forecast,
 *     GET /api/spend/breakdown, GET /api/spend/rules, PUT /api/spend/ceiling.
 *   - WebSocket: page.routeWebSocket() for /api/spend/stream, returns an
 *     immediate empty snapshot so the hook settles in a deterministic state
 *     without blocking page load.
 *   - No real backend or DB required.
 */

import { test, expect, Page } from "@playwright/test";

// ---------------------------------------------------------------------------
// Shared fixture data
// ---------------------------------------------------------------------------

const DAYS_ELAPSED = 17;
const DAYS_IN_MONTH = 31;

/** Deterministic forecast: 17 actual days + 14 projected days. */
function buildForecastDays() {
  const days = [];
  for (let i = 1; i <= DAYS_ELAPSED; i++) {
    days.push({
      date: `2026-05-${String(i).padStart(2, "0")}`,
      cost_usd: 0.5 + i * 0.1,
      projected: false,
    });
  }
  for (let i = DAYS_ELAPSED + 1; i <= DAYS_IN_MONTH; i++) {
    days.push({
      date: `2026-05-${String(i).padStart(2, "0")}`,
      cost_usd: 0.5 + i * 0.1,
      projected: true,
    });
  }
  return days;
}

const MOCK_FORECAST = {
  data: {
    days: buildForecastDays(),
    projected_eom_usd: 5.42,
    days_in_month: DAYS_IN_MONTH,
    days_elapsed: DAYS_ELAPSED,
    mtd_usd: 2.20,
    ceiling_usd: null,
  },
};

const MOCK_FORECAST_WITH_CEILING = {
  data: {
    ...MOCK_FORECAST.data,
    ceiling_usd: 10.0,
  },
};

const MOCK_BREAKDOWN = {
  data: {
    by: "butler",
    breakdown: {
      inbox: 1.5,
      calendar: 0.7,
    },
  },
};

const MOCK_RULES = {
  data: [],
};

// ---------------------------------------------------------------------------
// Helper: install all baseline API mocks
// ---------------------------------------------------------------------------

async function installBaseMocks(page: Page) {
  // Forecast endpoint
  await page.route("**/api/spend/forecast", (route) => {
    if (route.request().method() === "GET") {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_FORECAST),
      });
    } else {
      route.continue();
    }
  });

  // Breakdown endpoint (all ?by= variants)
  await page.route("**/api/spend/breakdown**", (route) => {
    if (route.request().method() === "GET") {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_BREAKDOWN),
      });
    } else {
      route.continue();
    }
  });

  // Routing rules endpoint
  await page.route("**/api/spend/rules", (route) => {
    if (route.request().method() === "GET") {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_RULES),
      });
    } else {
      route.continue();
    }
  });

  // WebSocket spend stream - return an empty snapshot immediately so the
  // useSpendStream hook settles without blocking page interaction.
  await page.routeWebSocket("**/api/spend/stream", (ws) => {
    ws.onopen(() => {
      ws.send(JSON.stringify({ kind: "snapshot", events: [] }));
    });
  });
}

// ---------------------------------------------------------------------------
// Helper: navigate to the spend page, skip gracefully if server is absent
// ---------------------------------------------------------------------------

async function gotoSpendPage(page: Page, baseURL: string | undefined): Promise<boolean> {
  try {
    await page.goto("/settings/spend", { timeout: 10_000 });
  } catch {
    test.skip(
      true,
      `Dev server not reachable at ${baseURL} -- start it with: npm run dev`,
    );
    return false;
  }
  return true;
}

// ---------------------------------------------------------------------------
// Test 1: Happy path - page renders and KPI strip populates
// ---------------------------------------------------------------------------

test("spend: page renders and KPI strip shows spend totals", async ({ page, baseURL }) => {
  await installBaseMocks(page);

  const reachable = await gotoSpendPage(page, baseURL);
  if (!reachable) return;

  // MTD Spend cell header label
  const mtdCell = page.getByText("MTD Spend");
  await expect(mtdCell).toBeVisible();

  // The formatted MTD value from MOCK_FORECAST.data.mtd_usd (2.20 -> "$2.20")
  await expect(page.getByText("$2.20")).toBeVisible();

  // Projected EOM cell header label
  await expect(page.getByText("Projected EOM")).toBeVisible();

  // Projected value from MOCK_FORECAST.data.projected_eom_usd (5.42 -> "$5.42")
  await expect(page.getByText("$5.42")).toBeVisible();

  // Monthly Ceiling cell is present (value is "-" since ceiling_usd is null)
  await expect(page.getByText("Monthly Ceiling")).toBeVisible();

  // Days in Month cell shows the correct count
  await expect(page.getByText("Days in Month")).toBeVisible();
  await expect(page.getByText(String(DAYS_IN_MONTH))).toBeVisible();

  // Days elapsed sub-label
  await expect(page.getByText(`${DAYS_ELAPSED} days elapsed`)).toBeVisible();
});

// ---------------------------------------------------------------------------
// Test 2: Chart render - SVG forecast chart is visible with data segments
// ---------------------------------------------------------------------------

test("spend: forecast chart is visible and contains actual + projected segments", async ({ page, baseURL }) => {
  await installBaseMocks(page);

  const reachable = await gotoSpendPage(page, baseURL);
  if (!reachable) return;

  // The hand-rolled SVG has aria-label="Spend forecast chart"
  const chart = page.getByRole("img", { name: /spend forecast chart/i });
  await expect(chart).toBeVisible();

  // The chart must contain two polylines:
  //   1. Actual spend - solid (no stroke-dasharray)
  //   2. Projected spend - dashed (stroke-dasharray="6 4")
  const polylines = chart.locator("polyline");
  await expect(polylines).toHaveCount(2);

  // The projected segment must carry the dashed stroke attribute
  const projectedLine = chart.locator("polyline[stroke-dasharray]");
  await expect(projectedLine).toHaveCount(1);
  await expect(projectedLine).toHaveAttribute("stroke-dasharray", "6 4");

  // Card description text is rendered below the chart header
  await expect(page.getByText(/solid = actual mtd spend/i)).toBeVisible();
});

// ---------------------------------------------------------------------------
// Test 3: Ceiling-update flow - open edit, submit, assert PUT, re-render
// ---------------------------------------------------------------------------

test("spend: ceiling-update flow submits PUT and re-renders with new ceiling", async ({ page, baseURL }) => {
  // Track PUT /api/spend/ceiling calls
  let putCalled = false;
  let putBody: Record<string, unknown> | null = null;

  // Flag toggled after PUT so the subsequent GET /forecast re-fetch
  // returns updated data with the new ceiling value.
  let ceilingSet = false;

  // Install the ceiling PUT interceptor before base mocks so it takes
  // precedence over the wildcard **/api/spend/** route in installBaseMocks.
  await page.route("**/api/spend/ceiling", (route) => {
    if (route.request().method() === "PUT") {
      putCalled = true;
      const raw = route.request().postData() ?? "{}";
      putBody = JSON.parse(raw) as Record<string, unknown>;
      ceilingSet = true;
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: null, meta: {} }),
      });
    } else {
      route.continue();
    }
  });

  // After the PUT the page invalidates and re-fetches /spend/forecast.
  // Register forecast route before installBaseMocks to intercept it.
  await page.route("**/api/spend/forecast", (route) => {
    if (route.request().method() === "GET") {
      const body = ceilingSet ? MOCK_FORECAST_WITH_CEILING : MOCK_FORECAST;
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(body),
      });
    } else {
      route.continue();
    }
  });

  await installBaseMocks(page);

  const reachable = await gotoSpendPage(page, baseURL);
  if (!reachable) return;

  // Initially the ceiling is null - button reads "Set ceiling"
  const setCeilingBtn = page.getByRole("button", { name: /set ceiling/i });
  await expect(setCeilingBtn).toBeVisible();

  // Click to open the inline edit form
  await setCeilingBtn.click();

  // The number input should be focused; fill it with the new ceiling
  const ceilingInput = page.locator('input[type="number"]');
  await expect(ceilingInput).toBeVisible();
  await ceilingInput.fill("10");

  // Click Save
  const saveBtn = page.getByRole("button", { name: /^save$/i });
  await expect(saveBtn).toBeVisible();
  await saveBtn.click();

  // PUT must have been called with { monthly_usd: 10 }
  await expect.poll(() => putCalled, { timeout: 5_000 }).toBe(true);
  expect(putBody).toMatchObject({ monthly_usd: 10 });

  // After success the edit form collapses and the ceiling appears in the KPI strip.
  // MOCK_FORECAST_WITH_CEILING has ceiling_usd = 10.0 -> "$10.00"
  await expect(page.getByText("$10.00")).toBeVisible();

  // The "Set ceiling" button is replaced by "Edit ceiling ($10.00)"
  await expect(page.getByRole("button", { name: /edit ceiling/i })).toBeVisible();
});
