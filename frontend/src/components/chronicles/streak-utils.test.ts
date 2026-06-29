// ---------------------------------------------------------------------------
// Tests for findLongestStreaks — bu-ig72b.34
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"

import { findLongestStreaks } from "./streak-utils"
import type { ChroniclerEpisode } from "@/api/types"

// ---------------------------------------------------------------------------
// Fixture helper
// ---------------------------------------------------------------------------

let epId = 1

function makeEpisode(
  sourceName: string,
  startIso: string,
  endIso: string | null,
): ChroniclerEpisode {
  return {
    id: String(epId++),
    source_name: sourceName,
    source_ref: "test",
    episode_type: "active",
    start_at: startIso,
    end_at: endIso,
    precision: "exact",
    title: null,
    payload: {},
    privacy: "normal",
    retention_days: null,
    tombstone_at: null,
    canonical_start_at: startIso,
    canonical_end_at: endIso,
    canonical_title: null,
    canonical_privacy: "normal",
    corrected_at: null,
    correction_note: null,
    created_at: startIso,
    updated_at: startIso,
    category: sourceName,
  }
}

// Helper: build ISO string with minutes offset from a base
function at(baseIso: string, offsetMinutes: number): string {
  const d = new Date(baseIso)
  d.setMinutes(d.getMinutes() + offsetMinutes)
  return d.toISOString()
}

const BASE = "2026-01-01T08:00:00.000Z"

// ---------------------------------------------------------------------------
// Empty input
// ---------------------------------------------------------------------------

describe("findLongestStreaks — empty input", () => {
  it("returns empty array for no episodes", () => {
    expect(findLongestStreaks([])).toEqual([])
  })
})

// ---------------------------------------------------------------------------
// Single episode
// ---------------------------------------------------------------------------

describe("findLongestStreaks — single episode", () => {
  it("returns the episode as a streak if >= 30 min", () => {
    const ep = makeEpisode("work", BASE, at(BASE, 60)) // 1h
    const results = findLongestStreaks([ep])
    expect(results).toHaveLength(1)
    expect(results[0].category).toBe("work")
    expect(results[0].durationSeconds).toBe(3600)
    expect(results[0].startAt).toBe(BASE)
    expect(results[0].endAt).toBe(at(BASE, 60))
  })

  it("returns empty array when the single episode is < 30 min", () => {
    const ep = makeEpisode("work", BASE, at(BASE, 20)) // 20 min
    expect(findLongestStreaks([ep])).toEqual([])
  })

  it("skips single episode with null end_at", () => {
    const ep = makeEpisode("work", BASE, null)
    expect(findLongestStreaks([ep])).toEqual([])
  })

  it("skips episode with unknown source_name", () => {
    const ep = makeEpisode("unknown_adapter", BASE, at(BASE, 60))
    expect(findLongestStreaks([ep])).toEqual([])
  })
})

// ---------------------------------------------------------------------------
// Gap boundary — 5 min rule
// ---------------------------------------------------------------------------

describe("findLongestStreaks — gap boundary", () => {
  it("merges two episodes with gap exactly == 5 min into one streak", () => {
    // ep1: 08:00 → 08:30, ep2: 08:35 → 09:30 — gap = 5 min (should merge)
    const ep1 = makeEpisode("work", at(BASE, 0), at(BASE, 30))
    const ep2 = makeEpisode("work", at(BASE, 35), at(BASE, 90))
    const results = findLongestStreaks([ep1, ep2])
    expect(results).toHaveLength(1)
    // streak spans 90 min total (08:00 → 09:30)
    expect(results[0].durationSeconds).toBe(90 * 60)
    expect(results[0].startAt).toBe(at(BASE, 0))
    expect(results[0].endAt).toBe(at(BASE, 90))
  })

  it("treats gap > 5 min as a streak break", () => {
    // ep1: 08:00 → 08:30, ep2: 08:36 → 09:30 — gap = 6 min (should break)
    const ep1 = makeEpisode("work", at(BASE, 0), at(BASE, 30))  // 30 min streak
    const ep2 = makeEpisode("work", at(BASE, 36), at(BASE, 96)) // 60 min streak
    const results = findLongestStreaks([ep1, ep2])
    // ep1 streak is 30 min, filtered; ep2 streak is 60 min, passes filter
    expect(results).toHaveLength(1)
    expect(results[0].durationSeconds).toBe(60 * 60)
    expect(results[0].startAt).toBe(at(BASE, 36))
  })

  it("returns empty when both break-segments are under 30 min", () => {
    const ep1 = makeEpisode("work", at(BASE, 0), at(BASE, 20))  // 20 min
    const ep2 = makeEpisode("work", at(BASE, 30), at(BASE, 49)) // 19 min, gap = 10 min
    expect(findLongestStreaks([ep1, ep2])).toEqual([])
  })
})

