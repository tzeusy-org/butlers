// ---------------------------------------------------------------------------
// widget-states.test.tsx — bu-ig72b.25
//
// Tests for loading, error, and empty states across all Chronicles widgets:
//   - GanttSwimlaneInner  (empty + text; loading/error via GanttSwimlane wrapper)
//   - AggregatePieChart   (loading / error / empty)
//   - AggregateStackedBar (loading / error / empty)
//   - StreakCallouts       (loading skeleton / error hide)
//   - MapWidgetInner       (empty with correct text + data-testid)
//
// Test strategy: renderToStaticMarkup (server-side) — same pattern as the
// other chronicles tests in this directory. No @testing-library/react needed.
// ---------------------------------------------------------------------------

import { describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

// ---------------------------------------------------------------------------
// Mocks — set up before component imports
// ---------------------------------------------------------------------------

// maplibre-gl mock (required for MapWidgetInner)
vi.mock("maplibre-gl", async () => {
  class MockMap {
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    constructor(..._args: unknown[]) {}
    isStyleLoaded() { return true }
    fitBounds() {}
    remove() {}
    on() {}
    off() {}
  }
  class MockMarker {
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    setLngLat(..._args: unknown[]) { return this }
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    addTo(..._args: unknown[]) { return this }
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    setPopup(..._args: unknown[]) { return this }
    remove() {}
  }
  class MockPopup {
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    setText(..._args: unknown[]) { return this }
  }
  class MockLngLatBounds {
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    extend(..._args: unknown[]) { return this }
  }
  const mock = { Map: MockMap, Marker: MockMarker, Popup: MockPopup, LngLatBounds: MockLngLatBounds }
  return { default: mock, ...mock }
})
vi.mock("maplibre-gl/dist/maplibre-gl.css", () => ({}))

// recharts mock (required for AggregatePieChart and AggregateStackedBar)
import * as React from "react"
vi.mock("recharts", () => {
  const PieChart = ({ children }: { children: React.ReactNode }) =>
    React.createElement("div", { "data-testid": "recharts-pie-chart" }, children)
  const ResponsiveContainer = ({ children }: { children: React.ReactNode }) =>
    React.createElement("div", { "data-testid": "recharts-responsive-container" }, children)
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const Pie = ({ data: _data, children }: { data: unknown[]; children?: React.ReactNode }) =>
    React.createElement("div", { "data-testid": "recharts-pie" }, children)
  const Cell = ({ fill }: { fill?: string }) =>
    React.createElement("span", { "data-testid": "recharts-cell", fill })
  const Tooltip = () => null
  const BarChart = ({ children }: { children: React.ReactNode }) =>
    React.createElement("div", { "data-testid": "recharts-bar-chart" }, children)
  const Bar = () => null
  const XAxis = () => null
  const YAxis = () => null
  const Legend = () => null
  return { PieChart, ResponsiveContainer, Pie, Cell, Tooltip, BarChart, Bar, XAxis, YAxis, Legend }
})

// useChroniclesEpisodes mock (required for StreakCallouts)
vi.mock("@/hooks/use-chronicles", () => ({
  useChroniclesEpisodes: vi.fn(),
}))

// ---------------------------------------------------------------------------
// Imports after mocks
// ---------------------------------------------------------------------------

import { AggregatePieChart } from "./AggregatePieChart"
import { AggregateStackedBar } from "./AggregateStackedBar"
import { GanttSwimlaneInner } from "./GanttSwimlaneInner"
import { StreakCallouts } from "./StreakCallouts"
import { MapWidgetInner } from "./MapWidgetInner"
import { useChroniclesEpisodes } from "@/hooks/use-chronicles"
import type { ChroniclerCategoryBucket } from "@/api/types"
import type { ChroniclerAggregateByDayRow } from "@/api/types"

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const WINDOW_START = new Date("2026-04-25T00:00:00Z")
const WINDOW_END = new Date("2026-04-25T23:59:59Z")

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

function makeByDayRow(
  day: string,
  category: string,
  total_seconds: number,
): ChroniclerAggregateByDayRow {
  return {
    day,
    category,
    total_seconds,
    episode_count: 1,
    day_start: `${day}T00:00:00+00:00`,
    day_end: `${day}T23:59:59+00:00`,
    source_breakdown: [],
    precision: "exact",
    retention_floor_days: null,
  }
}

// ---------------------------------------------------------------------------
// AggregatePieChart — loading state
// ---------------------------------------------------------------------------

describe("AggregatePieChart — loading state", () => {
  it("renders the skeleton when isLoading=true", () => {
    const html = renderToStaticMarkup(
      <AggregatePieChart buckets={[]} isLoading={true} />,
    )
    expect(html).toContain("pie-skeleton")
  })

  it("does NOT render the chart or empty state when isLoading=true", () => {
    const html = renderToStaticMarkup(
      <AggregatePieChart buckets={[]} isLoading={true} />,
    )
    expect(html).not.toContain("pie-chart-container")
    expect(html).not.toContain("pie-empty-state")
  })

  it("skeleton has accessible label", () => {
    const html = renderToStaticMarkup(
      <AggregatePieChart buckets={[]} isLoading={true} />,
    )
    expect(html).toContain("Loading pie chart")
  })
})

// ---------------------------------------------------------------------------
// AggregatePieChart — error state
// ---------------------------------------------------------------------------

describe("AggregatePieChart — error state", () => {
  it("renders the error fallback when isError=true", () => {
    const html = renderToStaticMarkup(
      <AggregatePieChart buckets={[]} isError={true} />,
    )
    expect(html).toContain("pie-error")
  })

  it("does NOT render the chart or empty state when isError=true", () => {
    const html = renderToStaticMarkup(
      <AggregatePieChart buckets={[]} isError={true} />,
    )
    expect(html).not.toContain("pie-chart-container")
    expect(html).not.toContain("pie-empty-state")
  })

  it("renders a retry button when onRetry is provided", () => {
    const html = renderToStaticMarkup(
      <AggregatePieChart buckets={[]} isError={true} onRetry={() => {}} />,
    )
    expect(html).toContain("Try again")
  })

  it("error message mentions failure to load", () => {
    const html = renderToStaticMarkup(
      <AggregatePieChart buckets={[]} isError={true} />,
    )
    expect(html.toLowerCase()).toContain("failed")
  })
})

// ---------------------------------------------------------------------------
// AggregatePieChart — empty state (no data, not loading, not error)
// ---------------------------------------------------------------------------

describe("AggregatePieChart — empty state", () => {
  it("renders empty state when buckets is empty and not loading/error", () => {
    const html = renderToStaticMarkup(
      <AggregatePieChart buckets={[]} />,
    )
    expect(html).toContain("pie-empty-state")
    expect(html).toContain("No activity recorded for this window")
  })

  it("does NOT render skeleton or error when buckets empty with no flags", () => {
    const html = renderToStaticMarkup(
      <AggregatePieChart buckets={[]} />,
    )
    expect(html).not.toContain("pie-skeleton")
    expect(html).not.toContain("pie-error")
  })
})

// ---------------------------------------------------------------------------
// AggregatePieChart — loading takes priority over data
// ---------------------------------------------------------------------------

describe("AggregatePieChart — state priority", () => {
  it("shows skeleton even when buckets are non-empty while loading", () => {
    const buckets = [makeBucket("work", 3600)]
    const html = renderToStaticMarkup(
      <AggregatePieChart buckets={buckets} isLoading={true} />,
    )
    expect(html).toContain("pie-skeleton")
    expect(html).not.toContain("pie-chart-container")
  })

  it("shows error even when buckets are non-empty while error", () => {
    const buckets = [makeBucket("work", 3600)]
    const html = renderToStaticMarkup(
      <AggregatePieChart buckets={buckets} isError={true} />,
    )
    expect(html).toContain("pie-error")
    expect(html).not.toContain("pie-chart-container")
  })
})

// ---------------------------------------------------------------------------
// AggregateStackedBar — loading state
// ---------------------------------------------------------------------------

describe("AggregateStackedBar — loading state", () => {
  it("renders the skeleton when isLoading=true", () => {
    const html = renderToStaticMarkup(
      <AggregateStackedBar data={[]} isLoading={true} />,
    )
    expect(html).toContain("stacked-bar-skeleton")
  })

  it("does NOT render the chart or empty state when isLoading=true", () => {
    const html = renderToStaticMarkup(
      <AggregateStackedBar data={[]} isLoading={true} />,
    )
    expect(html).not.toContain("recharts-bar-chart")
    expect(html).not.toContain("stacked-bar-empty")
  })

  it("skeleton has accessible label", () => {
    const html = renderToStaticMarkup(
      <AggregateStackedBar data={[]} isLoading={true} />,
    )
    expect(html).toContain("Loading stacked bar chart")
  })
})

// ---------------------------------------------------------------------------
// AggregateStackedBar — error state
// ---------------------------------------------------------------------------

describe("AggregateStackedBar — error state", () => {
  it("renders the error fallback when isError=true", () => {
    const html = renderToStaticMarkup(
      <AggregateStackedBar data={[]} isError={true} />,
    )
    expect(html).toContain("stacked-bar-error")
  })

  it("does NOT render the chart or empty state when isError=true", () => {
    const html = renderToStaticMarkup(
      <AggregateStackedBar data={[]} isError={true} />,
    )
    expect(html).not.toContain("recharts-bar-chart")
    expect(html).not.toContain("stacked-bar-empty")
  })

  it("renders a retry button when onRetry is provided", () => {
    const html = renderToStaticMarkup(
      <AggregateStackedBar data={[]} isError={true} onRetry={() => {}} />,
    )
    expect(html).toContain("Try again")
  })

  it("error message mentions failure to load", () => {
    const html = renderToStaticMarkup(
      <AggregateStackedBar data={[]} isError={true} />,
    )
    expect(html.toLowerCase()).toContain("failed")
  })
})

// ---------------------------------------------------------------------------
// AggregateStackedBar — empty state
// ---------------------------------------------------------------------------

describe("AggregateStackedBar — empty state", () => {
  it("renders empty message when data is empty and not loading/error", () => {
    const html = renderToStaticMarkup(
      <AggregateStackedBar data={[]} />,
    )
    expect(html).toContain("stacked-bar-empty")
    expect(html).toContain("No activity recorded for this window")
  })

  it("does NOT render skeleton or error when data empty with no flags", () => {
    const html = renderToStaticMarkup(
      <AggregateStackedBar data={[]} />,
    )
    expect(html).not.toContain("stacked-bar-skeleton")
    expect(html).not.toContain("stacked-bar-error")
  })
})

// ---------------------------------------------------------------------------
// AggregateStackedBar — loading takes priority over data
// ---------------------------------------------------------------------------

describe("AggregateStackedBar — state priority", () => {
  it("shows skeleton even when data is non-empty while loading", () => {
    const rows = [makeByDayRow("2026-04-25", "work", 3600)]
    const html = renderToStaticMarkup(
      <AggregateStackedBar data={rows} isLoading={true} />,
    )
    expect(html).toContain("stacked-bar-skeleton")
    expect(html).not.toContain("recharts-bar-chart")
  })

  it("shows error even when data is non-empty while error", () => {
    const rows = [makeByDayRow("2026-04-25", "work", 3600)]
    const html = renderToStaticMarkup(
      <AggregateStackedBar data={rows} isError={true} />,
    )
    expect(html).toContain("stacked-bar-error")
    expect(html).not.toContain("recharts-bar-chart")
  })
})

// ---------------------------------------------------------------------------
// GanttSwimlaneInner — empty state text matches acceptance criteria
// ---------------------------------------------------------------------------

describe("GanttSwimlaneInner — empty state text", () => {
  it("renders 'No activity recorded for this window' when episodes is empty", () => {
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    expect(html).toContain("No activity recorded for this window")
    expect(html).toContain("gantt-empty")
  })
})

// ---------------------------------------------------------------------------
// MapWidgetInner — empty state text and testid
// ---------------------------------------------------------------------------

describe("MapWidgetInner — empty state", () => {
  it("renders 'No activity recorded for this window' when points is empty", () => {
    const html = renderToStaticMarkup(<MapWidgetInner points={[]} />)
    expect(html).toContain("No activity recorded for this window")
  })

  it("renders map-empty data-testid on the empty state container", () => {
    const html = renderToStaticMarkup(<MapWidgetInner points={[]} />)
    expect(html).toContain("map-empty")
  })

  it("does NOT render map-empty when points are provided", () => {
    const html = renderToStaticMarkup(
      <MapWidgetInner points={[{ lng: 103.8, lat: 1.3 }]} />,
    )
    expect(html).not.toContain("map-empty")
    expect(html).toContain("map-container")
  })
})

// ---------------------------------------------------------------------------
// StreakCallouts — loading state
// ---------------------------------------------------------------------------

describe("StreakCallouts — loading state", () => {
  it("renders streak skeleton when isLoading=true and no cached data", () => {
    vi.mocked(useChroniclesEpisodes).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      refetch: vi.fn(),
    } as ReturnType<typeof useChroniclesEpisodes>)

    const html = renderToStaticMarkup(<StreakCallouts />)
    expect(html).toContain("streak-skeleton")
  })

  it("skeleton has accessible label", () => {
    vi.mocked(useChroniclesEpisodes).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      refetch: vi.fn(),
    } as ReturnType<typeof useChroniclesEpisodes>)

    const html = renderToStaticMarkup(<StreakCallouts />)
    expect(html).toContain("Loading streaks")
  })

  it("does NOT show streak skeleton when isLoading=true but cached data exists", () => {
    vi.mocked(useChroniclesEpisodes).mockReturnValue({
      data: { data: [], meta: { total: 0, offset: 0, limit: 500, has_more: false } },
      isLoading: true,
      isError: false,
      refetch: vi.fn(),
    } as ReturnType<typeof useChroniclesEpisodes>)

    const html = renderToStaticMarkup(<StreakCallouts />)
    // With stale data present, no skeleton — we render or hide based on streaks
    expect(html).not.toContain("streak-skeleton")
  })
})

// ---------------------------------------------------------------------------
// StreakCallouts — error state
// ---------------------------------------------------------------------------

describe("StreakCallouts — error state", () => {
  it("renders nothing (null) when isError=true", () => {
    vi.mocked(useChroniclesEpisodes).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch: vi.fn(),
    } as ReturnType<typeof useChroniclesEpisodes>)

    const html = renderToStaticMarkup(<StreakCallouts />)
    // Silently hides on error — streaks are supplementary
    expect(html).toBe("")
  })
})

// ---------------------------------------------------------------------------
// StreakCallouts — empty / no-data state
// ---------------------------------------------------------------------------

describe("StreakCallouts — empty state", () => {
  it("renders nothing when there are no episodes", () => {
    vi.mocked(useChroniclesEpisodes).mockReturnValue({
      data: { data: [], meta: { total: 0, offset: 0, limit: 500, has_more: false } },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as ReturnType<typeof useChroniclesEpisodes>)

    const html = renderToStaticMarkup(<StreakCallouts />)
    // No streaks above 30-min threshold → hidden
    expect(html).toBe("")
  })
})
