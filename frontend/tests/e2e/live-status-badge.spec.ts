/**
 * Playwright e2e spec — LiveStatusBadge honesty contract at the browser layer
 * (bu-ii0o7, discovered from bu-jad4j.5 / PR #3115).
 *
 * The badge derives its state from real event freshness:
 *   isDown wins → "Down"; else undefined → "checking"; null → "Idle";
 *   string → freshness (received within 60 s → "Live", else "Idle").
 * See frontend/src/components/ui/live-status-badge.tsx (deriveStatus).
 *
 * The dead-API-impersonates-idle defect class (bu-qvnce.2 on /timeline,
 * PR #3115 on /ingestion) was only locked at the vitest layer. This spec
 * asserts the three badge states through a real browser, for BOTH of the
 * badge's homes:
 *   - /timeline          (fleet chronicle; isDown ← useTimelineLedger)
 *   - /ingestion         (IngestionTimelinePage; isDown ← TimelineTab head poll)
 *
 * The load-bearing case is "Down": a 500 on the live-feed endpoint MUST render
 * the distinct red "Down" pill, never the muted "Idle" dot a genuinely quiet
 * pipeline gets — that impersonation is the exact defect this locks.
 *
 * Determinism:
 * - All data comes from route interception (no live backend).
 * - Playwright matches routes LIFO (last registered = first checked): the
 *   catch-all is registered FIRST, the specific live-feed route LAST.
 * - "Live" freshness is injected relative to now via the mock payload (a fixed
 *   recent-vs-stale timestamp), so there is no wall-clock flake — the freshness
 *   window is 60 s and each test completes well inside it.
 */

import { test, expect, type Page } from "@playwright/test";
import { makeIngestionEventSummary } from "./fixtures/ingestion.ts";

const TIMEOUT_MS = 10_000;

// A timestamp a few seconds old → inside the 60 s freshness window → "Live".
// Computed relative to now so the test never depends on the wall clock being
// near a hardcoded date.
function recentIso(): string {
  return new Date(Date.now() - 3_000).toISOString();
}

// A timestamp far in the past → outside the freshness window → "Idle". A fixed
// long-ago date is always stale regardless of when the suite runs.
const STALE_ISO = "2026-01-01T00:00:00Z";

const BADGE_LIVE = "[data-testid='live-status-badge-live']";
const BADGE_IDLE = "[data-testid='live-status-badge-idle']";
const BADGE_DOWN = "[data-testid='live-status-badge-down']";

/**
 * The global Sidebar reads approvals metrics on both badge routes. Its
 * availability metadata is part of the response contract, so a generic list
 * catch-all cannot safely stand in for this healthy, empty metric.
 */
async function mockHealthyApprovalMetrics(page: Page) {
  await page.route("**/api/approvals/metrics", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: { total_pending: 0 },
        meta: {},
      }),
    });
  });
}

// ---------------------------------------------------------------------------
// /ingestion (IngestionTimelinePage) — badge driven by the events head poll
// ---------------------------------------------------------------------------

/**
 * Install the ingestion route intercepts the Timeline page needs, with the
 * events-list response parameterized so each test can drive a specific badge
 * state. 'events' is either a fixture array (200) or the number 500 (server
 * error → the head poll's isError → "Down").
 */
async function mockIngestion(page: Page, events: unknown[] | 500) {
  // Catch-all FIRST (lowest LIFO precedence) — absorbs sidebar/histogram/rollup
  // requests (/api/butlers, /api/ingestion/events/histogram, etc.).
  await page.route("**/api/**", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ data: [] }),
    });
  });

  await mockHealthyApprovalMetrics(page);

  // Per-event sub-resources — more specific than the events list, registered
  // before it (the star glob does not cross a slash, so /events?... below
  // never swallows /events/<id>/sessions).
  await page.route("**/api/ingestion/events/*/sender-contact", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ data: { resolved: false, name: null, raw: null } }),
    });
  });
  await page.route("**/api/ingestion/events/*/sessions", (route) => {
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: [] }) });
  });

  // Events list — registered LAST so it wins LIFO. 500 for the "Down" case.
  await page.route("**/api/ingestion/events*", (route) => {
    if (events === 500) {
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ error: { code: "internal", message: "boom" } }),
      });
      return;
    }
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ data: events, meta: { next_cursor: null, has_more: false } }),
    });
  });
}

