// ---------------------------------------------------------------------------
// Tests for pivotRollupDays (bu-333dq, telemetry-distillation bead 5)
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"

import { pivotRollupDays } from "./rollup-trend-utils"
import { LANE_TAXONOMY } from "./lane-taxonomy"
import type { ChroniclerRollupDay } from "@/api/types"

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

function absentDay(localDate: string, status: "not_yet_materialized" | "unknown"): ChroniclerRollupDay {
  return {
    local_date: localDate,
    timezone: "Asia/Singapore",
    status,
    lanes: [],
    flags: [],
  }
}

describe("pivotRollupDays", () => {
  it("returns empty array for empty input", () => {
    expect(pivotRollupDays([])).toEqual([])
  })

  it("carries local_date and status through as day/status", () => {
    const rows = pivotRollupDays([materializedDay("2026-07-05", [{ lane: "work", seconds: 3600 }])])
    expect(rows[0].day).toBe("2026-07-05")
    expect(rows[0].status).toBe("materialized")
  })

  it("assigns seconds for lanes present, zero-fills every other lane", () => {
    const rows = pivotRollupDays([
      materializedDay("2026-07-05", [
        { lane: "work", seconds: 3600 },
        { lane: "sleep", seconds: 21600 },
      ]),
    ])
    expect(rows[0].work).toBe(3600)
    expect(rows[0].sleep).toBe(21600)
    // Every other lane in the fixed taxonomy (minus the frontend-only "other")
    // is zero-filled, not omitted.
    for (const lane of Object.keys(LANE_TAXONOMY)) {
      if (lane === "other") continue
      if (lane === "work" || lane === "sleep") continue
      expect(rows[0][lane]).toBe(0)
    }
  })

  it("does not add an 'other' key (the backend never emits it for rollups)", () => {
    const rows = pivotRollupDays([materializedDay("2026-07-05", [{ lane: "work", seconds: 100 }])])
    expect(rows[0].other).toBeUndefined()
  })

  it("zero-fills a not_yet_materialized/unknown day (no lanes)", () => {
    const rows = pivotRollupDays([absentDay("2026-07-06", "not_yet_materialized")])
    expect(rows[0].status).toBe("not_yet_materialized")
    expect(rows[0].work).toBe(0)
    expect(rows[0].unavailableLanes.size).toBe(0)
  })

  it("collects unavailable lane names into a Set", () => {
    const rows = pivotRollupDays([
      materializedDay("2026-07-05", [
        { lane: "sleep", seconds: 0, unavailable: true },
        { lane: "work", seconds: 3600, unavailable: false },
      ]),
    ])
    expect(rows[0].unavailableLanes.has("sleep")).toBe(true)
    expect(rows[0].unavailableLanes.has("work")).toBe(false)
  })

  it("carries the day's flags through unchanged", () => {
    const flags: ChroniclerRollupDay["flags"] = [
      { flag_type: "feeder_dark", severity: "warning", detail: { dark_sources: ["x"] } },
    ]
    const rows = pivotRollupDays([materializedDay("2026-07-05", [], flags)])
    expect(rows[0].flags).toEqual(flags)
  })

  it("preserves the input day order (the API already returns local_date ASC)", () => {
    const rows = pivotRollupDays([
      materializedDay("2026-07-01", [{ lane: "work", seconds: 100 }]),
      absentDay("2026-07-02", "not_yet_materialized"),
      materializedDay("2026-07-03", [{ lane: "work", seconds: 200 }]),
    ])
    expect(rows.map((r) => r.day)).toEqual(["2026-07-01", "2026-07-02", "2026-07-03"])
  })
})
