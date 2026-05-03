// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// ButlerHeartbeatTile tests
//
// Coverage:
//   - Loading state: skeleton rendered, no butler rows
//   - Error state: error message rendered
//   - Empty butler list: "No butlers registered" message
//   - Healthy butlers: name, relative time, active session badge
//   - Stale butlers: stale indicator (>5 min age)
//   - schema_unreachable per-butler: "unreachable" badge, tile does not crash
//   - Sorting: most-recently-seen butler appears first
// ---------------------------------------------------------------------------

import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import { ButlerHeartbeatTile } from "./ButlerHeartbeatTile";
import { useButlerHeartbeats } from "@/hooks/use-system";
import type { ButlerHeartbeat } from "@/api/types";

// ---------------------------------------------------------------------------
// Mock useButlerHeartbeats
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-system", () => ({
  useButlerHeartbeats: vi.fn(),
}));

// ---------------------------------------------------------------------------
// Mock <Time> to avoid ChroniclesTimezoneProvider / date-fns-tz in tests.
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

function makeButler(overrides: Partial<ButlerHeartbeat> = {}): ButlerHeartbeat {
  return {
    name: "general",
    last_heartbeat_at: "2026-05-03T10:00:00Z",
    last_session_at: null,
    active_session_count: 0,
    heartbeat_age_seconds: 30,
    error: null,
    ...overrides,
  };
}

function setLoading() {
  vi.mocked(useButlerHeartbeats).mockReturnValue({
    data: undefined,
    isLoading: true,
    error: null,
  } as AnyMock);
}

function setError(err: Error = new Error("Network error")) {
  vi.mocked(useButlerHeartbeats).mockReturnValue({
    data: undefined,
    isLoading: false,
    error: err,
  } as AnyMock);
}

function setData(butlers: ButlerHeartbeat[]) {
  vi.mocked(useButlerHeartbeats).mockReturnValue({
    data: { data: { butlers }, meta: {} },
    isLoading: false,
    error: null,
  } as AnyMock);
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
    expect(html).toContain("animate-pulse");
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
    expect(html).not.toContain("animate-pulse");
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

describe("ButlerHeartbeatTile -- healthy butlers", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setData([
      makeButler({ name: "general", heartbeat_age_seconds: 60, active_session_count: 0 }),
      makeButler({ name: "memory", heartbeat_age_seconds: 120, active_session_count: 2, last_heartbeat_at: "2026-05-03T09:00:00Z" }),
    ]);
  });

  it("renders all butler names", () => {
    const html = render();
    expect(html).toContain("general");
    expect(html).toContain("memory");
  });

  it("renders the last_heartbeat_at timestamp for each butler", () => {
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
    const count = (html.match(/active/g) ?? []).length;
    expect(count).toBe(1);
  });

  it("renders a healthy indicator for fresh heartbeats", () => {
    const html = render();
    expect(html).toContain("Healthy heartbeat");
  });

  it("shows the butler count in the header", () => {
    const html = render();
    expect(html).toContain("2 butlers");
  });
});

describe("ButlerHeartbeatTile -- stale butlers", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setData([
      makeButler({ name: "stale-butler", heartbeat_age_seconds: 400 }),
      makeButler({ name: "very-stale", heartbeat_age_seconds: null, last_heartbeat_at: null }),
    ]);
  });

  it("renders a stale indicator for overdue heartbeats (>5 min)", () => {
    const html = render();
    const staleCount = (html.match(/Stale heartbeat/g) ?? []).length;
    expect(staleCount).toBe(2);
  });

  it("renders 'No heartbeat recorded' for butlers with no last_heartbeat_at", () => {
    const html = render();
    expect(html).toContain("No heartbeat recorded");
  });

  it("does not show a healthy indicator for stale butlers", () => {
    const html = render();
    expect(html).not.toContain("Healthy heartbeat");
  });
});

describe("ButlerHeartbeatTile -- schema_unreachable per butler", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setData([
      makeButler({ name: "broken", error: "schema_unreachable", heartbeat_age_seconds: 600 }),
      makeButler({ name: "healthy", heartbeat_age_seconds: 10, error: null }),
    ]);
  });

  it("does not crash when one butler has schema_unreachable", () => {
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
    const html = render();
    expect(html).toContain("2 butlers");
  });
});

describe("ButlerHeartbeatTile -- sort order", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setData([
      makeButler({ name: "oldest", last_heartbeat_at: "2026-05-01T00:00:00Z", heartbeat_age_seconds: 172800 }),
      makeButler({ name: "newest", last_heartbeat_at: "2026-05-03T12:00:00Z", heartbeat_age_seconds: 5 }),
      makeButler({ name: "middle", last_heartbeat_at: "2026-05-02T00:00:00Z", heartbeat_age_seconds: 86400 }),
    ]);
  });

  it("renders the most-recently-seen butler first", () => {
    const html = render();
    const newestPos = html.indexOf("newest");
    const middlePos = html.indexOf("middle");
    const oldestPos = html.indexOf("oldest");
    expect(newestPos).toBeLessThan(middlePos);
    expect(middlePos).toBeLessThan(oldestPos);
  });
});
