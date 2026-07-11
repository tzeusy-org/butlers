// @vitest-environment jsdom
/**
 * CostStripeChart (bu-86c4c.1 truth amnesty; bu-86c4c.11 real per-butler stack).
 *
 * Regression guard (bu-86c4c.1): this chart used to split each day's real
 * total into per-butler stripes by applying the *period-aggregate* by_butler
 * proportions uniformly across every day — every bar ended up with
 * identical butler ratios, fabricating a per-day distribution that never
 * existed. It was demoted to one honest total bar per day.
 *
 * bu-86c4c.11: GET /api/spend/daily now returns real `by_butler` per day, so
 * the chart stacks those real values — verified here via one mocked <Bar>
 * per distinct butler dataKey, not via fabricated proportional math.
 * recharts is mocked (same pattern as SessionStripeChart.test.tsx) because
 * ResponsiveContainer renders zero-size content under jsdom without a real
 * ResizeObserver.
 */

import * as React from "react"
import { describe, expect, it, afterEach, vi } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"

vi.mock("recharts", () => {
  const BarChart = ({ children }: { children?: React.ReactNode }) =>
    React.createElement("div", { "data-testid": "recharts-bar-chart" }, children)
  const Bar = ({ dataKey }: { dataKey: string }) =>
    React.createElement("div", { "data-testid": `recharts-bar-${dataKey}` })
  const XAxis = () => null
  const YAxis = () => null
  const Tooltip = () => null
  const Legend = () =>
    React.createElement("div", { "data-testid": "recharts-legend" })
  const ResponsiveContainer = ({ children }: { children?: React.ReactNode }) =>
    React.createElement("div", { "data-testid": "recharts-responsive-container" }, children)

  return { BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer }
})

import { CostStripeChart } from "./CostStripeChart"
import type { DailySpend } from "@/api/types"

afterEach(() => {
  cleanup()
})

const DATA: DailySpend[] = [
  { date: "2026-05-01", cost_usd: 0.1, sessions: 1, input_tokens: 100, output_tokens: 50 },
  { date: "2026-05-02", cost_usd: 0.4, sessions: 2, input_tokens: 200, output_tokens: 100 },
]

const DATA_WITH_BUTLERS: DailySpend[] = [
  {
    date: "2026-05-01",
    cost_usd: 0.3,
    sessions: 2,
    input_tokens: 200,
    output_tokens: 100,
    by_butler: { general: 0.2, memory: 0.1 },
  },
  {
    date: "2026-05-02",
    cost_usd: 0.1,
    sessions: 1,
    input_tokens: 100,
    output_tokens: 50,
    by_butler: { general: 0.1 },
  },
]

describe("CostStripeChart", () => {
  it("renders a single honest total bar when no by_butler data is present", () => {
    render(<CostStripeChart data={DATA} />)
    expect(screen.getByTestId("cost-stripe-chart")).toBeTruthy()
    // Exactly one series (cost_usd) — no fabricated stripes, no legend.
    expect(screen.getByTestId("recharts-bar-cost_usd")).toBeTruthy()
    expect(screen.queryByTestId("recharts-legend")).toBeNull()
  })

  it("stacks one real bar series per distinct butler when by_butler is present", () => {
    render(<CostStripeChart data={DATA_WITH_BUTLERS} />)
    expect(screen.getByTestId("cost-stripe-chart")).toBeTruthy()
    // Two distinct butlers (general, memory) across the series -> two real
    // stacked <Bar> series (largest-total-first), plus a legend.
    expect(screen.getByTestId("recharts-bar-general")).toBeTruthy()
    expect(screen.getByTestId("recharts-bar-memory")).toBeTruthy()
    expect(screen.queryByTestId("recharts-bar-cost_usd")).toBeNull()
    expect(screen.getByTestId("recharts-legend")).toBeTruthy()
  })

  it("shows the empty state for an empty series", () => {
    render(<CostStripeChart data={[]} />)
    expect(screen.getByTestId("cost-stripe-empty")).toBeTruthy()
  })

  it("shows the error state honestly on fetch failure", () => {
    render(<CostStripeChart data={[]} isError />)
    expect(screen.getByTestId("cost-stripe-error")).toBeTruthy()
  })

  // -------------------------------------------------------------------------
  // Degraded-source honesty (bu-jad4j.3): GET /api/spend/daily drops any butler
  // whose cost source failed from the fan-out and names it in
  // meta.unavailable_butlers. A dropped butler is simply missing from the stack,
  // so the chart under-represents real spend — it must footnote the vanished
  // butlers rather than let them silently disappear, and an all-butlers-down
  // outage must name the source rather than read as a genuine "$0" window.
  // -------------------------------------------------------------------------

  it("footnotes butlers dropped from the fan-out alongside the populated chart", () => {
    render(<CostStripeChart data={DATA_WITH_BUTLERS} unavailableButlers={["finance", "home"]} />)
    // The chart still renders its (partial) bars...
    expect(screen.getByTestId("cost-stripe-chart")).toBeTruthy()
    // ...and a named degraded footnote qualifies them — never suppressed.
    const note = screen.getByTestId("cost-stripe-unavailable")
    expect(note.getAttribute("role")).toBe("alert")
    expect(note.textContent).toContain("finance, home")
  })

  it("names the vanished source instead of the calm empty line when the outage empties the series", () => {
    // Every butler dropped out → empty series. This is an outage, not a genuine
    // $0 window, so the calm "No cost data" empty state must NOT appear.
    render(<CostStripeChart data={[]} unavailableButlers={["finance", "home"]} />)
    expect(screen.queryByTestId("cost-stripe-empty")).toBeNull()
    const note = screen.getByTestId("cost-stripe-unavailable")
    expect(note.getAttribute("role")).toBe("alert")
    expect(note.textContent).toContain("finance, home")
  })

  it("shows no footnote on the happy path (unavailableButlers absent/empty)", () => {
    // Mutation guard: the footnote must depend on the flag. With every butler
    // present, the chart renders clean and the empty series keeps its honest
    // empty state.
    render(<CostStripeChart data={DATA_WITH_BUTLERS} />)
    expect(screen.queryByTestId("cost-stripe-unavailable")).toBeNull()

    cleanup()
    render(<CostStripeChart data={[]} unavailableButlers={[]} />)
    expect(screen.getByTestId("cost-stripe-empty")).toBeTruthy()
    expect(screen.queryByTestId("cost-stripe-unavailable")).toBeNull()
  })
})
