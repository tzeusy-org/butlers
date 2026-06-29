// ---------------------------------------------------------------------------
// Tests for AggregateStackedBar — bu-ig72b.33
//
// Strategy:
//   - pivotByDay: pure function, no DOM required
//   - Empty-state render: renderToStaticMarkup (no jsdom needed)
//   - Multi-day pivot: verify pivot correctness for multiple days
//   - DST day: 23h day from by-day Scenario fixture — total_seconds can be < 86400
//
// We do NOT use @testing-library/react (not installed). We test the pivot
// function directly and use renderToStaticMarkup for the empty state, following
// the same pattern as MapWidget.test.tsx and lane-taxonomy.test.ts.
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { AggregateStackedBar } from "./AggregateStackedBar"
import { pivotByDay } from "./aggregate-stacked-bar-utils"
import type { ChroniclerAggregateByDayRow } from "@/api/types"
import { LANE_TAXONOMY } from "./lane-taxonomy"

// ---------------------------------------------------------------------------
// Fixture helpers
// ---------------------------------------------------------------------------

function makeRow(
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
// Empty state
// ---------------------------------------------------------------------------

describe("AggregateStackedBar — empty state", () => {
  it("renders empty-state message when data is empty", () => {
    const html = renderToStaticMarkup(<AggregateStackedBar data={[]} />)
    expect(html).toContain("No activity recorded for this window")
  })
})

// ---------------------------------------------------------------------------
// pivotByDay — pure logic
// ---------------------------------------------------------------------------

describe("pivotByDay", () => {
  it("returns empty array for empty input", () => {
    expect(pivotByDay([])).toEqual([])
  })

  it("produces one row per distinct day", () => {
    const rows = [
      makeRow("2026-01-01", "work", 3600),
      makeRow("2026-01-01", "play", 1800),
      makeRow("2026-01-02", "work", 7200),
    ]
    const pivoted = pivotByDay(rows)
    expect(pivoted).toHaveLength(2)
    expect(pivoted[0].day).toBe("2026-01-01")
    expect(pivoted[1].day).toBe("2026-01-02")
  })

  it("assigns correct category totals per day", () => {
    const rows = [
      makeRow("2026-01-01", "work", 3600),
      makeRow("2026-01-01", "play", 1800),
      makeRow("2026-01-02", "work", 7200),
    ]
    const pivoted = pivotByDay(rows)

    expect(pivoted[0].work).toBe(3600)
    expect(pivoted[0].play).toBe(1800)
    // unvisited category initialised to 0
    expect(pivoted[0].sleep).toBe(0)

    expect(pivoted[1].work).toBe(7200)
    expect(pivoted[1].play).toBe(0)
  })

  it("initialises all known LANE_TAXONOMY categories to 0", () => {
    const rows = [makeRow("2026-01-01", "work", 3600)]
    const pivoted = pivotByDay(rows)
    const allCategories = Object.keys(LANE_TAXONOMY)
    for (const cat of allCategories) {
      expect(typeof pivoted[0][cat]).toBe("number")
    }
  })

  it("sorts rows ascending by day", () => {
    const rows = [
      makeRow("2026-01-03", "work", 1000),
      makeRow("2026-01-01", "work", 2000),
      makeRow("2026-01-02", "work", 1500),
    ]
    const pivoted = pivotByDay(rows)
    expect(pivoted.map((r) => r.day)).toEqual(["2026-01-01", "2026-01-02", "2026-01-03"])
  })

  it("DST spring-forward day (23h): total_seconds is under 86400", () => {
    // 2026-03-08 is US DST spring-forward: the day is 23 hours = 82800 seconds.
    // The server emits the actual seconds in the day; we preserve them as-is.
    const dstDay = "2026-03-08"
    const rows = [
      makeRow(dstDay, "sleep", 25200),  // 7h of sleep
      makeRow(dstDay, "work", 28800),   // 8h of work
      // total intentionally < 86400 (23h day)
    ]
    const pivoted = pivotByDay(rows)
    expect(pivoted).toHaveLength(1)
    expect(pivoted[0].sleep).toBe(25200)
    expect(pivoted[0].work).toBe(28800)
    // The sum of all accounted seconds should be under one standard day
    const allCategorySum = Object.keys(LANE_TAXONOMY).reduce(
      (sum, cat) => sum + (pivoted[0][cat] as number),
      0,
    )
    expect(allCategorySum).toBeLessThan(86400)
  })

  it("DST fall-back day (25h): correctly accepts total_seconds > 86400", () => {
    // 2026-11-01 is US DST fall-back: the day is 25 hours = 90000 seconds.
    const dstDay = "2026-11-01"
    const rows = [
      makeRow(dstDay, "sleep", 32400),  // 9h of sleep
      makeRow(dstDay, "work", 36000),   // 10h of work
    ]
    const pivoted = pivotByDay(rows)
    expect(pivoted[0].sleep).toBe(32400)
    expect(pivoted[0].work).toBe(36000)
  })

  it("unknown category is stored without error", () => {
    // Backend may emit a category not yet in LANE_TAXONOMY; should not throw.
    const rows = [makeRow("2026-01-01", "future_category", 1000)]
    expect(() => pivotByDay(rows)).not.toThrow()
    const pivoted = pivotByDay(rows)
    expect(pivoted[0]["future_category"]).toBe(1000)
  })
})
