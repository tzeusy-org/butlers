// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// StatusBoardCell tests — bu-hb7dh.6
//
// Coverage:
//   - Renders for activity='running' (green chip, no state rail).
//   - Renders for activity='offline' (red rail, red chip).
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

// StatusBoardCell now uses Link and useNavigate from react-router. Mock them so
// tests don't require a full router context: Link renders as a plain <a> and
// useNavigate returns a no-op to avoid context errors.
const mockNavigate = vi.fn()
vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>()
  return {
    ...actual,
    Link: ({ to, children, ...props }: { to: string; children: React.ReactNode; [key: string]: unknown }) =>
      <a href={to as string} {...props as Record<string, unknown>}>{children}</a>,
    useNavigate: vi.fn(() => mockNavigate),
  }
})

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
    quarantineReason: null,
    quarantinedAt: null,
    sessions24h: 7,
    costToday: 1.23,
    loadPct: 50,
    activeSessionCount: 0,
    lastRunISO: "2026-05-10T06:00:00.000Z",
    lastHeartbeatISO: null,
    heartbeatAgeSeconds: null,
    hourlyStripe: Array(24).fill(0),
    hourlyTotal: 7,
    hourlyStripeLoading: false,
    hourlyStripeError: false,
    schemaUnreachable: false,
    heartbeatUnavailable: false,
    cadenceSeconds: null,
    cadenceLabel: null,
    silenceSeconds: null,
    cadenceStatus: "unknown",
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

  it("renders emerald state rail for active eligibility", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ activity: "running", cellTone: "green", eligibility: "active" })} />,
    )
    expect(html).toContain("bg-[var(--green)]")
    expect(html).not.toContain("bg-destructive")
    expect(html).not.toContain("bg-[var(--amber)]")
  })
})

// ---------------------------------------------------------------------------
// activity='offline'
// ---------------------------------------------------------------------------

describe("StatusBoardCell: activity=offline", () => {
  it("renders OFFLINE chip", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ activity: "offline", cellTone: "red", status: "down" })} />,
    )
    expect(html).toContain("OFFLINE")
  })

  it("renders emerald rail for offline butler with active eligibility", () => {
    // Rail is keyed off eligibility, not activity: an active-registered offline butler gets emerald.
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ activity: "offline", cellTone: "red", status: "down", eligibility: "active" })} />,
    )
    expect(html).toContain("bg-[var(--green)]")
    expect(html).not.toContain("bg-destructive")
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

  it("renders red state rail for quarantined eligibility", () => {
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
    // The cell still renders the unrelated activity-stripe door <button>
    // (bu-27dxl.8.3), which comes after the KPI quartet in markup order --
    // scope the assertion to everything up to "SESS 24H" (the chip's region)
    // so it doesn't false-pass or false-fail on that unrelated button.
    const html = renderToStaticMarkup(
      <StatusBoardCell
        row={makeRow({ activity: "quarantined", cellTone: "red", eligibility: "quarantined" })}
      />,
    )
    const chipRegion = html.slice(0, html.indexOf("SESS 24H"))
    expect(chipRegion).not.toContain("<button")
  })
})

// ---------------------------------------------------------------------------
// activity='overdue' — cron-expectation join (bu-86c4c.17)
// ---------------------------------------------------------------------------

describe("StatusBoardCell: activity=overdue", () => {
  it("renders OVERDUE chip with amber tone", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ activity: "overdue", cellTone: "amber" })} />,
    )
    expect(html).toContain("OVERDUE")
    expect(html).toContain("text-[var(--amber-text)]")
  })

  it("chip title surfaces the cron-expectation message (silent Xd, expected Y)", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell
        row={makeRow({
          activity: "overdue",
          cellTone: "amber",
          cadenceLabel: "daily",
          silenceSeconds: 5 * 86400,
        })}
      />,
    )
    expect(html).toContain("Silent 5d, expected daily")
  })
})

// ---------------------------------------------------------------------------
// activity='unknown' — heartbeat-independent unknown state
// ---------------------------------------------------------------------------

describe("StatusBoardCell: activity=unknown", () => {
  it("renders UNKNOWN chip label when not masked by heartbeatUnavailable dash", () => {
    // activityLabel("unknown") is exercised directly through the exhaustive
    // switch; heartbeatUnavailable=false so the raw label renders.
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ activity: "unknown", cellTone: "neutral", heartbeatUnavailable: false })} />,
    )
    expect(html).toContain("UNKNOWN")
  })
})

// ---------------------------------------------------------------------------
// Quarantine reason surfaced inline (bu-86c4c.17)
// ---------------------------------------------------------------------------

