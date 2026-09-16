import { expect, test, type Locator, type Page, type Route } from "@playwright/test";

const SESSION_ID = "scroll-session-1";
const TIMELINE_EVENT_ID = "scroll-event-1";
const FACT_ID = "scroll-fact-1";
const ISSUE_KEY = "audit_error_group:scroll";
const SHELL_SCROLL_OFFSET = 640;
const SCROLLABLE_ROUTE_CONTENT_HEIGHT = 3600;
const SCROLL_FIXTURE_STYLE_ID = "shell-scroll-memory-route-fixture";
const SCROLL_RESTORATION_TOLERANCE = 4;

function envelope(data: unknown, meta: Record<string, unknown> = {}): string {
  return JSON.stringify({ data, meta });
}

function sessionSummary() {
  return {
    id: SESSION_ID,
    butler: "home",
    prompt: "Scroll memory fixture",
    trigger_source: "manual",
    request_id: "scroll-request-1",
    success: false,
    started_at: new Date(Date.now() - 60_000).toISOString(),
    completed_at: new Date(Date.now() - 30_000).toISOString(),
    duration_ms: 30_000,
    input_tokens: 10,
    output_tokens: 20,
    cancelled_by_owner: false,
    model: "fixture-model",
    complexity: null,
    purpose_lane: "standard",
  };
}

async function mockScrollMemoryApis(page: Page): Promise<void> {
  await page.route("**/api/**", async (route: Route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.startsWith("/api/auth/owner/")) {
      await route.fallback();
      return;
    }

    if (pathname === "/api/sessions") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: envelope([sessionSummary()], {
          has_more: false,
          next_cursor: null,
          sources_degraded: [],
        }),
      });
      return;
    }
    if (pathname === "/api/sessions/aggregate") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: envelope({
          total: 1,
          success_count: 0,
          failed_count: 1,
          running_count: 0,
          success_rate: 0,
          input_tokens: 10,
          output_tokens: 20,
          by_butler: [{ butler: "home", count: 1 }],
          by_trigger_source: [],
          trigger_breakdown_degraded_sources: [],
        }),
      });
      return;
    }
    if (pathname === `/api/sessions/${SESSION_ID}`) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: envelope({
          ...sessionSummary(),
          butler: "home",
          result: "fixture result",
          tool_calls: [],
          trace_id: null,
          cost: null,
          error: null,
          parent_session_id: null,
          resolution_source: null,
          linked_message: null,
          process_log: null,
        }),
      });
      return;
    }

    if (pathname === "/api/timeline") {
      const timestamp = new Date(Date.now() - 30_000).toISOString();
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: envelope(
          [
            {
              id: TIMELINE_EVENT_ID,
              type: "notification",
              butler: "home",
              timestamp,
              summary: "Scroll memory timeline fixture",
              is_heartbeat: false,
              data: {},
            },
          ],
          {
            cursor: null,
            has_more: false,
            heartbeat_rollup: { ticks: 0, butlers: 0, failed: 0 },
            degraded_sources: [],
            degraded_butlers: [],
          },
        ),
      });
      return;
    }
    if (pathname === "/api/timeline/histogram") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: envelope([], { degraded_sources: [], degraded_butlers: [] }),
      });
      return;
    }
    if (pathname === "/api/timeline/attention") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: envelope([], { degraded_sources: [], degraded_butlers: [] }),
      });
      return;
    }

    if (pathname === "/api/memory/stats") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: envelope({
          total_episodes: 0,
          unconsolidated_episodes: 0,
          total_facts: 1,
          active_facts: 1,
          fading_facts: 0,
          total_rules: 0,
          candidate_rules: 0,
          established_rules: 0,
          proven_rules: 0,
          anti_pattern_rules: 0,
          retired_rules: 0,
          last_consolidation_at: null,
          last_consolidation_facts_produced: null,
          dead_letter_episodes: 0,
          expired_retained_episodes: null,
          retention_eligible_episodes: null,
          expired_retained_ratio: null,
        }),
      });
      return;
    }
    if (pathname === "/api/memory/facts") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: envelope([
          {
            id: FACT_ID,
            subject: "owner",
            predicate: "prefers",
            content: "Scroll memory",
            importance: 5,
            confidence: 0.9,
            decay_rate: 0,
            permanence: "stable",
            source_butler: "memory",
            source_episode_id: null,
            source_episode_status: null,
            session_id: null,
            supersedes_id: null,
            superseded_by: null,
            entity_id: null,
            entity_name: null,
            object_entity_id: null,
            object_entity_name: null,
            validity: "active",
            scope: "owner",
            reference_count: 0,
            created_at: new Date().toISOString(),
            last_referenced_at: null,
            last_confirmed_at: null,
            tags: [],
            metadata: {},
          },
        ], { has_more: false, total: 1 }),
      });
      return;
    }
    if (pathname === `/api/memory/facts/${FACT_ID}`) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: envelope({ id: FACT_ID, subject: "owner", predicate: "prefers", content: "Scroll memory" }),
      });
      return;
    }

    if (pathname === "/api/issues") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: envelope([
          {
            severity: "warning",
            type: ISSUE_KEY,
            butler: "home",
            description: "Scroll memory issue fixture",
            link: null,
            error_message: "fixture",
            occurrences: 1,
            first_seen_at: new Date(Date.now() - 60_000).toISOString(),
            last_seen_at: new Date().toISOString(),
            issue_key: ISSUE_KEY,
            dismissed: false,
          },
        ]),
      });
      return;
    }
    if (pathname === `/api/issues/${encodeURIComponent(ISSUE_KEY)}/occurrences`) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: envelope([
          {
            id: 1,
            ts: new Date().toISOString(),
            actor: "home",
            action: "fixture",
            request_id: "scroll-request-1",
          },
        ], { total: 1, has_more: false }),
      });
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: envelope([]),
    });
  });
}

