// ---------------------------------------------------------------------------
// Tests for LANE_TAXONOMY — bu-ig72b.5
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"

import { LANE_TAXONOMY, type Category, type LaneConfig } from "./lane-taxonomy"

// All 9 stable category strings defined by the backend (aggregations.py).
const EXPECTED_CATEGORIES: Category[] = [
  "work",
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
  it("contains exactly the 9 expected categories", () => {
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
