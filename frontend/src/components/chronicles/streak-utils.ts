// ---------------------------------------------------------------------------
// Streak detection utilities — bu-ig72b.34
//
// Computes the longest contiguous span (no gap > 5 min) per category over a
// set of ChroniclerEpisode records. Category is derived by treating
// `source_name` as a Category key into LANE_TAXONOMY.
//
// Only streaks >= 30 min are returned. Results are sorted by duration DESC.
// ---------------------------------------------------------------------------

import type { ChroniclerEpisode } from "@/api/types"
import type { Category } from "./lane-taxonomy"
import { LANE_TAXONOMY } from "./lane-taxonomy"

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Maximum gap between consecutive episodes to consider them part of one streak (ms). */
const MAX_GAP_MS = 5 * 60 * 1000

/** Minimum streak duration to include in results (ms). */
const MIN_STREAK_MS = 30 * 60 * 1000

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface StreakResult {
  category: Category
  durationSeconds: number
  startAt: string
  endAt: string
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Derive a Category from source_name. Returns null if source_name is not a
 * known LANE_TAXONOMY key. */
function categoryFor(sourceName: string): Category | null {
  if (sourceName in LANE_TAXONOMY) return sourceName as Category
  return null
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Find the longest contiguous streak per category over the given episodes.
 *
 * Algorithm:
 *   1. Group episodes by category (episodes whose source_name is not a known
 *      category are ignored).
 *   2. Within each group, sort episodes by start_at ASC.
 *   3. Walk the sorted list: extend the current streak when the gap between
 *      current.end_at and next.start_at is <= 5 min; otherwise close the
 *      streak and start a new one.
 *   4. Track the longest span per category.
 *   5. Filter out streaks < 30 min and return sorted by duration DESC.
 *
 * Episodes with null end_at are skipped (open/ongoing episode — duration
 * cannot be determined).
 *
 * @param episodes - All fetched ChroniclerEpisode records.
 * @returns Array of StreakResult sorted by durationSeconds DESC.
 */
export function findLongestStreaks(episodes: ChroniclerEpisode[]): StreakResult[] {
  // Group by category, skipping unknown source_names and open episodes
  const groups = new Map<Category, ChroniclerEpisode[]>()

  for (const ep of episodes) {
    if (ep.end_at === null) continue
    const cat = categoryFor(ep.source_name)
    if (cat === null) continue
    const list = groups.get(cat)
    if (list) {
      list.push(ep)
    } else {
      groups.set(cat, [ep])
    }
  }

  const results: StreakResult[] = []

  for (const [category, eps] of groups) {
    // Sort by start_at ascending
    eps.sort((a, b) => a.start_at.localeCompare(b.start_at))

    let best: StreakResult | null = null
    let maxDurationMs = -1

    // Walk episodes, extending or closing the current streak
    let streakStartAt = eps[0].start_at
    let streakEndAt = eps[0].end_at!

    for (let i = 1; i < eps.length; i++) {
      const ep = eps[i]
      const gapMs = Date.parse(ep.start_at) - Date.parse(streakEndAt)

      if (gapMs <= MAX_GAP_MS) {
        // Extend: advance end if this episode ends later
        if (ep.end_at! > streakEndAt) {
          streakEndAt = ep.end_at!
        }
      } else {
        // Close current streak and compare with best
        const durationMs = Date.parse(streakEndAt) - Date.parse(streakStartAt)
        if (durationMs > maxDurationMs) {
          maxDurationMs = durationMs
          best = {
            category,
            durationSeconds: Math.round(durationMs / 1000),
            startAt: streakStartAt,
            endAt: streakEndAt,
          }
        }
        // Start new streak
        streakStartAt = ep.start_at
        streakEndAt = ep.end_at!
      }
    }

    // Close final streak
    const finalDurationMs = Date.parse(streakEndAt) - Date.parse(streakStartAt)
    if (finalDurationMs > maxDurationMs) {
      best = {
        category,
        durationSeconds: Math.round(finalDurationMs / 1000),
        startAt: streakStartAt,
        endAt: streakEndAt,
      }
    }

    if (best !== null && best.durationSeconds >= MIN_STREAK_MS / 1000) {
      results.push(best)
    }
  }

  // Sort by duration DESC
  results.sort((a, b) => b.durationSeconds - a.durationSeconds)

  return results
}
