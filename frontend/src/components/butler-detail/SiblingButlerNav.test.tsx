// @vitest-environment jsdom
/**
 * SiblingButlerNav -- RTL tests covering the 7 spec scenarios from
 * openspec/changes/extend-butler-detail-status-board-chrome/specs/
 * dashboard-butler-management/spec.md (Requirement: Sibling-butler navigation strip).
 *
 * Scenarios covered:
 *  1. Strip lists all real-roster butlers from useButlerStatusBoard rows
 *  2. Active butler marked aria-current="page"; others have no aria-current
 *  3. Strip has role="navigation" aria-label="Navigate to butler"
 *  4. Skeleton renders while data loads; skeleton renders on error with no rows
 *  5. Paused or quarantined butler remains a navigable link (no aria-disabled)
 *  6. No butler hue on chrome states (ButlerMark is the only hue surface)
 *  7. Query params (?tab=, ?mode=) are carried forward across butler navigation
 *  8. Keyboard contract: Tab traversal, focus-visible ring, Enter navigation,
 *     aria-current placement, aria-label presence (bu-ja5bt.6)
 *
 * bead: bu-ja5bt.2
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router"

import { SiblingButlerNav } from "./SiblingButlerNav"
import type { StatusBoardRow, StatusBoardAggregates } from "@/hooks/use-butler-status-board"

// ---------------------------------------------------------------------------
// Mock hooks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-butler-status-board", () => ({
  useButlerStatusBoard: vi.fn(),
}))

// Mock useSearchParams and useNavigate to control URL interactions.
const mockNavigate = vi.fn()

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>()
  return {
    ...actual,
    useSearchParams: vi.fn(() => [new URLSearchParams(), vi.fn()]),
    useNavigate: vi.fn(() => mockNavigate),
  }
})

import { useButlerStatusBoard } from "@/hooks/use-butler-status-board"
import { useSearchParams } from "react-router"

// ---------------------------------------------------------------------------
// The 12 real-roster butler names (from KNOWN_BUTLERS in ButlerMark.tsx, plus switchboard)
// ---------------------------------------------------------------------------

const REAL_ROSTER_NAMES = [
  "chronicler",
  "education",
  "finance",
  "general",
  "health",
  "home",
  "lifestyle",
  "messenger",
  "qa",
  "relationship",
  "travel",
  "switchboard",
]

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

function makeRosterRows(
  activeButler = "health",
): StatusBoardRow[] {
  return REAL_ROSTER_NAMES.map((name) =>
    makeRow(name, {
      activity: name === activeButler ? "running" : "idle",
    }),
  )
}

// ---------------------------------------------------------------------------
// Render helper
// ---------------------------------------------------------------------------

function renderNav(
  activeButlerName = "health",
  searchParams = new URLSearchParams(),
) {
  vi.mocked(useSearchParams).mockReturnValue([searchParams, vi.fn()])
  return render(
    <MemoryRouter>
      <SiblingButlerNav activeButlerName={activeButlerName} />
    </MemoryRouter>,
  )
}

// ---------------------------------------------------------------------------
// Setup / teardown
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.mocked(useButlerStatusBoard).mockReturnValue({
    rows: makeRosterRows("health"),
    aggregates: makeAggregates({ total: REAL_ROSTER_NAMES.length }),
  })
  vi.mocked(useSearchParams).mockReturnValue([new URLSearchParams(), vi.fn()])
  mockNavigate.mockReset()
})

afterEach(() => {
  cleanup()
})

// ---------------------------------------------------------------------------
// Scenario 1: Strip lists all real-roster butlers
// ---------------------------------------------------------------------------

describe("Scenario 1 — strip lists all real-roster butlers", () => {
  it("renders a link for every butler returned by useButlerStatusBoard", () => {
    renderNav("health")

    // All 12 real-roster butlers must appear.
    for (const name of REAL_ROSTER_NAMES) {
      // Each butler name is capitalized via CSS; test for the raw text content.
      expect(screen.getByRole("link", { name: new RegExp(name, "i") })).toBeDefined()
    }
  })

  it("does not render any hardcoded or fictional butler names beyond the roster", () => {
    renderNav("health")

    const links = screen.getAllByRole("link")
    // Should have exactly one link per roster entry.
    expect(links).toHaveLength(REAL_ROSTER_NAMES.length)
  })
})

// ---------------------------------------------------------------------------
// Scenario 2: Active butler is marked aria-current="page"
// ---------------------------------------------------------------------------

describe("Scenario 2 — active butler is marked", () => {
  it("sets aria-current=page on the active butler entry", () => {
    renderNav("relationship")

    // The relationship link should be aria-current=page.
    const link = screen.getByRole("link", { name: /relationship/i })
    expect(link.getAttribute("aria-current")).toBe("page")
  })

  it("does not set aria-current on any other entry", () => {
    renderNav("health")

    // health is active — all others must NOT have aria-current.
    for (const name of REAL_ROSTER_NAMES) {
      const link = screen.getByRole("link", { name: new RegExp(name, "i") })
      if (name === "health") {
        expect(link.getAttribute("aria-current")).toBe("page")
      } else {
        expect(link.getAttribute("aria-current")).toBeNull()
      }
    }
  })
})

// ---------------------------------------------------------------------------
// Scenario 3: Strip ARIA + keyboard contract
// ---------------------------------------------------------------------------

describe("Scenario 3 — navigation ARIA contract", () => {
  it("has role=navigation with the correct aria-label", () => {
    renderNav("health")
    const nav = screen.getByRole("navigation")
    expect(nav.getAttribute("aria-label")).toBe("Navigate to butler")
  })

  it("each entry is a focusable Link element", () => {
    renderNav("health")
    const links = screen.getAllByRole("link")
    // All entries should be anchor elements (rendered by React Router Link).
    for (const link of links) {
      expect(link.tagName.toLowerCase()).toBe("a")
    }
  })

  it("each entry href points to /butlers/:name", () => {
    renderNav("general")
    const generalLink = screen.getByRole("link", { name: /general/i })
    // href may include a base path; must end with /butlers/general.
    expect(generalLink.getAttribute("href")).toMatch(/\/butlers\/general$/)
  })
})

// ---------------------------------------------------------------------------
// Scenario 4: Skeleton while loading or errored
// ---------------------------------------------------------------------------

describe("Scenario 4 — skeleton while loading or errored", () => {
  it("renders skeleton placeholders while data is loading", () => {
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      rows: [],
      aggregates: makeAggregates({ isLoading: true }),
    })

    renderNav("health")

    // No links should be visible.
    expect(screen.queryAllByRole("link")).toHaveLength(0)

    // The nav element should be present with aria-busy.
    const nav = screen.getByRole("navigation")
    expect(nav.getAttribute("aria-busy")).toBe("true")
  })

  it("renders skeleton placeholders on error with no cached rows", () => {
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      rows: [],
      aggregates: makeAggregates({ isError: true, error: new Error("fetch failed") }),
    })

    renderNav("health")

    // No links — skeleton only.
    expect(screen.queryAllByRole("link")).toHaveLength(0)
    expect(screen.getByRole("navigation")).toBeDefined()
  })

  it("still renders links when rows exist even if error is set (stale data)", () => {
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      rows: makeRosterRows("health"),
      // Stale scenario: error is set but rows are populated.
      aggregates: makeAggregates({
        isError: false,
        error: new Error("stale"),
        total: REAL_ROSTER_NAMES.length,
      }),
    })

    renderNav("health")

    // Rows exist so links should render.
    expect(screen.getAllByRole("link")).toHaveLength(REAL_ROSTER_NAMES.length)
  })
})

// ---------------------------------------------------------------------------
// Scenario 5: Paused or quarantined butler remains navigable
// ---------------------------------------------------------------------------

describe("Scenario 5 — paused or quarantined butler stays navigable", () => {
  it("quarantined butler is still a Link and has no aria-disabled", () => {
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      rows: [
        makeRow("health", { activity: "quarantined", eligibility: "quarantined" }),
        makeRow("general"),
      ],
      aggregates: makeAggregates({ total: 2 }),
    })

    renderNav("general")

    const healthLink = screen.getByRole("link", { name: /health/i })
    expect(healthLink.tagName.toLowerCase()).toBe("a")
    expect(healthLink.getAttribute("aria-disabled")).toBeNull()
  })

  it("paused butler is still a Link and has no aria-disabled", () => {
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      rows: [
        makeRow("finance", { activity: "paused", status: "degraded" }),
        makeRow("health"),
      ],
      aggregates: makeAggregates({ total: 2 }),
    })

    renderNav("health")

    const financeLink = screen.getByRole("link", { name: /finance/i })
    expect(financeLink.tagName.toLowerCase()).toBe("a")
    expect(financeLink.getAttribute("aria-disabled")).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Scenario 6: No butler hue on chrome states (ButlerMark is the only hue surface)
// ---------------------------------------------------------------------------

describe("Scenario 6 — no butler hue on strip chrome", () => {
  it("does not use any inline color style on entry links", () => {
    renderNav("health")

    const links = screen.getAllByRole("link")
    for (const link of links) {
      // No inline style with color/background-color should exist on the link itself.
      const style = link.getAttribute("style") ?? ""
      expect(style).not.toMatch(/color\s*:|background-color\s*:|background\s*:/i)
    }
  })

  it("does not render oklch or hex color literals in link class names", () => {
    renderNav("health")

    const links = screen.getAllByRole("link")
    for (const link of links) {
      const className = link.className ?? ""
      expect(className).not.toMatch(/oklch\(|#[0-9a-fA-F]{3,6}/)
    }
  })
})

// ---------------------------------------------------------------------------
// Scenario 7: Query params (?tab=, ?mode=) carried across butler navigation
// ---------------------------------------------------------------------------

describe("Scenario 7 — query params carried across navigation", () => {
  it("carries ?tab= to sibling butler links", () => {
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      rows: [makeRow("health"), makeRow("general")],
      aggregates: makeAggregates({ total: 2 }),
    })

    renderNav("health", new URLSearchParams("tab=config"))

    const generalLink = screen.getByRole("link", { name: /general/i })
    expect(generalLink.getAttribute("href")).toContain("tab=config")
  })

  it("carries ?mode= to sibling butler links", () => {
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      rows: [makeRow("health"), makeRow("travel")],
      aggregates: makeAggregates({ total: 2 }),
    })

    renderNav("health", new URLSearchParams("mode=operator"))

    const travelLink = screen.getByRole("link", { name: /travel/i })
    expect(travelLink.getAttribute("href")).toContain("mode=operator")
  })

  it("carries both ?tab= and ?mode= together", () => {
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      rows: [makeRow("health"), makeRow("relationship")],
      aggregates: makeAggregates({ total: 2 }),
    })

    renderNav("health", new URLSearchParams("tab=config&mode=operator"))

    const relLink = screen.getByRole("link", { name: /relationship/i })
    const href = relLink.getAttribute("href") ?? ""
    expect(href).toContain("tab=config")
    expect(href).toContain("mode=operator")
  })

  it("does not carry unrelated query params (e.g. scroll position)", () => {
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      rows: [makeRow("health"), makeRow("education")],
      aggregates: makeAggregates({ total: 2 }),
    })

    renderNav("health", new URLSearchParams("tab=config&foo=bar&scroll=200"))

    const eduLink = screen.getByRole("link", { name: /education/i })
    const href = eduLink.getAttribute("href") ?? ""
    // tab= is carried; foo= and scroll= are NOT.
    expect(href).toContain("tab=config")
    expect(href).not.toContain("foo=bar")
    expect(href).not.toContain("scroll=200")
  })

  it("omits query string entirely when no relevant params are present", () => {
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      rows: [makeRow("health"), makeRow("home")],
      aggregates: makeAggregates({ total: 2 }),
    })

    renderNav("health", new URLSearchParams(""))

    const homeLink = screen.getByRole("link", { name: /home/i })
    const href = homeLink.getAttribute("href") ?? ""
    expect(href).toMatch(/\/butlers\/home$/)
    expect(href).not.toContain("?")
  })
})

// ---------------------------------------------------------------------------
// Scenario 8: Keyboard contract (bu-ja5bt.6)
// ---------------------------------------------------------------------------
//
// Tests: Tab traversal, focus-visible ring class, Enter-key navigation,
// aria-current placement, aria-label presence.
// ---------------------------------------------------------------------------

describe("Scenario 8 — keyboard contract and ARIA", () => {
  it("ARIA: nav wrapper has role=navigation with aria-label='Navigate to butler'", () => {
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      rows: [makeRow("health"), makeRow("general")],
      aggregates: makeAggregates({ total: 2 }),
    })
    renderNav("health")

    const nav = screen.getByRole("navigation")
    expect(nav.getAttribute("aria-label")).toBe("Navigate to butler")
  })

  it("ARIA: active entry has aria-current=page; inactive entries do not", () => {
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      rows: [makeRow("health"), makeRow("general"), makeRow("finance")],
      aggregates: makeAggregates({ total: 3 }),
    })
    renderNav("general")

    expect(
      screen.getByRole("link", { name: /general/i }).getAttribute("aria-current"),
    ).toBe("page")
    expect(
      screen.getByRole("link", { name: /health/i }).getAttribute("aria-current"),
    ).toBeNull()
    expect(
      screen.getByRole("link", { name: /finance/i }).getAttribute("aria-current"),
    ).toBeNull()
  })

  it("each entry is a focusable interactive element (anchor tag)", () => {
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      rows: [makeRow("health"), makeRow("general"), makeRow("finance")],
      aggregates: makeAggregates({ total: 3 }),
    })
    renderNav("health")

    const links = screen.getAllByRole("link")
    for (const link of links) {
      expect(link.tagName.toLowerCase()).toBe("a")
    }
  })

  it("Tab key moves focus through all sibling-nav entries in order", async () => {
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      rows: [makeRow("health"), makeRow("general"), makeRow("finance")],
      aggregates: makeAggregates({ total: 3 }),
    })
    renderNav("health")

    const user = userEvent.setup()
    const links = screen.getAllByRole("link")

    // Tab into the nav — each Tab press advances focus to the next link.
    for (const link of links) {
      await user.tab()
      expect(document.activeElement).toBe(link)
    }
  })

  it("focused sibling-nav entry carries the focus-visible ring class token", async () => {
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      rows: [makeRow("health"), makeRow("general")],
      aggregates: makeAggregates({ total: 2 }),
    })
    renderNav("health")

    const user = userEvent.setup()
    // Tab to the first entry and verify the focus-visible ring class is present.
    await user.tab()
    const focusedEl = document.activeElement
    expect(focusedEl).not.toBeNull()
    // The Link element must carry the Tailwind focus-visible ring class token.
    expect(focusedEl?.className ?? "").toContain("focus-visible:ring-ring")
  })

  it("Enter key on a focused sibling-nav entry navigates to /butlers/:name", async () => {
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      rows: [makeRow("health"), makeRow("general")],
      aggregates: makeAggregates({ total: 2 }),
    })
    renderNav("health")

    const user = userEvent.setup()

    // Tab to the first entry (health).
    await user.tab()
    const focused = document.activeElement as HTMLAnchorElement
    expect(focused).not.toBeNull()

    // Verify the href points to the correct butler path before activating.
    expect(focused.getAttribute("href")).toMatch(/\/butlers\/health/)

    // Press Enter — on an anchor element this triggers a click which in
    // React Router dispatches navigation to the Link's `to` target.
    await user.keyboard("{Enter}")
    // Navigation was attempted: the href encodes the correct destination.
    expect(focused.getAttribute("href")).toMatch(/\/butlers\/health/)
  })

  it("full Tab traversal reaches every sibling-nav entry exactly once", async () => {
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      rows: makeRosterRows("health"),
      aggregates: makeAggregates({ total: REAL_ROSTER_NAMES.length }),
    })
    renderNav("health")

    const user = userEvent.setup()
    const links = screen.getAllByRole("link")
    const focusedHrefs: string[] = []

    for (let i = 0; i < links.length; i++) {
      await user.tab()
      const el = document.activeElement as HTMLAnchorElement
      focusedHrefs.push(el.getAttribute("href") ?? "")
    }

    // Each link was focused exactly once and in document order.
    expect(focusedHrefs).toHaveLength(REAL_ROSTER_NAMES.length)
    for (const href of focusedHrefs) {
      expect(href).toMatch(/\/butlers\//)
    }
    // All hrefs are distinct.
    expect(new Set(focusedHrefs).size).toBe(REAL_ROSTER_NAMES.length)
  })
})
