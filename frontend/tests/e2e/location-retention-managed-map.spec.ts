import { expect, test, type Route } from "@playwright/test";

import type {
  ChroniclerPointEvent,
  ChroniclesBriefing,
  LocationRetentionStatus,
} from "../../src/api/types";

const DAY_MS = 86_400_000;
// Local blank raster: the real MapLibre canvas never contacts a tile provider.
const TILE = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8S8AAAAASUVORK5CYII=",
  "base64",
);

function point(since: string, id: string, offset: number): ChroniclerPointEvent {
  const time = new Date(Date.parse(since) + offset).toISOString();
  return {
    id, source_name: "owntracks", source_ref: id, event_type: "location",
    occurred_at: time, canonical_occurred_at: time, precision: "point",
    title: null, canonical_title: null, payload: { lat: 1.31415926, lon: 103.81234567 },
    privacy: "normal", canonical_privacy: "normal", retention_days: 30,
    tombstone_at: null, corrected_at: null, correction_note: null,
    created_at: time, updated_at: time,
  };
}

function briefing(date: string): ChroniclesBriefing {
  return {
    date, state_class: "quiet", headline: "Synthetic covered day",
    voice_paragraph: "Synthetic coverage for this browser control.", voice_source: "templated",
    kpi: {
      hours_by_top_lanes: [], longest_episode_minutes: 0, longest_episode_title: null,
      longest_gap_minutes: 0, sleep_minutes: 0, streaks: { sleep: 0, exercise: 0 },
    },
    attention_items: [], recent_days: [], earliest_date: "2000-01-01",
  };
}

function policy(revision: number): LocationRetentionStatus {
  return {
    privacy_revision: String(revision), days: revision === 0 ? 30 : 2,
    version: revision + 1, updated_at: "2026-01-01T00:00:00Z",
    precision_after_forgetting_m: 150, widening_restores_forgotten_points: false,
    prepared_decisions_may_finish: true, status: "incomplete", reason_code: "holder_pending",
    receipt: null, completion_at: null, blocked_count: 0, holder_pending_count: 1,
  };
}

// REQ-location-retention-007: actual managed browser/MapLibre lifetime only.
// Synthetic API transitions do not attest server purge or a remote recipient.
test("shortening fences old current/archive canvases and admits a fresh equal-coordinate generation", async ({ page }) => {
  const current = new Date(Date.now() - DAY_MS).toISOString().slice(0, 10);
  const archive = new Date(Date.parse(`${current}T00:00:00Z`) - DAY_MS).toISOString().slice(0, 10);
  const requests: Array<{ since: string; revision: number }> = [];
  const held: Route[] = [];
  const obsolete: Route[] = [];
  let holdOldCurrent = false;
  let revision = 0;
  let holdFresh = true;
  const events = (since: string) => {
    const data = revision === 0
      ? [point(since, "synthetic-old-start", 30 * 60_000), point(since, "synthetic-old-end", DAY_MS - 30 * 60_000)]
      : since.startsWith(current)
        ? [point(since, "synthetic-fresh-equal-coordinate", DAY_MS - 30 * 60_000)] : [];
    return { data, meta: { total: data.length, offset: 0, limit: 500, has_more: false } };
  };
  await page.route("https://*.basemaps.cartocdn.com/**", route =>
    route.fulfill({ status: 200, contentType: "image/png", body: TILE }));
  await page.route("**/api/**", async route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith("/api/auth/owner/")) return route.fallback();
    if (url.pathname === "/api/settings/general") {
      return route.fulfill({ json: { data: { timezone: "UTC" } } });
    }
    if (url.pathname === "/api/chronicler/briefing") {
      return route.fulfill({ json: briefing(url.searchParams.get("date") ?? current) });
    }
    if (url.pathname === "/api/chronicler/location-retention") {
      if (route.request().method() === "PUT") {
        expect(route.request().postDataJSON()).toEqual({ days: 2, version: 1 });
        revision = 1;
      }
      return route.fulfill({ json: { data: policy(revision) } });
    }
    if (url.pathname === "/api/chronicler/events") {
      const since = url.searchParams.get("since");
      expect(since).not.toBeNull();
      requests.push({ since: since!, revision });
      if (revision === 0 && holdOldCurrent && since!.startsWith(current)) {
        obsolete.push(route); return;
      }
      if (revision === 1 && holdFresh) { held.push(route); return; }
      return route.fulfill({ json: events(since!) });
    }
    if (url.pathname === "/api/chronicler/episodes") {
      return route.fulfill({ json: { data: [], meta: { total: 0, offset: 0, limit: 500, has_more: false } } });
    }
    return route.fulfill({ status: 404, json: { error: { code: "fixture_unavailable", message: "Unconfigured browser fixture route" } } });
  });

  await page.goto(`/chronicles?date=${current}`, { waitUntil: "domcontentloaded" });
  const canvas = page.getByTestId("map-container").locator("canvas.maplibregl-canvas");
  await expect(canvas).toBeVisible();
  await page.getByRole("button", { name: "Previous day", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`date=${archive}`));
  await expect(canvas).toBeVisible();
  holdOldCurrent = true;
  await page.getByRole("button", { name: "Next day", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`date=${current}`));
  await expect(canvas).toBeVisible();
  expect(requests.some(request => request.since.startsWith(archive) && request.revision === 0)).toBe(true);
  await expect.poll(() => obsolete.length).toBeGreaterThan(0);
  const oldCanvas = await canvas.elementHandle();
  expect(oldCanvas).not.toBeNull();
  expect(await oldCanvas!.evaluate(element => {
    const surface = element as HTMLCanvasElement;
    const gl = surface.getContext("webgl2") ?? surface.getContext("webgl");
    return gl !== null && !gl.isContextLost();
  })).toBe(true);

  await page.getByLabel("Keep exact trail for").fill("2");
  await page.getByRole("button", { name: "Save retention", exact: true }).click();
  await page.getByRole("button", { name: "Confirm shorter retention", exact: true }).click();
  await expect.poll(() => held.length).toBeGreaterThan(0);
  await expect(canvas).toHaveCount(0);
  await expect.poll(() => obsolete.every(route => route.request().failure() !== null)).toBe(true);
  expect(await oldCanvas!.evaluate(element => element.isConnected)).toBe(false);
  await expect.poll(() => oldCanvas!.evaluate(element => {
    const surface = element as HTMLCanvasElement;
    const gl = surface.getContext("webgl2") ?? surface.getContext("webgl");
    return gl !== null && gl.isContextLost();
  })).toBe(true);

  holdFresh = false;
  for (const route of held) {
    // Actual AbortSignal cancellation is terminal for that obsolete request;
    // only surviving current requests get the fresh fixture response.
    if (route.request().failure() !== null) continue;
    const since = new URL(route.request().url()).searchParams.get("since")!;
    await route.fulfill({ json: events(since) });
  }
  await expect(canvas).toBeVisible();
  expect(await oldCanvas!.evaluate(element => element.isConnected)).toBe(false);
  expect(await canvas.evaluate(element => {
    const surface = element as HTMLCanvasElement;
    const gl = surface.getContext("webgl2") ?? surface.getContext("webgl");
    return gl !== null && !gl.isContextLost();
  })).toBe(true);

  await page.getByRole("button", { name: "Previous day", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`date=${archive}`));
  await expect(page.getByTestId("map-empty")).toBeVisible();
  await expect(canvas).toHaveCount(0);
  expect(requests.some(request => request.since.startsWith(archive) && request.revision === 1)).toBe(true);
  await expect(page.getByText("Deletion is not confirmed.", { exact: false })).toBeVisible();
});
