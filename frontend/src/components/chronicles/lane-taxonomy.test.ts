// ---------------------------------------------------------------------------
// Tests for LANE_TAXONOMY — bu-ig72b.5
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"

import {
  LANE_TAXONOMY,
  categoryForSource,
  type Category,
  type LaneConfig,
} from "./lane-taxonomy"

// All 10 stable category strings defined by the backend (aggregations.py).
// core.sessions episodes are split into "conversations" and "tasks" based on
// trigger_source; the old "work" lane has been replaced by these two lanes.
const EXPECTED_CATEGORIES: Category[] = [
  "conversations",
  "tasks",
  "calendar",
  "music",
  "gaming",
  "travel",
  "sleep",
  "meal",
  "home",
  "other",
]

describe("LANE_TAXONOMY", () => {
  it("contains exactly the 10 expected categories", () => {
    const keys = Object.keys(LANE_TAXONOMY).sort()
    expect(keys).toEqual([...EXPECTED_CATEGORIES].sort())
  })

  it.each(EXPECTED_CATEGORIES)("category '%s' has all four required fields", (category) => {
    const config: LaneConfig = LANE_TAXONOMY[category]

    expect(typeof config.label).toBe("string")
    expect(config.label.length).toBeGreaterThan(0)

    expect(typeof config.colour).toBe("string")
    expect(config.colour.length).toBeGreaterThan(0)

    // Lucide icons are React.forwardRef objects; they are non-null objects.
    expect(config.icon).toBeTruthy()
    expect(typeof config.icon === "function" || typeof config.icon === "object").toBe(true)

    expect(typeof config.sortOrder).toBe("number")
  })

  it("all sortOrder values are unique", () => {
    const orders = EXPECTED_CATEGORIES.map((c) => LANE_TAXONOMY[c].sortOrder)
    const unique = new Set(orders)
    expect(unique.size).toBe(EXPECTED_CATEGORIES.length)
  })

  it("all sortOrder values are non-negative integers", () => {
    for (const category of EXPECTED_CATEGORIES) {
      const order = LANE_TAXONOMY[category].sortOrder
      expect(Number.isInteger(order)).toBe(true)
      expect(order).toBeGreaterThanOrEqual(0)
    }
  })

  it("colour values are Tailwind bg-* utility classes (no hardcoded hex)", () => {
    for (const category of EXPECTED_CATEGORIES) {
      const colour = LANE_TAXONOMY[category].colour
      // Must start with 'bg-' and contain no '#'
      expect(colour).toMatch(/^bg-/)
      expect(colour).not.toContain("#")
    }
  })
})

// ---------------------------------------------------------------------------
// categoryForSource — fallback lookup mirroring backend `_CATEGORY_MAP`.
// Bug 1: ensures (source_name, episode_type) maps to the right lane.
// ---------------------------------------------------------------------------

describe("categoryForSource", () => {
  it.each<[string, string, Category]>([
    // core.sessions: fallback path cannot resolve trigger_source, defaults to "tasks"
    ["core.sessions", "work", "tasks"],
    ["google_calendar.completed", "scheduled_block", "calendar"],
    ["spotify.session_summary", "listening_episode", "music"],
    ["steam.play_history", "play_episode", "gaming"],
    ["owntracks.points", "movement_episode", "travel"],
    ["google_health.measurements", "sleep_episode", "sleep"],
    ["health.meals", "eating_event", "meal"],
    ["home_assistant.history", "presence_episode", "home"],
  ])("maps (%s, %s) → %s", (source, type, expected) => {
    expect(categoryForSource(source, type)).toBe(expected)
  })

  it("returns 'other' for an unknown source/type pair", () => {
    expect(categoryForSource("totally.unknown", "mystery_event")).toBe("other")
  })

  it("returns 'other' when source matches but episode_type does not", () => {
    expect(categoryForSource("core.sessions", "not-work")).toBe("other")
  })

  it("returns 'other' for the empty pair", () => {
    expect(categoryForSource("", "")).toBe("other")
  })
})
