// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { cleanup, fireEvent, render as renderDom, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation, useNavigate } from "react-router";

import TimelinePage from "@/pages/TimelinePage";
import { useTimelineLedger } from "@/hooks/use-timeline-ledger";
import { useTimelineHistogram } from "@/hooks/use-timeline";
import { useButlers } from "@/hooks/use-butlers";
import {
  useTimelineSavedViews,
  useCreateTimelineSavedView,
  useDeleteTimelineSavedView,
} from "@/hooks/use-timeline-saved-views";

vi.mock("@/hooks/use-timeline-ledger", () => ({
  useTimelineLedger: vi.fn(),
}));
vi.mock("@/hooks/use-timeline", () => ({ useTimelineHistogram: vi.fn() }));

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

function BrowserBack() {
  const navigate = useNavigate();
  return <button type="button" onClick={() => navigate(-1)}>Browser back</button>;
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
        <BrowserBack />
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
  });

  it("fails closed for malformed URL intervals and distinguishes aggregate availability", () => {
    const invalidRender = renderDom(
      <MemoryRouter initialEntries={["/timeline?since=2026-07-04T14:00:00Z"]}>
        <TimelinePage />
      </MemoryRouter>,
    );
    expect(screen.getByRole("alert").textContent).toContain("interval in this URL is invalid");
    expect(useTimelineLedger).toHaveBeenLastCalledWith(expect.any(Object), { enabled: false });
    expect(useTimelineHistogram).toHaveBeenLastCalledWith(expect.any(Object), false);

    vi.mocked(useTimelineHistogram).mockReturnValue({
      data: {
        data: [],
        meta: {
          since: "2026-07-04T14:00:00Z",
          until: "2026-07-04T15:00:00Z",
          bucket_seconds: 60,
          availability: "unavailable",
          expected_sources: 1,
          healthy_sources: 0,
          degraded_sources: ["notifications"],
          degraded_butlers: [],
        },
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useTimelineHistogram>);
    invalidRender.unmount();
    renderDom(
      <MemoryRouter initialEntries={["/timeline"]}>
        <TimelinePage />
      </MemoryRouter>,
    );
    expect(screen.getByText(/Density unavailable/)).toBeTruthy();
    expect(screen.getByText(/Counts are not shown/)).toBeTruthy();
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
