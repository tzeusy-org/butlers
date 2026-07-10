import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import type { ChroniclerTrendsResponse } from "@/api/types"
import { TrendsLens } from "./TrendsLens"

function makeResponse(
  overrides: Partial<ChroniclerTrendsResponse> = {},
): ChroniclerTrendsResponse {
  return {
    window: "week",
    start_date: "2026-04-19",
    end_date: "2026-04-25",
    tz: "Asia/Singapore",
    baseline_lookback_days: 28,
    lanes: [
      {
        lane: "work",
        days: [
          {
            local_date: "2026-04-24",
            status: "materialized",
            seconds: 4 * 3600,
            baseline_seconds: 3 * 3600,
            delta_seconds: 3600,
            unavailable: false,
          },
          {
            local_date: "2026-04-25",
            status: "materialized",
            seconds: 5 * 3600,
            baseline_seconds: 3 * 3600,
            delta_seconds: 2 * 3600,
            unavailable: false,
          },
        ],
        streak_days: 2,
      },
    ],
    anomalies: [
      {
        lane: "work",
        local_date: "2026-04-25",
        seconds: 5 * 3600,
        baseline_seconds: 3 * 3600,
        delta_seconds: 2 * 3600,
        direction: "spike",
      },
    ],
    trends_source_error: false,
    ...overrides,
  }
}

const noop = () => {}

describe("TrendsLens — states", () => {
  it("always renders the week/month toggle", () => {
    const html = renderToStaticMarkup(
      <TrendsLens data={undefined} isLoading window="week" onWindowChange={noop} />,
    )
    expect(html).toContain("trends-window-week")
    expect(html).toContain("trends-window-month")
  })

  it("renders a skeleton while loading", () => {
    const html = renderToStaticMarkup(
      <TrendsLens data={undefined} isLoading window="week" onWindowChange={noop} />,
    )
    expect(html).toContain("trends-skeleton")
  })

  it("renders a degraded note when trends_source_error is true", () => {
    const html = renderToStaticMarkup(
      <TrendsLens
        data={makeResponse({ trends_source_error: true, lanes: [], anomalies: [] })}
        window="week"
        onWindowChange={noop}
      />,
    )
    expect(html).toContain("Trends")
    expect(html).toContain("role=\"alert\"")
  })

  it("renders per-lane series, a streak, and an anomaly", () => {
    const html = renderToStaticMarkup(
      <TrendsLens data={makeResponse()} window="week" onWindowChange={noop} />,
    )
    expect(html).toContain("trends-lens")
    expect(html).toContain("trends-lane-work")
    expect(html).toContain("trends-streak-work")
    expect(html).toContain("trends-anomalies")
    expect(html).toContain("spiked")
  })

  it("marks the active window button as pressed", () => {
    const html = renderToStaticMarkup(
      <TrendsLens data={makeResponse({ window: "month" })} window="month" onWindowChange={noop} />,
    )
    expect(html).toContain("Trailing 30 days")
  })

  it("renders an empty state when no lane has activity", () => {
    const html = renderToStaticMarkup(
      <TrendsLens
        data={makeResponse({ lanes: [], anomalies: [] })}
        window="week"
        onWindowChange={noop}
      />,
    )
    expect(html).toContain("trends-empty")
  })
})
