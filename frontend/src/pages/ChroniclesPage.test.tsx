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
 *   - Manual refresh button (bu-hzqr0):
 *     - ManualRefreshButton is rendered when pollingDisabled=true (historical window).
 *     - ManualRefreshButton is hidden when pollingDisabled=false (live window).
 *   - OwnTracks trail derivation from pointEvents (bu-ig72b.35):
 *     - Sensitive events excluded from trailPoints.
 *     - Only events with lat/lon in payload included.
 *     - Points sorted by canonical_occurred_at ascending.
 *
 * Mock coverage note (bu-gu3xn):
 *   EpisodeDrawer (bu-ig72b.31) is stubbed at the component level so its
 *   internal hooks stay out of the page-level mock surface. The use-chronicles
 *   mock nevertheless declares useChroniclerExplain and the episode-fetch hooks
 *   so any future refactor that inlines them into ChroniclesPage cannot silently
 *   introduce undefined-hook errors. EpisodeDrawer behaviour is tested
 *   exhaustively in EpisodeDrawer.test.tsx.
 */

import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, Route, Routes } from "react-router";

import ChroniclesPage from "@/pages/ChroniclesPage";

import type { ChroniclerPointEvent } from "@/api/types";

// ---------------------------------------------------------------------------
// Control variables for mocks (overridden per-test as needed)
// ---------------------------------------------------------------------------

let _pollingDisabled = false;
let _autoRefreshEnabled = true;
let _autoRefreshInterval = 30_000;
let _lastDefaultInterval: number | undefined;
// Captured pointEvents returned by useChroniclesPointEvents (for trail tests)
let _mockPointEvents: ChroniclerPointEvent[] = [];
// Captured trailPoints passed to MapWidget (for trail derivation tests)
let _capturedMapWidgetTrailPoints: Array<{ lng: number; lat: number }> | undefined = undefined;

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

