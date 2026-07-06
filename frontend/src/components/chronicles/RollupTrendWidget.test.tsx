// ---------------------------------------------------------------------------
// Tests for RollupTrendWidget (bu-333dq, telemetry-distillation bead 5)
//
// Strategy mirrors widget-states.test.tsx: mock recharts (no real SVG/DOM
// needed to verify our own render branches) and mock useChroniclesRollups
// directly, then assert with renderToStaticMarkup — no @testing-library/react.
// ---------------------------------------------------------------------------

import { describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"
import * as React from "react"

// recharts mock — same shape as widget-states.test.tsx.
vi.mock("recharts", () => {
  const ResponsiveContainer = ({ children }: { children: React.ReactNode }) =>
    React.createElement("div", { "data-testid": "recharts-responsive-container" }, children)
  const BarChart = ({ children }: { children: React.ReactNode }) =>
    React.createElement("div", { "data-testid": "recharts-bar-chart" }, children)
  const Bar = ({ children }: { children?: React.ReactNode }) =>
    React.createElement("div", { "data-testid": "recharts-bar" }, children)
  const Cell = ({ fill }: { fill?: string }) =>
    React.createElement("span", { "data-testid": "recharts-cell", "data-fill": fill })
  const Tooltip = () => null
  const XAxis = () => null
  const YAxis = () => null
  return { ResponsiveContainer, BarChart, Bar, Cell, Tooltip, XAxis, YAxis }
})

vi.mock("@/hooks/use-chronicles", () => ({
  useChroniclesRollups: vi.fn(),
}))

import { RollupTrendWidget } from "./RollupTrendWidget"
import { useChroniclesRollups } from "@/hooks/use-chronicles"
import { LANE_TAXONOMY } from "./lane-taxonomy"
import type { ChroniclerRollupDay, ChroniclerRollupsResponse } from "@/api/types"

function materializedDay(
  localDate: string,
  lanes: Array<{ lane: string; seconds: number; unavailable?: boolean }>,
  flags: ChroniclerRollupDay["flags"] = [],
): ChroniclerRollupDay {
  return {
    local_date: localDate,
    timezone: "Asia/Singapore",
    status: "materialized",
    lanes: lanes.map((l) => ({
      lane: l.lane,
      seconds: l.seconds,
      episode_count: 1,
      distinct_place_count: null,
      unavailable: l.unavailable ?? false,
    })),
    flags,
  }
}

function mockRollupsQuery(overrides: {
  data?: { data: ChroniclerRollupsResponse; meta: Record<string, unknown> }
  isLoading?: boolean
  isError?: boolean
}) {
  vi.mocked(useChroniclesRollups).mockReturnValue({
    data: overrides.data,
    isLoading: overrides.isLoading ?? false,
    isError: overrides.isError ?? false,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useChroniclesRollups>)
}

function response(days: ChroniclerRollupDay[], rollups_source_error = false): {
  data: ChroniclerRollupsResponse
  meta: Record<string, unknown>
} {
  return {
    data: {
      start_date: days[0]?.local_date ?? "2026-07-01",
      end_date: days[days.length - 1]?.local_date ?? "2026-07-01",
      tz: "Asia/Singapore",
      days,
      rollups_source_error,
    },
    meta: {},
  }
}

describe("RollupTrendWidget — loading state", () => {
  it("renders the skeleton when isLoading=true", () => {
    mockRollupsQuery({ isLoading: true })
    const html = renderToStaticMarkup(<RollupTrendWidget endDate="2026-07-05" />)
    expect(html).toContain("rollup-trend-skeleton")
    expect(html).not.toContain("recharts-bar-chart")
  })
})

describe("RollupTrendWidget — error state", () => {
  it("renders the error fallback when isError=true", () => {
    mockRollupsQuery({ isError: true })
    const html = renderToStaticMarkup(<RollupTrendWidget endDate="2026-07-05" />)
    expect(html).toContain("rollup-trend-error")
    expect(html).not.toContain("recharts-bar-chart")
  })

  it("renders a retry button", () => {
    mockRollupsQuery({ isError: true })
    const html = renderToStaticMarkup(<RollupTrendWidget endDate="2026-07-05" />)
    expect(html).toContain("Try again")
  })
})

describe("RollupTrendWidget — degraded (rollups_source_error)", () => {
  it("renders SourceDegradedNote instead of the chart, never a fabricated zero trend", () => {
    mockRollupsQuery({ data: response([materializedDay("2026-07-05", [])], true) })
    const html = renderToStaticMarkup(<RollupTrendWidget endDate="2026-07-05" />)
    expect(html).not.toContain("recharts-bar-chart")
    expect(html).toContain('role="alert"')
    expect(html.toLowerCase()).toContain("unreachable")
  })
})

describe("RollupTrendWidget — normal render", () => {
  it("renders the chart and flag row when data is present", () => {
    mockRollupsQuery({
      data: response([materializedDay("2026-07-05", [{ lane: "work", seconds: 3600 }])]),
    })
    const html = renderToStaticMarkup(<RollupTrendWidget endDate="2026-07-05" />)
    expect(html).toContain("rollup-trend-widget")
    expect(html).toContain("recharts-bar-chart")
    expect(html).toContain("rollup-trend-flags")
  })

  it("marks a feeder_dark-affected lane's cell with the hatch pattern fill, not the lane color", () => {
    mockRollupsQuery({
      data: response([
        materializedDay(
          "2026-07-05",
          [{ lane: "sleep", seconds: 0, unavailable: true }],
          [{ flag_type: "feeder_dark", severity: "warning", detail: {} }],
        ),
      ]),
    })
    const html = renderToStaticMarkup(<RollupTrendWidget endDate="2026-07-05" />)
    // The pattern <defs> always renders; what proves the cell is actually
    // hatched (not just present in the unused defs) is its own fill attribute.
    expect(html).toContain('data-fill="url(#rollup-trend-unavailable-hatch)"')
  })

  it("uses the lane's solid color (not the hatch) when the lane is available", () => {
    mockRollupsQuery({
      data: response([materializedDay("2026-07-05", [{ lane: "work", seconds: 3600 }])]),
    })
    const html = renderToStaticMarkup(<RollupTrendWidget endDate="2026-07-05" />)
    expect(html).not.toContain('data-fill="url(#rollup-trend-unavailable-hatch)"')
    expect(html).toContain(`data-fill="${LANE_TAXONOMY.work.hex}"`)
  })

  it("renders a muted marker (not a flag glyph) for a not_yet_materialized day", () => {
    mockRollupsQuery({
      data: response([
        { local_date: "2026-07-06", timezone: "Asia/Singapore", status: "not_yet_materialized", lanes: [], flags: [] },
      ]),
    })
    const html = renderToStaticMarkup(<RollupTrendWidget endDate="2026-07-06" />)
    expect(html).toContain("Not yet available")
  })

  it("renders nothing when the query has not resolved data yet", () => {
    mockRollupsQuery({ data: undefined })
    const html = renderToStaticMarkup(<RollupTrendWidget endDate="2026-07-05" />)
    expect(html).toBe("")
  })
})
