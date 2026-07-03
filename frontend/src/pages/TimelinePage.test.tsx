import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

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
    ...partial,
  } as unknown as UseTimelineLedgerResult);
}

function render(): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <TimelinePage />
    </MemoryRouter>,
  );
}

describe("TimelinePage — error vs empty state", () => {
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

  it("renders the new-events pill only when newCount is positive", () => {
    setLedger({ newCount: 0 });
    expect(render()).not.toContain('data-testid="new-events-pill"');

    setLedger({ newCount: 3 });
    const html = render();
    expect(html).toContain('data-testid="new-events-pill"');
    expect(html).toContain("3 new events");
  });
});
