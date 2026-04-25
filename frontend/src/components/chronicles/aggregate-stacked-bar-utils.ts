// ---------------------------------------------------------------------------
// Pivot utilities for AggregateStackedBar — bu-ig72b.33
// ---------------------------------------------------------------------------

import { LANE_TAXONOMY, type Category } from "./lane-taxonomy"
import type { ChroniclerAggregateByDayRow } from "@/api/types"

/** One recharts row: { day, work: N, calendar: N, ... } */
export interface StackedDayRow {
  day: string
  [category: string]: number | string
}

/**
 * Pivot flat (day, category, total_seconds) rows into one row per day,
 * keyed by category.
 *
 * Days are de-duplicated and sorted ascending by ISO date string.
 * Missing categories are filled with 0 so recharts always has a complete row.
 */
export function pivotByDay(rows: ChroniclerAggregateByDayRow[]): StackedDayRow[] {
  const byDay = new Map<string, StackedDayRow>()

  for (const row of rows) {
    if (!byDay.has(row.day)) {
      // Initialise with 0 for every known category
      const seed: StackedDayRow = { day: row.day }
      for (const cat of Object.keys(LANE_TAXONOMY) as Category[]) {
        seed[cat] = 0
      }
      byDay.set(row.day, seed)
    }
    const dayRow = byDay.get(row.day)!
    dayRow[row.category] = row.total_seconds
  }

  return Array.from(byDay.values()).sort((a, b) => a.day.localeCompare(b.day))
}
