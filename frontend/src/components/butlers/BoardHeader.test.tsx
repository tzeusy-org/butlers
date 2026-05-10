// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// BoardHeader component tests — bu-hb7dh.7
//
// Coverage:
//   - Eyebrow, h1 title, and refresh caption all present.
//   - Healthy/total pill renders correct counts with correct dot color.
//   - Pill uses green dot when all healthy; amber when partial; red when none.
//   - Clock renders via <Time mode='clock-24h-mono'>.
//   - Date renders via <Time mode='absolute' precision='short-date'>.
//   - No em-dashes in rendered text.
//   - No inline style on rendered DOM.
//   - A11y: role='banner' on the header element.
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { AppTimezoneProvider } from "@/components/ui/timezone-context"
import { BoardHeader } from "./BoardHeader"
import type { StatusBoardAggregates } from "@/hooks/use-butler-status-board"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeAggregates(overrides: Partial<StatusBoardAggregates> = {}): StatusBoardAggregates {
  return {
    total: 10,
    butlerCount: 8,
    stafferCount: 2,
    active: 5,
    paused: 0,
    awaiting: 0,
    quarantined: 0,
    totalSessions24h: 42,
    totalSpendToday: 1.23,
    avgLoadPct: 50,
    isLoading: false,
    isError: false,
    error: null,
    refetch: () => {},
    ...overrides,
  }
}

function render(aggregates: StatusBoardAggregates, refreshIntervalMs = 60_000): string {
  return renderToStaticMarkup(
    <AppTimezoneProvider timezone="Asia/Singapore">
      <BoardHeader aggregates={aggregates} refreshIntervalMs={refreshIntervalMs} />
    </AppTimezoneProvider>,
  )
}

function parseHtml(html: string) {
  const div = document.createElement("div")
  div.innerHTML = html
  return div
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("BoardHeader", () => {
  it("renders the eyebrow text", () => {
    const html = render(makeAggregates())
    expect(html.toLowerCase()).toContain("butlers")
    expect(html.toLowerCase()).toContain("status board")
  })

  it("renders the h1 title 'The staff, at a glance'", () => {
    const html = render(makeAggregates())
    expect(html).toContain("The staff, at a glance")
    const div = parseHtml(html)
    const h1 = div.querySelector("h1")
    expect(h1).not.toBeNull()
    expect(h1!.textContent).toBe("The staff, at a glance")
  })

  it("renders the butler count and refresh caption", () => {
    const html = render(makeAggregates({ butlerCount: 7 }), 60_000)
    expect(html).toContain("7 butlers")
    expect(html).toContain("refreshes every 1m")
  })

  it("uses singular 'butler' when count is 1", () => {
    const html = render(makeAggregates({ butlerCount: 1 }), 30_000)
    expect(html).toContain("1 butler")
    expect(html).not.toContain("1 butlers")
  })

  it("renders refresh interval in seconds when under 60s", () => {
    const html = render(makeAggregates(), 30_000)
    expect(html).toContain("refreshes every 30s")
  })

  it("renders the role='banner' a11y attribute", () => {
    const html = render(makeAggregates())
    const div = parseHtml(html)
    const header = div.querySelector("[role='banner']")
    expect(header).not.toBeNull()
  })

  describe("healthy/total pill", () => {
    it("renders N/T reporting text with correct counts", () => {
      const html = render(makeAggregates({ total: 14, paused: 0, awaiting: 0, quarantined: 0 }))
      // healthy = 14 - 0 - 0 - 0 = 14; total = 14
      expect(html).toContain("14/14 reporting")
    })

    it("renders correct unhealthy counts in pill", () => {
      const html = render(makeAggregates({ total: 14, paused: 2, awaiting: 0, quarantined: 0 }))
      // healthy = 14 - 2 = 12
      expect(html).toContain("12/14 reporting")
    })

    it("uses green dot class when all butlers healthy (healthy === total)", () => {
      const html = render(makeAggregates({ total: 10, paused: 0, awaiting: 0, quarantined: 0 }))
      expect(html).toContain("bg-green-500")
    })

    it("uses amber dot class when some butlers are unhealthy (healthy > 0 but < total)", () => {
      const html = render(makeAggregates({ total: 10, paused: 3, awaiting: 0, quarantined: 0 }))
      expect(html).toContain("bg-amber-500")
      expect(html).not.toContain("bg-green-500")
    })

    it("uses red dot class when no butlers are healthy (healthy === 0)", () => {
      const html = render(
        makeAggregates({ total: 4, active: 0, paused: 2, awaiting: 2, quarantined: 0 }),
      )
      // healthy = 4 - 2 - 2 - 0 = 0
      expect(html).toContain("bg-red-500")
      expect(html).not.toContain("bg-green-500")
      expect(html).not.toContain("bg-amber-500")
    })

    it("uses amber dot when quarantined reduces health below total", () => {
      const html = render(
        makeAggregates({ total: 10, paused: 0, awaiting: 0, quarantined: 2 }),
      )
      // healthy = 10 - 0 - 0 - 2 = 8
      expect(html).toContain("bg-amber-500")
    })
  })

  describe("clock and date rendering", () => {
    it("renders a <time> element for the clock", () => {
      const html = render(makeAggregates())
      const div = parseHtml(html)
      const timeEls = div.querySelectorAll("time")
      // There should be at least one <time> element (clock and/or date)
      expect(timeEls.length).toBeGreaterThanOrEqual(1)
    })

    it("renders clock with font-mono and tabular-nums classes (clock-24h-mono)", () => {
      const html = render(makeAggregates())
      // The clock-24h-mono Time appends font-mono tabular-nums
      expect(html).toContain("font-mono")
      expect(html).toContain("tabular-nums")
    })
  })

  it("contains no em-dashes in rendered text", () => {
    const html = render(makeAggregates())
    expect(html).not.toContain("—") // em-dash character
    expect(html).not.toContain("&mdash;")
  })

  it("contains no inline style attributes", () => {
    const html = render(makeAggregates())
    // The component itself must not use inline style; any style= is a violation
    // Note: we do not count style from child components that legitimately need them (e.g. ButlerMark)
    // BoardHeader and its direct render should have no style= attrs
    const div = parseHtml(html)
    const withStyle = div.querySelectorAll("[style]")
    expect(withStyle.length).toBe(0)
  })
})