describe("StatusBoardCell: quarantine reason", () => {
  it("renders the quarantine reason as an inline line when present", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell
        row={makeRow({
          activity: "quarantined",
          cellTone: "red",
          eligibility: "quarantined",
          quarantineReason: "3 consecutive heartbeat misses",
        })}
        onRestore={() => void 0}
      />,
    )
    expect(html).toContain("3 consecutive heartbeat misses")
  })

  it("renders no reason line when quarantineReason is null", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell
        row={makeRow({ activity: "quarantined", cellTone: "red", eligibility: "quarantined", quarantineReason: null })}
        onRestore={() => void 0}
      />,
    )
    expect(html).not.toContain("text-destructive leading-snug")
  })
})

// ---------------------------------------------------------------------------
// eligibility='stale' — amber rail + honest STALE chip label
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

  it("renders amber state rail for stale eligibility", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ activity: "idle", eligibility: "stale" })} />,
    )
    expect(html).toContain("bg-[var(--amber)]")
    expect(html).not.toContain("bg-destructive")
    expect(html).not.toContain("bg-[var(--green)]")
  })

  it("renders STALE chip label (not IDLE) for stale eligibility", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell
        row={makeRow({ activity: "idle", eligibility: "stale" })}
        onRestore={() => void 0}
      />,
    )
    expect(html).toContain("STALE")
    expect(html).toContain("text-[var(--amber-text)]")
    expect(html).not.toContain("IDLE")
  })
})

// ---------------------------------------------------------------------------
// Eligibility rail mapping — all four states
// ---------------------------------------------------------------------------

describe("StatusBoardCell: eligibility rail colors", () => {
  it("active eligibility → emerald rail", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ eligibility: "active" })} />,
    )
    expect(html).toContain("bg-[var(--green)]")
  })

  it("stale eligibility → amber rail", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ eligibility: "stale" })} />,
    )
    expect(html).toContain("bg-[var(--amber)]")
  })

  it("quarantined eligibility → red rail", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell
        row={makeRow({ activity: "quarantined", eligibility: "quarantined" })}
      />,
    )
    expect(html).toContain("bg-destructive")
  })

  it("unavailable eligibility → dim rail", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ eligibility: "unavailable" })} />,
    )
    expect(html).toContain("bg-muted-foreground/30")
    expect(html).not.toContain("bg-[var(--green)]")
    expect(html).not.toContain("bg-[var(--amber)]")
    expect(html).not.toContain("bg-destructive")
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
// Root tile navigation — div[role=link] fallback (bu-27dxl.8.3: every cell
// nests the activity-stripe door, so the root is never a real <a>)
// ---------------------------------------------------------------------------

describe("StatusBoardCell: root tile navigation", () => {
  it("renders the root container as div[role=link], not a real anchor", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ name: "health" })} />,
    )
    expect(html).toContain('role="link"')
    expect(html).not.toMatch(/<a [^>]*>/)
  })

  it("clicking the root tile navigates to /butlers/{name} (Overview, no tab param)", () => {
    mockNavigate.mockClear()
    const { getByRole } = render(
      <StatusBoardCell row={makeRow({ name: "health" })} />,
    )
    fireEvent.click(getByRole("link"))
    expect(mockNavigate).toHaveBeenCalledWith("/butlers/health")
  })

  it("Enter on the focused root tile navigates to /butlers/{name}", () => {
    mockNavigate.mockClear()
    const { getByRole } = render(
      <StatusBoardCell row={makeRow({ name: "health" })} />,
    )
    fireEvent.keyDown(getByRole("link"), { key: "Enter" })
    expect(mockNavigate).toHaveBeenCalledWith("/butlers/health")
  })
})

// ---------------------------------------------------------------------------
// Activity stripe door (bu-27dxl.8.3)
// ---------------------------------------------------------------------------

