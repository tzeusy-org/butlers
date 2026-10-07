// @vitest-environment jsdom
// REQ-dashboard-design-language-007: mounted keyed count marks, listening shape/text and accessible bounds.
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

import { afterEach, describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"
import userEvent from "@testing-library/user-event"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"

import { ActivityStripe as SourceActivityStripe } from "./ActivityStripe"
import { AppTimezoneProvider } from "@/components/ui/timezone-context"
import { denseCountBuckets, orderedBuckets } from "@/lib/bucket-series"
import type { CountBucket } from "@/lib/bucket-series"

function ActivityStripe({ counts, windowEnd = new Date("2026-05-10T23:00:00Z"), ...props }: {
  counts: number[]; windowEnd?: Date; className?: string; onBarClick?: (index: number) => void
}) {
  // Explicit test source window; production never reconstructs it from position.
  const end = Math.floor(windowEnd.getTime() / 3_600_000) * 3_600_000 + 3_600_000
  const buckets: CountBucket[] = counts.map((count, index) => ({
    bucket_start: new Date(end - (24-index) * 3_600_000).toISOString(),
    bucket_end: new Date(end - (23-index) * 3_600_000).toISOString(), count, listening: "unknown",
  }))
  return <AppTimezoneProvider timezone="UTC"><SourceActivityStripe buckets={buckets} {...props} /></AppTimezoneProvider>
}

afterEach(() => cleanup())

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
    expect(html).not.toContain("background-color")
    expect(html).toContain("liveness unknown")
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
    expect(html).toContain("liveness unknown")
  })
})

// ---------------------------------------------------------------------------
// windowEnd prop — UTC-aligned peak-hour label
// ---------------------------------------------------------------------------

