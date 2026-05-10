// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// StatusBoardCell tests — bu-hb7dh.6
//
// Coverage:
//   - Renders for activity='running' (green chip, no state rail).
//   - Renders for activity='paused' (red rail, red chip).
//   - Renders for activity='awaiting' (amber rail, amber chip).
//   - Renders for activity='quarantined' (red rail, clickable chip; click invokes onRestore).
//   - Missing data: lastRunISO=null shows '—'; loadPct=null shows '—'; costToday=0 shows '$0.00'.
//   - Hover state surfaces 'open →'.
//   - Link href is /butlers/{name}.
//   - A11y label asserted.
//   - No forbidden inline style on rendered DOM (except ActivityStripe intensity cells).
// ---------------------------------------------------------------------------

import { afterEach, describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"
import { render, fireEvent, cleanup } from "@testing-library/react"

import { StatusBoardCell } from "./StatusBoardCell"
import type { StatusBoardRow } from "@/hooks/use-butler-status-board"

afterEach(() => { cleanup() })

// ---------------------------------------------------------------------------
// Helper: build a minimal StatusBoardRow with safe defaults
// ---------------------------------------------------------------------------

function makeRow(overrides: Partial<StatusBoardRow> = {}): StatusBoardRow {
  return {
    name: "general",
    type: "butler",
    description: "The general-purpose assistant.",
    status: "ok",
    activity: "idle",
    cellTone: "neutral",
    eligibility: "active",
    sessions24h: 7,
    costToday: 1.23,
    loadPct: 50,
    lastRunISO: "2026-05-10T06:00:00.000Z",
    hourlyStripe: Array(24).fill(0),
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// activity='running'
// ---------------------------------------------------------------------------

describe("StatusBoardCell: activity=running", () => {
  it("renders RUNNING chip", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ activity: "running", cellTone: "green" })} />,
    )
    expect(html).toContain("RUNNING")
  })

  it("does not render a state rail for neutral/green tone", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ activity: "running", cellTone: "green" })} />,
    )
    // Rail only appears for red/amber. bg-destructive and bg-amber-500 should be absent.
    expect(html).not.toContain("bg-destructive")
    expect(html).not.toContain("bg-amber-500")
  })
})

// ---------------------------------------------------------------------------
// activity='paused'
// ---------------------------------------------------------------------------

describe("StatusBoardCell: activity=paused", () => {
  it("renders PAUSED chip", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ activity: "paused", cellTone: "red" })} />,
    )
    expect(html).toContain("PAUSED")
  })

  it("renders red state rail for tone=red", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ activity: "paused", cellTone: "red" })} />,
    )
    expect(html).toContain("bg-destructive")
  })
})

// ---------------------------------------------------------------------------
// activity='awaiting'
// ---------------------------------------------------------------------------

describe("StatusBoardCell: activity=awaiting", () => {
  it("renders AWAITING chip", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ activity: "awaiting", cellTone: "amber" })} />,
    )
    expect(html).toContain("AWAITING")
  })

  it("renders amber state rail for tone=amber", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ activity: "awaiting", cellTone: "amber" })} />,
    )
    expect(html).toContain("bg-amber-500")
  })
})

// ---------------------------------------------------------------------------
// activity='quarantined' — clickable chip
// ---------------------------------------------------------------------------

describe("StatusBoardCell: activity=quarantined", () => {
  it("renders QUARANTINED chip", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell
        row={makeRow({ activity: "quarantined", cellTone: "red", eligibility: "quarantined" })}
        onRestore={() => void 0}
      />,
    )
    expect(html).toContain("QUARANTINED")
  })

  it("renders red state rail for quarantined activity", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell
        row={makeRow({ activity: "quarantined", cellTone: "red", eligibility: "quarantined" })}
        onRestore={() => void 0}
      />,
    )
    expect(html).toContain("bg-destructive")
  })

  it("renders chip as a <button> when onRestore is provided", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell
        row={makeRow({ activity: "quarantined", cellTone: "red", eligibility: "quarantined" })}
        onRestore={() => void 0}
      />,
    )
    expect(html).toContain("<button")
  })

  it("chip is not a button when onRestore is absent", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell
        row={makeRow({ activity: "quarantined", cellTone: "red", eligibility: "quarantined" })}
      />,
    )
    expect(html).not.toContain("<button")
  })
})

