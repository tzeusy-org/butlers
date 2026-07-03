// @vitest-environment jsdom
/**
 * CostStripeChart (bu-86c4c.1 — truth amnesty).
 *
 * Regression guard: this chart used to split each day's real total into
 * per-butler stripes by applying the *period-aggregate* by_butler
 * proportions uniformly across every day — every bar ended up with
 * identical butler ratios, fabricating a per-day distribution that never
 * existed. It no longer accepts a byButler prop at all; it renders one
 * honest total bar per day.
 */

import { describe, expect, it, afterEach } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { CostStripeChart } from "./CostStripeChart";
import type { DailySpend } from "@/api/types";

afterEach(() => {
  cleanup();
});

const DATA: DailySpend[] = [
  { date: "2026-05-01", cost_usd: 0.1, sessions: 1, input_tokens: 100, output_tokens: 50 },
  { date: "2026-05-02", cost_usd: 0.4, sessions: 2, input_tokens: 200, output_tokens: 100 },
];

describe("CostStripeChart", () => {
  it("renders the real daily total series without a byButler prop", () => {
    render(<CostStripeChart data={DATA} />);
    expect(screen.getByTestId("cost-stripe-chart")).toBeTruthy();
  });

  it("shows the empty state for an empty series", () => {
    render(<CostStripeChart data={[]} />);
    expect(screen.getByTestId("cost-stripe-empty")).toBeTruthy();
  });

  it("shows the error state honestly on fetch failure", () => {
    render(<CostStripeChart data={[]} isError />);
    expect(screen.getByTestId("cost-stripe-error")).toBeTruthy();
  });
});
