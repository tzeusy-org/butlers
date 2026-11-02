// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// ButlerHeartbeatTile tests (bu-86c4c.17)
//
// Board activity retains its own interaction contract. Observation/policy/
// compatibility/eligibility now come from the separate existing heartbeat
// query. Old tone/label expectations survive through the real HTTP matrix.
//
// Coverage:
//   - Loading state: skeleton rendered, no butler rows
//   - Error state: error message rendered
//   - Empty butler list: "No butlers registered" message
//   - Running/idle rows: name, relative time, active session badge, healthy dot
//   - Overdue rows: amber dot, no active badge
//   - Offline/quarantined rows: red dot
//   - schemaUnreachable per-butler: "unreachable" badge, tile does not crash
//   - Sorting: most-recently-seen butler appears first (nulls last)
// ---------------------------------------------------------------------------

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render as renderDom, screen, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useButlerHeartbeats } from "@/hooks/use-system";
import type { ButlerHeartbeat } from "@/api/types";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import { ButlerHeartbeatTile } from "./ButlerHeartbeatTile";
import { useButlerStatusBoard } from "@/hooks/use-butler-status-board";
import type { StatusBoardRow, StatusBoardAggregates } from "@/hooks/use-butler-status-board";


vi.mock("@/hooks/use-system", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/use-system")>();
  return { ...actual, useButlerHeartbeats: vi.fn() };
});

function heartbeatFact(name: string, overrides: Partial<ButlerHeartbeat> = {}): ButlerHeartbeat {
  return { name, last_heartbeat_at: "2026-05-03T10:00:00Z", last_probe_at: "2026-05-03T10:01:00Z",
    last_session_at: null, active_session_count: 0, heartbeat_age_seconds: 60,
    observation_state: "healthy", administrative_policy: "active", route_compatibility: "compatible",
    effective_eligibility: "eligible", ...overrides };
}

function setHeartbeatFacts(butlers: ButlerHeartbeat[], flags = {}) {
  vi.mocked(useButlerHeartbeats).mockReturnValue({data: {data: {butlers}, meta: {}},
    isPending: false, isFetching: false, isError: false, refetch: vi.fn(), ...flags} as AnyMock);
}

// ---------------------------------------------------------------------------
// Mock useButlerStatusBoard
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-butler-status-board", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/use-butler-status-board")>();
  return { ...actual, useButlerStatusBoard: vi.fn() };
});

// Trigger-tick remedy (bu-86c4c.15) -- mock the mutation hook so these tests
// don't need a real QueryClientProvider; the interactive tick-button behavior
// itself is covered separately in ButlerHeartbeatTile.trigger-tick.test.tsx.
vi.mock("@/hooks/use-butlers", () => ({
  useForceButlerTick: vi.fn(() => ({
    mutate: vi.fn(),
    isPending: false,
    variables: undefined,
  })),
}));

// ---------------------------------------------------------------------------
// Mock <Time> to avoid timezone-context / date-fns-tz setup in tests.
// Renders the ISO value so assertions on rendered heartbeat timestamps work.
// ---------------------------------------------------------------------------