// ---------------------------------------------------------------------------
// eligibility='stale' — also triggers clickable chip
// ---------------------------------------------------------------------------

describe("StatusBoardCell: eligibility=stale", () => {
  it("renders chip as a <button> when eligibility is stale and onRestore provided", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell
        row={makeRow({ activity: "idle", eligibility: "stale" })}
        onRestore={() => void 0}
      />,
    )
    expect(html).toContain("<button")
  })
})

// ---------------------------------------------------------------------------
// Missing data fallbacks
// ---------------------------------------------------------------------------

describe("StatusBoardCell: missing data", () => {
  it("shows — for LAST when lastRunISO is null", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ lastRunISO: null })} />,
    )
    // The KPI LAST cell should contain the dash fallback.
    expect(html).toContain("—")
  })

  it("shows — for LOAD when loadPct is null", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ loadPct: null })} />,
    )
    expect(html).toContain("—")
  })

  it("shows $0.00 for SPEND when costToday is 0", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ costToday: 0 })} />,
    )
    expect(html).toContain("$0.00")
  })
})

// ---------------------------------------------------------------------------
// Hover affordance
// ---------------------------------------------------------------------------

describe("StatusBoardCell: hover affordance", () => {
  it("renders 'open →' text in the DOM", () => {
    const html = renderToStaticMarkup(<StatusBoardCell row={makeRow()} />)
    expect(html).toContain("open →")
  })

  it("uses group-hover opacity transition (not inline style) for the affordance", () => {
    const html = renderToStaticMarkup(<StatusBoardCell row={makeRow()} />)
    expect(html).toContain("group-hover:opacity-85")
  })
})

// ---------------------------------------------------------------------------
// Link href
// ---------------------------------------------------------------------------

describe("StatusBoardCell: link href", () => {
  it("links to /butlers/{name}", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ name: "health" })} />,
    )
    expect(html).toContain('href="/butlers/health"')
  })
})

// ---------------------------------------------------------------------------
// A11y label
// ---------------------------------------------------------------------------

describe("StatusBoardCell: a11y", () => {
  it("renders an aria-label on the link element", () => {
    const html = renderToStaticMarkup(<StatusBoardCell row={makeRow()} />)
    expect(html).toContain("aria-label=")
  })

  it("aria-label includes butler name", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ name: "finance" })} />,
    )
    expect(html).toContain("finance")
  })

  it("aria-label includes activity", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ activity: "running", cellTone: "green" })} />,
    )
    expect(html).toContain("running")
  })

  it("aria-label includes sessions24h count", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ sessions24h: 42 })} />,
    )
    expect(html).toContain("42 sessions in 24h")
  })
})

// ---------------------------------------------------------------------------
// No forbidden inline style
// ---------------------------------------------------------------------------

describe("StatusBoardCell: no illegal inline style", () => {
  it("does not render style= on the container link element", () => {
    // The outer <a> container must not have any inline style attribute.
    const html = renderToStaticMarkup(
      <StatusBoardCell
        row={makeRow({ hourlyStripe: Array(24).fill(0) })}
      />,
    )
    const linkMatch = html.match(/<a [^>]*>/)
    expect(linkMatch).not.toBeNull()
    expect(linkMatch![0]).not.toContain("style=")
  })

  it("does not render style= on the state rail element", () => {
    // The state rail (absolute div) must not have an inline style.
    const html = renderToStaticMarkup(
      <StatusBoardCell
        row={makeRow({ activity: "paused", cellTone: "red" })}
      />,
    )
    // Rail contains bg-destructive and absolute. Check no style= on it.
    const railMatch = html.match(/absolute[^"]*bg-destructive[^"]*"/)
    expect(railMatch).not.toBeNull()
    // The rail element opening tag must not have style=.
    const railTagMatch = html.match(/<div class="[^"]*absolute[^"]*bg-destructive[^"]*"[^>]*>/)
    expect(railTagMatch).not.toBeNull()
    expect(railTagMatch![0]).not.toContain("style=")
  })

  it("does not render style= on the container or rail elements for a non-zero stripe", () => {
    // Even with a non-zero stripe, the container, rail, and chip must have no style=.
    const stripe = Array(24).fill(0)
    stripe[12] = 5
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ hourlyStripe: stripe })} />,
    )
    // ActivityStripe's intensity cell IS allowed to have style= (typed-primitive exemption).
    // But all cells outside ActivityStripe must not. We check the container-level elements
    // do not have style= by verifying the link itself has no style attribute.
    const linkMatch = html.match(/<a [^>]*>/)
    expect(linkMatch).not.toBeNull()
    expect(linkMatch![0]).not.toContain("style=")
  })
})

