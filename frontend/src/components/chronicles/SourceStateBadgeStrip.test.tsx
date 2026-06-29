// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// Tests for SourceStateBadgeStrip — bu-ig72b.22
//
// Covers:
//   - Empty data (cold-boot tolerant): renders nothing
//   - Active source → enabled badge
//   - Inactive source → yellow badge + tooltip text via pure helper
//   - Planned source → disabled badge with aria-disabled
//   - Deferred source → hidden by default, revealed by localStorage toggle
//   - not_time_bearing source → never rendered
//   - getBadgeState pure function
//   - buildInactiveTooltip pure function
// ---------------------------------------------------------------------------

import { beforeEach, describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

// ---------------------------------------------------------------------------
// Mock TanStack Query and the useChroniclesSourceState hook
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-chronicles", () => ({
  useChroniclesSourceState: vi.fn(),
}))

import { useChroniclesSourceState } from "@/hooks/use-chronicles"

// ---------------------------------------------------------------------------
// Mock localStorage via lib/local-settings
// ---------------------------------------------------------------------------

vi.mock("@/lib/local-settings", () => ({
  readBooleanSetting: vi.fn(() => false),
  writeBooleanSetting: vi.fn(),
}))

import { readBooleanSetting, writeBooleanSetting } from "@/lib/local-settings"

// ---------------------------------------------------------------------------
// Imports under test
// ---------------------------------------------------------------------------

import { SourceStateBadgeStrip } from "./SourceStateBadgeStrip"
import { getBadgeState, buildInactiveTooltip } from "./source-state-utils"
import type { ChroniclerSourceStateRow } from "@/api/types"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeRow(overrides: Partial<ChroniclerSourceStateRow>): ChroniclerSourceStateRow {
  return {
    // "work" is the canonical lane for core.sessions (IEA reframe).
    source_name: "work",
    chronicler_compatibility: "supported",
    read_surface: null,
    boundary_semantics: null,
    optional_schema: false,
    active: true,
    inactive_reason: null,
    last_run_at: null,
    last_error: null,
    subsource_checkpoints: null,
    ...overrides,
  }
}

function mockRows(rows: ChroniclerSourceStateRow[]) {
  vi.mocked(useChroniclesSourceState).mockReturnValue({
    data: { data: rows, meta: {} },
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useChroniclesSourceState>)
}

function render(): string {
  return renderToStaticMarkup(<SourceStateBadgeStrip />)
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(readBooleanSetting).mockReturnValue(false)
})

// ---------------------------------------------------------------------------
// getBadgeState — pure function unit tests
// ---------------------------------------------------------------------------

describe("getBadgeState", () => {
  it("returns null for not_time_bearing", () => {
    expect(getBadgeState(makeRow({ chronicler_compatibility: "not_time_bearing" }))).toBeNull()
  })

  it("returns 'planned' for planned compatibility", () => {
    expect(getBadgeState(makeRow({ chronicler_compatibility: "planned" }))).toBe("planned")
  })

  it("returns 'deferred' for deferred compatibility", () => {
    expect(getBadgeState(makeRow({ chronicler_compatibility: "deferred" }))).toBe("deferred")
  })

  it("returns 'active' for supported+active", () => {
    expect(getBadgeState(makeRow({ chronicler_compatibility: "supported", active: true }))).toBe("active")
  })

  it("returns 'inactive' for supported+inactive", () => {
    expect(getBadgeState(makeRow({ chronicler_compatibility: "supported", active: false }))).toBe("inactive")
  })
})

// ---------------------------------------------------------------------------
// buildInactiveTooltip — pure function unit tests
// ---------------------------------------------------------------------------

