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

// The eight life-balance Activity lanes emitted by the backend
// (aggregations.LANES), plus the "other" catch-all used as a frontend fallback
// for unmapped categories. Music/gaming fold into Play; calendar is intent and
// is never a lane (IEA reframe, bu-3n44q5).
const EXPECTED_CATEGORIES: Category[] = [
  "sleep",
  "exercise",
  "work",
  "play",
  "social",
  "travel",
  "eat",
  "rest",
  "other",
]

describe("LANE_TAXONOMY", () => {
  it("contains exactly the eight Activity lanes plus the 'other' fallback", () => {
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
    // core.sessions: conversations + tasks both fold into the Work lane.
    ["core.sessions", "work", "work"],
    ["spotify.session_summary", "listening_episode", "play"],
    ["steam.play_history", "play_episode", "play"],
    ["owntracks.points", "movement_episode", "travel"],
    ["google_health.measurements", "sleep_episode", "sleep"],
    ["google_health.measurements", "workout_episode", "exercise"],
    ["health.meals", "eating_event", "eat"],
    ["home_assistant.history", "presence_episode", "rest"],
    ["chronicler.focus_inferred", "focus_block", "work"],
    ["chronicler.reading_inferred", "reading_block", "work"],
  ])("maps (%s, %s) → %s", (source, type, expected) => {
    expect(categoryForSource(source, type)).toBe(expected)
  })

  it("returns 'other' for an unknown source/type pair", () => {
    expect(categoryForSource("totally.unknown", "mystery_event")).toBe("other")
  })

  it("returns 'other' for a calendar (intent) episode — never a lane", () => {
    expect(categoryForSource("google_calendar.completed", "scheduled_block")).toBe("other")
  })

  it("returns 'other' when source matches but episode_type does not", () => {
    expect(categoryForSource("core.sessions", "not-work")).toBe("other")
  })

  it("returns 'other' for the empty pair", () => {
    expect(categoryForSource("", "")).toBe("other")
  })
})