vi.mock("@/components/ui/time", () => ({
  Time: ({ value }: { value: string }) => (
    <time dateTime={value}>{value}</time>
  ),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyMock = any;

function makeRow(overrides: Partial<StatusBoardRow> = {}): StatusBoardRow {
  return {
    name: "general",
    type: "butler",
    description: null,
    status: "ok",
    activity: "idle",
    cellTone: "neutral",
    eligibility: "active",
    quarantineReason: null,
    quarantinedAt: null,
    sessions24h: 0,
    costToday: null,
    loadPct: null,
    activeSessionCount: 0,
    lastRunISO: null,
    lastHeartbeatISO: "2026-05-03T10:00:00Z",
    heartbeatAgeSeconds: 30,
    hourlyStripe: Array(24).fill(0),
    hourlyTotal: 0,
    hourlyStripeLoading: false,
    hourlyStripeError: false,
    schemaUnreachable: false,
    heartbeatUnavailable: false,
    cadenceSeconds: null,
    cadenceLabel: null,
    silenceSeconds: null,
    cadenceStatus: "unknown",
    ...overrides,
  };
}

function makeAggregates(overrides: Partial<StatusBoardAggregates> = {}): StatusBoardAggregates {
  return {
    total: 0,
    butlerCount: 0,
    stafferCount: 0,
    active: 0,
    offline: 0,
    quarantined: 0,
    overdue: 0,
    totalSessions24h: 0,
    totalSpendToday: 0,
    avgLoadPct: null,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    heartbeatSourceError: false,
    registrySourceError: false,
    eligibilityUnavailable: 0,
    hasPerEntryErrors: false,
    costSourceError: false,
    sessionsSourceError: false,
    sourcesPartiallyDegraded: false,
    ...overrides,
    unknown: overrides.unknown ?? 0,
  };
}

function setState(rows: StatusBoardRow[], aggregates: StatusBoardAggregates) {
  setHeartbeatFacts(rows.map((row) => heartbeatFact(row.name, {last_heartbeat_at: row.lastHeartbeatISO})),
    {isPending: aggregates.isLoading, isError: aggregates.isError});
  vi.mocked(useButlerStatusBoard).mockReturnValue({ rows, aggregates, needsYou: [] } as AnyMock);
}

function setLoading() {
  setState([], makeAggregates({ isLoading: true }));
}

function setError(err: Error = new Error("Network error")) {
  setState([], makeAggregates({ isError: true, error: err }));
}

function setData(rows: StatusBoardRow[]) {
  setState(rows, makeAggregates({ total: rows.length, butlerCount: rows.length }));
}

function render(): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <ButlerHeartbeatTile />
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ButlerHeartbeatTile -- loading state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setLoading();
  });

  it("renders the tile title while loading", () => {
    const html = render();
    expect(html).toContain("Butler Heartbeats");
  });

  it("renders a loading skeleton, not a list", () => {
    const html = render();
    expect(html).toContain('data-testid="butler-heartbeat-skeleton"');
    expect(html).not.toContain("No butlers registered");
  });

  it("does not render butler names while loading", () => {
    const html = render();
    expect(html).not.toContain("general");
  });
});

describe("ButlerHeartbeatTile -- error state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setError();
  });

  it("renders the tile title on error", () => {
    const html = render();
    expect(html).toContain("Butler Heartbeats");
  });

  it("renders the error message", () => {
    const html = render();
    expect(html).toContain("Failed to load heartbeat data.");
  });

  it("does not render a butler list on error", () => {
    const html = render();
    expect(html).not.toContain("No butlers registered");
    expect(html).not.toContain('data-testid="butler-heartbeat-skeleton"');
  });
});

describe("ButlerHeartbeatTile -- empty butler list", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setData([]);
  });

  it("renders the tile title", () => {
    const html = render();
    expect(html).toContain("Butler Heartbeats");
  });

  it("renders the empty message", () => {
    const html = render();
    expect(html).toContain("No butlers registered.");
  });

  it("shows 0 butlers in the header count", () => {
    const html = render();
    expect(html).toContain("0 butlers");
  });
});

