import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import TimelinePage from "@/pages/TimelinePage";
import { useTimeline } from "@/hooks/use-timeline";
import { useButlers } from "@/hooks/use-butlers";

vi.mock("@/hooks/use-timeline", () => ({
  useTimeline: vi.fn(),
}));

vi.mock("@/hooks/use-butlers", () => ({
  useButlers: vi.fn(),
}));

type UseTimelineResult = ReturnType<typeof useTimeline>;
type UseButlersResult = ReturnType<typeof useButlers>;

function setTimeline(partial: Partial<UseTimelineResult>): void {
  vi.mocked(useTimeline).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
    ...partial,
  } as unknown as UseTimelineResult);
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
    } as unknown as UseButlersResult);
  });

  it("renders the error state (not the empty state) when the timeline query fails", () => {
    setTimeline({ isError: true, data: undefined });
    const html = render();
    expect(html).toContain("Could not load the timeline.");
    expect(html).toContain("Retry");
    // A fetch FAILURE must not masquerade as genuine no-activity.
    expect(html).not.toContain("No events found.");
  });

  it("renders the empty state only on a successful fetch with zero events", () => {
    setTimeline({
      isError: false,
      data: { data: [], meta: { cursor: null, has_more: false } },
    });
    const html = render();
    expect(html).toContain("No events found.");
    expect(html).not.toContain("Could not load the timeline.");
  });
});
