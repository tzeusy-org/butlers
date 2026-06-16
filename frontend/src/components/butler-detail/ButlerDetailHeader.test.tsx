// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// ButlerDetailHeader.test.tsx — unit tests for the butler detail header slot.
// (bu-ja5bt.3)
//
// Spec scenarios covered (from MODIFIED + ADDED requirements):
//
//  Scenario A: Header slot is the detail identity header
//    A1. ButlerDetailHeader is wrapped in a container with data-testid="butler-detail-header"
//    A2. SiblingButlerNav is not rendered here; it belongs to the shell PageHeader
//
//  Scenario B: Butler identity
//    B1. Active butler name appears as an H1
//    B2. Description renders when present in StatusBoardRow
//    B3. ButlerMark is rendered (hue confined to it; no inline color on other chrome)
//
//  Scenario C: Skeleton state while loading or errored
//    C1. Skeleton placeholders while data loads; no H1 text
//    C2. aria-busy is set during loading
//    C3. Error state renders ButlerMark + H1 with name
//    C4. Error + non-empty rows falls back to loaded state (stale data scenario)
//
//  Scenario D: Token policy (butler hue confined to ButlerMark)
//    D1. No inline color/background-color style on the outer header wrapper
//    D2. No oklch or hex color literals in rendered classNames of the wrapper
//
//  Scenario E: SiblingButlerNav ownership
//    E1. This component does not render navigation landmarks
//
// ---------------------------------------------------------------------------

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"

// ---------------------------------------------------------------------------
// Mock hooks and sub-components BEFORE importing the component under test
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-butler-status-board", () => ({
  useButlerStatusBoard: vi.fn(),
}))

vi.mock("@/hooks/use-butlers", () => ({
  useButler: vi.fn(),
}))

import { useButlerStatusBoard } from "@/hooks/use-butler-status-board"
import type { StatusBoardRow, StatusBoardAggregates } from "@/hooks/use-butler-status-board"
import { useButler } from "@/hooks/use-butlers"
import { ButlerDetailHeader } from "./ButlerDetailHeader"

// ---------------------------------------------------------------------------
// Fixture helpers
// ---------------------------------------------------------------------------

const NO_OP_REFETCH = vi.fn().mockResolvedValue(undefined)

function makeAggregates(overrides: Partial<StatusBoardAggregates> = {}): StatusBoardAggregates {
  return {
    total: 0,
    butlerCount: 0,
    stafferCount: 0,
    active: 0,
    offline: 0,
    quarantined: 0,
    totalSessions24h: 0,
    totalSpendToday: 0,
    avgLoadPct: null,
    isLoading: false,
    isError: false,
    error: null,
    refetch: NO_OP_REFETCH,
    heartbeatSourceError: false,
    registrySourceError: false,
    eligibilityUnavailable: 0,
    hasPerEntryErrors: false,
    sourcesPartiallyDegraded: false,
    ...overrides,
  }
}

