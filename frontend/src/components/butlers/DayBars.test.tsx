// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// DayBars tests — bu-iuol4.15
//
// Coverage:
//   - empty data renders nothing
//   - all-zero data renders bars with minimal visible height
//   - mixed values produce max-relative bar heights
//   - variable lengths (7, 30, custom)
//   - optional color prop applies tokens-only class
//   - aria-label includes total and max
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { DayBars } from "./DayBars"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function zeros(n: number): number[] {
  return Array(n).fill(0)
}

// ---------------------------------------------------------------------------
// Empty data
// ---------------------------------------------------------------------------

describe("DayBars: empty data", () => {
  it("renders nothing (null) for an empty array", () => {
    const html = renderToStaticMarkup(<DayBars data={[]} />)
    expect(html).toBe("")
  })
})

// ---------------------------------------------------------------------------
// All-zero data
// ---------------------------------------------------------------------------

describe("DayBars: all-zero data", () => {
  it("renders N bar elements for N-element all-zero array", () => {
    const html = renderToStaticMarkup(<DayBars data={zeros(7)} />)
    // Each bar has flex-1; count occurrences.
    const matches = html.match(/flex-1/g) ?? []
    expect(matches.length).toBe(7)
  })

  it("applies minimal visible height (2px) to all-zero bars", () => {
    const html = renderToStaticMarkup(<DayBars data={zeros(7)} />)
    // Bars must have at least a 2px height so the row has presence.
    expect(html).toContain("2px")
  })

  it("does not apply the color class on all-zero bars", () => {
    const html = renderToStaticMarkup(
      <DayBars data={zeros(7)} color="bg-chart-1" />,
    )
    // No filled bars — the color class should not appear.
    expect(html).not.toContain("bg-chart-1")
  })

  it("renders with neutral wash class (bg-muted/40) on all-zero bars", () => {
    const html = renderToStaticMarkup(<DayBars data={zeros(7)} />)
    expect(html).toContain("bg-muted/40")
  })
})

// ---------------------------------------------------------------------------
// Mixed values
// ---------------------------------------------------------------------------

describe("DayBars: mixed values — max-relative bar heights", () => {
  it("renders N bars for N data points", () => {
    const data = [1, 3, 5, 2, 7, 4, 6]
    const html = renderToStaticMarkup(<DayBars data={data} />)
    const matches = html.match(/flex-1/g) ?? []
    expect(matches.length).toBe(7)
  })

  it("peak bar reaches 100% height", () => {
    const data = [1, 3, 10, 2, 5]
    const html = renderToStaticMarkup(<DayBars data={data} />)
    // The max value (10) should produce 100% height.
    expect(html).toContain("100%")
  })

  it("applies the color class to filled bars", () => {
    const data = [0, 5, 0]
    const html = renderToStaticMarkup(
      <DayBars data={data} color="bg-primary" />,
    )
    expect(html).toContain("bg-primary")
  })

  it("zero-count bars within a mixed series still render with minimal height", () => {
    // Bars with count=0 in a series that has a non-zero max use the 2% floor.
    const data = [0, 10, 0]
    const html = renderToStaticMarkup(<DayBars data={data} />)
    // Two bars should have height:2% (the floor for zero-count bars).
    // Use non-greedy match to count each occurrence separately.
    const matches = html.match(/height:2%/g) ?? []
    expect(matches.length).toBeGreaterThanOrEqual(2)
  })
})

// ---------------------------------------------------------------------------
// Variable lengths
// ---------------------------------------------------------------------------

describe("DayBars: variable lengths", () => {
  it("renders 7 bars for 7-element input", () => {
    const data = [1, 2, 3, 4, 5, 6, 7]
    const html = renderToStaticMarkup(<DayBars data={data} />)
    const matches = html.match(/flex-1/g) ?? []
    expect(matches.length).toBe(7)
  })

  it("renders 30 bars for 30-element input", () => {
    const data = Array.from({ length: 30 }, (_, i) => i % 5)
    const html = renderToStaticMarkup(<DayBars data={data} />)
    const matches = html.match(/flex-1/g) ?? []
    expect(matches.length).toBe(30)
  })

  it("renders 14 bars for 14-element input", () => {
    const data = Array.from({ length: 14 }, (_, i) => i)
    const html = renderToStaticMarkup(<DayBars data={data} />)
    const matches = html.match(/flex-1/g) ?? []
    expect(matches.length).toBe(14)
  })
})

// ---------------------------------------------------------------------------
// Optional color prop — tokens only
// ---------------------------------------------------------------------------

describe("DayBars: color prop", () => {
  it("defaults to bg-foreground/60 when color is omitted", () => {
    const data = [1, 2, 3]
    const html = renderToStaticMarkup(<DayBars data={data} />)
    expect(html).toContain("bg-foreground/60")
  })

  it("applies a custom color class to filled bars", () => {
    const data = [2, 4, 6]
    const html = renderToStaticMarkup(
      <DayBars data={data} color="bg-chart-2" />,
    )
    expect(html).toContain("bg-chart-2")
  })

  it("applies bg-muted-foreground as a valid token color", () => {
    const data = [3, 1, 4]
    const html = renderToStaticMarkup(
      <DayBars data={data} color="bg-muted-foreground" />,
    )
    expect(html).toContain("bg-muted-foreground")
  })
})

// ---------------------------------------------------------------------------
// Aria label
// ---------------------------------------------------------------------------

describe("DayBars: aria-label", () => {
  it("includes role=img", () => {
    const html = renderToStaticMarkup(<DayBars data={[1, 2, 3]} />)
    expect(html).toContain('role="img"')
  })

  it("includes data length in aria-label", () => {
    const html = renderToStaticMarkup(<DayBars data={[1, 2, 3, 4, 5, 6, 7]} />)
    expect(html).toContain("7-day activity")
  })

  it("includes correct total in aria-label", () => {
    const data = [1, 2, 3, 4]
    const html = renderToStaticMarkup(<DayBars data={data} />)
    expect(html).toContain("total 10")
  })

  it("includes correct peak in aria-label", () => {
    const data = [1, 2, 8, 3]
    const html = renderToStaticMarkup(<DayBars data={data} />)
    expect(html).toContain("peak 8")
  })

  it("aria-label for all-zero is well-formed", () => {
    const html = renderToStaticMarkup(<DayBars data={zeros(7)} />)
    expect(html).toContain("total 0")
    expect(html).toContain("peak 0")
  })
})

// ---------------------------------------------------------------------------
// height prop
// ---------------------------------------------------------------------------

describe("DayBars: height prop", () => {
  it("applies default height of 32px to the container", () => {
    const html = renderToStaticMarkup(<DayBars data={[1, 2, 3]} />)
    expect(html).toContain("height:32px")
  })

  it("applies custom height to the container", () => {
    const html = renderToStaticMarkup(<DayBars data={[1, 2, 3]} height={24} />)
    expect(html).toContain("height:24px")
  })
})