test.describe("LiveStatusBadge — /ingestion (events head poll)", () => {
  test("recent event → Live", async ({ page }) => {
    await mockIngestion(page, [
      makeIngestionEventSummary({
        id: "aabbccdd-0000-0000-0000-000000000001",
        received_at: recentIso(),
      }),
    ]);

    await page.goto("/ingestion", { waitUntil: "networkidle" });

    await expect(page.locator(BADGE_LIVE)).toBeVisible({ timeout: TIMEOUT_MS });
    await expect(page.locator(BADGE_DOWN)).toHaveCount(0);
  });

  test("stale event → Idle", async ({ page }) => {
    await mockIngestion(page, [
      makeIngestionEventSummary({
        id: "aabbccdd-0000-0000-0000-000000000001",
        received_at: STALE_ISO,
      }),
    ]);

    await page.goto("/ingestion", { waitUntil: "networkidle" });

    await expect(page.locator(BADGE_IDLE)).toBeVisible({ timeout: TIMEOUT_MS });
    await expect(page.locator(BADGE_DOWN)).toHaveCount(0);
  });

  test("events endpoint 500 → Down (not Idle) — honesty contract", async ({ page }) => {
    await mockIngestion(page, 500);

    await page.goto("/ingestion", { waitUntil: "networkidle" });

    // The load-bearing assertion: a dead API renders the distinct "Down" pill,
    // NOT the muted "Idle" dot a genuinely quiet pipeline gets.
    await expect(page.locator(BADGE_DOWN)).toBeVisible({ timeout: TIMEOUT_MS });
    await expect(page.locator(BADGE_IDLE)).toHaveCount(0);
    await expect(page.locator(BADGE_LIVE)).toHaveCount(0);
  });
});

// ---------------------------------------------------------------------------
// /timeline (TimelinePage) — badge driven by useTimelineLedger's head poll
// ---------------------------------------------------------------------------

function timelineEvent(timestamp: string) {
  return {
    id: "11111111-0000-0000-0000-000000000001",
    type: "session",
    butler: "general",
    timestamp,
    summary: "A fleet session",
    is_heartbeat: false,
    data: {},
  };
}

const TIMELINE_META = {
  cursor: null,
  has_more: false,
  heartbeat_rollup: { ticks: 0, butlers: 0, failed: 0 },
  degraded_sources: [],
};

/**
 * Mock GET /api/timeline (the head poll behind useTimelineLedger). `events` is
 * a fixture array (200) or the number 500 (server error → isLiveFeedDown →
 * "Down"). The timeline-route glob does not cross a slash, so it never
 * intercepts /api/timeline/saved-views — that falls to the catch-all.
 */
async function mockTimeline(page: Page, events: unknown[] | 500) {
  await page.route("**/api/**", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ data: [] }),
    });
  });

  await mockHealthyApprovalMetrics(page);

  await page.route("**/api/timeline*", (route) => {
    if (events === 500) {
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ error: { code: "internal", message: "boom" } }),
      });
      return;
    }
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ data: events, meta: TIMELINE_META }),
    });
  });
}

test.describe("LiveStatusBadge — /timeline (fleet chronicle head poll)", () => {
  test("recent event → Live", async ({ page }) => {
    await mockTimeline(page, [timelineEvent(recentIso())]);

    await page.goto("/timeline", { waitUntil: "networkidle" });

    await expect(page.locator(BADGE_LIVE)).toBeVisible({ timeout: TIMEOUT_MS });
    await expect(page.locator(BADGE_DOWN)).toHaveCount(0);
  });

  test("stale event → Idle", async ({ page }) => {
    await mockTimeline(page, [timelineEvent(STALE_ISO)]);

    await page.goto("/timeline", { waitUntil: "networkidle" });

    await expect(page.locator(BADGE_IDLE)).toBeVisible({ timeout: TIMEOUT_MS });
    await expect(page.locator(BADGE_DOWN)).toHaveCount(0);
  });

  test("timeline endpoint 500 → Down (not Idle) — honesty contract", async ({ page }) => {
    await mockTimeline(page, 500);

    await page.goto("/timeline", { waitUntil: "networkidle" });

    await expect(page.locator(BADGE_DOWN)).toBeVisible({ timeout: TIMEOUT_MS });
    await expect(page.locator(BADGE_IDLE)).toHaveCount(0);
    await expect(page.locator(BADGE_LIVE)).toHaveCount(0);
  });
});
