// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// BoardFooter component tests — bu-hb7dh.7
//
// Coverage:
//   - All six KPIs render with correct values.
//   - Status-tone dots appear only when corresponding count > 0.
//   - Dot absence asserted at zero for each status.
//   - Composition addendum shows correct butler/staffer counts.
//   - avgLoadPct null renders '—'.
//   - spendToday formats with 2 decimal places.
//   - No em-dashes in rendered text (the '—' null-fallback is the only allowed
//     occurrence, checked separately).
//   - No inline style on rendered DOM.
//   - A11y: role='contentinfo' on the footer element.
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { BoardFooter } from "./BoardFooter"
import type { StatusBoardAggregates } from "@/hooks/use-butler-status-board"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeAggregates(overrides: Partial<StatusBoardAggregates> = {}): StatusBoardAggregates {
  return {
    total: 10,
    butlerCount: 8,
    stafferCount: 2,
    active: 3,
    offline: 1,
    quarantined: 0,
    totalSessions24h: 42,
    totalSpendToday: 1.23,
    avgLoadPct: 50,
    isLoading: false,
    isError: false,
    error: null,
    refetch: () => {},
    heartbeatSourceError: false,
    registrySourceError: false,
    eligibilityUnavailable: 0,
    hasPerEntryErrors: false,
    sourcesPartiallyDegraded: false,
    ...overrides,
  }
}

function render(aggregates: StatusBoardAggregates): string {
  return renderToStaticMarkup(<BoardFooter aggregates={aggregates} />)
}

function parseHtml(html: string) {
  const div = document.createElement("div")
  div.innerHTML = html
  return div
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("BoardFooter", () => {
  it("renders a semantic <footer> element (no explicit role=contentinfo needed; valid inside main)", () => {
    // BoardFooter intentionally omits role="contentinfo" so it is valid inside the
    // <main> landmark rendered by Shell.tsx. The implicit <footer> role is
    // sufficient when not at the top level of the document.
    const html = render(makeAggregates())
    const div = parseHtml(html)
    const footer = div.querySelector("footer")
    expect(footer).not.toBeNull()
  })

  describe("KPI cells", () => {
    it("renders the ACTIVE count", () => {
      const html = render(makeAggregates({ active: 7 }))
      expect(html.toLowerCase()).toContain("active")
      expect(html).toContain("7")
    })

    it("renders the OFFLINE count", () => {
      const html = render(makeAggregates({ offline: 3 }))
      expect(html.toLowerCase()).toContain("offline")
      expect(html).toContain("3")
    })

    it("renders the QUARANTINED count", () => {
      const html = render(makeAggregates({ quarantined: 2 }))
      expect(html.toLowerCase()).toContain("quarantined")
      expect(html).toContain("2")
    })

    it("renders SESSIONS 24H with localized number", () => {
      const html = render(makeAggregates({ totalSessions24h: 1234 }))
      expect(html.toLowerCase()).toContain("sessions")
      // toLocaleString may render "1,234" or "1234" depending on locale
      expect(html).toContain("1")
      expect(html).toContain("234")
    })

    it("renders SPEND TODAY formatted as $N.NN", () => {
      const html = render(makeAggregates({ totalSpendToday: 7.5 }))
      expect(html).toContain("$7.50")
    })

    it("renders SPEND TODAY with two decimal places when round number", () => {
      const html = render(makeAggregates({ totalSpendToday: 10 }))
      expect(html).toContain("$10.00")
    })

    it("renders AVG LOAD with percent sign", () => {
      const html = render(makeAggregates({ avgLoadPct: 75 }))
      expect(html).toContain("75%")
    })

    it("renders AVG LOAD as '—' when avgLoadPct is null", () => {
      const html = render(makeAggregates({ avgLoadPct: null }))
      // The '—' fallback character is explicitly allowed here
      expect(html).toContain("—")
      expect(html).not.toContain("%")
    })
  })

  describe("status-tone dots", () => {
    it("renders emerald dot when active > 0", () => {
      const html = render(makeAggregates({ active: 1 }))
      expect(html).toContain("bg-emerald-500")
    })

    it("does NOT render emerald dot when active === 0", () => {
      const html = render(makeAggregates({ active: 0 }))
      expect(html).not.toContain("bg-emerald-500")
    })

    it("renders destructive dot when offline > 0", () => {
      const html = render(makeAggregates({ offline: 2, quarantined: 0 }))
      expect(html).toContain("bg-destructive")
    })

    it("renders destructive dot when quarantined > 0", () => {
      const html = render(makeAggregates({ offline: 0, quarantined: 1 }))
      expect(html).toContain("bg-destructive")
    })

    it("does NOT render destructive dot when offline === 0 and quarantined === 0", () => {
      const html = render(makeAggregates({ offline: 0, quarantined: 0 }))
      expect(html).not.toContain("bg-destructive")
    })

    it("renders no dots at all when all counts are zero", () => {
      const html = render(
        makeAggregates({ active: 0, offline: 0, quarantined: 0 }),
      )
      expect(html).not.toContain("bg-emerald-500")
      expect(html).not.toContain("bg-destructive")
    })
  })

  describe("composition addendum", () => {
    it("renders butler and staffer counts", () => {
      const html = render(makeAggregates({ butlerCount: 11, stafferCount: 3 }))
      expect(html).toContain("11 butlers")
      expect(html).toContain("3 staffers")
    })

    it("uses a comma separator between butlers and staffers (no em-dash)", () => {
      const html = render(makeAggregates({ butlerCount: 5, stafferCount: 1 }))
      expect(html).toContain("5 butlers")
      expect(html).toContain("1 staffer")
      expect(html).not.toContain("1 staffers") // singular when count === 1
      expect(html).not.toContain("—") // no em-dash in addendum
    })

    it("pluralizes butler correctly for singular count", () => {
      const html = render(makeAggregates({ butlerCount: 1, stafferCount: 2 }))
      expect(html).toContain("1 butler")
      expect(html).not.toContain("1 butlers") // singular when count === 1
      expect(html).toContain("2 staffers")
    })
  })

  it("contains no inline style attributes", () => {
    const html = render(makeAggregates())
    const div = parseHtml(html)
    const withStyle = div.querySelectorAll("[style]")
    expect(withStyle.length).toBe(0)
  })

  it("contains no em-dashes in non-null rendered text", () => {
    // When avgLoadPct is set, the '—' fallback is NOT used, so no em-dash at all
    const html = render(makeAggregates({ avgLoadPct: 50 }))
    expect(html).not.toContain("—")
    expect(html).not.toContain("&mdash;")
  })
})