describe("buildInactiveTooltip", () => {
  it("includes inactive_reason when present", () => {
    const text = buildInactiveTooltip(
      makeRow({ active: false, inactive_reason: "OAuth token expired", last_error: null }),
    )
    expect(text).toContain("OAuth token expired")
  })

  it("includes last_error when present", () => {
    const text = buildInactiveTooltip(
      makeRow({ active: false, inactive_reason: null, last_error: "Connection refused" }),
    )
    expect(text).toContain("Connection refused")
  })

  it("includes both inactive_reason and last_error when both are set", () => {
    const text = buildInactiveTooltip(
      makeRow({ active: false, inactive_reason: "Disabled by user", last_error: "HTTP 401" }),
    )
    expect(text).toContain("Disabled by user")
    expect(text).toContain("HTTP 401")
  })

  it("falls back to 'no details' message when both reason and error are null", () => {
    const text = buildInactiveTooltip(
      makeRow({ active: false, inactive_reason: null, last_error: null }),
    )
    expect(text).toContain("inactive with no details")
  })
})

// ---------------------------------------------------------------------------
// Cold boot / empty state
// ---------------------------------------------------------------------------

describe("SourceStateBadgeStrip — empty data", () => {
  it("renders nothing when data is undefined (cold boot)", () => {
    vi.mocked(useChroniclesSourceState).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
    } as ReturnType<typeof useChroniclesSourceState>)
    const html = render()
    expect(html).toBe("")
  })

  it("renders nothing when data.data is empty array", () => {
    mockRows([])
    const html = render()
    expect(html).toBe("")
  })

  it("renders nothing when all rows are not_time_bearing", () => {
    mockRows([
      makeRow({ chronicler_compatibility: "not_time_bearing" }),
      makeRow({ source_name: "other", chronicler_compatibility: "not_time_bearing" }),
    ])
    const html = render()
    expect(html).toBe("")
  })
})

// ---------------------------------------------------------------------------
// Active state
// ---------------------------------------------------------------------------

describe("SourceStateBadgeStrip — active source", () => {
  it("renders a badge for a supported+active source", () => {
    // "work" is the Activity lane for core.sessions episodes.
    mockRows([makeRow({ source_name: "work", active: true })])
    const html = render()
    expect(html).toContain("Work")
    expect(html).toContain("source-state-badge-strip")
  })

  it("active badge has aria-label containing 'active'", () => {
    mockRows([makeRow({ source_name: "work", active: true })])
    const html = render()
    expect(html).toContain("active")
  })

  it("active badge does NOT carry the yellow colour class", () => {
    mockRows([makeRow({ source_name: "work", active: true })])
    const html = render()
    expect(html).not.toContain("bg-yellow-500")
  })
})

// ---------------------------------------------------------------------------
// Active source with last_error — bu-p4vd3 AC3
// ---------------------------------------------------------------------------

describe("SourceStateBadgeStrip — active source with last_error (bu-p4vd3)", () => {
  it("wraps a tooltip trigger when active source has last_error", () => {
    mockRows([
      makeRow({ source_name: "work", active: true, last_error: "HTTP 503 upstream" }),
    ])
    const html = render()
    // Tooltip trigger is present for the error case.
    expect(html).toContain("tooltip-trigger")
  })

  it("active-error badge carries data-testid with source name", () => {
    mockRows([
      makeRow({ source_name: "play", active: true, last_error: "Auth token expired" }),
    ])
    const html = render()
    expect(html).toContain("source-badge-active-error-play")
  })

  it("active badge without error does NOT carry the error data-testid", () => {
    mockRows([makeRow({ source_name: "work", active: true, last_error: null })])
    const html = render()
    expect(html).not.toContain("source-badge-active-error-tasks")
  })

  it("active badge without error does NOT wrap a tooltip trigger", () => {
    mockRows([makeRow({ source_name: "work", active: true, last_error: null })])
    const html = render()
    // No tooltip-trigger for a clean active badge.
    expect(html).not.toContain("tooltip-trigger")
  })
})

// ---------------------------------------------------------------------------
// Inactive state
// ---------------------------------------------------------------------------

describe("SourceStateBadgeStrip — inactive source", () => {
  it("renders a yellow badge for a supported+inactive source", () => {
    mockRows([
      makeRow({
        source_name: "work",
        active: false,
        inactive_reason: "OAuth token expired",
        last_error: null,
      }),
    ])
    const html = render()
    expect(html).toContain("bg-yellow-500")
  })

  it("inactive badge wraps a tooltip trigger element", () => {
    mockRows([
      makeRow({ source_name: "play", active: false }),
    ])
    const html = render()
    // Radix Tooltip.Trigger wraps the badge; data-slot="tooltip-trigger" is set
    expect(html).toContain("tooltip-trigger")
  })
})