describe("ActivityStripe: windowEnd prop", () => {
  it("derives peak-hour label from windowEnd UTC hours, not local clock", () => {
    // Construct a windowEnd at UTC 14:30 so slot 23 = UTC hour 14.
    // With all counts zero except slot 20 (peak), the peak is 3 hours before slot 23:
    //   peakHour = (14 - 23 + 20 + 24) % 24 = 35 % 24 = 11 → "11:00"
    const windowEnd = new Date("2026-05-10T14:30:00.000Z") // UTC hour = 14
    const data = counts({ 20: 5 })
    const html = renderToStaticMarkup(
      <ActivityStripe counts={data} windowEnd={windowEnd} />,
    )
    expect(html).toContain("11:00")
  })

  it("uses slot 23 as the anchor: windowEnd at UTC 00:45 → slot 23 = hour 00", () => {
    // windowEnd UTC hour = 0, peakIdx = 23 (most recent slot):
    //   peakHour = (0 - 23 + 23 + 24) % 24 = 24 % 24 = 0 → "00:00"
    const windowEnd = new Date("2026-05-11T00:45:00.000Z") // UTC hour = 0
    const data = counts({ 23: 3 })
    const html = renderToStaticMarkup(
      <ActivityStripe counts={data} windowEnd={windowEnd} />,
    )
    expect(html).toContain("12:00 AM UTC")
  })

  it("windowEnd at UTC 23:00, peak at slot 0 → hour 00:00", () => {
    // windowEnd UTC hour = 23, peakIdx = 0 (oldest slot):
    //   peakHour = (23 - 23 + 0 + 24) % 24 = 24 % 24 = 0 → "00:00"
    const windowEnd = new Date("2026-05-10T23:00:00.000Z") // UTC hour = 23
    const data = counts({ 0: 7 })
    const html = renderToStaticMarkup(
      <ActivityStripe counts={data} windowEnd={windowEnd} />,
    )
    expect(html).toContain("12:00 AM UTC")
  })

  it("falls back gracefully when windowEnd is omitted (aria-label still present)", () => {
    // Without windowEnd the component falls back to new Date(); just verify the
    // aria-label is well-formed (total + "peak N at").
    const data = counts({ 10: 2 })
    const html = renderToStaticMarkup(<ActivityStripe counts={data} />)
    expect(html).toContain("total 2 sessions")
    expect(html).toContain("peak 2 at")
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
    // Original hours 2/20 and missing 5-7 are deterministic wire conformance,
    // not an elapsed PostgreSQL receiver recording claim.
    const end = Date.parse("2026-11-01T12:00:00Z")
    const buckets: CountBucket[] = Array.from({ length: 24 }, (_, index) => ({
      bucket_start: new Date(end - (24-index) * 3_600_000).toISOString(),
      bucket_end: new Date(end - (23-index) * 3_600_000).toISOString(),
      count: index === 2 ? 2 : index === 20 ? 20 : 0,
      listening: [5,6,7].includes(index) ? "deaf" : "live",
    }))
    const actual = renderToStaticMarkup(<AppTimezoneProvider timezone="America/New_York">
      <SourceActivityStripe buckets={[...buckets].reverse()} /></AppTimezoneProvider>)
    expect(actual).toContain("not listening 3h")
    expect(actual).toContain("total 22 sessions")
    expect(actual).toContain("EDT")
    expect(actual).toContain("EST")
    const india = renderToStaticMarkup(<AppTimezoneProvider timezone="Asia/Kolkata">
      <SourceActivityStripe buckets={[{ bucket_start:"2026-11-01T00:00:00Z", bucket_end:"2026-11-01T01:00:00Z", count:2, listening:"unknown" }]} />
      </AppTimezoneProvider>)
    expect(india).toContain("5:30 AM")
    expect(india).toContain("liveness unknown")
    expect(orderedBuckets([buckets[2], buckets[2]])).toEqual([])
    const window = { window_start:buckets[0].bucket_start, window_end:buckets[23].bucket_end,
      bucket_width_s:3600, counts_available:true }
    const sparse = denseCountBuckets([buckets[20], buckets[2]], window)
    expect(sparse[2].count).toBe(2)
    expect(sparse[20].count).toBe(20)
    expect(sparse[5].count).toBe(0)
    expect(sparse[5].listening).toBe("unknown")
    const unavailable = denseCountBuckets([], { ...window, counts_available:false })
    expect(unavailable.every(bucket => bucket.count === null && bucket.listening === "unknown")).toBe(true)
    const unknownCounts = renderToStaticMarkup(<SourceActivityStripe buckets={unavailable} />)
    expect(unknownCounts).toContain("count unavailable")
    expect(unknownCounts).not.toContain("total 0")
    expect(renderToStaticMarkup(<SourceActivityStripe counts={counts({2:2,20:20})} />)).toContain("Count window unavailable")

    // The mounted primitive must consume the declared window, not compress
    // two sparse source keys into adjacent ordinal cells.
    const onBucketClick = vi.fn()
    const mounted = render(<AppTimezoneProvider timezone="America/New_York">
      <SourceActivityStripe buckets={[buckets[20], buckets[2]]}
        window={{ ...window, counts_available:false }} onBucketClick={onBucketClick} />
      </AppTimezoneProvider>)
    const cells = screen.getAllByRole("button")
    expect(cells).toHaveLength(24)
    expect(cells[2].getAttribute("aria-label")).toContain("2 sessions")
    expect(cells[20].getAttribute("aria-label")).toContain("20 sessions")
    expect(cells[5].getAttribute("aria-label")).toContain("count unavailable; liveness unknown")
    expect(mounted.container.textContent).not.toContain("not listening")
    expect(screen.getByRole("group").getAttribute("aria-label")).toContain("known total 22 sessions, count incomplete")
    expect(cells[5].className).toContain("min-w-11")
    expect(cells[5].className).toContain("min-h-11")
    fireEvent.click(cells[20])
    expect(onBucketClick).toHaveBeenLastCalledWith(buckets[20])
    fireEvent.click(cells[5])
    expect(onBucketClick).toHaveBeenLastCalledWith({
      bucket_start:buckets[5].bucket_start, bucket_end:buckets[5].bucket_end,
      count:null, filtered:null, listening:"unknown",
    })
    mounted.unmount()
    const measuredEmpty = renderToStaticMarkup(<SourceActivityStripe buckets={[]}
      window={window} />)
    expect((measuredEmpty.match(/relative flex-1/g) ?? [])).toHaveLength(24)
    expect(measuredEmpty).toContain("total 0 sessions")
    expect(measuredEmpty).toContain("liveness unknown")
    const shortWindow = { ...window, window_end:buckets[9].bucket_end }
    const short = renderToStaticMarkup(<SourceActivityStripe buckets={[buckets[2]]} window={shortWindow} />)
    expect((short.match(/relative flex-1/g) ?? [])).toHaveLength(10)
    expect(renderToStaticMarkup(<SourceActivityStripe buckets={[buckets[20]]} window={shortWindow} />))
      .toContain("Count window unavailable")
    expect(renderToStaticMarkup(<SourceActivityStripe buckets={buckets} window={{ ...window, bucket_width_s:0 }} />))
      .toContain("Count window unavailable")
    expect(renderToStaticMarkup(<SourceActivityStripe buckets={buckets} window={null} />))
      .toContain("Count window unavailable")
    const onSparseBarClick = vi.fn()
    const indexed = render(<SourceActivityStripe buckets={[buckets[20], buckets[2]]}
      window={window} onBarClick={onSparseBarClick} />)
    fireEvent.click(screen.getAllByRole("button")[20])
    expect(onSparseBarClick).toHaveBeenLastCalledWith(20)
    indexed.unmount()

  })
})

