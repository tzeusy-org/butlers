// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { cleanup, fireEvent, render as renderDom, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation, useNavigate } from "react-router";

import TimelinePage from "@/pages/TimelinePage";
import { useTimelineLedger } from "@/hooks/use-timeline-ledger";
import { useTimelineAttention, useTimelineHistogram } from "@/hooks/use-timeline";
import { useButlers } from "@/hooks/use-butlers";
import {
  useTimelineSavedViews,
  useCreateTimelineSavedView,
  useDeleteTimelineSavedView,
} from "@/hooks/use-timeline-saved-views";
import type { TimelineAttentionResponse } from "@/api/types.ts";

vi.mock("@/hooks/use-timeline-ledger", () => ({
  useTimelineLedger: vi.fn(),
}));
vi.mock("@/hooks/use-timeline", () => ({
  useTimelineAttention: vi.fn(),
  useTimelineHistogram: vi.fn(),
}));

vi.mock("@/hooks/use-butlers", () => ({
  useButlers: vi.fn(),
}));

vi.mock("@/hooks/use-timeline-saved-views", () => ({
  useTimelineSavedViews: vi.fn(),
  useCreateTimelineSavedView: vi.fn(),
  useUpdateTimelineSavedView: vi.fn(),
  useDeleteTimelineSavedView: vi.fn(),
}));

type UseTimelineLedgerResult = ReturnType<typeof useTimelineLedger>;
type UseTimelineAttentionResult = ReturnType<typeof useTimelineAttention>;

function setLedger(partial: Partial<UseTimelineLedgerResult>): void {
  vi.mocked(useTimelineLedger).mockReturnValue({
    events: [],
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
    hasMore: false,
    loadMore: vi.fn(),
    loadMoreError: false,
    retryLoadMore: vi.fn(),
    isLoadingMore: false,
    pinned: true,
    newCount: 0,
    showNewEvents: vi.fn(),
    degradedSources: [],
    degradedButlers: [],
    heartbeatRollup: { ticks: 0, butlers: 0, failed: 0 },
    isLiveFeedDown: false,
    ...partial,
  } as unknown as UseTimelineLedgerResult);
}

function setAttention(
  data: TimelineAttentionResponse | undefined,
  partial: Partial<UseTimelineAttentionResult> = {},
): void {
  vi.mocked(useTimelineAttention).mockReturnValue({
    data,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
    ...partial,
  } as unknown as UseTimelineAttentionResult);
}