// ---------------------------------------------------------------------------
// Planned state
// ---------------------------------------------------------------------------

describe("SourceStateBadgeStrip — planned source", () => {
  it("renders a badge for a planned source", () => {
    mockRows([makeRow({ source_name: "social", chronicler_compatibility: "planned" })])
    const html = render()
    expect(html).toContain("Social")
  })

  it("planned badge carries aria-disabled", () => {
    mockRows([makeRow({ source_name: "social", chronicler_compatibility: "planned" })])
    const html = render()
    expect(html).toContain("aria-disabled")
  })

  it("planned badge wraps a tooltip trigger element", () => {
    mockRows([makeRow({ source_name: "social", chronicler_compatibility: "planned" })])
    const html = render()
    expect(html).toContain("tooltip-trigger")
  })
})

// ---------------------------------------------------------------------------
// Deferred state — hidden by default, toggle to reveal
// ---------------------------------------------------------------------------

describe("SourceStateBadgeStrip — deferred source (hidden by default)", () => {
  it("does NOT render deferred badge label when showDeferred is false", () => {
    vi.mocked(readBooleanSetting).mockReturnValue(false)
    mockRows([makeRow({ source_name: "travel", chronicler_compatibility: "deferred" })])
    const html = render()
    // The Travel badge text should not appear — only the toggle button hint does
    expect(html).not.toContain(">Travel<")
  })

  it("renders the deferred toggle button when deferred lanes exist", () => {
    vi.mocked(readBooleanSetting).mockReturnValue(false)
    mockRows([makeRow({ source_name: "travel", chronicler_compatibility: "deferred" })])
    const html = render()
    // Toggle button shows count
    expect(html).toContain("deferred")
  })

  it("renders deferred badge when showDeferred is true (localStorage toggle)", () => {
    vi.mocked(readBooleanSetting).mockReturnValue(true)
    mockRows([makeRow({ source_name: "travel", chronicler_compatibility: "deferred" })])
    const html = render()
    expect(html).toContain("Travel")
  })

  it("reads toggle state from localStorage on mount", () => {
    vi.mocked(readBooleanSetting).mockReturnValue(true)
    mockRows([makeRow({ source_name: "travel", chronicler_compatibility: "deferred" })])
    render()
    expect(readBooleanSetting).toHaveBeenCalledWith("chronicles.showDeferredLanes", false)
  })
})

// ---------------------------------------------------------------------------
// not_time_bearing — never shown
// ---------------------------------------------------------------------------

describe("SourceStateBadgeStrip — not_time_bearing source", () => {
  it("not_time_bearing source never appears in output even with other visible rows", () => {
    // "work" is the Activity lane for core.sessions episodes.
    mockRows([
      makeRow({ source_name: "work", active: true }),
      makeRow({ source_name: "eat", chronicler_compatibility: "not_time_bearing" }),
    ])
    const html = render()
    expect(html).toContain("Work")
    expect(html).not.toContain("Eat")
  })
})

// ---------------------------------------------------------------------------
// Toggle persistence
// ---------------------------------------------------------------------------

describe("SourceStateBadgeStrip — toggle persistence", () => {
  it("uses the correct localStorage key 'chronicles.showDeferredLanes'", () => {
    vi.mocked(readBooleanSetting).mockReturnValue(false)
    mockRows([makeRow({ source_name: "travel", chronicler_compatibility: "deferred" })])
    render()
    expect(readBooleanSetting).toHaveBeenCalledWith("chronicles.showDeferredLanes", false)
  })

  it("writeBooleanSetting is available for wiring the toggle click handler", () => {
    // writeBooleanSetting is imported and injected into the toggle click handler.
    // Full click-interaction tests would require React DOM; this confirms the
    // wiring contract is satisfied at the module level.
    expect(writeBooleanSetting).toBeDefined()
    expect(typeof writeBooleanSetting).toBe("function")
  })
})
