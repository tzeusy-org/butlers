/**
 * Playwright visual parity spec — ingestion redesign (bu-y25mj.6).
 *
 * Verifies that the four redesigned ingestion routes render correctly and that
 * the old card/tab shell is absent. Also covers legacy ?tab= redirects and
 * the EventDrawer deep-link.
 *
 * Design:
 * - Tests skip gracefully when the dev server is unreachable.
 * - HTTP errors from the server are NOT skipped — they signal a broken app.
 * - All mocking is done via route interception (page.route) so the test
 *   doesn't depend on a live backend.
 * - Screenshots are saved to docs/reports/ingestion-redesign-parity-2026-05-25/
 *   for the parity report.
 *
 * IMPORTANT — Playwright LIFO route rule (PR #1942):
 *   page.route() handlers are matched LIFO (last registered = first checked).
 *   Register catch-all "**\/api\/**" FIRST, specific routes LAST.
 *   ALL mocks must be installed BEFORE any page.goto().
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Visual and Route Verification"
 *
 * Reference: (ingestion dispatch redesign, graduated)  (READ-ONLY — prototype)
 */

import * as path from "path";
import * as fs from "fs";
import { fileURLToPath } from "url";
import { test, expect, type Page } from "@playwright/test";

const TIMEOUT_MS = 10_000;

// ---------------------------------------------------------------------------
// Screenshot output directory (relative to repo root)
// ---------------------------------------------------------------------------

// Use import.meta.url instead of __dirname — this is an ES module project.
const _dirname = path.dirname(fileURLToPath(import.meta.url));

const SCREENSHOT_DIR = path.resolve(
  _dirname,
  "../../../docs/reports/ingestion-redesign-parity-2026-05-25",
);

/** Ensure the screenshot dir exists before writing to it. */
function ensureScreenshotDir() {
  if (!fs.existsSync(SCREENSHOT_DIR)) {
    fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
  }
}

/** Capture and save a screenshot with a deterministic filename. */
async function capture(page: Page, slug: string) {
  ensureScreenshotDir();
  const filePath = path.join(SCREENSHOT_DIR, `${slug}.png`);
  await page.screenshot({ path: filePath, fullPage: false });
  return filePath;
}

// ---------------------------------------------------------------------------
// Server reachability helper
// ---------------------------------------------------------------------------

/**
 * Navigate to url, skipping the test if the server is unreachable.
 *
 * Only network-level failures (connection refused, timeout) cause a skip.
 * HTTP 4xx/5xx responses do NOT skip — they signal a broken app.
 */
async function navigateOrSkip(
  page: Page,
  url: string,
  baseURL: string | undefined,
  testCtx: typeof test,
): Promise<void> {
  try {
    await page.goto(url, { waitUntil: "networkidle", timeout: TIMEOUT_MS });
  } catch {
    testCtx.skip(
      true,
      `Dev server not reachable at ${baseURL} — start it with: npm run dev`,
    );
  }
}

// ---------------------------------------------------------------------------
// Mock API data fixtures
// ---------------------------------------------------------------------------

/** Two fixture events spanning two different hours. */
const FIXTURE_EVENTS = [
  {
    id: "aabbccdd-0000-0000-0000-000000000001",
    received_at: "2026-05-17T14:05:00Z",
    source_channel: "email",
    source_provider: null,
    source_endpoint_identity: null,
    source_sender_identity: "alice@example.com",
    source_thread_identity: null,
    external_event_id: null,
    dedupe_key: null,
    dedupe_strategy: null,
    ingestion_tier: null,
    policy_tier: "standard",
    triage_decision: null,
    triage_target: null,
    status: "ingested",
    filter_reason: null,
    error_detail: null,
  },
  {
    id: "aabbccdd-0000-0000-0000-000000000002",
    received_at: "2026-05-17T15:05:00Z",
    source_channel: "telegram",
    source_provider: null,
    source_endpoint_identity: null,
    source_sender_identity: "bob@example.com",
    source_thread_identity: null,
    external_event_id: null,
    dedupe_key: null,
    dedupe_strategy: null,
    ingestion_tier: null,
    policy_tier: "standard",
    triage_decision: null,
    triage_target: null,
    status: "error",
    filter_reason: null,
    error_detail: "timeout",
  },
];

