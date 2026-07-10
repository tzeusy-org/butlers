import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import type { ChroniclerBalanceResponse } from "@/api/types"
import { BalanceRings } from "./BalanceRings"

function makeResponse(
  overrides: Partial<ChroniclerBalanceResponse> = {},
): ChroniclerBalanceResponse {
  return {
    local_date: "2026-04-25",
    timezone: "Asia/Singapore",
    status: "materialized",
    baseline_lookback_days: 28,
    lanes: [
      {
        lane: "work",
        seconds: 5 * 3600,
        baseline_seconds: 4 * 3600,
        delta_seconds: 3600,
        baseline_sample_days: 20,
        unavailable: false,
      },
    ],
    balance_source_error: false,
    ...overrides,
  }
}

describe("BalanceRings — states", () => {
  it("renders a skeleton while loading", () => {
    const html = renderToStaticMarkup(<BalanceRings data={undefined} isLoading />)
    expect(html).toContain("balance-skeleton")
  })

  it("renders a degraded note on isError (never a truthful-empty ring set)", () => {
    const html = renderToStaticMarkup(<BalanceRings data={undefined} isError />)
    expect(html).toContain("Balance vs usual")
    expect(html).toContain("role=\"alert\"")
  })

  it("renders a degraded note when balance_source_error is true", () => {
    const html = renderToStaticMarkup(
      <BalanceRings data={makeResponse({ balance_source_error: true, lanes: [] })} />,
    )
    expect(html).toContain("Balance vs usual")
  })

  it("renders a not-settled note for not_yet_materialized (not degraded, not zero)", () => {
    const html = renderToStaticMarkup(
      <BalanceRings data={makeResponse({ status: "not_yet_materialized", lanes: [] })} />,
    )
    expect(html).toContain("balance-not-settled")
    expect(html).not.toContain("role=\"alert\"")
  })

  it("renders a ring with a signed delta for an active lane", () => {
    const html = renderToStaticMarkup(<BalanceRings data={makeResponse()} />)
    expect(html).toContain("balance-ring-work")
    expect(html).toContain("+1h")
  })

  it("renders 'no usual yet' when the baseline is null (never a fake 0 delta)", () => {
    const html = renderToStaticMarkup(
      <BalanceRings
        data={makeResponse({
          lanes: [
            {
              lane: "work",
              seconds: 3600,
              baseline_seconds: null,
              delta_seconds: null,
              baseline_sample_days: 0,
              unavailable: false,
            },
          ],
        })}
      />,
    )
    expect(html).toContain("no usual yet")
  })

  it("renders 'unavailable' for a feeder_dark lane (never a truthful zero)", () => {
    const html = renderToStaticMarkup(
      <BalanceRings
        data={makeResponse({
          lanes: [
            {
              lane: "sleep",
              seconds: 0,
              baseline_seconds: null,
              delta_seconds: null,
              baseline_sample_days: 0,
              unavailable: true,
            },
          ],
        })}
      />,
    )
    expect(html).toContain("balance-ring-sleep-unavailable")
    expect(html).toContain("unavailable")
  })
})