describe("ButlerHeartbeatTile -- running/idle butlers", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setData([
      makeRow({ name: "general", activity: "idle", cellTone: "neutral", activeSessionCount: 0 }),
      makeRow({
        name: "memory",
        activity: "running",
        cellTone: "green",
        activeSessionCount: 2,
        lastHeartbeatISO: "2026-05-03T09:00:00Z",
      }),
    ]);
  });

  it("renders all butler names", () => {
    const html = render();
    expect(html).toContain("general");
    expect(html).toContain("memory");
  });

  it("renders the lastHeartbeatISO timestamp for each butler", () => {
    const html = render();
    expect(html).toContain("2026-05-03T10:00:00Z");
    expect(html).toContain("2026-05-03T09:00:00Z");
  });

  it("renders an active session badge for butlers with active sessions", () => {
    const html = render();
    expect(html).toContain("2 active");
  });

  it("does not render an active badge for butlers with zero sessions", () => {
    const html = render();
    // Only one badge for the memory butler -- no badge for general
    const count = (html.match(/\d+ active/g) ?? []).length;
    expect(count).toBe(1);
  });

  it("renders the canonical healthy tone dot for running/idle butlers", () => {
    const html = render();
    const healthyDots = (html.match(/var\(--green\)/g) ?? []).length;
    expect(healthyDots).toBe(2);
  });

  it("keeps the canonical liveness verdict in accessible and visual-hover labels", () => {
    const html = render();
    expect(html).toContain('aria-label="Observation: healthy"');
    expect(html).toContain('Activity: running');
    expect(html).toContain('Activity: idle');
    expect(html).toContain('title="healthy"');
  });

  it("shows the butler count in the header", () => {
    const html = render();
    expect(html).toContain("2 butlers");
  });
});

describe("ButlerHeartbeatTile -- overdue and offline/quarantined butlers", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setData([
      makeRow({
        name: "overdue-butler",
        activity: "overdue",
        cellTone: "amber",
        cadenceLabel: "daily",
        silenceSeconds: 5 * 86400,
      }),
      makeRow({ name: "never-seen", lastHeartbeatISO: null, heartbeatAgeSeconds: null }),
      makeRow({ name: "down-butler", activity: "offline", cellTone: "red", status: "down" }),
    ]);
    setHeartbeatFacts([heartbeatFact("overdue-butler", {observation_state: "stale", effective_eligibility: "ineligible"}),
      heartbeatFact("never-seen", {last_heartbeat_at: null, observation_state: "observer_unknown", effective_eligibility: "unknown"}),
      heartbeatFact("down-butler", {observation_state: "unavailable", effective_eligibility: "ineligible"})]);
  });

  it("renders an amber dot for the overdue butler (matches the roster board's tone)", () => {
    const html = render();
    expect(html).toContain("var(--amber)");
  });

  it("renders 'No heartbeat recorded' for butlers with no lastHeartbeatISO", () => {
    const html = render();
    expect(html).toContain("No heartbeat recorded");
  });

  it("renders a red dot for the offline butler", () => {
    const html = render();
    expect(html).toContain("var(--red)");
  });

  it("does not render an active badge for any of these non-running butlers", () => {
    const html = render();
    expect(html).not.toMatch(/\d+ active/);
  });
});

describe("ButlerHeartbeatTile -- schemaUnreachable per butler", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setData([
      makeRow({ name: "broken", schemaUnreachable: true, heartbeatUnavailable: true, activity: "unknown", cellTone: "neutral" }),
      makeRow({ name: "healthy", schemaUnreachable: false, activity: "idle" }),
    ]);
  });

  it("does not crash when one butler has schemaUnreachable", () => {
    expect(() => render()).not.toThrow();
  });

  it("renders the unreachable badge for the broken butler", () => {
    const html = render();
    expect(html).toContain("unreachable");
  });

  it("still renders the healthy butler alongside the broken one", () => {
    const html = render();
    expect(html).toContain("healthy");
    expect(html).toContain("broken");
  });

  it("shows both butlers -- tile does not drop the unreachable entry", () => {
    setData([
      makeRow({ name: "broken", schemaUnreachable: true, heartbeatUnavailable: true, activity: "unknown", cellTone: "neutral" }),
      makeRow({ name: "healthy", schemaUnreachable: false, activity: "idle" }),
    ]);
    const html = render();
    expect(html).toContain("2 butlers");
  });
});

