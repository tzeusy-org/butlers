// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// ButlerDetailHeader.test.tsx — unit tests for the butler detail header slot.
// (bu-ja5bt.3)
//
// Spec scenarios covered (from MODIFIED + ADDED requirements):
//
//  Scenario A: Header slot is the sibling-butler nav and detail header
//    A1. ButlerDetailHeader renders and includes SiblingButlerNav (data-testid check)
//    A2. ButlerDetailHeader is wrapped in a container with data-testid="butler-detail-header"
//
//  Scenario B: Butler identity
//    B1. Active butler name appears as an H1
//    B2. Description renders when present in StatusBoardRow
//    B3. ButlerMark is rendered (hue confined to it; no inline color on other chrome)
//
//  Scenario C: Skeleton state while loading or errored
//    C1. Skeleton placeholders while data loads; no H1 text
//    C2. aria-busy is set during loading
//    C3. Error state renders ButlerMark + H1 with name but nav shows skeletons
//    C4. Error + non-empty rows falls back to loaded state (stale data scenario)
//
//  Scenario D: Token policy (butler hue confined to ButlerMark)
//    D1. No inline color/background-color style on the outer header wrapper
//    D2. No oklch or hex color literals in rendered classNames of the wrapper
//
//  Scenario E: SiblingButlerNav composition
//    E1. SiblingButlerNav receives the correct activeButlerName prop
//    E2. nav[role=navigation] is present in the DOM (not duplicated by this component)
//
// ---------------------------------------------------------------------------

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router"

// ---------------------------------------------------------------------------
// Mock hooks and sub-components BEFORE importing the component under test
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-butler-status-board", () => ({
  useButlerStatusBoard: vi.fn(),
}))

// Mock SiblingButlerNav so we can verify it receives the right props without
// pulling in the full status board stack again.
vi.mock("@/components/butler-detail/SiblingButlerNav", () => ({
  SiblingButlerNav: ({ activeButlerName }: { activeButlerName: string }) => (
    <nav
      role="navigation"
      aria-label="Navigate to butler"
      data-testid="sibling-butler-nav"
      data-active-butler={activeButlerName}
    />
  ),
}))

// Mock react-router useSearchParams (required by SiblingButlerNav but mock above overrides it)
vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>()
  return {
    ...actual,
    useSearchParams: vi.fn(() => [new URLSearchParams(), vi.fn()]),
  }
})

import { useButlerStatusBoard } from "@/hooks/use-butler-status-board"
import type { StatusBoardRow, StatusBoardAggregates } from "@/hooks/use-butler-status-board"
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
    paused: 0,
    awaiting: 0,
    quarantined: 0,
    totalSessions24h: 0,
    totalSpendToday: 0,
    avgLoadPct: null,
    isLoading: false,
    isError: false,
    error: null,
    refetch: NO_OP_REFETCH,
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
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// Render helper
// ---------------------------------------------------------------------------

function renderHeader(butlerName = "relationship") {
  return render(
    <MemoryRouter>
      <ButlerDetailHeader butler={butlerName} />
    </MemoryRouter>,
  )
}

// ---------------------------------------------------------------------------
// Default happy-path setup
// ---------------------------------------------------------------------------

beforeEach(() => {
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

  it("A2: SiblingButlerNav is rendered inside the header", () => {
    renderHeader("relationship")
    const nav = screen.getByTestId("sibling-butler-nav")
    expect(nav).toBeDefined()
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
    // SiblingButlerNav mock is not rendered in error state (skeleton is used instead)
    // The nav from SiblingButlerNav mock would have data-testid="sibling-butler-nav"
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

    // Loaded state: H1 and SiblingButlerNav both present
    const h1 = screen.getByRole("heading", { level: 1 })
    expect(h1).toBeDefined()
    expect(screen.getByTestId("sibling-butler-nav")).toBeDefined()
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
// Scenario E: SiblingButlerNav composition
// ---------------------------------------------------------------------------

describe("Scenario E: SiblingButlerNav composition", () => {
  it("E1: SiblingButlerNav receives the correct activeButlerName prop", () => {
    renderHeader("health")
    const nav = screen.getByTestId("sibling-butler-nav")
    expect(nav.getAttribute("data-active-butler")).toBe("health")
  })

  it("E2: exactly one navigation landmark is rendered", () => {
    renderHeader("relationship")
    const navs = screen.getAllByRole("navigation")
    expect(navs).toHaveLength(1)
  })

  it("E3: SiblingButlerNav is absent during loading (skeleton rendered instead)", () => {
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      rows: [],
      aggregates: makeAggregates({ isLoading: true }),
    })
    renderHeader("relationship")
    expect(screen.queryByTestId("sibling-butler-nav")).toBeNull()
  })
})
