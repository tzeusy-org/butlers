// @vitest-environment jsdom

/**
 * Smoke tests for ChroniclesPage shell (bu-ig72b.4).
 *
 * Verifies:
 *   - ChroniclesPage renders its heading and three labelled widget regions.
 *   - /timeline route still renders TimelinePage (regression guard).
 *   - Auto-refresh is gated by pollingDisabled (bu-ig72b.27):
 *     - pollingDisabled=true  → refetchInterval passed to hooks is false.
 *     - pollingDisabled=false → refetchInterval is the configured interval.
 *     - AutoRefreshToggle is hidden when pollingDisabled=true.
 *     - AutoRefreshToggle is visible when pollingDisabled=false.
 */

import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, Route, Routes } from "react-router";

import ChroniclesPage from "@/pages/ChroniclesPage";

// ---------------------------------------------------------------------------
// Control variables for mocks (overridden per-test as needed)
// ---------------------------------------------------------------------------

let _pollingDisabled = false;
let _autoRefreshEnabled = true;
let _autoRefreshInterval = 30_000;
let _lastDefaultInterval: number | undefined;

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

// Mocks needed for SourceStateBadgeStrip, AggregateStackedBar, StreakCallouts, and Scrubber
vi.mock("@/hooks/use-chronicles", () => ({
  useChroniclesSourceState: () => ({ data: undefined, isLoading: false, isError: false }),
  useChroniclesAggregates: () => ({
    byCategory: { data: undefined, isLoading: false, isError: false },
    byDay: { data: [], isLoading: false, isError: false },
  }),
  useChroniclesEpisodes: () => ({ data: undefined, isLoading: false, isError: false }),
  useChroniclesPointEvents: () => ({ data: undefined, isLoading: false, isError: false }),
}));

vi.mock("@/hooks/use-time-window", () => ({
  useTimeWindow: () => ({
    from: new Date("2026-04-25T00:00:00Z"),
    to: new Date("2026-04-25T23:59:59Z"),
    preset: "today",
    pollingDisabled: _pollingDisabled,
    setPreset: vi.fn(),
    setCustomRange: vi.fn(),
  }),
}));

vi.mock("@/hooks/use-auto-refresh", () => ({
  useAutoRefresh: (defaultInterval?: number) => {
    _lastDefaultInterval = defaultInterval;
    return {
      enabled: _autoRefreshEnabled,
      interval: _autoRefreshInterval,
      refetchInterval: _autoRefreshEnabled ? _autoRefreshInterval : (false as const),
      setEnabled: vi.fn(),
      setInterval: vi.fn(),
    };
  },
}));

// Capture the last props AutoRefreshToggle received so tests can assert on them.
let _autoRefreshToggleProps: Record<string, unknown> | null = null;
vi.mock("@/components/ui/auto-refresh-toggle", () => ({
  AutoRefreshToggle: (props: Record<string, unknown>) => {
    _autoRefreshToggleProps = props;
    return null;
  },
}));

vi.mock("@/components/chronicles/TimeWindowPicker", () => ({
  TimeWindowPicker: () => null,
}));
vi.mock("@/components/chronicles/Scrubber", () => ({
  Scrubber: () => null,
}));
vi.mock("@/components/chronicles/MapWidget", () => ({
  MapWidget: () => null,
}));
vi.mock("@/components/chronicles/GanttSwimlane", () => ({
  GanttSwimlane: () => null,
}));
vi.mock("@/components/chronicles/AggregatePieChart", () => ({
  AggregatePieChart: () => null,
}));

// Mocks needed for TimelinePage (regression guard)
vi.mock("@/hooks/use-butlers", () => ({
  useButlers: () => ({ data: undefined }),
}));
vi.mock("@/hooks/use-timeline", () => ({
  useTimeline: () => ({ data: undefined, isLoading: false }),
}));
vi.mock("@/components/timeline/UnifiedTimeline", () => ({
  default: () => <div data-testid="unified-timeline-stub" />,
}));

