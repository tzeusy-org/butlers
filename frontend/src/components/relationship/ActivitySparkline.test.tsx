// @vitest-environment jsdom
/**
 * Component tests for ActivitySparkline (entity v3 sparkline, bu-xzh76).
 *
 * Covers:
 * - 90 sticks rendered, quiet days honestly at 4% opacity (no day omitted);
 * - count caption reflects total + active days;
 * - empty window renders the canned serif line, not an empty chart;
 * - loading + error states.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

vi.mock("@/hooks/use-entities", () => ({
  useEntityActivityBins: vi.fn(),
}));

import { useEntityActivityBins } from "@/hooks/use-entities";
import type { ActivityBin } from "@/api/types";
import { ActivitySparkline } from "./ActivitySparkline";

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
}

let container: HTMLDivElement;
let root: Root;

function render() {
  const qc = makeQueryClient();
  act(() => {
    root.render(
      <QueryClientProvider client={qc}>
        <ActivitySparkline entityId="ent-1" />
      </QueryClientProvider>,
    );
  });
}

function mockBins(bins: ActivityBin[]) {
  vi.mocked(useEntityActivityBins).mockReturnValue({
    data: { bins },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useEntityActivityBins>);
}

/** Build a 90-day dense series with activity on the given count of days. */
function denseSeries(activeDayIndices: number[]): ActivityBin[] {
  const bins: ActivityBin[] = [];
  for (let i = 0; i < 90; i++) {
    const day = new Date(2026, 0, 1 + i).toISOString().slice(0, 10);
    bins.push({ date: day, count: activeDayIndices.includes(i) ? 1 : 0 });
  }
  return bins;
}

beforeEach(() => {
  vi.resetAllMocks();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("ActivitySparkline", () => {
  it("renders 90 sticks with 87 quiet at 4% opacity when 3 days have activity", () => {
    mockBins(denseSeries([0, 45, 89]));
    render();

    const sticks = container.querySelectorAll('[data-testid="sparkline-stick"]');
    expect(sticks.length).toBe(90);

    const quiet = container.querySelectorAll('[data-quiet="true"]');
    expect(quiet.length).toBe(87);
    // Quiet days honestly dimmed, never collapsed out.
    expect((quiet[0] as HTMLElement).style.opacity).toBe("0.04");
  });

  it("caption reports total events and active days (tabular nums)", () => {
    mockBins(denseSeries([0, 45, 89]));
    render();
    const text = container.textContent ?? "";
    expect(text).toContain("3 events");
    expect(text).toContain("3 active days");
  });

  it("renders the canned serif line when there is no activity", () => {
    mockBins(denseSeries([]));
    render();
    expect(container.querySelector('[data-testid="sparkline-empty"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="activity-sparkline"]')).toBeNull();
    expect(container.textContent).toContain("No activity in the last 90 days.");
  });

  it("shows a loading placeholder while fetching", () => {
    vi.mocked(useEntityActivityBins).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
    } as unknown as ReturnType<typeof useEntityActivityBins>);
    render();
    expect(container.querySelector('[data-testid="sparkline-loading"]')).not.toBeNull();
  });

  it("renders nothing on error", () => {
    vi.mocked(useEntityActivityBins).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    } as unknown as ReturnType<typeof useEntityActivityBins>);
    render();
    expect(container.innerHTML).toBe("");
  });
});