// ---------------------------------------------------------------------------
// KPI values rendered
// ---------------------------------------------------------------------------

describe("StatusBoardCell: KPI values", () => {
  it("renders sessions24h count", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ sessions24h: 13 })} />,
    )
    expect(html).toContain(">13<")
  })

  it("renders SPEND with dollar sign and 2 decimals", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ costToday: 2.5 })} />,
    )
    expect(html).toContain("$2.50")
  })

  it("renders loadPct with percent sign", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ loadPct: 75 })} />,
    )
    expect(html).toContain("75%")
  })
})

// ---------------------------------------------------------------------------
// ActivityStripe embedded
// ---------------------------------------------------------------------------

describe("StatusBoardCell: ActivityStripe embedded", () => {
  it("renders the 24H ACTIVITY label", () => {
    const html = renderToStaticMarkup(<StatusBoardCell row={makeRow()} />)
    expect(html).toContain("24H ACTIVITY")
  })

  it("renders past-24h label without em-dash", () => {
    const html = renderToStaticMarkup(<StatusBoardCell row={makeRow()} />)
    expect(html).toContain("past 24 h")
    expect(html).not.toContain("—")  // no em-dash in stripe caption area
  })

  it("renders 24 ActivityStripe cells (role=img wrapper)", () => {
    const html = renderToStaticMarkup(<StatusBoardCell row={makeRow()} />)
    // The ActivityStripe is wrapped in role="img". Extract that section and count flex-1 cells.
    const stripeStart = html.indexOf('role="img"')
    expect(stripeStart).toBeGreaterThan(-1)
    const stripeSection = html.slice(stripeStart)
    const matches = stripeSection.match(/flex-1/g) ?? []
    expect(matches.length).toBe(24)
  })
})

// ---------------------------------------------------------------------------
// onRestore callback
// ---------------------------------------------------------------------------

describe("StatusBoardCell: onRestore callback", () => {
  it("chip renders as button for stale eligibility", () => {
    const onRestore = vi.fn()
    const html = renderToStaticMarkup(
      <StatusBoardCell
        row={makeRow({ eligibility: "stale", activity: "idle" })}
        onRestore={onRestore}
      />,
    )
    expect(html).toContain("<button")
  })

  it("clicking the restore chip invokes onRestore with the butler name", () => {
    const onRestore = vi.fn()
    const { getByRole } = render(
      <StatusBoardCell
        row={makeRow({ eligibility: "stale", activity: "idle", name: "finance" })}
        onRestore={onRestore}
      />,
    )
    const btn = getByRole("button")
    fireEvent.click(btn)
    expect(onRestore).toHaveBeenCalledOnce()
    expect(onRestore).toHaveBeenCalledWith("finance")
  })

  it("clicking the restore chip for quarantined activity invokes onRestore", () => {
    const onRestore = vi.fn()
    const { getByRole } = render(
      <StatusBoardCell
        row={makeRow({ activity: "quarantined", cellTone: "red", eligibility: "quarantined", name: "qa" })}
        onRestore={onRestore}
      />,
    )
    const btn = getByRole("button")
    fireEvent.click(btn)
    expect(onRestore).toHaveBeenCalledOnce()
    expect(onRestore).toHaveBeenCalledWith("qa")
  })
})
