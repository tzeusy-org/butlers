// @vitest-environment jsdom

/**
 * Tests for AggregatePieChart — bu-ig72b.32
 *
 * Covers:
 *   - Empty state renders when buckets is empty
 *   - Pie chart container renders when buckets are present
 *   - Slices are ordered by total_seconds DESC (API sort order preserved)
 *   - Colour binding uses LANE_TAXONOMY hex values
 *   - Unknown category string falls back to "other"
 *   - Tooltip data contract: episodeCount and _total present on each datum
 */

import * as React from "react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

// ---------------------------------------------------------------------------
// Capture the last data array passed to <Pie> so tests can assert ordering.
// The variable lives in module scope; the mock factory closes over it.
// ---------------------------------------------------------------------------

let _lastPieData: Array<Record<string, unknown>> = []

vi.mock("recharts", () => {
  const PieChart = ({ children }: { children: React.ReactNode }) =>
    React.createElement("div", { "data-testid": "recharts-pie-chart" }, children)

  const ResponsiveContainer = ({ children }: { children: React.ReactNode }) =>
    React.createElement("div", { "data-testid": "recharts-responsive-container" }, children)

  const Pie = ({
    data,
    children,
  }: {
    data: Array<Record<string, unknown>>
    children?: React.ReactNode
  }) => {
    _lastPieData = data ?? []
    return React.createElement("div", { "data-testid": "recharts-pie" }, children)
  }

  const Cell = ({ fill }: { fill?: string }) =>
    React.createElement("span", { "data-testid": "recharts-cell", fill })

  const Tooltip = () => null

  return { PieChart, ResponsiveContainer, Pie, Cell, Tooltip }
})

// ---------------------------------------------------------------------------
// Imports under test (after mock registration)
// ---------------------------------------------------------------------------

import { AggregatePieChart } from "./AggregatePieChart"
import type { ChroniclerCategoryBucket } from "@/api/types"
import { LANE_TAXONOMY } from "./lane-taxonomy"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeBucket(
  category: string,
  totalSeconds: number,
  episodeCount = 1,
): ChroniclerCategoryBucket {
  return {
    category,
    total_seconds: totalSeconds,
    episode_count: episodeCount,
    source_breakdown: [],
    precision: "minute",
    retention_floor_days: null,
  }
}

function render(buckets: ChroniclerCategoryBucket[]): string {
  _lastPieData = []
  return renderToStaticMarkup(<AggregatePieChart buckets={buckets} />)
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  _lastPieData = []
})

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

describe("AggregatePieChart — empty state", () => {
  it("renders the empty state element when buckets is empty", () => {
    const html = render([])
    expect(html).toContain("pie-empty-state")
  })

  it("does NOT render the pie chart when buckets is empty", () => {
    const html = render([])
    expect(html).not.toContain("pie-chart-container")
  })

  it("empty state message mentions no activity", () => {
    const html = render([])
    expect(html.toLowerCase()).toContain("no activity")
  })
})

// ---------------------------------------------------------------------------
// Pie chart renders with data
// ---------------------------------------------------------------------------

describe("AggregatePieChart — data rendering", () => {
  it("renders the pie chart container when buckets are non-empty", () => {
    const html = render([makeBucket("work",3600)])
    expect(html).toContain("pie-chart-container")
  })

  it("does NOT render the empty state when buckets are non-empty", () => {
    const html = render([makeBucket("work",3600)])
    expect(html).not.toContain("pie-empty-state")
  })

  it("renders a recharts PieChart element", () => {
    const html = render([makeBucket("work",3600), makeBucket("sleep", 1800)])
    expect(html).toContain("recharts-pie-chart")
  })
})

// ---------------------------------------------------------------------------
// Slice ordering — API sort order (total_seconds DESC) is preserved
// ---------------------------------------------------------------------------

