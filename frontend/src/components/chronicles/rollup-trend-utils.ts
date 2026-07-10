// ---------------------------------------------------------------------------
// Pivot utility for RollupTrendWidget (bu-333dq, telemetry-distillation
// bead 5) — produces one recharts row per day, sourced from
// GET /api/chronicler/rollups (already one row per day, unlike the flat
// (day, category) rows aggregate/by-day returns).
// ---------------------------------------------------------------------------

import { LANE_TAXONOMY, type Category } from "./lane-taxonomy"
import type { ChroniclerRollupDay, ChroniclerRollupFlagRow } from "@/api/types"

/** One recharts row: { day, status, flags, unavailableLanes, work: N, sleep: N, ... } */
export interface RollupTrendDayRow {
  day: string
  status: ChroniclerRollupDay["status"]
  flags: ChroniclerRollupFlagRow[]
  /** Lanes flagged unavailable (feeder_dark) this day — never a truthful zero. */
  unavailableLanes: Set<string>
  [lane: string]: unknown
}

const LANE_KEYS = (Object.keys(LANE_TAXONOMY) as Category[]).filter((c) => c !== "other")

/**
 * Convert ``ChroniclerRollupDay[]`` (already one entry per day) into one
 * recharts-ready row per day, zero-filling every lane in the fixed taxonomy
 * (except the frontend-only ``other`` catch-all, which the backend never
 * emits for rollups) so a day with sparse lane coverage still renders a
 * complete stacked column.
 *
 * A ``not_yet_materialized``/``unknown`` day (``lanes`` empty) zero-fills the
 * same way — the chart renders an empty column for it, and callers should
 * consult ``status`` (not bar height) to tell "genuinely quiet day" from
 * "no data yet".
 */
export function pivotRollupDays(days: ChroniclerRollupDay[]): RollupTrendDayRow[] {
  return days.map((d) => {
    const row: RollupTrendDayRow = {
      day: d.local_date,
      status: d.status,
      flags: d.flags,
      unavailableLanes: new Set(d.lanes.filter((lane) => lane.unavailable).map((lane) => lane.lane)),
    }
    for (const lane of LANE_KEYS) {
      row[lane] = 0
    }
    for (const laneRow of d.lanes) {
      row[laneRow.lane] = laneRow.seconds
    }
    return row
  })
}