describe("StatusBoardCell: activity stripe door", () => {
  it("renders a nested <button> for the activity stripe", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ name: "health" })} />,
    )
    expect(html).toContain(`aria-label="Open health activity"`)
  })

  it("clicking the activity stripe navigates to /butlers/{name}?tab=activity", () => {
    mockNavigate.mockClear()
    const { getByRole } = render(
      <StatusBoardCell row={makeRow({ name: "health" })} />,
    )
    fireEvent.click(getByRole("button", { name: "Open health activity" }))
    expect(mockNavigate).toHaveBeenCalledWith("/butlers/health?tab=activity")
  })

  it("clicking the activity stripe does not also trigger the root tile's Overview navigation", () => {
    mockNavigate.mockClear()
    const { getByRole } = render(
      <StatusBoardCell row={makeRow({ name: "health" })} />,
    )
    fireEvent.click(getByRole("button", { name: "Open health activity" }))
    expect(mockNavigate).toHaveBeenCalledOnce()
    expect(mockNavigate).toHaveBeenCalledWith("/butlers/health?tab=activity")
  })

  it("the activity stripe door is present even while loading", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ hourlyStripeLoading: true, name: "health" })} />,
    )
    expect(html).toContain(`aria-label="Open health activity"`)
  })

  it("the activity stripe door is present even on error (sparse data stays reachable)", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ hourlyStripeError: true, name: "health" })} />,
    )
    expect(html).toContain(`aria-label="Open health activity"`)
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

  it("aria-label includes hourlyTotal count when loaded", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ hourlyTotal: 42, hourlyStripeLoading: false })} />,
    )
    expect(html).toContain("42 sessions in 24h")
  })

  it("aria-label uses truthful relative time for lastRunISO — not hardcoded 'recently' (bu-3dvwb)", () => {
    // 3 days ago should produce "3d ago", not "recently"
    vi.useFakeTimers()
    const now = new Date("2026-06-16T12:00:00.000Z")
    vi.setSystemTime(now)
    const threeDaysAgo = new Date(now.getTime() - 3 * 24 * 60 * 60 * 1_000).toISOString()

    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ lastRunISO: threeDaysAgo })} />,
    )
    expect(html).toContain("3d ago")
    expect(html).not.toContain("recently")

    vi.useRealTimers()
  })

  it("aria-label uses 'now' for very recent lastRunISO (bu-3dvwb)", () => {
    vi.useFakeTimers()
    const now = new Date("2026-06-16T12:00:00.000Z")
    vi.setSystemTime(now)
    const thirtySecsAgo = new Date(now.getTime() - 30_000).toISOString()

    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ lastRunISO: thirtySecsAgo })} />,
    )
    expect(html).toContain("now")
    expect(html).not.toContain("recently")

    vi.useRealTimers()
  })
})

// ---------------------------------------------------------------------------
// No forbidden inline style
// ---------------------------------------------------------------------------

describe("StatusBoardCell: no illegal inline style", () => {
  it("does not render style= on the container link element", () => {
    // The outer div[role=link] container must not have any inline style attribute.
    const html = renderToStaticMarkup(
      <StatusBoardCell
        row={makeRow({ hourlyStripe: Array(24).fill(0) })}
      />,
    )
    const linkMatch = html.match(/<div role="link"[^>]*>/)
    expect(linkMatch).not.toBeNull()
    expect(linkMatch![0]).not.toContain("style=")
  })

  it("does not render style= on the state rail element", () => {
    // The state rail must not have an inline style. Use quarantined eligibility for the red rail.
    const html = renderToStaticMarkup(
      <StatusBoardCell
        row={makeRow({ activity: "quarantined", eligibility: "quarantined", cellTone: "red" })}
        onRestore={() => void 0}
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
    const linkMatch = html.match(/<div role="link"[^>]*>/)
    expect(linkMatch).not.toBeNull()
    expect(linkMatch![0]).not.toContain("style=")
  })
})

// ---------------------------------------------------------------------------
// KPI values rendered
// ---------------------------------------------------------------------------

describe("StatusBoardCell: KPI values", () => {
  it("renders hourlyTotal as the SESS 24H KPI value", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ hourlyTotal: 13, hourlyStripeLoading: false })} />,
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

  it("renders a skeleton while hourly-activity is loading", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ hourlyStripeLoading: true })} />,
    )
    // Skeleton replaces both the SESS·24H number and the stripe bars
    expect(html).toContain('data-slot="skeleton"')
    // The 24 bar cells should NOT appear — stripe is hidden during load
    expect(html).not.toContain("flex gap-px h-[22px]")
  })

  it("renders an error indicator when hourly-activity has errored", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ hourlyStripeError: true, hourlyStripeLoading: false })} />,
    )
    expect(html).toContain("data unavailable")
    // SESS 24H KPI should show the dash fallback instead of 0
    expect(html).toContain(">—<")
    // The 24 bar cells should NOT appear — stripe is replaced by error message
    expect(html).not.toContain("flex gap-px h-[22px]")
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
    // Two buttons render per cell now (bu-27dxl.8.3 activity door + restore
    // chip) — select the chip specifically by its visible label.
    const onRestore = vi.fn()
    const { getByRole } = render(
      <StatusBoardCell
        row={makeRow({ eligibility: "stale", activity: "idle", name: "finance" })}
        onRestore={onRestore}
      />,
    )
    const btn = getByRole("button", { name: "STALE" })
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
    const btn = getByRole("button", { name: "QUARANTINED" })
    fireEvent.click(btn)
    expect(onRestore).toHaveBeenCalledOnce()
    expect(onRestore).toHaveBeenCalledWith("qa")
  })
})

