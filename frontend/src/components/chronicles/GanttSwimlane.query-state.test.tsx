// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

vi.mock("@/hooks/use-chronicles", () => ({
  useChroniclesEpisodes: vi.fn(),
}));

// These tests exercise the wrapper's query-state policy. The SVG renderer has
// focused coverage in GanttSwimlane.test.tsx; keeping it as a small stand-in
// here avoids coupling retained-data coverage to code-split loading timing.
vi.mock("./GanttSwimlaneInner", () => ({
  GanttSwimlaneInner: ({ episodes }: { episodes: ChroniclerEpisode[] }) => {
    if (episodes.length === 0) {
      return <div data-testid="gantt-empty" />
    }

    return (
      <div data-testid="gantt-inner">
        {episodes.map((episode) => (
          <div key={episode.id} data-testid={`gantt-bar-${episode.id}`} />
        ))}
      </div>
    )
  },
}));

import type { ChroniclerEpisode } from "@/api/types";
import { useChroniclesEpisodes } from "@/hooks/use-chronicles";
import { GanttSwimlane } from "./GanttSwimlane";

const WINDOW_START = new Date("2026-04-25T00:00:00Z");
const WINDOW_END = new Date("2026-04-25T23:59:59Z");

function makeEpisode(id: string): ChroniclerEpisode {
  return {
    id,
    source_name: "core.sessions",
    source_ref: id,
    episode_type: "work",
    start_at: "2026-04-25T09:00:00Z",
    end_at: "2026-04-25T10:00:00Z",
    precision: "minute",
    title: null,
    payload: {},
    privacy: "normal",
    retention_days: null,
    tombstone_at: null,
    canonical_start_at: "2026-04-25T09:00:00Z",
    canonical_end_at: "2026-04-25T10:00:00Z",
    canonical_title: null,
    canonical_privacy: "normal",
    corrected_at: null,
    correction_note: null,
    created_at: "2026-04-25T00:00:00Z",
    updated_at: "2026-04-25T00:00:00Z",
    category: "work",
  };
}

function renderGantt() {
  return render(<GanttSwimlane windowStart={WINDOW_START} windowEnd={WINDOW_END} />);
}

describe("GanttSwimlane query states", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it("shows the accessible loading skeleton before episodes resolve", () => {
    vi.mocked(useChroniclesEpisodes).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useChroniclesEpisodes>);

    renderGantt();

    expect(screen.getByRole("status", { name: "Loading Gantt chart" })).not.toBeNull();
    expect(screen.queryByTestId("gantt-empty")).toBeNull();
  });

  it("names an initial unavailable episodes request, offers Retry, and never claims an empty window", () => {
    const refetch = vi.fn();
    vi.mocked(useChroniclesEpisodes).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch,
    } as unknown as ReturnType<typeof useChroniclesEpisodes>);

    renderGantt();

    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("Gantt timeline: unavailable");
    const retryButtons = screen.getAllByRole("button", { name: "Retry" });
    expect(retryButtons).toHaveLength(1);
    fireEvent.click(retryButtons[0]);
    expect(refetch).toHaveBeenCalledOnce();
    expect(screen.queryByTestId("gantt-empty")).toBeNull();
  });

  it("preserves cached episode bars and renders one degraded retry alert when refresh fails", async () => {
    const refetch = vi.fn();
    vi.mocked(useChroniclesEpisodes).mockReturnValue({
      data: { data: [makeEpisode("cached-episode")], meta: {} },
      isLoading: false,
      isError: true,
      refetch,
    } as unknown as ReturnType<typeof useChroniclesEpisodes>);

    renderGantt();

    expect(await screen.findByTestId("gantt-bar-cached-episode")).not.toBeNull();
    expect(screen.queryByTestId("gantt-skeleton")).toBeNull();
    const alerts = screen.getAllByRole("alert");
    expect(alerts).toHaveLength(1);
    expect(alerts[0].textContent).toContain(
      "Gantt timeline: unavailable; showing last loaded data",
    );
    const retryButtons = screen.getAllByRole("button", { name: "Retry" });
    expect(retryButtons).toHaveLength(1);
    fireEvent.click(retryButtons[0]);
    expect(refetch).toHaveBeenCalledOnce();
    expect(screen.queryByTestId("gantt-empty")).toBeNull();
  });

  it("keeps the current calm empty state only for a settled healthy empty response", async () => {
    vi.mocked(useChroniclesEpisodes).mockReturnValue({
      data: { data: [], meta: {} },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useChroniclesEpisodes>);

    renderGantt();

    expect(await screen.findByTestId("gantt-empty")).not.toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByTestId("gantt-skeleton")).toBeNull();
  });
});