beforeEach(() => {
  vi.mocked(useTimelineHistogram).mockReturnValue({
    data: {
      data: [],
      meta: {
        since: "2026-07-04T13:00:00Z",
        until: "2026-07-04T14:00:00Z",
        bucket_seconds: 60,
        availability: "complete",
        expected_sources: 0,
        healthy_sources: 0,
        degraded_sources: [],
        degraded_butlers: [],
      },
    },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useTimelineHistogram>);
  vi.mocked(useTimelineAttention).mockReturnValue({
    data: {
      data: [],
      meta: {
        since: "2026-07-03T14:00:00Z",
        until: "2026-07-04T14:00:00Z",
        failed_sessions: 0,
        failed_notifications: 0,
        total: 0,
        has_more: false,
        availability: "complete",
        expected_sources: 0,
        healthy_sources: 0,
        degraded_sources: [],
        degraded_butlers: [],
      },
    },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useTimelineAttention>);
});

function render(initialEntry = "/timeline"): string {
  return renderToStaticMarkup(
    <MemoryRouter initialEntries={[initialEntry]}>
      <TimelinePage />
    </MemoryRouter>,
  );
}

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="timeline-location">{location.search}</output>;
}

function BrowserHistoryControls() {
  const navigate = useNavigate();
  return (
    <>
      <button type="button" onClick={() => navigate(-1)}>Browser back</button>
      <button type="button" onClick={() => navigate(1)}>Browser forward</button>
    </>
  );
}

describe("TimelinePage — error vs empty state", () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    vi.mocked(useButlers).mockReturnValue({
      data: { data: [] },
    } as unknown as ReturnType<typeof useButlers>);
    vi.mocked(useTimelineSavedViews).mockReturnValue({
      data: { data: [] },
    } as unknown as ReturnType<typeof useTimelineSavedViews>);
    vi.mocked(useCreateTimelineSavedView).mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useCreateTimelineSavedView>);
    vi.mocked(useDeleteTimelineSavedView).mockReturnValue({
      mutate: vi.fn(),
    } as unknown as ReturnType<typeof useDeleteTimelineSavedView>);
  });

  it("renders the error state (not the empty state) when the timeline query fails", () => {
    setLedger({ isError: true, events: [] });
    const html = render();
    expect(html).toContain("Could not load the timeline.");
    expect(html).toContain("Retry");
    expect(html).not.toContain("No events found.");
  });

  it("renders the empty state only on a successful fetch with zero events", () => {
    setLedger({ isError: false, events: [] });
    const html = render();
    expect(html).toContain("No events found.");
    expect(html).not.toContain("Could not load the timeline.");
  });

  it.each([
    ["a Timeline source", { degradedSources: ["notifications"] }],
    ["a named session butler", { degradedButlers: ["atlas"] }],
  ])("does not present an empty partial snapshot as a genuine empty result when %s is unavailable", (_, degraded) => {
    setLedger({ events: [], isError: false, ...degraded } as Partial<UseTimelineLedgerResult>);

    const html = render();

    expect(html).toContain("Timeline data is partially unavailable.");
    expect(html).not.toContain("No events found.");
    expect(html).toContain('data-testid="timeline-degraded-banner"');
  });

  it("renders the degraded-sources banner when a source is partial", () => {
    setLedger({ degradedSources: ["notifications"] });
    const html = render();
    expect(html).toContain("Partial data");
    expect(html).toContain("notifications");
  });

  it("announces partial-source evidence when it arrives after the initial timeline paint", () => {
    setLedger({ degradedSources: [], degradedButlers: [] });
    const view = renderDom(
      <MemoryRouter initialEntries={["/timeline"]}>
        <TimelinePage />
      </MemoryRouter>,
    );

    expect(screen.queryByTestId("timeline-degraded-banner")).toBeNull();

    setLedger({ degradedSources: ["sessions"], degradedButlers: ["home"] });
    view.rerender(
      <MemoryRouter initialEntries={["/timeline"]}>
        <TimelinePage />
      </MemoryRouter>,
    );

    const alert = screen.getByTestId("timeline-degraded-banner");
    expect(alert.getAttribute("role")).toBe("alert");
    expect(alert.textContent).toContain("Session data from home is unavailable.");
  });

  it("names the failed butler pools alongside generic partial Timeline metadata", () => {
    setLedger({
      degradedSources: ["sessions"],
      degradedButlers: ["atlas", "home"],
    } as unknown as Partial<UseTimelineLedgerResult>);
    const html = render();

    expect(html).toContain("atlas");
    expect(html).toContain("home");
    expect(html).toContain("Partial data");
  });

  it("does not render the degraded banner when all sources are healthy", () => {
    setLedger({ degradedSources: [] });
    const html = render();
    expect(html).not.toContain("Partial data");
  });

  it("renders the honest heartbeat rollup line from the backend, not a client miscount", () => {
    setLedger({ heartbeatRollup: { ticks: 32, butlers: 8, failed: 1 } });
    const html = render();
    expect(html).toContain("32 ticks");
    expect(html).toContain("8 butlers ticked");
    expect(html).toContain("1 failed");
  });

  it("renders source facet chips for sessions, errors, and notifications", () => {
    setLedger({});
    const html = render();
    expect(html).toContain('data-testid="facet-session"');
    expect(html).toContain('data-testid="facet-error"');
    expect(html).toContain('data-testid="facet-notification"');
  });

  it("names unavailable butler facets and retries that reader without hiding Timeline evidence", () => {
    const retryButlerFacets = vi.fn();
    vi.mocked(useButlers).mockReturnValue({
      data: { data: [{ name: "atlas" }] },
      isError: true,
      refetch: retryButlerFacets,
    } as unknown as ReturnType<typeof useButlers>);
    setLedger({
      events: [
        {
          id: "e1",
          type: "session",
          butler: "home",
          timestamp: "2026-07-04T14:32:00Z",
          summary: "reachable event",
          is_heartbeat: false,
          data: {},
        },
      ],
    });

    renderDom(
      <MemoryRouter initialEntries={["/timeline"]}>
        <TimelinePage />
      </MemoryRouter>,
    );

    expect(screen.getByText("Butler filters are temporarily unavailable.")).toBeTruthy();
    expect(screen.queryByText("No butlers available")).toBeNull();
    expect(screen.getByText("atlas")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry butler filters" }));
    expect(retryButlerFacets).toHaveBeenCalledOnce();
    expect(screen.getByText("reachable event")).toBeTruthy();
  });

  it("names unavailable saved views and retries that reader while built-in views remain usable", () => {
    const retrySavedViews = vi.fn();
    vi.mocked(useTimelineSavedViews).mockReturnValue({
      data: {
        data: [
          {
            id: "saved-house",
            name: "House events",
            filter_spec: { event_type: ["session"], butler: ["home"] },
          },
        ],
      },
      isError: true,
      refetch: retrySavedViews,
    } as unknown as ReturnType<typeof useTimelineSavedViews>);
    setLedger({});

    renderDom(
      <MemoryRouter initialEntries={["/timeline"]}>
        <TimelinePage />
      </MemoryRouter>,
    );

    expect(screen.getByText("Saved views are temporarily unavailable.")).toBeTruthy();
    expect(screen.getByTestId("saved-view-all")).toBeTruthy();
    expect(screen.getByText("House events")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry saved views" }));
    expect(retrySavedViews).toHaveBeenCalledOnce();
  });

  it("uses an accessible URL-backed Internal lens without replacing existing filters", () => {
    setLedger({});

    renderDom(
      <MemoryRouter initialEntries={["/timeline?butler=home&type=session"]}>
        <TimelinePage />
        <LocationProbe />
      </MemoryRouter>,
    );

    const internalLens = screen.getByRole("button", { name: "Show internal activity" });
    expect(internalLens.getAttribute("aria-pressed")).toBe("false");

    fireEvent.click(internalLens);

    expect(internalLens.getAttribute("aria-pressed")).toBe("true");
    const enabled = new URLSearchParams(screen.getByTestId("timeline-location").textContent ?? "");
    expect(enabled.get("internal")).toBe("1");
    expect(enabled.get("butler")).toBe("home");
    expect(enabled.get("type")).toBe("session");

    fireEvent.click(internalLens);

    expect(internalLens.getAttribute("aria-pressed")).toBe("false");
    const disabled = new URLSearchParams(screen.getByTestId("timeline-location").textContent ?? "");
    expect(disabled.get("internal")).toBeNull();
  });

  it("forwards a trace URL scope to the timeline ledger", () => {
    setLedger({});

    render("/timeline?trace=trace-001");

    expect(useTimelineLedger).toHaveBeenLastCalledWith({
      butler: undefined,
      event_type: undefined,
      trace: "trace-001",
      since: undefined,
      until: undefined,
    }, { enabled: true });
  });

  it("does not present a whitespace trace query as an active scope", () => {
    setLedger({});

    const html = render("/timeline?trace=%20%20");

    expect(html).not.toContain('data-testid="trace-scope-banner"');
    expect(useTimelineLedger).toHaveBeenLastCalledWith({
      butler: undefined,
      event_type: undefined,
      trace: undefined,
      since: undefined,
      until: undefined,
    }, { enabled: true });
  });

  it("names a trace scope, explains notification coverage, and lets the operator clear it", () => {
    setLedger({});

    renderDom(
      <MemoryRouter
        initialEntries={["/timeline?trace=trace-001&butler=home,general&type=session&view=errors"]}
      >
        <TimelinePage />
        <LocationProbe />
      </MemoryRouter>,
    );

    const banner = screen.getByTestId("trace-scope-banner");
    expect(banner.textContent).toContain("Scoped to trace trace-001");
    expect(banner.textContent).toContain("Matching sessions and trace-attributed notifications.");

    fireEvent.click(screen.getByRole("button", { name: "Clear trace filter" }));

    expect(screen.queryByTestId("trace-scope-banner")).toBeNull();
    const params = new URLSearchParams(screen.getByTestId("timeline-location").textContent ?? "");
    expect(params.get("trace")).toBeNull();
    expect(params.get("butler")).toBe("home,general");
    expect(params.get("type")).toBe("session");
    expect(params.get("view")).toBe("errors");
    expect(useTimelineLedger).toHaveBeenLastCalledWith({
      butler: ["home", "general"],
      event_type: ["session"],
      trace: undefined,
      since: undefined,
      until: undefined,
    }, { enabled: true });
  });

  it("renders the new-events pill only when newCount is positive", () => {
    setLedger({ newCount: 0 });
    expect(render()).not.toContain('data-testid="new-events-pill"');

    setLedger({ newCount: 3 });
    const html = render();
    expect(html).toContain('data-testid="new-events-pill"');
    expect(html).toContain("3 new events");
  });

  // A dead API after the first successful paint must not look like a quiet
  // fleet -- both used to render the same muted "Idle" dot (bu-qvnce.2).
  it("renders the live-status badge as Down when the head poll is failing, even with stale events on screen", () => {
    setLedger({
      isLiveFeedDown: true,
      isError: false,
      events: [
        {
          id: "e1",
          type: "session",
          butler: "home",
          timestamp: "2026-07-04T14:32:00Z",
          summary: "event e1",
          is_heartbeat: false,
          data: {},
        },
      ],
    });
    const html = render();
    expect(html).toContain('data-testid="live-status-badge-down"');
    expect(html).not.toContain('data-testid="live-status-badge-idle"');
    expect(html).not.toContain('data-testid="live-status-badge-live"');
  });

  it("renders the live-status badge as Idle (not Down) when the feed is merely quiet", () => {
    setLedger({ isLiveFeedDown: false, events: [] });
    const html = render();
    expect(html).toContain('data-testid="live-status-badge-idle"');
    expect(html).not.toContain('data-testid="live-status-badge-down"');
  });

  it("does not dim committed history while an unpinned head poll refreshes", () => {
    setLedger({
      pinned: false,
      isFetching: true,
      events: [
        {
          id: "e1",
          type: "session",
          butler: "home",
          timestamp: "2026-07-04T14:32:00Z",
          summary: "committed event",
          is_heartbeat: false,
          data: {},
        },
      ],
    });

    const html = render();

    expect(html).toContain('aria-busy="false"');
    expect(html).not.toContain("opacity-60");
  });
});