async function setShellScroll(page: Page): Promise<number> {
  const metrics = await page.locator("#main-content").evaluate(
    (main, { fixtureStyleId, routeContentHeight, scrollOffset }) => {
      const element = main as HTMLElement;
      if (!document.getElementById(fixtureStyleId)) {
        // The route root, not the persistent shell `<main>`, supplies overflow.
        // That keeps this regression test on the production scroll boundary.
        const fixtureStyle = document.createElement("style");
        fixtureStyle.id = fixtureStyleId;
        fixtureStyle.textContent = `#main-content > * { min-height: ${routeContentHeight}px !important; }`;
        document.head.append(fixtureStyle);
      }

      const routeContent = element.firstElementChild;
      if (!routeContent) throw new Error("Expected routed content inside #main-content");

      element.scrollTop = scrollOffset;
      element.dispatchEvent(new Event("scroll"));
      return {
        clientHeight: element.clientHeight,
        routeContentHeight: routeContent.getBoundingClientRect().height,
        scrollHeight: element.scrollHeight,
        scrollTop: element.scrollTop,
      };
    },
    {
      fixtureStyleId: SCROLL_FIXTURE_STYLE_ID,
      routeContentHeight: SCROLLABLE_ROUTE_CONTENT_HEIGHT,
      scrollOffset: SHELL_SCROLL_OFFSET,
    },
  );

  expect(metrics.routeContentHeight).toBeGreaterThan(metrics.clientHeight);
  expect(metrics.scrollHeight).toBeGreaterThan(metrics.clientHeight);
  expect(metrics.scrollTop).toBe(SHELL_SCROLL_OFFSET);
  return metrics.scrollTop;
}

async function expectShellScroll(page: Page, expected: number): Promise<void> {
  expect(expected).toBeGreaterThan(0);
  // Scroll anchoring can adjust a restored offset slightly as routed data settles.
  await expect.poll(
    () => page.locator("#main-content").evaluate((main) => (main as HTMLElement).scrollTop),
  ).toBeGreaterThanOrEqual(expected - SCROLL_RESTORATION_TOLERANCE);
  await expect.poll(
    () => page.locator("#main-content").evaluate((main) => (main as HTMLElement).scrollTop),
  ).toBeLessThanOrEqual(expected + SCROLL_RESTORATION_TOLERANCE);
}