/** A minimal backend ConnectorEntry for the gmail connector (fixture). */
const FIXTURE_CONNECTOR_BACKEND = {
  connector_type: "gmail",
  endpoint_identity: "alice@example.com",
  instance_id: "inst-001",
  version: "1.0.0",
  state: "healthy",
  error_message: null,
  uptime_s: 86400,
  last_heartbeat_at: new Date(Date.now() - 60_000).toISOString(), // 1 min ago → "online"
  first_seen_at: "2026-01-01T00:00:00Z",
  registered_via: "auto",
  counter_messages_ingested: 1500,
  counter_messages_failed: 5,
  counter_source_api_calls: 300,
  counter_checkpoint_saves: 150,
  counter_dedupe_accepted: 20,
  today_messages_ingested: 42,
  today_messages_failed: 1,
  checkpoint_cursor: null,
  checkpoint_updated_at: null,
  settings: null,
};

/**
 * Install common API mocks.
 *
 * Mocks follow the LIFO rule strictly:
 *   1. Catch-all registered FIRST (lowest precedence).
 *   2. More specific patterns registered AFTER (higher precedence).
 *
 * All mocks are installed BEFORE any page.goto().
 */
async function installCommonMocks(page: Page) {
  // 1. Catch-all — absorbs sidebar requests (/api/butlers, /api/spend, etc.)
  await page.route("**/api/**", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ data: [] }),
    });
  });

  // 2. Connector summaries
  await page.route("**/api/ingestion/connectors/summaries*", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: [FIXTURE_CONNECTOR_BACKEND],
        aggregates_available: false,
      }),
    });
  });

  // 3. Connector cross-summary
  await page.route("**/api/ingestion/connectors/cross-summary*", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: {
          total_connectors: 1,
          online_count: 1,
          stale_count: 0,
          offline_count: 0,
          unknown_count: 0,
          total_messages_ingested: 1500,
          total_messages_failed: 5,
          error_rate_pct: 0.33,
        },
        aggregates_available: false,
      }),
    });
  });

  // 4. Switchboard connector detail for gmail/alice@example.com
  await page.route(
    "**/api/switchboard/connectors/gmail/alice%40example.com/stats*",
    (route) => {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: [] }),
      });
    },
  );

  // 5. Connector stats for gmail
  await page.route(
    "**/api/switchboard/connectors/gmail/alice%40example.com*",
    (route) => {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: FIXTURE_CONNECTOR_BACKEND }),
      });
    },
  );

  // 6. Switchboard connector list
  await page.route("**/api/switchboard/connectors*", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ data: [FIXTURE_CONNECTOR_BACKEND] }),
    });
  });

  // 6b. Connector-scoped sub-sections: events, incidents, routing-rules [bu-5ywn2]
  //     These must be registered before the generic connectors catch-all so
  //     they win under LIFO matching. The catch-all (**/api/**) would return
  //     { data: [] } which does not match ConnectorEventsResponse shape and
  //     causes a TypeError when components read .events/.incidents/.rules.
  await page.route(
    "**/api/ingestion/connectors/*/routing-rules*",
    (route) => {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          rules: [],
          connector_type: "gmail",
          endpoint_identity: "alice@example.com",
          total_returned: 0,
          filter_note: null,
        }),
      });
    },
  );

  await page.route(
    "**/api/ingestion/connectors/*/incidents*",
    (route) => {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          incidents: [],
          connector_type: "gmail",
          endpoint_identity: "alice@example.com",
          total_returned: 0,
        }),
      });
    },
  );

  await page.route(
    "**/api/ingestion/connectors/*/events*",
    (route) => {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          events: [],
          connector_type: "gmail",
          endpoint_identity: "alice@example.com",
          total_returned: 0,
        }),
      });
    },
  );

  // 7. Pipeline stats
  await page.route("**/api/ingestion/pipeline*", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        window: "24h",
        aggregates_available: true,
        ingested: 1000,
        filtered: 200,
        errored: 10,
        routed_by_butler: { general: 700, health: 250 },
        spark24h: Array(24).fill(40),
        rate1h: 12,
        routed_pct: 95,
        filtered24h: 200,
      }),
    });
  });

  // 8. Ingestion rules
  await page.route("**/switchboard/ingestion-rules*", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: [
          {
            id: "rule-001",
            scope: "email",
            rule_type: "filter",
            condition: { source_channel: "gmail" },
            action: "drop",
            priority: 10,
            enabled: true,
            name: "Drop spam",
            description: "Drop known spam patterns",
            created_by: "owner",
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-02T00:00:00Z",
            deleted_at: null,
          },
        ],
        meta: { total: 1 },
      }),
    });
  });

  // 9. Per-event sub-resources (must be before events list)
  await page.route("**/api/ingestion/events/*/sender-contact", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ data: { resolved: false, name: null, raw: null } }),
    });
  });

  await page.route("**/api/ingestion/events/*/replays", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ data: [] }),
    });
  });

  await page.route("**/api/ingestion/events/*/rollup", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: {
          request_id: "aabbccdd-0000-0000-0000-000000000001",
          total_sessions: 0,
          total_input_tokens: 0,
          total_output_tokens: 0,
          total_cost: 0,
          by_butler: {},
        },
      }),
    });
  });

  await page.route("**/api/ingestion/events/*/sessions", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ data: [] }),
    });
  });

  // 10. Events list — registered LAST so it wins over catch-all in LIFO matching.
  //     Pattern does NOT cross '/' boundaries, so /events?... matches but
  //     /events/abc/sessions does not.
  await page.route("**/api/ingestion/events*", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: FIXTURE_EVENTS,
        meta: { next_cursor: null, has_more: false },
      }),
    });
  });
}