describe("AggregatePieChart — slice ordering", () => {
  it("preserves API sort order (total_seconds DESC) in pie data", () => {
    // API returns buckets sorted by total_seconds DESC; the component must
    // NOT reorder them.
    const buckets = [
      makeBucket("work",7200),
      makeBucket("sleep", 3600),
      makeBucket("eat", 1800),
    ]
    render(buckets)

    expect(_lastPieData.length).toBe(3)
    expect(_lastPieData[0].value).toBe(7200)
    expect(_lastPieData[1].value).toBe(3600)
    expect(_lastPieData[2].value).toBe(1800)
  })

  it("maps category label from LANE_TAXONOMY", () => {
    render([makeBucket("work",3600)])
    expect(_lastPieData[0].name).toBe(LANE_TAXONOMY.work.label)
  })
})

// ---------------------------------------------------------------------------
// Colour binding
// ---------------------------------------------------------------------------

describe("AggregatePieChart — colour binding", () => {
  it("pie data carries hex colour from LANE_TAXONOMY", () => {
    render([makeBucket("work",3600)])
    expect(_lastPieData[0].hex).toBe(LANE_TAXONOMY.work.hex)
  })

  it("falls back to 'other' taxonomy entry for unknown category", () => {
    render([makeBucket("unknown_category", 3600)])
    expect(_lastPieData[0].name).toBe(LANE_TAXONOMY.other.label)
    expect(_lastPieData[0].hex).toBe(LANE_TAXONOMY.other.hex)
  })
})

// ---------------------------------------------------------------------------
// Tooltip data contract
// ---------------------------------------------------------------------------

describe("AggregatePieChart — tooltip data contract", () => {
  it("pie data carries episodeCount for tooltip", () => {
    render([makeBucket("sleep", 3600, 5)])
    expect(_lastPieData[0].episodeCount).toBe(5)
  })

  it("pie data carries _total for percentage calculation", () => {
    render([makeBucket("work",7200), makeBucket("sleep", 3600)])
    // _total is the sum of all buckets (7200 + 3600 = 10800)
    expect(_lastPieData[0]._total).toBe(10800)
    expect(_lastPieData[1]._total).toBe(10800)
  })
})

// ---------------------------------------------------------------------------
// All-categories legend (bu-p4vd3 AC1 + AC2)
// ---------------------------------------------------------------------------

describe("AggregatePieChart — all-categories legend (bu-p4vd3)", () => {
  it("renders legend items for all LANE_TAXONOMY categories including empty ones", () => {
    // Only the work bucket present; all other lanes are absent.
    const html = render([makeBucket("work", 3600)])
    // Legend container is present.
    expect(html).toContain("pie-all-categories-legend")
    // Active category has its legend entry.
    expect(html).toContain('pie-legend-work')
    // Empty categories also have legend entries.
    expect(html).toContain('pie-legend-play')
    expect(html).toContain('pie-legend-eat')
    expect(html).toContain('pie-legend-rest')
  })

  it("marks empty categories with the empty affordance data-testid", () => {
    const html = render([makeBucket("work", 3600)])
    // Play has no data → shows empty affordance.
    expect(html).toContain('pie-legend-empty-play')
    // Work has data → no empty affordance for it.
    expect(html).not.toContain('pie-legend-empty-work')
  })

  it("renders legend in empty state with all lanes", () => {
    // When buckets is empty, the EmptyState still renders the full legend.
    const html = render([])
    expect(html).toContain("pie-all-categories-legend")
    // All LANE_TAXONOMY lanes appear.
    expect(html).toContain('pie-legend-work')
    expect(html).toContain('pie-legend-sleep')
    expect(html).toContain('pie-legend-other')
    // All show the empty affordance dash.
    expect(html).toContain('pie-legend-empty-work')
    expect(html).toContain('pie-legend-empty-sleep')
  })

  it("does not show empty affordance for active categories", () => {
    const html = render([makeBucket("sleep", 1800), makeBucket("play", 900)])
    // Active lanes: sleep, play — no empty affordance for them.
    expect(html).not.toContain('pie-legend-empty-sleep')
    expect(html).not.toContain('pie-legend-empty-play')
    // Other lanes are empty.
    expect(html).toContain('pie-legend-empty-work')
  })
})
