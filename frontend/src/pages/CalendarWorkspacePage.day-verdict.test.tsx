// Spec: REQ-module-calendar-036
// @vitest-environment jsdom

import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router";
import CalendarWorkspacePage from "@/pages/CalendarWorkspacePage";

// Real page, hooks and apiFetch; only the SSE transport is dormant.
vi.mock("@/lib/event-bus", () => ({
  useEventBus: () => ({ health: "healthy", status: "open", subscribe: () => () => {} }),
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function Probe() {
  const location = useLocation();
  return <div data-testid="location">{location.search}</div>;
}

const NEXT_START = new Date(Date.now() + 3 * 3600_000);
const NEXT_END = new Date(NEXT_START.getTime() + 3600_000);
const DAY = NEXT_START.toISOString().slice(0, 10);

const entry = {
  entry_id: "entry-next",
  title: "Quarterly review",
  start_at: NEXT_START.toISOString(),
  end_at: NEXT_END.toISOString(),
  timezone: "UTC",
  view: "user",
  source_type: "provider_event",
  source_key: "google:primary",
  event_id: "event-next",
  all_day: false,
  calendar_id: "primary",
  provider_event_id: "provider-next",
  butler_name: "general",
  schedule_id: null,
  reminder_id: null,
  rrule: null,
  cron: null,
  until_at: null,
  status: "active",
  sync_state: "fresh",
  editable: false,
  metadata: {},
  linked_people: [],
};

const verdict = {
  available: true,
  date: DAY,
  zone: "UTC",
  relative: "today",
  freshness: { state: "current", as_of: new Date().toISOString(), sources_unreadable: [], sources_total: 1 },
  current: null,
  next: {
    ref: entry.entry_id,
    start: entry.start_at,
    end: entry.end_at,
    title: entry.title,
    people_count: 0,
    door: `/calendar?entry=${entry.entry_id}`,
  },
  committed_minutes: 60,
  committed_count: 1,
  tentative_count: 0,
  free_window: null,
  empty: false,
};

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(status === 200 ? { data } : { error: { code: "boom", message: "boom" } }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch(options: { briefing500?: boolean } = {}) {
  const fetch = vi.fn(async (input: string) => {
    const path = new URL(input, "http://localhost").pathname;
    if (path.includes("/entries/")) return json(entry);
    if (path.endsWith("/day-verdict")) return json(verdict);
    if (path.endsWith("/day-briefing")) {
      return options.briefing500
        ? json(null, 500)
        : json({ has_entries: false, has_domain_context: false, entries: [], groups: [] });
    }
    if (path.endsWith("/search")) return json({ entries: [], available: true });
    if (path.endsWith("/invitations")) {
      return json({
        entries: [],
        issues_available: true,
        conflicts_available: true,
        sources_degraded: [],
        has_more: false,
        next_cursor: null,
      });
    }
    if (path.endsWith("/conflicts")) {
      return json({ issues: [], issues_available: true, scan_window: { start: DAY, end: DAY } });
    }
    if (path.endsWith("/meta")) {
      return json({
        connected_sources: [],
        lanes: [],
        writable_calendars: [],
        default_timezone: "UTC",
        sources_available: true,
      });
    }
    if (path.endsWith("/accounts")) return json({ accounts: [], health_available: true });
    if (path.endsWith("/duplicates")) {
      return json({ clusters: [], available: true, rules: { match_strategy: "balanced", noisy_threshold: 2 } });
    }
    return json({
      entries: [entry],
      source_freshness: [],
      lanes: [],
      entries_source_available: true,
      sources_available: true,
      has_more: false,
      next_cursor: null,
      total: 1,
      offset: 0,
      limit: 50,
    });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

function mount(search: string) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/calendar?${search}`]}>
        <CalendarWorkspacePage />
        <Probe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

it("a day-briefing HTTP 500 reaches the card as a degraded note, never a clear day", async () => {
  stubFetch({ briefing500: true });
  mount(`view=user&range=week&anchor=${DAY}&overlays=1`);

  const card = await screen.findByTestId("day-briefing-card");
  const alert = await within(card).findByRole("alert");

  expect(alert.textContent).toContain("Couldn't load this day briefing");
  expect(within(card).queryByTestId("day-briefing-clear")).toBeNull();
  expect(card.textContent).not.toContain("Nothing scheduled");
  expect(card.textContent).not.toContain("No domain context");
});

it("clicking the opener's next clause focuses that entry in the workspace", async () => {
  const fetch = stubFetch();
  const { container } = mount(`view=user&range=week&anchor=${DAY}`);

  const next = await screen.findByRole("link", { name: /Next Quarterly review at/ });
  fireEvent.click(next);

  await waitFor(() =>
    expect(container.querySelector('[data-testid="entry-detail-panel"]')).not.toBeNull(),
  );
  expect(
    fetch.mock.calls.some(([url]) => String(url).includes("/entries/entry-next")),
  ).toBe(true);
  expect(container.querySelector('[data-testid="entry-detail-panel"]')?.textContent).toContain(
    "Quarterly review",
  );
  await waitFor(() => {
    const search = new URLSearchParams(screen.getByTestId("location").textContent ?? "");
    expect(search.get("entry")).toBeNull();
    expect(search.get("range")).toBe("day");
  });
});