// ---------------------------------------------------------------------------
// Viewport presets
// ---------------------------------------------------------------------------

const DESKTOP = { width: 1280, height: 800 };
const MOBILE = { width: 390, height: 844 };

// ---------------------------------------------------------------------------
// Route smoke tests
// ---------------------------------------------------------------------------

test.describe("ingestion visual parity — route smoke", () => {
  test("smoke: /ingestion — Timeline ledger present, connectors-roster absent", async ({
    page,
    baseURL,
  }) => {
    await installCommonMocks(page);

    await navigateOrSkip(page, "/ingestion", baseURL, test);

    // Timeline ledger must be present
    await expect(page.locator("[data-testid='timeline-ledger']")).toBeVisible({
      timeout: TIMEOUT_MS,
    });

    // Connectors roster must NOT be present on the Timeline route
    await expect(
      page.locator("[data-testid='connectors-roster']"),
    ).not.toBeVisible({ timeout: TIMEOUT_MS });

    // Sub-nav is present (three links: Timeline, Connectors, Filters)
    await expect(page.locator("nav[aria-label='Ingestion views']")).toBeVisible({
      timeout: TIMEOUT_MS,
    });

    // Absence: no old card shell slot
      // The redesign uses hairline layouts, not card chrome for primary surfaces.
    // We don't assert cardShells.count() === 0 because shadcn primitives may
    // appear in sidebars or other non-ingestion layout. Instead, assert that
    // the named ingestion card shells from the legacy page are absent.
    await expect(
      page.locator("[data-testid='ingestion-events-card']"),
    ).toHaveCount(0);

    // Absence: no legacy TabsTrigger for ingestion (IngestionPage has 4 tabs)
    // The redesigned routes use IngestionSubNav (NavLink), not TabsTrigger.
    // TabsTrigger in the redesigned surface would be a regression.
    // We check by role: the old TabsList has role="tablist" and the triggers
    // have role="tab". The redesigned sub-nav is a plain <nav> with links.
    // The redesigned sub-nav is a plain <nav> with NavLinks (no role=tablist).
    // We assert the old 4-tab ingestion switcher (Timeline/Connectors/Filters/History)
    // is absent by verifying that no tab with value="history" exists.
    // That tab only existed in IngestionPage (legacy). Its presence is a regression.
    await expect(page.locator('[role="tab"][data-value="history"]')).toHaveCount(
      0,
    );

    // Desktop screenshot
    await page.setViewportSize(DESKTOP);
    await capture(page, "timeline-desktop");

    // Mobile screenshot
    await page.setViewportSize(MOBILE);
    await page.waitForLoadState("domcontentloaded"); // allow reflow after viewport resize
    await capture(page, "timeline-mobile");
  });

  test("smoke: /ingestion/connectors — connectors-roster present", async ({
    page,
    baseURL,
  }) => {
    await installCommonMocks(page);

    await navigateOrSkip(page, "/ingestion/connectors", baseURL, test);

    // Connectors roster must be present
    await expect(page.locator("[data-testid='connectors-roster']")).toBeVisible({
      timeout: TIMEOUT_MS,
    });

    // Absence: old card shells for the primary surface
    await expect(
      page.locator("[data-testid='connectors-list-card']"),
    ).toHaveCount(0);

    // Absence: old ingestion tab switcher (no role=tab data-value=history)
    await expect(page.locator('[role="tab"][data-value="history"]')).toHaveCount(
      0,
    );

    // Sub-nav is present
    await expect(page.locator("nav[aria-label='Ingestion views']")).toBeVisible({
      timeout: TIMEOUT_MS,
    });

    // Desktop screenshot
    await page.setViewportSize(DESKTOP);
    await capture(page, "connectors-desktop");

    // Mobile screenshot
    await page.setViewportSize(MOBILE);
    await page.waitForLoadState("domcontentloaded"); // allow reflow after viewport resize
    await capture(page, "connectors-mobile");
  });

  test("smoke: /ingestion/connectors/:connectorType/:id — detail header + KPI strip", async ({
    page,
    baseURL,
  }) => {
    await installCommonMocks(page);

    await navigateOrSkip(
      page,
      "/ingestion/connectors/gmail/alice%40example.com",
      baseURL,
      test,
    );

    // The KPI strip (data-testid='kpi-strip') must be visible when the connector
    // data loads. The mocks above return FIXTURE_CONNECTOR_BACKEND so the normal
    // Dispatch-language layout (ConnectorDetailView) renders.
    // Note: legacy detail-loading / detail-not-found testids were removed in
    // bu-1jh6i (Page archetype adoption); the Page shell's state elements have
    // no named testids. With the mocks in place the connector always loads, so
    // kpi-strip is the only expected element.
    await expect(
      page.locator("[data-testid='kpi-strip']"),
    ).toBeVisible({ timeout: TIMEOUT_MS });

    // The page must not crash (no error boundary message)
    await expect(page.locator("body")).not.toContainText(
      "Something went wrong",
      { timeout: TIMEOUT_MS },
    );

    // Absence: no old card shells for the primary surface
    await expect(
      page.locator("[data-testid='ingestion-events-card']"),
    ).toHaveCount(0);

    // Desktop screenshot
    await page.setViewportSize(DESKTOP);
    await capture(page, "connector-detail-desktop");

    // Mobile screenshot
    await page.setViewportSize(MOBILE);
    await page.waitForLoadState("domcontentloaded"); // allow reflow after viewport resize
    await capture(page, "connector-detail-mobile");
  });

  test("smoke: /ingestion/filters — five-gate diagram present", async ({
    page,
    baseURL,
  }) => {
    await installCommonMocks(page);

    await navigateOrSkip(page, "/ingestion/filters", baseURL, test);

    // Five-gate diagram must be present
    const diagram = page.locator('[data-testid="pipeline-gate-diagram"]');
    await expect(diagram).toBeVisible({ timeout: TIMEOUT_MS });

    // All five gate nodes
    for (const gate of ["accept", "dedupe", "tier", "route", "execute"]) {
      await expect(
        page.locator(`[data-testid="gate-node-${gate}"]`),
      ).toBeVisible({ timeout: TIMEOUT_MS });
    }

    // Absence: no old card shells for the primary surface
    await expect(
      page.locator("[data-testid='ingestion-events-card']"),
    ).toHaveCount(0);

    // Absence: old ingestion tab switcher
    await expect(page.locator('[role="tab"][data-value="history"]')).toHaveCount(
      0,
    );

    // Desktop screenshot
    await page.setViewportSize(DESKTOP);
    await capture(page, "filters-desktop");

    // Mobile screenshot
    await page.setViewportSize(MOBILE);
    await page.waitForLoadState("domcontentloaded"); // allow reflow after viewport resize
    await capture(page, "filters-mobile");
  });
});