// ---------------------------------------------------------------------------
// Optional interaction
// ---------------------------------------------------------------------------

describe("ActivityStripe: optional bar interaction", () => {
  it("renders focusable bars and reports the clicked slot when onBarClick is supplied", async () => {
    const onBarClick = vi.fn()
    render(<ActivityStripe counts={counts({ 5: 3 })} onBarClick={onBarClick} />)

    expect(screen.getByRole("group", { name: /Count activity/i })).toBeDefined()
    const bars = screen.getAllByRole("button")
    expect(bars).toHaveLength(24)

    fireEvent.click(bars[5])
    expect(onBarClick).toHaveBeenCalledWith(5)
    bars[5].focus()
    const user = userEvent.setup()
    await user.keyboard("{Enter}")
    await user.keyboard(" ")
    expect(onBarClick).toHaveBeenCalledTimes(3)
    expect(onBarClick).toHaveBeenLastCalledWith(5)
  })

  it("keeps interactive bars at the minimum target size inside a horizontally scrollable group", () => {
    render(<ActivityStripe counts={zeros()} onBarClick={() => {}} />)

    const stripe = screen.getByRole("group", { name: /Count activity/i })
    expect(stripe.className).toContain("overflow-x-auto")
    expect(screen.getAllByRole("button")[0].className).toContain("min-w-11")
    expect(screen.getAllByRole("button")[0].className).toContain("min-h-11")
  })

  it("renders a two-pixel focus indicator for each interactive slot", () => {
    render(<ActivityStripe counts={zeros()} onBarClick={() => {}} />)

    const firstBar = screen.getAllByRole("button")[0]
    expect(firstBar.className).toContain("focus-visible:ring-2")
    expect(firstBar.className).toContain("focus-visible:ring-offset-2")
    expect(firstBar.className).toContain("focus-visible:outline-1")
    expect(firstBar.className).toContain("focus-visible:outline-focus")
  })

  it("uses the supplied window end to label interactive slots accurately", () => {
    render(
      <ActivityStripe
        counts={counts({ 20: 5 })}
        windowEnd={new Date("2026-05-10T14:30:00.000Z")}
        onBarClick={() => {}}
      />,
    )

    expect(screen.getAllByRole("button")[20].getAttribute("aria-label")).toContain("11:00")
    expect(screen.getAllByRole("button")[20].getAttribute("aria-label")).toContain("UTC")
    expect(screen.getAllByRole("button")[20].getAttribute("aria-label")).toContain("5 sessions")
  })
})