describe("ButlerHeartbeatTile -- drill-down link (bu-86c4c.4)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setData([
      makeRow({ name: "general", activity: "idle" }),
      makeRow({ name: "qa patrol", activity: "running", cellTone: "green" }),
    ]);
  });

  it("wraps each row in a real <a> to /butlers/:name, not a div-onClick", () => {
    const html = render();
    expect(html).toContain('href="/butlers/general"');
    const anchorMatch = html.match(
      /<a[^>]*href="\/butlers\/general"[^>]*>([\s\S]*?)<\/a>/,
    );
    expect(anchorMatch).not.toBeNull();
    expect(anchorMatch![1]).toContain("general");
  });

  it("URI-encodes butler names with special characters", () => {
    const html = render();
    expect(html).toContain('href="/butlers/qa%20patrol"');
  });
});

describe("ButlerHeartbeatTile -- trigger-tick remedy (bu-86c4c.15)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setData([
      makeRow({ name: "healthy", activity: "idle" }),
      makeRow({ name: "overdue-butler", activity: "overdue", cellTone: "amber" }),
      makeRow({ name: "down-butler", activity: "offline", cellTone: "red" }),
    ]);
  });

  it("renders a Trigger tick button for overdue and offline rows", () => {
    const html = render();
    expect(html.match(/Trigger tick/g)?.length).toBe(2);
  });

  it("does not render a Trigger tick button for a healthy (idle) row", () => {
    const html = render();
    const healthyRowMatch = html.match(
      /<a[^>]*href="\/butlers\/healthy"[^>]*>([\s\S]*?)<\/a>/,
    );
    expect(healthyRowMatch).not.toBeNull();
    expect(healthyRowMatch![1]).not.toContain("Trigger tick");
  });

  it("switches a stale row's wrapper to role=link (nested button, not a real <a>)", () => {
    const html = render();
    // The overdue row's <a> must not exist -- it nests the Trigger tick
    // button, so RowLink falls back to an accessible role="link" div.
    expect(html).not.toMatch(/<a[^>]*href="\/butlers\/overdue-butler"/);
    expect(html).toMatch(/role="link"[^>]*aria-label="View overdue-butler"/);
  });
});

describe("ButlerHeartbeatTile -- sort order", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setData([
      makeRow({ name: "oldest", lastHeartbeatISO: "2026-05-01T00:00:00Z" }),
      makeRow({ name: "newest", lastHeartbeatISO: "2026-05-03T12:00:00Z" }),
      makeRow({ name: "middle", lastHeartbeatISO: "2026-05-02T00:00:00Z" }),
      makeRow({ name: "never", lastHeartbeatISO: null }),
    ]);
  });

  it("renders the most-recently-seen butler first, nulls last", () => {
    const html = render();
    const newestPos = html.indexOf("newest");
    const middlePos = html.indexOf("middle");
    const oldestPos = html.indexOf("oldest");
    const neverPos = html.indexOf("never");
    expect(newestPos).toBeLessThan(middlePos);
    expect(middlePos).toBeLessThan(oldestPos);
    expect(oldestPos).toBeLessThan(neverPos);
  });
});

