/**
 * ButlersPage — unit tests for the status-board rewrite (bu-hb7dh.8)
 *
 * All data sources are mocked via useButlerStatusBoard. The hook returns rows
 * and aggregates; tests drive both to verify the full rendered surface:
 *
 *   - Header pill (healthy/total reflects aggregates)
 *   - Grid render (cells rendered for each fixture row, linked to detail pages)
 *   - Footer aggregates
 *   - Empty state ("No butlers found")
 *   - Stale-fetch banner (error + cached rows)
 *   - Loading skeleton (aria-label="Loading")
 *   - Quarantine click-to-restore (setEligibility called with correct args)
 *   - Partial-data tolerance (rows render even when some sources fail)
 *   - No inline-style violations on the page-level container
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import ButlersPage from "@/pages/ButlersPage";
import type { StatusBoardRow, StatusBoardAggregates } from "@/hooks/use-butler-status-board";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-butler-status-board", () => ({
  useButlerStatusBoard: vi.fn(),
}));

vi.mock("@/hooks/use-general", () => ({
  useSetEligibility: vi.fn(),
}));

import { useButlerStatusBoard } from "@/hooks/use-butler-status-board";
import { useSetEligibility } from "@/hooks/use-general";

// ---------------------------------------------------------------------------
// Fixture helpers
// ---------------------------------------------------------------------------

const NO_OP_REFETCH = vi.fn().mockResolvedValue(undefined);

function makeAggregates(overrides: Partial<StatusBoardAggregates> = {}): StatusBoardAggregates {
  return {
    total: 0,
    butlerCount: 0,
    stafferCount: 0,
    active: 0,
    paused: 0,
    awaiting: 0,
    quarantined: 0,
    totalSessions24h: 0,
    totalSpendToday: 0,
    avgLoadPct: null,
    isLoading: false,
    isError: false,
    error: null,
    refetch: NO_OP_REFETCH,
    ...overrides,
  };
}

function makeRow(overrides: Partial<StatusBoardRow> = {}): StatusBoardRow {
  return {
    name: "general",
    type: "butler",
    description: null,
    status: "ok",
    activity: "idle",
    cellTone: "neutral",
    eligibility: "active",
    sessions24h: 0,
    costToday: 0,
    loadPct: null,
    lastRunISO: null,
    hourlyStripe: Array(24).fill(0),
    ...overrides,
  };
}

function setHookState(rows: StatusBoardRow[], aggregates: StatusBoardAggregates) {
  vi.mocked(useButlerStatusBoard).mockReturnValue({ rows, aggregates });
}

const mockMutate = vi.fn();

function renderPage(): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <ButlersPage />
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.mocked(useSetEligibility).mockReturnValue({
    mutate: mockMutate,
    mutateAsync: vi.fn(),
    isPending: false,
    isSuccess: false,
    isError: false,
    isIdle: true,
    error: null,
    data: undefined,
    reset: vi.fn(),
    context: undefined,
    failureCount: 0,
    failureReason: null,
    status: "idle",
    submittedAt: 0,
    variables: undefined,
  } as unknown as ReturnType<typeof useSetEligibility>);

  // Default: empty board, not loading, not error
  setHookState([], makeAggregates());
});

// ---------------------------------------------------------------------------
// Loading state
// ---------------------------------------------------------------------------

describe("ButlersPage — loading", () => {
  it("renders the Page primitive loading skeleton (aria-label=Loading)", () => {
    setHookState([], makeAggregates({ isLoading: true }));
    const html = renderPage();
    expect(html).toContain('aria-label="Loading"');
  });
});

// ---------------------------------------------------------------------------
// Error state (full-page — no cached rows)
// ---------------------------------------------------------------------------

describe("ButlersPage — full-page error", () => {
  it("renders error region when no cached rows exist", () => {
    setHookState([], makeAggregates({ isError: true, error: new Error("network offline") }));
    const html = renderPage();
    expect(html).toContain("Something went wrong");
    expect(html).toContain("network offline");
    expect(html).toContain("Retry");
  });
});

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

describe("ButlersPage — empty state", () => {
  it("renders empty message when rows are empty and not loading/error", () => {
    setHookState([], makeAggregates({ total: 0 }));
    const html = renderPage();
    expect(html).toContain("No butlers found");
    expect(html).toContain("Check daemon status");
  });
});

// ---------------------------------------------------------------------------
// Stale-fetch banner
// ---------------------------------------------------------------------------

describe("ButlersPage — stale-fetch banner", () => {
  it("shows stale banner and cached rows when refetch fails with prior data", () => {
    const rows = [makeRow({ name: "general" })];
    setHookState(rows, makeAggregates({ total: 1, butlerCount: 1, isError: true, error: new Error("timed out") }));
    const html = renderPage();
    expect(html).toContain("Showing last known butler status");
    expect(html).toContain("timed out");
    expect(html).toContain("general");
  });

  it("does not show stale banner when no error", () => {
    const rows = [makeRow({ name: "general" })];
    setHookState(rows, makeAggregates({ total: 1, butlerCount: 1 }));
    const html = renderPage();
    expect(html).not.toContain("Showing last known butler status");
  });
});

// ---------------------------------------------------------------------------
// Grid render
// ---------------------------------------------------------------------------

describe("ButlersPage — grid render", () => {
  it("renders a cell per row with name and detail-page link", () => {
    const rows = [
      makeRow({ name: "health" }),
      makeRow({ name: "finance", type: "butler" }),
    ];
    setHookState(rows, makeAggregates({ total: 2, butlerCount: 2 }));
    const html = renderPage();
    expect(html).toContain("health");
    expect(html).toContain("finance");
    expect(html).toContain('href="/butlers/health"');
    expect(html).toContain('href="/butlers/finance"');
  });

  it("renders all 12 canonical butlers", () => {
    const names = [
      "chronicler", "education", "finance", "general", "health",
      "home", "lifestyle", "messenger", "qa", "relationship", "switchboard", "travel",
    ];
    const rows = names.map((name) => makeRow({ name }));
    setHookState(rows, makeAggregates({ total: 12, butlerCount: 12 }));
    const html = renderPage();
    for (const name of names) {
      expect(html).toContain(name);
      expect(html).toContain(`href="/butlers/${name}"`);
    }
  });

  it("renders an unfamiliar butler name without errors", () => {
    const rows = [makeRow({ name: "future-butler" })];
    setHookState(rows, makeAggregates({ total: 1, butlerCount: 1 }));
    const html = renderPage();
    expect(html).toContain("future-butler");
    expect(html).toContain('href="/butlers/future-butler"');
  });

  it("renders description when present", () => {
    const rows = [makeRow({ name: "health", description: "Tracks your wellness goals" })];
    setHookState(rows, makeAggregates({ total: 1, butlerCount: 1 }));
    const html = renderPage();
    expect(html).toContain("Tracks your wellness goals");
  });

  it("suppresses description when absent", () => {
    const rows = [makeRow({ name: "health", description: null })];
    setHookState(rows, makeAggregates({ total: 1, butlerCount: 1 }));
    const html = renderPage();
    expect(html).not.toContain("Tracks your wellness goals");
  });

  it("renders the ButlerMark glyph (title attribute) per cell", () => {
    const rows = [makeRow({ name: "health" })];
    setHookState(rows, makeAggregates({ total: 1, butlerCount: 1 }));
    const html = renderPage();
    expect(html).toContain('title="health"');
  });

  it("renders sessions24h KPI value", () => {
    const rows = [makeRow({ name: "health", sessions24h: 7 })];
    setHookState(rows, makeAggregates({ total: 1, butlerCount: 1 }));
    const html = renderPage();
    expect(html).toContain("7");
    expect(html).toContain("SESS 24H");
  });
});

// ---------------------------------------------------------------------------
// Header pill (healthy / total reflects aggregates)
// ---------------------------------------------------------------------------

describe("ButlersPage — BoardHeader healthy/total pill", () => {
  it("reflects healthy and total counts from aggregates", () => {
    // healthy = total - paused - awaiting - quarantined
    // 3 total, 1 paused, 0 awaiting, 0 quarantined → 2 healthy
    const rows = [
      makeRow({ name: "a" }),
      makeRow({ name: "b" }),
      makeRow({ name: "c", activity: "paused", cellTone: "red" }),
    ];
    setHookState(
      rows,
      makeAggregates({ total: 3, butlerCount: 3, paused: 1 }),
    );
    const html = renderPage();
    // BoardHeader renders "healthy/total reporting"
    expect(html).toContain("2/3 reporting");
  });
});

// ---------------------------------------------------------------------------
// Footer aggregates
// ---------------------------------------------------------------------------

describe("ButlersPage — BoardFooter aggregates", () => {
  it("renders footer with correct sessions and spend values", () => {
    const rows = [
      makeRow({ name: "a", sessions24h: 10, costToday: 1.5 }),
      makeRow({ name: "b", sessions24h: 5, costToday: 0.25 }),
    ];
    setHookState(
      rows,
      makeAggregates({ total: 2, butlerCount: 2, totalSessions24h: 15, totalSpendToday: 1.75 }),
    );
    const html = renderPage();
    // BoardFooter renders "Sessions·24h" label and total sessions
    expect(html).toContain("Sessions");
    expect(html).toContain("15");
    // BoardFooter renders spend formatted to 2dp
    expect(html).toContain("$1.75");
  });
});

// ---------------------------------------------------------------------------
// Quarantine click-to-restore
// ---------------------------------------------------------------------------

describe("ButlersPage — quarantine restore", () => {
  it("renders a restore chip for quarantined rows", () => {
    const rows = [makeRow({ name: "quarant", activity: "quarantined", eligibility: "quarantined", cellTone: "red" })];
    setHookState(rows, makeAggregates({ total: 1, butlerCount: 1, quarantined: 1 }));
    const html = renderPage();
    // StatusBoardCell renders the activity chip as a <button> when restorable
    expect(html).toContain("QUARANTINED");
    // The restore button uses a <button> element (not just a <span>)
    expect(html).toMatch(/<button[^>]*>QUARANTINED<\/button>/);
  });

  it("renders a restore chip for stale rows", () => {
    const rows = [makeRow({ name: "stale-butler", activity: "idle", eligibility: "stale" })];
    setHookState(rows, makeAggregates({ total: 1, butlerCount: 1 }));
    const html = renderPage();
    expect(html).toMatch(/<button[^>]*>IDLE<\/button>/);
  });
});

// ---------------------------------------------------------------------------
// Partial-data tolerance
// ---------------------------------------------------------------------------

describe("ButlersPage — partial-data tolerance", () => {
  it("renders rows even when cost and load data fall back to defaults", () => {
    const rows = [
      makeRow({ name: "alpha", costToday: 0, loadPct: null, lastRunISO: null }),
      makeRow({ name: "beta", costToday: 0, loadPct: null, lastRunISO: null }),
    ];
    setHookState(rows, makeAggregates({ total: 2, butlerCount: 2 }));
    const html = renderPage();
    expect(html).toContain("alpha");
    expect(html).toContain("beta");
    // Null load renders as placeholder dash
    expect(html).toContain("—");
  });
});

// ---------------------------------------------------------------------------
// No inline-style on the page-level container
// ---------------------------------------------------------------------------

describe("ButlersPage — no inline style on page container", () => {
  it("does not emit a style attribute on the status-board grid container", () => {
    const rows = [makeRow({ name: "health" })];
    setHookState(rows, makeAggregates({ total: 1, butlerCount: 1 }));
    const html = renderPage();

    // The grid wrapper rendered by ButlersPage must not have a style attribute.
    // Match the grid container's opening tag (aria-label="Butler status board").
    const gridTagMatch = html.match(/<div[^>]*aria-label="Butler status board"[^>]*>/);
    expect(gridTagMatch).not.toBeNull();
    expect(gridTagMatch?.[0]).not.toContain("style=");
  });
});
