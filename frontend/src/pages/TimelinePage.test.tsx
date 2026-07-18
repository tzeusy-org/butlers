// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { cleanup, fireEvent, render as renderDom, screen } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router";

import TimelinePage from "@/pages/TimelinePage";
import { useTimelineLedger } from "@/hooks/use-timeline-ledger";
import { useButlers } from "@/hooks/use-butlers";
import {
  useTimelineSavedViews,
  useCreateTimelineSavedView,
  useDeleteTimelineSavedView,
} from "@/hooks/use-timeline-saved-views";

vi.mock("@/hooks/use-timeline-ledger", () => ({
  useTimelineLedger: vi.fn(),
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

function setLedger(partial: Partial<UseTimelineLedgerResult>): void {
  vi.mocked(useTimelineLedger).mockReturnValue({
    events: [],
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
    hasMore: false,
    loadMore: vi.fn(),
    isLoadingMore: false,
    pinned: true,
    newCount: 0,
    showNewEvents: vi.fn(),
    degradedSources: [],
    heartbeatRollup: { ticks: 0, butlers: 0, failed: 0 },
    isLiveFeedDown: false,
    ...partial,
  } as unknown as UseTimelineLedgerResult);
}

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

  it("renders the degraded-sources banner when a source is partial", () => {
    setLedger({ degradedSources: ["notifications"] });
    const html = render();
    expect(html).toContain("Partial data");
    expect(html).toContain("notifications");
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

  it("forwards a trace URL scope to the timeline ledger", () => {
    setLedger({});

    render("/timeline?trace=trace-001");

    expect(useTimelineLedger).toHaveBeenLastCalledWith({
      butler: undefined,
      event_type: undefined,
      trace: "trace-001",
    });
  });

  it("does not present a whitespace trace query as an active scope", () => {
    setLedger({});

    const html = render("/timeline?trace=%20%20");

    expect(html).not.toContain('data-testid="trace-scope-banner"');
    expect(useTimelineLedger).toHaveBeenLastCalledWith({
      butler: undefined,
      event_type: undefined,
      trace: undefined,
    });
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
    });
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