// Mocks needed for SourceStateBadgeStrip, AggregateStackedBar, StreakCallouts, Scrubber,
// and EpisodeDrawer (EpisodeDrawer is rendered in ChroniclesPage but tested separately).
vi.mock("@/hooks/use-chronicles", () => ({
  useChroniclesSourceState: () => ({ data: undefined, isLoading: false, isError: false }),
  useChroniclesAggregates: () => ({
    byCategory: { data: undefined, isLoading: false, isError: false },
    byDay: { data: [], isLoading: false, isError: false },
  }),
  useChroniclesEpisodes: () => ({ data: undefined, isLoading: false, isError: false }),
  useChroniclesPointEvents: () => ({
    data: _mockPointEvents.length > 0 ? { data: _mockPointEvents } : undefined,
    isLoading: false,
    isError: false,
  }),
  // Episode-drawer hooks (bu-ig72b.31) — included here to prevent future drift
  // when EpisodeDrawer's hook calls become visible to the page-level mock.
  useChroniclerEpisode: () => ({ data: undefined, isLoading: false, error: null }),
  useChroniclerEpisodeEvents: () => ({ data: undefined, isLoading: false, error: null }),
  useChroniclerEpisodeCorrections: () => ({ data: undefined, isLoading: false, error: null }),
  useChroniclerExplain: vi.fn(() => ({
    mutate: vi.fn(),
    isPending: false,
    isSuccess: false,
    isError: false,
    error: null,
    reset: vi.fn(),
  })),
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

// Capture the last props ManualRefreshButton received so tests can assert on them.
let _manualRefreshButtonProps: Record<string, unknown> | null = null;
vi.mock("@/components/chronicles/ManualRefreshButton", () => ({
  ManualRefreshButton: (props: Record<string, unknown>) => {
    _manualRefreshButtonProps = props;
    return null;
  },
}));

vi.mock("@/components/chronicles/TimeWindowPicker", () => ({
  TimeWindowPicker: () => null,
}));

// Track whether ManualRefreshButton was rendered (for bu-hzqr0 tests).
let _manualRefreshButtonRendered = false;
vi.mock("@/components/chronicles/ManualRefreshButton", () => ({
  ManualRefreshButton: () => {
    _manualRefreshButtonRendered = true;
    return <span data-testid="manual-refresh-button-stub">Refresh</span>;
  },
}));
vi.mock("@/components/chronicles/Scrubber", () => ({
  Scrubber: () => null,
}));
vi.mock("@/components/chronicles/MapWidget", () => ({
  MapWidget: (props: Record<string, unknown>) => {
    _capturedMapWidgetTrailPoints = props.trailPoints as Array<{ lng: number; lat: number }> | undefined;
    return null;
  },
}));
vi.mock("@/components/chronicles/GanttSwimlane", () => ({
  GanttSwimlane: () => null,
}));
vi.mock("@/components/chronicles/AggregatePieChart", () => ({
  AggregatePieChart: () => null,
}));
vi.mock("@/components/chronicles/EpisodeDrawer", () => ({
  EpisodeDrawer: () => null,
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
  _manualRefreshButtonProps = null;
  _lastDefaultInterval = undefined;
  _capturedMapWidgetTrailPoints = undefined;
  _manualRefreshButtonRendered = false;
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
// Manual refresh button — ChroniclesPage conditional rendering (bu-hzqr0)
// ---------------------------------------------------------------------------

describe("ChroniclesPage manual refresh button", () => {
  it("renders ManualRefreshButton when pollingDisabled=true (historical window)", () => {
    _pollingDisabled = true;
    const html = renderChroniclesPage();
    // Either the stub text appears in HTML or the tracking flag was set.
    expect(_manualRefreshButtonRendered).toBe(true);
    expect(html).toContain("Refresh");
  });

  it("does not render ManualRefreshButton when pollingDisabled=false (live window)", () => {
    _pollingDisabled = false;
    renderChroniclesPage();
    expect(_manualRefreshButtonRendered).toBe(false);
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

// ---------------------------------------------------------------------------
// OwnTracks trail derivation — ChroniclesPage → MapWidget.trailPoints (bu-ig72b.35)
// ---------------------------------------------------------------------------

function makePointEvent(
  id: string,
  canonical_occurred_at: string,
  overrides: Partial<ChroniclerPointEvent> = {},
): ChroniclerPointEvent {
  return {
    id,
    source_name: "owntracks",
    source_ref: `ref:${id}`,
    event_type: "location",
    occurred_at: canonical_occurred_at,
    precision: "exact",
    title: null,
    payload: { lat: 1.35, lon: 103.8 },
    privacy: "normal",
    retention_days: 30,
    tombstone_at: null,
    canonical_occurred_at,
    canonical_title: null,
    canonical_privacy: "normal",
    corrected_at: null,
    correction_note: null,
    created_at: canonical_occurred_at,
    updated_at: canonical_occurred_at,
    ...overrides,
  };
}

describe("ChroniclesPage OwnTracks trail derivation", () => {
  it("passes empty trailPoints to MapWidget when there are no point events", () => {
    _mockPointEvents = [];
    renderChroniclesPage();
    expect(_capturedMapWidgetTrailPoints).toEqual([]);
  });

  it("excludes events with canonical_privacy='sensitive' from trailPoints", () => {
    _mockPointEvents = [
      makePointEvent("ev-1", "2026-04-25T10:00:00Z", {
        canonical_privacy: "sensitive",
        payload: { lat: 1.35, lon: 103.8 },
      }),
      makePointEvent("ev-2", "2026-04-25T11:00:00Z", {
        canonical_privacy: "normal",
        payload: { lat: 48.86, lon: 2.35 },
      }),
    ];
    renderChroniclesPage();
    // Only the normal event should produce a trail point.
    expect(_capturedMapWidgetTrailPoints).toHaveLength(1);
    expect(_capturedMapWidgetTrailPoints?.[0]).toEqual({ lng: 2.35, lat: 48.86 });
  });

  it("excludes events without lat/lon in payload", () => {
    _mockPointEvents = [
      makePointEvent("ev-1", "2026-04-25T10:00:00Z", { payload: {} }),
      makePointEvent("ev-2", "2026-04-25T11:00:00Z", { payload: { lat: 1.35, lon: 103.8 } }),
    ];
    renderChroniclesPage();
    expect(_capturedMapWidgetTrailPoints).toHaveLength(1);
    expect(_capturedMapWidgetTrailPoints?.[0]).toEqual({ lng: 103.8, lat: 1.35 });
  });

  it("sorts trailPoints by canonical_occurred_at ascending", () => {
    _mockPointEvents = [
      makePointEvent("ev-late", "2026-04-25T14:00:00Z", { payload: { lat: 3.0, lon: 3.0 } }),
      makePointEvent("ev-early", "2026-04-25T09:00:00Z", { payload: { lat: 1.0, lon: 1.0 } }),
      makePointEvent("ev-mid", "2026-04-25T11:00:00Z", { payload: { lat: 2.0, lon: 2.0 } }),
    ];
    renderChroniclesPage();
    expect(_capturedMapWidgetTrailPoints).toHaveLength(3);
    expect(_capturedMapWidgetTrailPoints?.[0]).toEqual({ lng: 1.0, lat: 1.0 });
    expect(_capturedMapWidgetTrailPoints?.[1]).toEqual({ lng: 2.0, lat: 2.0 });
    expect(_capturedMapWidgetTrailPoints?.[2]).toEqual({ lng: 3.0, lat: 3.0 });
  });

  it("accepts events with 'lng' key instead of 'lon' in payload", () => {
    _mockPointEvents = [
      makePointEvent("ev-1", "2026-04-25T10:00:00Z", {
        payload: { lat: 51.5, lng: -0.12 },
      }),
    ];
    renderChroniclesPage();
    expect(_capturedMapWidgetTrailPoints).toHaveLength(1);
    expect(_capturedMapWidgetTrailPoints?.[0]).toEqual({ lng: -0.12, lat: 51.5 });
  });
});

// ---------------------------------------------------------------------------
// ManualRefreshButton integration — ChroniclesPage threads timeWindow (bu-zlzxz)
// ---------------------------------------------------------------------------

describe("ChroniclesPage ManualRefreshButton integration", () => {
  it("renders ManualRefreshButton always (independent of pollingDisabled)", () => {
    _pollingDisabled = false;
    renderChroniclesPage();
    expect(_manualRefreshButtonProps).not.toBeNull();
  });

  it("renders ManualRefreshButton even when pollingDisabled=true", () => {
    _pollingDisabled = true;
    renderChroniclesPage();
    expect(_manualRefreshButtonProps).not.toBeNull();
  });

  it("passes timeWindow.from and timeWindow.to as Date instances", () => {
    _pollingDisabled = false;
    renderChroniclesPage();
    // The mock useTimeWindow returns fixed Date objects; verify they are threaded through.
    const tw = _manualRefreshButtonProps?.timeWindow as { from: Date; to: Date } | undefined;
    expect(tw).toBeDefined();
    expect(tw?.from).toBeInstanceOf(Date);
    expect(tw?.to).toBeInstanceOf(Date);
  });

  it("timeWindow.from matches the mock window start (2026-04-25T00:00:00Z)", () => {
    _pollingDisabled = false;
    renderChroniclesPage();
    const tw = _manualRefreshButtonProps?.timeWindow as { from: Date; to: Date } | undefined;
    expect(tw?.from.toISOString()).toBe(new Date("2026-04-25T00:00:00Z").toISOString());
  });
});
