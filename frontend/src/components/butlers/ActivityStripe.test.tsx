// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// ActivityStripe tests — bu-hb7dh.6
//
// Coverage:
//   - 24 cells rendered
//   - intensity scales with counts
//   - all-zero row renders neutral wash (bg-muted/40)
//   - aria-label includes total and peak
//   - no illegal inline style on empty cells
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { ActivityStripe } from "./ActivityStripe"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function zeros(): number[] {
  return Array(24).fill(0)
}

function counts(overrides: Partial<Record<number, number>> = {}): number[] {
  const arr = zeros()
  for (const [k, v] of Object.entries(overrides)) {
    if (v !== undefined) arr[Number(k)] = v
  }
  return arr
}

// ---------------------------------------------------------------------------
// Cell count
// ---------------------------------------------------------------------------

describe("ActivityStripe: 24 cells rendered", () => {
  it("renders exactly 24 child cells", () => {
    const html = renderToStaticMarkup(<ActivityStripe counts={zeros()} />)
    // Each cell is a div with flex-1 class; count occurrences.
    const matches = html.match(/flex-1/g) ?? []
    expect(matches.length).toBe(24)
  })
})

// ---------------------------------------------------------------------------
// All-zero row
// ---------------------------------------------------------------------------

describe("ActivityStripe: all-zero row", () => {
  it("applies neutral wash class (bg-muted/40) to all cells when all counts are 0", () => {
    const html = renderToStaticMarkup(<ActivityStripe counts={zeros()} />)
    // Every cell should be a neutral wash cell (no inline background-color).
    expect(html).not.toContain("background-color")
    expect(html).toContain("bg-muted/40")
  })

  it("does not render any inline style on empty cells", () => {
    const html = renderToStaticMarkup(<ActivityStripe counts={zeros()} />)
    expect(html).not.toContain("style=")
  })
})

// ---------------------------------------------------------------------------
// Intensity scaling
// ---------------------------------------------------------------------------

describe("ActivityStripe: intensity scales with counts", () => {
  it("renders inline style for filled cells", () => {
    const data = counts({ 5: 3, 10: 6 })
    const html = renderToStaticMarkup(<ActivityStripe counts={data} />)
    // Filled cells get an inline background-color style.
    expect(html).toContain("background-color")
  })

  it("peak cell has higher opacity than non-peak filled cell", () => {
    // slot 0: count 1 (low), slot 1: count 10 (peak)
    const data = counts({ 0: 1, 1: 10 })
    const html = renderToStaticMarkup(<ActivityStripe counts={data} />)
    // Extract all opacity percentages from color-mix calls.
    const opacities = [...html.matchAll(/var\(--foreground\) (\d+)%/g)].map(
      (m) => Number(m[1]),
    )
    expect(opacities.length).toBe(2)
    // The larger count (slot 1, peak) should have a higher opacity percentage.
    const [lowOpacity, peakOpacity] = opacities
    expect(peakOpacity).toBeGreaterThan(lowOpacity)
  })

  it("peak cell reaches ~75% opacity (0.20 + 1.0 * 0.55 = 0.75)", () => {
    // Only one non-zero cell — it is the max so intensity = 0.20 + 0.55 = 0.75.
    const data = counts({ 12: 5 })
    const html = renderToStaticMarkup(<ActivityStripe counts={data} />)
    // color-mix renders as "75%"
    expect(html).toContain("75%")
  })
})

// ---------------------------------------------------------------------------
// Aria label
// ---------------------------------------------------------------------------

describe("ActivityStripe: aria-label", () => {
  it("includes role=img", () => {
    const html = renderToStaticMarkup(<ActivityStripe counts={zeros()} />)
    expect(html).toContain('role="img"')
  })

  it("includes total session count", () => {
    const data = counts({ 3: 2, 7: 5 })
    const html = renderToStaticMarkup(<ActivityStripe counts={data} />)
    // total = 2 + 5 = 7
    expect(html).toContain("total 7 sessions")
  })

  it("includes peak session count", () => {
    const data = counts({ 3: 2, 7: 5 })
    const html = renderToStaticMarkup(<ActivityStripe counts={data} />)
    // peak = 5
    expect(html).toContain("peak 5 at")
  })

  it("all-zero row has total 0 and peak 0", () => {
    const html = renderToStaticMarkup(<ActivityStripe counts={zeros()} />)
    expect(html).toContain("total 0 sessions")
    expect(html).toContain("peak 0 at")
  })
})

// ---------------------------------------------------------------------------
// className forwarding
// ---------------------------------------------------------------------------

describe("ActivityStripe: className forwarding", () => {
  it("merges extra className onto the container", () => {
    const html = renderToStaticMarkup(
      <ActivityStripe counts={zeros()} className="my-custom-class" />,
    )
    expect(html).toContain("my-custom-class")
  })
})