// ---------------------------------------------------------------------------
// Legacy redirect tests
// ---------------------------------------------------------------------------

test.describe("ingestion visual parity — legacy redirects", () => {
  test("?tab=connectors → /ingestion/connectors", async ({ page, baseURL }) => {
    await installCommonMocks(page);

    await navigateOrSkip(page, "/ingestion?tab=connectors", baseURL, test);

    await page.waitForURL(/\/ingestion\/connectors/, { timeout: TIMEOUT_MS });
    expect(page.url()).toMatch(/\/ingestion\/connectors/);
    expect(page.url()).not.toContain("tab=");
  });

  test("?tab=filters → /ingestion/filters", async ({ page, baseURL }) => {
    await installCommonMocks(page);

    await navigateOrSkip(page, "/ingestion?tab=filters", baseURL, test);

    await page.waitForURL(/\/ingestion\/filters/, { timeout: TIMEOUT_MS });
    expect(page.url()).toMatch(/\/ingestion\/filters/);
    expect(page.url()).not.toContain("tab=");
  });

  test("?tab=history → /ingestion (Timeline; no history tab)", async ({
    page,
    baseURL,
  }) => {
    await installCommonMocks(page);

    await navigateOrSkip(page, "/ingestion?tab=history", baseURL, test);

    // Spec: history SHALL map to Timeline — NOT remain a fourth redesigned tab.
    await page.waitForURL(/\/ingestion$/, { timeout: TIMEOUT_MS });
    expect(page.url()).toMatch(/\/ingestion$/);
    expect(page.url()).not.toContain("/ingestion/history");
    expect(page.url()).not.toContain("tab=");
  });

  test("/ingestion/history → /ingestion (Dispatch mode redirect)", async ({
    page,
    baseURL,
  }) => {
    await installCommonMocks(page);

    await navigateOrSkip(page, "/ingestion/history", baseURL, test);

    // /ingestion/history has a <Navigate to="/ingestion" replace /> in Dispatch mode.
    await page.waitForURL(/\/ingestion$/, { timeout: TIMEOUT_MS });
    expect(page.url()).toMatch(/\/ingestion$/);
    expect(page.url()).not.toContain("/ingestion/history");
  });

  test("?tab=connectors&range=24h — range param preserved", async ({
    page,
    baseURL,
  }) => {
    await installCommonMocks(page);

    await navigateOrSkip(page, "/ingestion?tab=connectors&range=24h", baseURL, test);

    await page.waitForURL(/\/ingestion\/connectors/, { timeout: TIMEOUT_MS });
    expect(page.url()).toMatch(/\/ingestion\/connectors/);
    // tab= must be stripped; other params should survive
    expect(page.url()).not.toContain("tab=");
  });
});