// ---------------------------------------------------------------------------
// 30-minute threshold filter
// ---------------------------------------------------------------------------

describe("findLongestStreaks — 30 min threshold", () => {
  it("includes streak of exactly 30 min", () => {
    const ep = makeEpisode("play", BASE, at(BASE, 30))
    const results = findLongestStreaks([ep])
    expect(results).toHaveLength(1)
    expect(results[0].durationSeconds).toBe(1800)
  })

  it("excludes streak of 29 min 59 sec", () => {
    // 1799 seconds
    const start = BASE
    const end = new Date(new Date(BASE).getTime() + 1799 * 1000).toISOString()
    const ep = makeEpisode("play", start, end)
    expect(findLongestStreaks([ep])).toEqual([])
  })
})

// ---------------------------------------------------------------------------
// Multi-category
// ---------------------------------------------------------------------------

describe("findLongestStreaks — multi-category", () => {
  it("returns one result per category", () => {
    const eps = [
      makeEpisode("work", at(BASE, 0), at(BASE, 60)),   // 1h tasks
      makeEpisode("play", at(BASE, 0), at(BASE, 45)),  // 45m music
    ]
    const results = findLongestStreaks(eps)
    expect(results).toHaveLength(2)
    const categories = results.map((r) => r.category)
    expect(categories).toContain("work")
    expect(categories).toContain("play")
  })

  it("sorts results by duration DESC", () => {
    const eps = [
      makeEpisode("play", at(BASE, 0), at(BASE, 45)),  // 45m — second
      makeEpisode("work", at(BASE, 0), at(BASE, 90)),  // 90m — first
      makeEpisode("sleep", at(BASE, 0), at(BASE, 60)),  // 60m — middle
    ]
    const results = findLongestStreaks(eps)
    expect(results[0].category).toBe("work")
    expect(results[1].category).toBe("sleep")
    expect(results[2].category).toBe("play")
  })

  it("returns only categories with streaks >= 30 min", () => {
    const eps = [
      makeEpisode("work", at(BASE, 0), at(BASE, 60)),  // 60m tasks — passes
      makeEpisode("play", at(BASE, 0), at(BASE, 20)), // 20m music — filtered
    ]
    const results = findLongestStreaks(eps)
    expect(results).toHaveLength(1)
    expect(results[0].category).toBe("work")
  })

  it("tracks best streak per category independently", () => {
    // Two tasks streaks: 40 min and 80 min, with a >5 min gap between them
    // One music streak of 50 min
    const eps = [
      makeEpisode("work", at(BASE, 0), at(BASE, 40)),   // tasks streak A: 40m
      makeEpisode("work", at(BASE, 50), at(BASE, 130)), // tasks streak B: 80m (gap = 10min)
      makeEpisode("play", at(BASE, 0), at(BASE, 50)),  // music streak: 50m
    ]
    const results = findLongestStreaks(eps)
    const tasksResult = results.find((r) => r.category === "work")!
    expect(tasksResult.durationSeconds).toBe(80 * 60)
    expect(tasksResult.startAt).toBe(at(BASE, 50))
  })
})
