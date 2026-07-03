// @vitest-environment jsdom
/**
 * CostWidget — 7-day trend sparkline (bu-86c4c.1 — truth amnesty).
 *
 * Regression guard: the sparkline used to fabricate bar heights from a
 * pseudo-random formula (`20 + ((i * 37 + 13) % 80)`), labeled "7-day trend"
 * as if it were real data. It must now render the real daily cost series
 * passed in, or an honest "unavailable" note when that data is absent/erred.
 */

import { describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach } from "vitest";
import { MemoryRouter } from "react-router";

import CostWidget from "./CostWidget";
import type { DailySpend } from "@/api/types";

afterEach(() => {
  cleanup();
});

function renderWidget(props: Partial<React.ComponentProps<typeof CostWidget>> = {}) {
  return render(
    <MemoryRouter>
      <CostWidget
        totalCostUsd={1.5}
        topButler="general"
        topButlerCost={0.9}
        {...props}
      />
    </MemoryRouter>,
  );
}

const DAILY: DailySpend[] = [
  { date: "2026-05-01", cost_usd: 0.1, sessions: 1, input_tokens: 100, output_tokens: 50 },
  { date: "2026-05-02", cost_usd: 0.4, sessions: 2, input_tokens: 200, output_tokens: 100 },
  { date: "2026-05-03", cost_usd: 0.2, sessions: 1, input_tokens: 100, output_tokens: 50 },
];

describe("CostWidget — trend sparkline", () => {
  it("renders one bar per real daily-cost entry, not a fixed 7-bar fabrication", () => {
    const { container } = renderWidget({ dailyCosts: DAILY });

    const sparkline = screen.getByTestId("cost-widget-sparkline");
    expect(sparkline.children.length).toBe(DAILY.length);
    // The tallest bar corresponds to the real max-cost day (0.4), not an
    // arbitrary pseudo-random height.
    const bars = Array.from(sparkline.children) as HTMLElement[];
    expect(bars[1].style.height).toBe("100%");
    expect(container.textContent).not.toContain("Tool #");
  });

  it("shows an honest unavailable note when no daily data is provided", () => {
    renderWidget({ dailyCosts: undefined });

    expect(screen.getByTestId("cost-widget-trend-unavailable")).toBeTruthy();
    expect(screen.queryByTestId("cost-widget-sparkline")).toBeNull();
  });

  it("shows an honest unavailable note when the daily-cost source errored", () => {
    renderWidget({ dailyCosts: DAILY, dailyCostsError: true });

    expect(screen.getByTestId("cost-widget-trend-unavailable")).toBeTruthy();
    expect(screen.queryByTestId("cost-widget-sparkline")).toBeNull();
  });

  it("never renders a nonzero total as $0.00", () => {
    renderWidget({ totalCostUsd: 0.004 });

    expect(screen.getByText("<$0.01")).toBeTruthy();
  });
});
