import { expect, test, type Page } from "@playwright/test";

const HOUR_MS = 60 * 60 * 1_000;
const MINUTE_MS = 60 * 1_000;

function completeHour() {
  const until = Math.floor(Date.now() / HOUR_MS) * HOUR_MS;
  return { since: until - HOUR_MS, until };
}

function timelineEvent(id: string, timestamp: string) {
  return {
    id,
    type: "notification",
    butler: "home",
    timestamp,
    summary: `notification ${id}`,
    is_heartbeat: false,
    data: { status: "failed" },
  };
}

async function mockTimeline(page: Page) {
  const window = completeHour();
  const rows = [
    timelineEvent("row-1", new Date(window.until - MINUTE_MS).toISOString()),
    timelineEvent("attention-1", new Date(window.until - 2 * MINUTE_MS).toISOString()),
  ];
  const buckets = Array.from({ length: 60 }, (_, index) => ({
    start: new Date(window.since + index * MINUTE_MS).toISOString(),
    end: new Date(window.since + (index + 1) * MINUTE_MS).toISOString(),
    count: index === 59 ? 2 : 0,
  }));

  await page.route("**/api/**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: [] }) }),
  );
  await page.route(/\/api\/timeline\/histogram(?:\?.*)?$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: buckets,
        meta: {
          since: new Date(window.since).toISOString(),
          until: new Date(window.until).toISOString(),
          bucket_seconds: 60,
          availability: "partial",
          expected_sources: 2,
          healthy_sources: 1,
          degraded_sources: ["notifications"],
          degraded_butlers: [],
        },
      }),
    }),
  );
  await page.route(/\/api\/timeline\/attention(?:\?.*)?$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: [
          {
            id: "attention-1",
            kind: "notification",
            butler: "home",
            timestamp: rows[1].timestamp,
          },
        ],
        meta: {
          since: new Date(window.until - 24 * HOUR_MS).toISOString(),
          until: new Date(window.until).toISOString(),
          failed_sessions: 0,
          failed_notifications: 1,
          total: 1,
          has_more: false,
          availability: "partial",
          expected_sources: 2,
          healthy_sources: 1,
          degraded_sources: ["notifications"],
          degraded_butlers: [],
        },
      }),
    }),
  );
  await page.route(/\/api\/timeline(?:\?.*)?$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: rows,
        meta: {
          cursor: null,
          has_more: false,
          heartbeat_rollup: { ticks: 0, butlers: 0, failed: 0 },
          degraded_sources: ["notifications"],
          degraded_butlers: [],
        },
      }),
    }),
  );

  return window;
}

test.use({ reducedMotion: "reduce" });

test("first j survives composed Timeline URL and drawer focus transitions", async ({ page }) => {
  const window = await mockTimeline(page);
  const initial = new URLSearchParams({
    since: new Date(window.since).toISOString(),
    until: new Date(window.until).toISOString(),
    bucket_since: new Date(window.until - MINUTE_MS).toISOString(),
    bucket_until: new Date(window.until).toISOString(),
  });
  await page.goto(`/timeline?${initial.toString()}`, { waitUntil: "networkidle" });

  await expect(page.getByTestId("timeline-degraded-banner")).toBeVisible();
  await page.getByTestId("timeline-attention-item-notification").click();
  await expect(page).not.toHaveURL(/bucket_since=/);
  await expect(page.getByTestId("timeline-event-drawer")).toBeVisible();

  await page.getByTestId("timeline-density-bucket").last().evaluate((bucket) => {
    (bucket as HTMLButtonElement).click();
    const key = new KeyboardEvent("keydown", { key: "j", bubbles: true, cancelable: true });
    document.activeElement?.dispatchEvent(key);
  });
  await expect(page.getByTestId("timeline-event-drawer")).toHaveCount(0);

  const firstDisclosure = page.locator('[data-timeline-disclosure-id="event:row-1"]');
  await expect(firstDisclosure).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("timeline-event-drawer")).toBeVisible();
  await expect(page.getByText("Event detail: home, notification", { exact: true })).toBeFocused();

  await page.keyboard.press("Escape");
  await expect(page.getByTestId("timeline-event-drawer")).toHaveCount(0);
  await expect(firstDisclosure).toBeFocused();

  await page.goBack();
  await expect(page).toHaveURL(/event=row-1/);
  await expect(page.getByTestId("timeline-event-drawer")).toBeVisible();
  await expect(page.getByText("Event detail: home, notification", { exact: true })).toBeFocused();
});
