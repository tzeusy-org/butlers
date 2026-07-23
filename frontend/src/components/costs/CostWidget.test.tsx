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
import type { DailySpend, UnpricedModelUsage } from "@/api/types";

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

const UNPRICED: UnpricedModelUsage[] = [
  {
    model: "unknown-executed-model",
    calls: 3,
    input_tokens: 100,
    output_tokens: 50,
    cached_input_tokens: 0,
    cache_creation_tokens: 0,
  },
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

  it("renders a direct summary failure as unavailable instead of a fallback total or top butler", () => {
    renderWidget({
      totalCostUsd: 0,
      topButler: "general",
      topButlerCost: 0.9,
      isUnavailable: true,
      dailyCosts: DAILY,
    });

    expect(screen.getByTestId("cost-widget-summary-unavailable")).toBeTruthy();
    expect(screen.queryByText("$0.00")).toBeNull();
    expect(screen.queryByText(/Top: general/)).toBeNull();
    expect(screen.queryByTestId("cost-widget-source-unavailable")).toBeNull();
  });

  it("keeps a successful zero-cost summary calm", () => {
    renderWidget({ totalCostUsd: 0, topButler: null, dailyCosts: DAILY });

    expect(screen.getByText("$0.00")).toBeTruthy();
    expect(screen.queryByTestId("cost-widget-summary-unavailable")).toBeNull();
    expect(screen.queryByText(/^Top:/)).toBeNull();
  });

  it("never renders a nonzero total as $0.00", () => {
    renderWidget({ totalCostUsd: 0.004 });

    expect(screen.getByText("<$0.01")).toBeTruthy();
  });

  it("does not present a priced subtotal as today's total when model pricing is absent", () => {
    renderWidget({ dailyCosts: DAILY, unpricedModels: UNPRICED });

    expect(screen.getByTestId("cost-widget-unpriced").textContent).toContain("—/unpriced");
    expect(screen.getByText("3 unpriced calls excluded")).toBeTruthy();
    expect(screen.queryByText("$1.50")).toBeNull();
    expect(screen.queryByText(/Top: general/)).toBeNull();
  });

  it("labels a partial daily trend even when today's subtotal is fully priced", () => {
    renderWidget({ dailyCosts: DAILY, dailyUnpricedModels: UNPRICED });

    expect(screen.getByTestId("cost-widget-trend-unpriced").textContent).toContain(
      "3 unpriced calls",
    );
    expect(screen.getByTestId("cost-widget-sparkline")).toBeTruthy();
  });
});