async function expectShellScrollAtTop(page: Page): Promise<void> {
  await expect.poll(
    () => page.locator("#main-content").evaluate((main) => (main as HTMLElement).scrollTop),
  ).toBeLessThanOrEqual(4);
}

async function activateWithoutScrollingShell(target: Locator): Promise<void> {
  // Playwright's pointer action and HTMLElement.click() both focus an off-screen
  // target before dispatching it. React receives this bubbling event normally,
  // without altering the shell offset under test.
  await target.evaluate((element) => {
    element.dispatchEvent(new MouseEvent("click", { bubbles: true, button: 0, cancelable: true }));
  });
}

test.describe("shell scroll memory at detail boundaries", () => {
  test.beforeEach(async ({ page }) => {
    await mockScrollMemoryApis(page);
  });

  test("Sessions restores the list offset after opening a session detail", async ({ page }) => {
    await page.goto("/sessions", { waitUntil: "networkidle" });
    await expect(page.getByTestId("session-row").first()).toBeVisible();
    const detailLink = page.getByRole("link", { name: /nearest running session/ });
    await expect(detailLink).toBeVisible();
    const offset = await setShellScroll(page);
    await expectShellScroll(page, offset);
    await activateWithoutScrollingShell(detailLink);
    await expect(page).toHaveURL(/\/sessions\/scroll-session-1$/);
    await expectShellScrollAtTop(page);
    await page.goBack();
    await expect(page).toHaveURL(/\/sessions$/);
    await expectShellScroll(page, offset);
  });

  test("Timeline restores the ledger offset after opening an event detail", async ({ page }) => {
    await page.goto("/timeline", { waitUntil: "networkidle" });
    const disclosure = page.locator(`[data-timeline-disclosure-id="event:${TIMELINE_EVENT_ID}"]`);
    await expect(disclosure).toBeVisible();
    const offset = await setShellScroll(page);
    await expectShellScroll(page, offset);
    await activateWithoutScrollingShell(disclosure);
    await expect(page).toHaveURL(/\/timeline\?event=scroll-event-1/);
    await expectShellScrollAtTop(page);
    await page.goBack();
    await expect(page).toHaveURL(/\/timeline$/);
    await expectShellScroll(page, offset);
  });

  test("Memory restores the register offset after opening a fact detail", async ({ page }) => {
    await page.goto("/memory", { waitUntil: "networkidle" });
    const factLink = page.getByRole("link", { name: /Fact: owner prefers/ });
    await expect(factLink).toBeVisible();
    const offset = await setShellScroll(page);
    await expectShellScroll(page, offset);
    await activateWithoutScrollingShell(factLink);
    await expect(page).toHaveURL(/\/memory\/facts\/scroll-fact-1/);
    await expectShellScrollAtTop(page);
    await page.goBack();
    await expect(page).toHaveURL(/\/memory$/);
    await expectShellScroll(page, offset);
  });

  test("Issues restores the feed offset after opening an occurrence's session", async ({ page }) => {
    await page.goto("/issues", { waitUntil: "networkidle" });
    await expect(page.getByTestId("issue-row")).toBeVisible();
    await page.locator('[data-testid="issue-row"] [role="button"]').click();
    const sessionLink = page.getByRole("link", { name: "Session →" });
    await expect(sessionLink).toBeVisible();
    const offset = await setShellScroll(page);
    await expectShellScroll(page, offset);
    await activateWithoutScrollingShell(sessionLink);
    await expect(page).toHaveURL(/\/sessions\?request=scroll-request-1/);
    await expectShellScrollAtTop(page);
    await page.goBack();
    await expect(page).toHaveURL(/\/issues$/);
    await expectShellScroll(page, offset);
  });
});