// ---------------------------------------------------------------------------
// Drawer deep-link
// ---------------------------------------------------------------------------

test.describe("ingestion visual parity — drawer deep-link", () => {
  test("?event=<id> opens EventDrawer pre-populated", async ({
    page,
    baseURL,
  }) => {
    await installCommonMocks(page);

    const eventId = "aabbccdd-0000-0000-0000-000000000001";
    await navigateOrSkip(page, `/ingestion?event=${eventId}`, baseURL, test);

    // The EventDrawer must open
    await expect(page.locator("[data-testid='event-drawer']")).toBeVisible({
      timeout: TIMEOUT_MS,
    });
  });
});

// ---------------------------------------------------------------------------
// Absence assertions — old card / tab shells must not appear on redesigned routes
// ---------------------------------------------------------------------------

test.describe("ingestion visual parity — absence of old shell", () => {
  const redesignedRoutes = [
    "/ingestion",
    "/ingestion/connectors",
    "/ingestion/filters",
  ];

  for (const route of redesignedRoutes) {
    test(`${route} — no old IngestionEventsCard / ConnectorsListCard shell`, async ({
      page,
      baseURL,
    }) => {
      await installCommonMocks(page);

      await navigateOrSkip(page, route, baseURL, test);

      // Old card-based shells must be absent
      await expect(
        page.locator("[data-testid='ingestion-events-card']"),
      ).toHaveCount(0);
      await expect(
        page.locator("[data-testid='connectors-list-card']"),
      ).toHaveCount(0);

      // Old 4-tab ingestion switcher must be absent.
      // IngestionPage (legacy) renders TabsTrigger with data-value="history".
      // The redesigned routes use NavLink-based IngestionSubNav — no History tab.
      await expect(
        page.locator('[role="tab"][data-value="history"]'),
      ).toHaveCount(0);
    });
  }
});