describe("TimelinePage — density and historical seek", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  beforeEach(() => {
    vi.mocked(useButlers).mockReturnValue({ data: { data: [] } } as unknown as ReturnType<
      typeof useButlers
    >);
    vi.mocked(useTimelineSavedViews).mockReturnValue({ data: { data: [] } } as unknown as ReturnType<
      typeof useTimelineSavedViews
    >);
    vi.mocked(useCreateTimelineSavedView).mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useCreateTimelineSavedView>);
    vi.mocked(useDeleteTimelineSavedView).mockReturnValue({
      mutate: vi.fn(),
    } as unknown as ReturnType<typeof useDeleteTimelineSavedView>);
    setLedger({});
  });

  it("atomically materializes an implicit chart window when selecting a minute and reloads that scope", async () => {
    vi.spyOn(Date, "now").mockReturnValue(Date.parse("2026-07-04T15:20:00Z"));
    vi.mocked(useTimelineHistogram).mockReturnValue({
      data: {
        data: [
          { start: "2026-07-04T14:00:00Z", end: "2026-07-04T14:01:00Z", count: 0 },
          { start: "2026-07-04T14:01:00Z", end: "2026-07-04T14:02:00Z", count: 61 },
        ],
        meta: {
          since: "2026-07-04T14:00:00Z",
          until: "2026-07-04T15:00:00Z",
          bucket_seconds: 60,
          availability: "complete",
          expected_sources: 1,
          healthy_sources: 1,
          degraded_sources: [],
          degraded_butlers: [],
        },
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useTimelineHistogram>);

    const mounted = renderDom(
      <MemoryRouter initialEntries={["/timeline?type=session&trace=trace-001"]}>
        <TimelinePage />
        <LocationProbe />
        <BrowserHistoryControls />
      </MemoryRouter>,
    );
    const bars = screen.getAllByTestId("timeline-density-bucket");
    expect(bars.map((bar) => bar.tabIndex)).toEqual([0, -1]);
    fireEvent.keyDown(bars[0], { key: "ArrowRight" });
    expect(document.activeElement).toBe(bars[1]);
    await userEvent.keyboard("{Enter}");

    const selected = new URLSearchParams(screen.getByTestId("timeline-location").textContent ?? "");
    expect(selected.get("since")).toBe("2026-07-04T14:00:00.000Z");
    expect(selected.get("until")).toBe("2026-07-04T15:00:00.000Z");
    expect(selected.get("bucket_since")).toBe("2026-07-04T14:01:00Z");
    expect(selected.get("bucket_until")).toBe("2026-07-04T14:02:00Z");
    expect(selected.get("type")).toBe("session");
    expect(selected.get("trace")).toBe("trace-001");
    expect(useTimelineLedger).toHaveBeenLastCalledWith(
      expect.objectContaining({
        since: "2026-07-04T14:01:00.000Z",
        until: "2026-07-04T14:02:00.000Z",
        event_type: ["session"],
        trace: "trace-001",
      }),
      { enabled: true },
    );

    fireEvent.click(screen.getByRole("button", { name: "Browser back" }));
    const restoredInitial = new URLSearchParams(
      screen.getByTestId("timeline-location").textContent ?? "",
    );
    expect(restoredInitial.get("bucket_since")).toBeNull();
    expect(restoredInitial.get("type")).toBe("session");
    expect(restoredInitial.get("trace")).toBe("trace-001");

    fireEvent.click(screen.getByRole("button", { name: "Browser forward" }));
    expect(screen.getByTestId("timeline-location").textContent).toBe(`?${selected.toString()}`);

    vi.spyOn(Date, "now").mockReturnValue(Date.parse("2026-07-04T16:20:00Z"));
    const restoredUrl = `/timeline?${selected.toString()}`;
    mounted.unmount();
    renderDom(
      <MemoryRouter initialEntries={[restoredUrl]}>
        <TimelinePage />
      </MemoryRouter>,
    );
    expect(useTimelineLedger).toHaveBeenLastCalledWith(
      expect.objectContaining({
        since: "2026-07-04T14:01:00.000Z",
        until: "2026-07-04T14:02:00.000Z",
      }),
      { enabled: true },
    );
    expect(useTimelineHistogram).toHaveBeenLastCalledWith(
      expect.objectContaining({
        since: "2026-07-04T14:00:00.000Z",
        until: "2026-07-04T15:00:00.000Z",
      }),
      true,
    );
  });

  it("fails closed for malformed or timezone-naive URL intervals and distinguishes aggregate availability", () => {
    setLedger({
      events: [
        {
          id: "cached-live",
          type: "session",
          butler: "home",
          timestamp: "2026-07-04T15:00:00Z",
          summary: "cached live event",
          is_heartbeat: false,
          data: {},
        },
      ],
    });
    const invalidRender = renderDom(
      <MemoryRouter
        initialEntries={["/timeline", "/timeline?since=2026-07-04T14:00:00Z"]}
        initialIndex={0}
      >
        <TimelinePage />
        <BrowserHistoryControls />
      </MemoryRouter>,
    );
    expect(screen.getByText("cached live event")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Browser forward" }));
    expect(screen.getByRole("alert").textContent).toContain("interval in this URL is invalid");
    expect(screen.queryByText("cached live event")).toBeNull();
    expect(screen.queryByText("No events found.")).toBeNull();
    expect(screen.queryByTestId("live-status-badge-idle")).toBeNull();
    expect(screen.queryByTestId("saved-view-all")).toBeNull();
    expect(screen.queryByTestId("timeline-density")).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
    expect(useTimelineLedger).toHaveBeenLastCalledWith(expect.any(Object), { enabled: false });
    expect(useTimelineHistogram).toHaveBeenLastCalledWith(expect.any(Object), false);

    fireEvent.click(screen.getByRole("button", { name: "Browser back" }));
    expect(screen.getByText("cached live event")).toBeTruthy();

    invalidRender.unmount();
    for (const naiveUrl of [
      "/timeline?since=2026-07-04T14:00:00&until=2026-07-04T15:00:00",
      "/timeline?since=2026-07-04T14:00:00Z&until=2026-07-04T15:00:00Z&bucket_since=2026-07-04T14:01:00&bucket_until=2026-07-04T14:02:00",
    ]) {
      const naiveRender = renderDom(
        <MemoryRouter initialEntries={[naiveUrl]}>
          <TimelinePage />
        </MemoryRouter>,
      );
      expect(screen.getByRole("alert").textContent).toContain("interval in this URL is invalid");
      expect(screen.queryByText("cached live event")).toBeNull();
      expect(screen.queryByText("No events found.")).toBeNull();
      expect(screen.queryByTestId("live-status-badge-idle")).toBeNull();
      expect(screen.queryByTestId("saved-view-all")).toBeNull();
      expect(screen.queryByTestId("timeline-density")).toBeNull();
      expect(screen.queryByRole("status")).toBeNull();
      expect(useTimelineLedger).toHaveBeenLastCalledWith(expect.any(Object), { enabled: false });
      expect(useTimelineHistogram).toHaveBeenLastCalledWith(expect.any(Object), false);

      fireEvent.click(screen.getByRole("button", { name: "Clear interval" }));
      expect(screen.queryByRole("alert")).toBeNull();
      expect(screen.getByText("cached live event")).toBeTruthy();
      expect(useTimelineLedger).toHaveBeenLastCalledWith(expect.any(Object), { enabled: true });
      naiveRender.unmount();
    }
  });

  it.each([
    ["healthy-zero", "complete", 0, 1, 1, [], "0 events · 1 of 1 sources available", true],
    [
      "partial", "partial", 3, 2, 1, ["notifications"],
      "3 events · 1 of 2 sources available · partial", true,
    ],
    ["complete", "complete", 3, 2, 2, [], "3 events · 2 of 2 sources available", true],
    [
      "unavailable", "unavailable", 0, 1, 0, ["notifications"],
      "Density unavailable · 0 of 1 sources available", false,
    ],
  ] as const)("renders the %s availability presentation", (
    _, availability, count, expectedSources, healthySources, degradedSources, summary, rendersBuckets,
  ) => {
    vi.mocked(useTimelineHistogram).mockReturnValue({
      data: {
        data: count > 0 || rendersBuckets
          ? [{ start: "2026-07-04T14:00:00Z", end: "2026-07-04T14:01:00Z", count }]
          : [],
        meta: {
          since: "2026-07-04T14:00:00Z",
          until: "2026-07-04T15:00:00Z",
          bucket_seconds: 60,
          availability,
          expected_sources: expectedSources,
          healthy_sources: healthySources,
          degraded_sources: [...degradedSources],
          degraded_butlers: [],
        },
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useTimelineHistogram>);
    renderDom(
      <MemoryRouter initialEntries={["/timeline"]}>
        <TimelinePage />
      </MemoryRouter>,
    );
    expect(screen.getByText(summary)).toBeTruthy();
    expect(screen.queryByTestId("timeline-density-bucket") !== null).toBe(rendersBuckets);
    if (availability === "partial") {
      expect(screen.getByText("Unavailable sources: notifications.")).toBeTruthy();
    }
    if (availability === "unavailable") {
      expect(screen.getByText(/Counts are not shown/)).toBeTruthy();
    }
  });
});

describe("TimelinePage — current failed records", () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    vi.mocked(useButlers).mockReturnValue({ data: { data: [] } } as unknown as ReturnType<typeof useButlers>);
    vi.mocked(useTimelineSavedViews).mockReturnValue({ data: { data: [] } } as unknown as ReturnType<typeof useTimelineSavedViews>);
    vi.mocked(useCreateTimelineSavedView).mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useCreateTimelineSavedView>);
    vi.mocked(useDeleteTimelineSavedView).mockReturnValue({
      mutate: vi.fn(),
    } as unknown as ReturnType<typeof useDeleteTimelineSavedView>);
    setLedger({});
  });

  function response(
    data: TimelineAttentionResponse["data"],
    overrides: Partial<TimelineAttentionResponse["meta"]> = {},
  ): TimelineAttentionResponse {
    return {
      data,
      meta: {
        since: "2026-07-03T14:00:00Z",
        until: "2026-07-04T14:00:00Z",
        failed_sessions: data.filter((item) => item.kind === "session").length,
        failed_notifications: data.filter((item) => item.kind === "notification").length,
        total: data.length,
        has_more: false,
        availability: "complete",
        expected_sources: 2,
        healthy_sources: 2,
        degraded_sources: [],
        degraded_butlers: [],
        ...overrides,
      },
    };
  }

  it("renders source counts and exact session/event destinations without content fields", () => {
    const sessionId = "session-failed-001";
    const notificationId = "notification-failed-001";
    setAttention(
      response([
        { id: sessionId, kind: "session", butler: "home", timestamp: "2026-07-04T13:59:00Z" },
        { id: notificationId, kind: "notification", butler: "atlas", timestamp: "2026-07-04T13:58:00Z" },
      ], { failed_sessions: 3, failed_notifications: 2, total: 5 }),
    );

    renderDom(
      <MemoryRouter
        initialEntries={[
          "/timeline?butler=home,atlas&trace=trace-7&since=2026-07-04T13:00:00Z&until=2026-07-04T14:00:00Z&bucket_since=2026-07-04T13:20:00Z&bucket_until=2026-07-04T13:21:00Z",
        ]}
      >
        <TimelinePage />
      </MemoryRouter>,
    );

    expect(screen.getByText("Recent records marked failed (created in last 24h)")).toBeTruthy();
    expect(screen.getByTestId("timeline-attention-failed-sessions").textContent).toBe("Runs: 3");
    expect(screen.getByTestId("timeline-attention-failed-notifications").textContent).toBe(
      "Delivery records: 2",
    );
    expect(screen.getByTestId("timeline-attention-total").textContent).toBe("Total: 5");

    const sessionLink = screen.getByRole("link", { name: `Inspect failed session ${sessionId}` });
    expect(sessionLink.getAttribute("href")).toBe(`/sessions/${sessionId}?butler=home`);

    const notificationLink = screen.getByRole("link", {
      name: `Inspect failed notification ${notificationId}`,
    });
    const destination = new URL(notificationLink.getAttribute("href")!, "http://test");
    expect(destination.pathname).toBe("/timeline");
    expect(destination.searchParams.get("event")).toBe(notificationId);
    expect(destination.searchParams.get("butler")).toBe("home,atlas");
    expect(destination.searchParams.get("trace")).toBe("trace-7");
    expect(destination.searchParams.get("since")).toBeNull();
    expect(destination.searchParams.get("until")).toBeNull();
    expect(destination.searchParams.get("bucket_since")).toBeNull();
    expect(destination.searchParams.get("bucket_until")).toBeNull();
    expect(screen.getByTestId("timeline-attention-item-notification").textContent).not.toContain("message");
  });

  it("keeps counts and degradation visible when collapsed, shows truncation, and resets expanded on remount", () => {
    const items = Array.from({ length: 5 }, (_, index) => ({
      id: `failed-${index}`,
      kind: "session" as const,
      butler: "home",
      timestamp: `2026-07-04T13:0${index}:00Z`,
    }));
    setAttention(response(items, {
      failed_sessions: 7,
      total: 7,
      has_more: true,
      availability: "partial",
      expected_sources: 2,
      healthy_sources: 1,
      degraded_sources: ["notifications"],
    }));

    const view = renderDom(
      <MemoryRouter initialEntries={["/timeline"]}>
        <TimelinePage />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("timeline-attention-truncated").textContent).toBe("Showing 5 of 7");
    expect(screen.getByTestId("timeline-attention-degraded").textContent).toContain("notifications");

    fireEvent.click(screen.getByRole("button", { name: "Hide details" }));
    expect(screen.getByRole("button", { name: "Show details" }).getAttribute("aria-expanded")).toBe("false");
    expect(screen.getByTestId("timeline-attention-total").textContent).toBe("Total: 7");
    expect(screen.getByTestId("timeline-attention-degraded").textContent).toContain("notifications");
    expect(screen.getAllByTestId("timeline-attention-item-session")[0].closest("[hidden]")).not.toBeNull();

    view.unmount();
    renderDom(
      <MemoryRouter initialEntries={["/timeline"]}>
        <TimelinePage />
      </MemoryRouter>,
    );
    expect(screen.getByRole("button", { name: "Hide details" }).getAttribute("aria-expanded")).toBe("true");
    expect(screen.getAllByTestId("timeline-attention-item-session")).toHaveLength(5);
  });

  it.each([
    ["healthy empty", response([]), "No matching records currently marked failed", false],
    [
      "partial",
      response([], {
        availability: "partial",
        expected_sources: 2,
        healthy_sources: 1,
        degraded_sources: ["sessions"],
      }),
      "No complete failure count is available while a source is unavailable.",
      false,
    ],
    [
      "unavailable",
      response([], {
        availability: "unavailable",
        expected_sources: 2,
        healthy_sources: 0,
        degraded_sources: ["sessions", "notifications"],
      }),
      "Recent failed records are unavailable.",
      true,
    ],
  ] as const)("renders the %s current-status state without a false all-clear", (_, data, copy, retryable) => {
    setAttention(data);
    renderDom(
      <MemoryRouter initialEntries={["/timeline"]}>
        <TimelinePage />
      </MemoryRouter>,
    );

    if (retryable) {
      expect(screen.getByTestId("timeline-attention-unavailable").textContent).toContain(copy);
    } else {
      expect(screen.getByText(copy)).toBeTruthy();
    }
    if (data.meta.availability === "complete") {
      expect(screen.getByTestId("timeline-attention-empty")).toBeTruthy();
    } else {
      expect(screen.queryByTestId("timeline-attention-empty")).toBeNull();
    }
  });

  it("keeps the last successful rows visibly stale and retryable after a refresh failure", () => {
    const retry = vi.fn();
    const data = response([
      { id: "stale-failure", kind: "session", butler: "home", timestamp: "2026-07-04T13:59:00Z" },
    ]);
    setAttention(data, { isError: true, refetch: retry });

    renderDom(
      <MemoryRouter initialEntries={["/timeline"]}>
        <TimelinePage />
      </MemoryRouter>,
    );

    expect(screen.getByTestId("timeline-attention-refresh-error").textContent).toContain(
      "Showing the last successful read",
    );
    expect(screen.getByTestId("timeline-attention-item-session")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalledOnce();
  });
});