// Governing successor REQ-system-overview-page-007: real query -> session HTTP -> UI.
// The old board-dot assertions survive as this independent four-fact consumer;
// activity, navigation, ordering and explicit tick assertions remain above.
describe("ButlerHeartbeatTile -- actual heartbeat consumer", () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it("keeps every legal server fact dimension independent of board activity and timestamp age", async () => {
    vi.resetAllMocks();
    const facts: ButlerHeartbeat[] = [];
    for (const observation of ["healthy", "stale", "unavailable", "observer_unknown"] as const) {
      for (const policy of ["active", "paused", "quarantined", "review_required"] as const) {
        for (const compatibility of ["compatible", "incompatible", "unknown"] as const) {
          for (const accepting of [true, false]) {
            const eligibility = policy === "paused" ? "paused" : policy === "active" && observation === "healthy" && compatibility === "compatible" && accepting ? "eligible" : "ineligible";
            facts.push(heartbeatFact(`service-${facts.length}`, {observation_state: observation,
              administrative_policy: policy, route_compatibility: compatibility, effective_eligibility: eligibility,
              last_heartbeat_at: observation === "healthy" ? "2001-01-01T00:00:00Z" : "2080-01-01T00:00:00Z",
              last_probe_at: "2080-01-01T00:01:00Z", heartbeat_age_seconds: observation === "healthy" ? 999999999 : 0}));
          }
        }
      }
    }
    facts.push(heartbeatFact("legacy", {observation_state: undefined, administrative_policy: undefined,
      route_compatibility: undefined, effective_eligibility: undefined} as unknown as Partial<ButlerHeartbeat>));
    facts.push(heartbeatFact("future-enum", {observation_state: "new-state", administrative_policy: "new-policy",
      route_compatibility: "new-compatibility", effective_eligibility: "new-eligibility"} as unknown as Partial<ButlerHeartbeat>));
    facts.push(heartbeatFact("unknown", {observation_state: "observer_unknown", administrative_policy: "unknown", route_compatibility: "unknown", effective_eligibility: "unknown"}));
    facts.push(heartbeatFact("schema-only", {error: "schema_unreachable"}));
    facts.push(heartbeatFact("case"));
    setData([...facts.filter((entry) => entry.name !== "schema-only").map((entry) => makeRow({name: entry.name, activity: "running", lastHeartbeatISO: "2080-01-01T00:00:00Z"})), makeRow({name: "Case"})]);
    const actual = await vi.importActual<typeof import("@/hooks/use-system")>("@/hooks/use-system");
    vi.mocked(useButlerHeartbeats).mockImplementation(actual.useButlerHeartbeats);
    const calls: {url: string; method: string}[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({url, method: init?.method ?? "GET"});
      return new Response(JSON.stringify({data: {butlers: facts}, meta: {}}), {status: 200});
    }));
    vi.useFakeTimers({toFake: ["setInterval", "clearInterval"]});
    const client = new QueryClient({defaultOptions: {queries: {retry: false, gcTime: 0}}});
    try {
      const view = renderDom(<QueryClientProvider client={client}><MemoryRouter><ButlerHeartbeatTile /></MemoryRouter></QueryClientProvider>);
      await waitFor(() => expect(screen.getByRole("link", {name: "View service-0"}).querySelectorAll("dd")[0]?.textContent).toBe("healthy"));
      for (const entry of facts) {
        const row = screen.getByRole("link", {name: `View ${entry.name}`});
        const values = [...row.querySelectorAll("dd")].map((value) => value.textContent);
        expect.soft(values, entry.name).toEqual([entry.observation_state, entry.administrative_policy, entry.route_compatibility, entry.effective_eligibility]
          .map((value) => !value || value.startsWith("new-") ? "unknown" : value.replaceAll("_", " ")));
      }
      expect.soft([...screen.getByRole("link", {name: "View Case"}).querySelectorAll("dd")].map((value) => value.textContent), "missing exact-name facts").toEqual(["unknown", "unknown", "unknown", "unknown"]);
      expect(screen.getByRole("link", {name: "View schema-only"}).textContent).toContain("unreachable");
      expect(screen.getByRole("link", {name: "View service-0"}).textContent).toContain("2001-01-01T00:00:00Z");
      expect(screen.getByRole("link", {name: "View service-0"}).textContent).toContain("Last probe attempt");
      expect(calls).toEqual([{url: "/api/system/butlers/heartbeat", method: "GET"}]);
      await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
      await waitFor(() => expect(calls).toHaveLength(2));
      expect(calls.every((entry) => entry.url === "/api/system/butlers/heartbeat" && entry.method === "GET")).toBe(true);
      vi.mocked(useButlerStatusBoard).mockReturnValue({rows: [], needsYou: [], aggregates: makeAggregates({isError: true})});
      view.rerender(<QueryClientProvider client={client}><MemoryRouter><ButlerHeartbeatTile /></MemoryRouter></QueryClientProvider>);
      expect(screen.getByText("Board activity unavailable.").textContent).toContain("unavailable");
      expect(screen.getByRole("link", {name: "View schema-only"}).querySelectorAll("dd")[0]?.textContent).toBe("healthy");
    } finally { cleanup(); client.clear(); vi.useRealTimers(); }
  });
});