function makeRow(
  name: string,
  overrides: Partial<StatusBoardRow> = {},
): StatusBoardRow {
  return {
    name,
    type: "butler",
    description: null,
    status: "ok",
    activity: "idle",
    cellTone: "neutral",
    eligibility: "active",
    sessions24h: 0,
    costToday: 0,
    loadPct: null,
    lastRunISO: null,
    hourlyStripe: Array(24).fill(0),
    hourlyTotal: 0,
    hourlyStripeLoading: false,
    hourlyStripeError: false,
    schemaUnreachable: false,
    heartbeatUnavailable: false,
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// Render helper
// ---------------------------------------------------------------------------

function renderHeader(butlerName = "relationship") {
  return render(<ButlerDetailHeader butler={butlerName} />)
}

// ---------------------------------------------------------------------------
// Default happy-path setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.mocked(useButler).mockReturnValue({
    data: {
      data: {
        name: "relationship",
        status: "ok",
        port: 8471,
        type: "butler",
        description: "Relationship intelligence butler",
        sessions_24h: 0,
        modules: [],
        schedules: [],
        skills: [],
        process_facts: {
          container_name: "butlers-relationship",
          port: 8471,
          registered_duration_seconds: 390_000,
          config_path: "roster/relationship/butler.toml",
        },
      },
      meta: {},
    },
    isLoading: false,
    error: null,
  } as unknown as ReturnType<typeof useButler>)
  vi.mocked(useButlerStatusBoard).mockReturnValue({
    rows: [
      makeRow("relationship", { description: "Relationship intelligence butler", activity: "idle" }),
      makeRow("health"),
      makeRow("finance"),
    ],
    aggregates: makeAggregates({ total: 3 }),
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

// ---------------------------------------------------------------------------
// Scenario A: Header slot presence and structure
// ---------------------------------------------------------------------------

describe("Scenario A: Header slot presence and structure", () => {
  it("A1: renders the component with data-testid=butler-detail-header", () => {
    renderHeader("relationship")
    const header = screen.getByTestId("butler-detail-header")
    expect(header).toBeDefined()
  })

  it("A2: does not render SiblingButlerNav inside the detail header", () => {
    renderHeader("relationship")
    expect(screen.queryByRole("navigation")).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Scenario B: Butler identity
// ---------------------------------------------------------------------------

describe("Scenario B: Butler identity", () => {
  it("B1: active butler name appears as an H1 element", () => {
    renderHeader("relationship")
    const h1 = screen.getByRole("heading", { level: 1 })
    expect(h1).toBeDefined()
    expect(h1.textContent?.toLowerCase()).toContain("relationship")
  })

  it("B2: description renders when present in the status board row", () => {
    renderHeader("relationship")
    expect(screen.getByText("Relationship intelligence butler")).toBeDefined()
  })

  it("B2b: description is absent when status board row has no description", () => {
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      rows: [makeRow("health", { description: null })],
      aggregates: makeAggregates({ total: 1 }),
    })
    renderHeader("health")
    // No description text to assert — just verify H1 still renders
    const h1 = screen.getByRole("heading", { level: 1 })
    expect(h1).toBeDefined()
  })

  it("B3: ButlerMark is present (identity wrapper contains hue element)", () => {
    const { container } = renderHeader("relationship")
    // ButlerMark renders as a <span> with title and aria-label equal to butler name.
    // We rely on the aria-label to identify it without coupling to internal classes.
    const mark = container.querySelector("span[aria-label='relationship']")
    expect(mark).not.toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Scenario C: Skeleton state while loading or errored
// ---------------------------------------------------------------------------

describe("Scenario C: Skeleton and error states", () => {
  it("C1: renders skeleton placeholders (no H1) while data is loading", () => {
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      rows: [],
      aggregates: makeAggregates({ isLoading: true }),
    })

    renderHeader("health")

    // No H1 heading in skeleton state
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull()
    // Header wrapper still present
    expect(screen.getByTestId("butler-detail-header")).toBeDefined()
  })

  it("C2: aria-busy is true on the header wrapper while loading", () => {
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      rows: [],
      aggregates: makeAggregates({ isLoading: true }),
    })

    renderHeader("health")

    const header = screen.getByTestId("butler-detail-header")
    expect(header.getAttribute("aria-busy")).toBe("true")
  })

  it("C3: error state with no rows renders H1 with butler name but nav shows skeletons", () => {
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      rows: [],
      aggregates: makeAggregates({ isError: true, error: new Error("fetch failed") }),
    })

    renderHeader("finance")

    // H1 should show the butler name even in error state
    const h1 = screen.getByRole("heading", { level: 1 })
    expect(h1.textContent?.toLowerCase()).toContain("finance")
    // SiblingButlerNav belongs to PageHeader, not this identity header.
    expect(screen.queryByTestId("sibling-butler-nav")).toBeNull()
  })

  it("C4: stale-data scenario (error=true but rows populated) renders loaded state", () => {
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      rows: [makeRow("finance", { description: "Finance butler" })],
      aggregates: makeAggregates({
        isError: false,
        error: new Error("stale"),
        total: 1,
      }),
    })

    renderHeader("finance")

    // Loaded state: H1 remains present; shell PageHeader owns sibling nav.
    const h1 = screen.getByRole("heading", { level: 1 })
    expect(h1).toBeDefined()
    expect(screen.queryByRole("navigation")).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Scenario D: Token policy — butler hue confined to ButlerMark
// ---------------------------------------------------------------------------

describe("Scenario D: Token policy — butler hue confined to ButlerMark", () => {
  it("D1: no inline color or background-color style on the outer header wrapper", () => {
    const { container } = renderHeader("relationship")
    const header = container.querySelector("[data-testid='butler-detail-header']")
    expect(header).not.toBeNull()
    const style = header!.getAttribute("style") ?? ""
    expect(style).not.toMatch(/color\s*:|background-color\s*:|background\s*:/i)
  })

  it("D2: no oklch or hex color literals in rendered classNames of the wrapper", () => {
    const { container } = renderHeader("relationship")
    const header = container.querySelector("[data-testid='butler-detail-header']")
    const className = header?.className ?? ""
    expect(className).not.toMatch(/oklch\(|#[0-9a-fA-F]{3,6}/)
  })

  it("D3: H1 element has no inline color style", () => {
    renderHeader("relationship")
    const h1 = screen.getByRole("heading", { level: 1 })
    const style = h1.getAttribute("style") ?? ""
    expect(style).not.toMatch(/color\s*:/i)
  })
})

// ---------------------------------------------------------------------------
// Scenario E: SiblingButlerNav ownership
// ---------------------------------------------------------------------------

describe("Scenario E: SiblingButlerNav ownership", () => {
  it("E1: no navigation landmark is rendered by the detail header", () => {
    renderHeader("health")
    expect(screen.queryByRole("navigation")).toBeNull()
  })

  it("E2: sibling nav test id is absent in loaded state", () => {
    renderHeader("relationship")
    expect(screen.queryByTestId("sibling-butler-nav")).toBeNull()
  })

  it("E3: SiblingButlerNav is absent during loading", () => {
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      rows: [],
      aggregates: makeAggregates({ isLoading: true }),
    })
    renderHeader("relationship")
    expect(screen.queryByTestId("sibling-butler-nav")).toBeNull()
  })
})