// TimelinePage import (for the regression guard below)
import TimelinePage from "@/pages/TimelinePage";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderChroniclesPage(): string {
  _autoRefreshToggleProps = null;
  _lastDefaultInterval = undefined;
  return renderToStaticMarkup(
    <MemoryRouter initialEntries={["/chronicles"]}>
      <Routes>
        <Route path="/chronicles" element={<ChroniclesPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function renderTimelinePage(): string {
  return renderToStaticMarkup(
    <MemoryRouter initialEntries={["/timeline"]}>
      <Routes>
        <Route path="/timeline" element={<TimelinePage />} />
      </Routes>
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// ChroniclesPage — structural smoke tests
// ---------------------------------------------------------------------------

describe("ChroniclesPage", () => {
  it("renders the Chronicles page heading", () => {
    _pollingDisabled = false;
    const html = renderChroniclesPage();
    expect(html).toContain("Chronicles");
  });

  it("renders the Gantt area widget region", () => {
    _pollingDisabled = false;
    const html = renderChroniclesPage();
    expect(html).toContain("Gantt area");
  });

  it("renders the Map area widget region", () => {
    _pollingDisabled = false;
    const html = renderChroniclesPage();
    expect(html).toContain("Map area");
  });

  it("renders the Aggregations area widget region", () => {
    _pollingDisabled = false;
    const html = renderChroniclesPage();
    expect(html).toContain("Aggregations area");
  });
});

// ---------------------------------------------------------------------------
// ChroniclesPage — auto-refresh gating (bu-ig72b.27)
// ---------------------------------------------------------------------------

describe("ChroniclesPage useAutoRefresh integration", () => {
  it("hides AutoRefreshToggle when pollingDisabled=true (older window)", () => {
    _pollingDisabled = true;
    _autoRefreshEnabled = true;
    _autoRefreshInterval = 30_000;
    renderChroniclesPage();
    // Toggle should not have been rendered
    expect(_autoRefreshToggleProps).toBeNull();
  });

  it("shows AutoRefreshToggle when pollingDisabled=false (today window)", () => {
    _pollingDisabled = false;
    _autoRefreshEnabled = true;
    _autoRefreshInterval = 30_000;
    renderChroniclesPage();
    // Toggle should have been rendered with the hook's values
    expect(_autoRefreshToggleProps).not.toBeNull();
    expect(_autoRefreshToggleProps?.enabled).toBe(true);
    expect(_autoRefreshToggleProps?.interval).toBe(30_000);
  });

  it("uses 30s as the default auto-refresh interval", () => {
    _pollingDisabled = false;
    renderChroniclesPage();
    // Verify the component passed the correct default to the hook
    expect(_lastDefaultInterval).toBe(30_000);
  });

  it("propagates configured interval when pollingDisabled=false and enabled=true", () => {
    _pollingDisabled = false;
    _autoRefreshEnabled = true;
    _autoRefreshInterval = 60_000;
    renderChroniclesPage();
    expect(_autoRefreshToggleProps).not.toBeNull();
    expect(_autoRefreshToggleProps?.interval).toBe(60_000);
    expect(_autoRefreshToggleProps?.enabled).toBe(true);
  });

  it("propagates enabled=false when user pauses and pollingDisabled=false", () => {
    _pollingDisabled = false;
    _autoRefreshEnabled = false;
    _autoRefreshInterval = 30_000;
    renderChroniclesPage();
    // Toggle is still shown (user can resume), but enabled=false
    expect(_autoRefreshToggleProps).not.toBeNull();
    expect(_autoRefreshToggleProps?.enabled).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// TimelinePage regression guard — /timeline must still render TimelinePage
// ---------------------------------------------------------------------------

describe("TimelinePage smoke", () => {
  it("renders the Timeline page heading", () => {
    const html = renderTimelinePage();
    expect(html).toContain("Timeline");
  });

  it("does not render Chronicles heading text when at /timeline", () => {
    const html = renderTimelinePage();
    // TimelinePage has an h1 ("Timeline") but it must not say "Chronicles"
    expect(html).not.toContain(">Chronicles<");
  });
});