// ---------------------------------------------------------------------------
// Hot-loop keyboard coverage (bu-ep4ks.12): the densest telemetry page had
// zero useRegisterShortcut bindings.
// ---------------------------------------------------------------------------

describe("TimelinePage — keyboard shortcuts (bu-ep4ks.12)", () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    vi.mocked(useButlers).mockReturnValue({
      data: { data: [] },
    } as unknown as ReturnType<typeof useButlers>);
    vi.mocked(useTimelineSavedViews).mockReturnValue({
      data: { data: [] },
    } as unknown as ReturnType<typeof useTimelineSavedViews>);
    vi.mocked(useCreateTimelineSavedView).mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useCreateTimelineSavedView>);
    vi.mocked(useDeleteTimelineSavedView).mockReturnValue({
      mutate: vi.fn(),
    } as unknown as ReturnType<typeof useDeleteTimelineSavedView>);
  });

  function renderPage() {
    return renderDom(
      <MemoryRouter initialEntries={["/timeline"]}>
        <TimelinePage />
      </MemoryRouter>,
    );
  }

  it("r refreshes the timeline", () => {
    const refetch = vi.fn();
    setLedger({ refetch });
    renderPage();

    fireEvent.keyDown(window, { key: "r" });

    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("n jumps to the latest events only when there are new ones to jump to", () => {
    const showNewEvents = vi.fn();
    setLedger({ newCount: 0, showNewEvents });
    const { unmount } = renderPage();

    fireEvent.keyDown(window, { key: "n" });
    expect(showNewEvents).not.toHaveBeenCalled();
    unmount();

    setLedger({ newCount: 2, showNewEvents });
    renderPage();
    fireEvent.keyDown(window, { key: "n" });
    expect(showNewEvents).toHaveBeenCalledTimes(1);
  });
});
