// ---------------------------------------------------------------------------
// widget-states.test.tsx — bu-ig72b.25 (IEA reframe: pie + stacked-bar removed)
//
// Tests for loading, error, and empty states across the remaining Chronicles
// widgets:
//   - GanttSwimlaneInner  (empty + text; loading/error via GanttSwimlane wrapper)
//   - StreakCallouts       (loading skeleton / error hide)
//   - MapWidgetInner       (empty with correct text + data-testid)
//
// The AggregatePieChart and AggregateStackedBar widgets were removed in the
// IEA reframe (tasks.md §10) — the "where the time went" surface is now the
// Day Ribbon + Balance rings (covered by DayRibbon.test / BalanceRings.test).
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

// useChroniclesEpisodes mock (required for StreakCallouts)
vi.mock("@/hooks/use-chronicles", () => ({
  useChroniclesEpisodes: vi.fn(),
}))

// ---------------------------------------------------------------------------
// Imports after mocks
// ---------------------------------------------------------------------------

import { GanttSwimlaneInner } from "./GanttSwimlaneInner"
import { StreakCallouts } from "./StreakCallouts"
import { MapWidgetInner } from "./MapWidgetInner"
import { useChroniclesEpisodes } from "@/hooks/use-chronicles"

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const WINDOW_START = new Date("2026-04-25T00:00:00Z")
const WINDOW_END = new Date("2026-04-25T23:59:59Z")

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
    } as unknown as ReturnType<typeof useChroniclesEpisodes>)

    const html = renderToStaticMarkup(<StreakCallouts />)
    expect(html).toContain("streak-skeleton")
  })

  it("skeleton has accessible label", () => {
    vi.mocked(useChroniclesEpisodes).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useChroniclesEpisodes>)

    const html = renderToStaticMarkup(<StreakCallouts />)
    expect(html).toContain("Loading streaks")
  })

  it("does NOT show streak skeleton when isLoading=true but cached data exists", () => {
    vi.mocked(useChroniclesEpisodes).mockReturnValue({
      data: { data: [], meta: { total: 0, offset: 0, limit: 500, has_more: false } },
      isLoading: true,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useChroniclesEpisodes>)

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
    } as unknown as ReturnType<typeof useChroniclesEpisodes>)

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
    } as unknown as ReturnType<typeof useChroniclesEpisodes>)

    const html = renderToStaticMarkup(<StreakCallouts />)
    // No streaks above 30-min threshold → hidden
    expect(html).toBe("")
  })
})