// ---------------------------------------------------------------------------
// isRestorePending — disabled/pending chip state (bu-klxx6)
// ---------------------------------------------------------------------------

describe("StatusBoardCell: isRestorePending", () => {
  it("button has disabled attribute when isRestorePending=true", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell
        row={makeRow({ activity: "quarantined", cellTone: "red", eligibility: "quarantined" })}
        onRestore={() => void 0}
        isRestorePending={true}
      />,
    )
    expect(html).toContain('disabled=""')
  })

  it("button shows RESTORING text when isRestorePending=true", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell
        row={makeRow({ activity: "quarantined", cellTone: "red", eligibility: "quarantined" })}
        onRestore={() => void 0}
        isRestorePending={true}
      />,
    )
    expect(html).toContain("RESTORING")
    expect(html).not.toContain("QUARANTINED")
  })

  it("stale chip shows RESTORING and is disabled when isRestorePending=true", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell
        row={makeRow({ activity: "idle", eligibility: "stale" })}
        onRestore={() => void 0}
        isRestorePending={true}
      />,
    )
    expect(html).toContain("RESTORING")
    expect(html).toContain('disabled=""')
    expect(html).not.toContain("STALE")
  })

  it("button is not disabled when isRestorePending=false (default)", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell
        row={makeRow({ activity: "quarantined", cellTone: "red", eligibility: "quarantined" })}
        onRestore={() => void 0}
      />,
    )
    expect(html).not.toContain('disabled=""')
    expect(html).toContain("QUARANTINED")
  })
})

// ---------------------------------------------------------------------------
// staffer type affordance (bu-f2tcy)
// ---------------------------------------------------------------------------

describe("StatusBoardCell: staffer type affordance", () => {
  it("renders circular ButlerMark (border-radius:50%) for type=staffer", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ name: "switchboard", type: "staffer" })} />,
    )
    expect(html).toContain("border-radius:50%")
  })

  it("renders squircle ButlerMark for type=butler", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ name: "general", type: "butler" })} />,
    )
    expect(html).not.toContain("border-radius:50%")
  })
})

// ---------------------------------------------------------------------------
// heartbeatUnavailable display (bu-ywz06)
// ---------------------------------------------------------------------------

describe("StatusBoardCell: heartbeatUnavailable=true renders honest state", () => {
  it("shows '—' for activity chip when heartbeatUnavailable=true instead of 'IDLE'", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell
        row={makeRow({ activity: "idle", heartbeatUnavailable: true })}
      />,
    )
    // The activity chip must show the dash, not the activity verb.
    expect(html).toContain("—")
    expect(html).not.toContain("IDLE")
  })

  it("does not show '—' for activity chip when heartbeatUnavailable=false", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell
        row={makeRow({ activity: "idle", heartbeatUnavailable: false })}
      />,
    )
    expect(html).toContain("IDLE")
  })

  it("aria-label includes 'heartbeat unavailable' when heartbeatUnavailable=true", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ heartbeatUnavailable: true })} />,
    )
    expect(html).toContain("heartbeat unavailable")
  })

  it("aria-label includes activity verb when heartbeatUnavailable=false", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell row={makeRow({ activity: "running", heartbeatUnavailable: false })} />,
    )
    expect(html).toContain("running")
    expect(html).not.toContain("heartbeat unavailable")
  })

  it("schemaUnreachable=true row still renders without crashing", () => {
    const html = renderToStaticMarkup(
      <StatusBoardCell
        row={makeRow({ schemaUnreachable: true, heartbeatUnavailable: true, loadPct: null })}
      />,
    )
    // The LOAD KPI should show '—' (loadPct=null)
    expect(html).toContain("—")
  })

  it("restorable button chip shows '—' (not activity label) when heartbeatUnavailable=true", () => {
    const onRestore = vi.fn()
    const html = renderToStaticMarkup(
      <StatusBoardCell
        row={makeRow({ activity: "quarantined", cellTone: "red", eligibility: "quarantined", heartbeatUnavailable: true })}
        onRestore={onRestore}
      />,
    )
    expect(html).toContain("—")
    expect(html).not.toContain("QUARANTINED")
  })
})
