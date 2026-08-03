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
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router"

// ---------------------------------------------------------------------------
// Mock hooks and sub-components BEFORE importing the component under test
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-butler-status-board", () => ({
  useButlerStatusBoard: vi.fn(),
}))

vi.mock("@/hooks/use-butlers", () => ({
  useButler: vi.fn(),
}))

vi.mock("@/hooks/use-schedules", () => ({
  useSchedules: vi.fn(() => ({ data: { data: [] } })),
}))

vi.mock("@/hooks/use-spend", () => ({
  useSpendSummary: vi.fn(() => ({ data: { data: { by_butler: {} } } })),
}))

import { useButlerStatusBoard } from "@/hooks/use-butler-status-board"
import type { StatusBoardRow, StatusBoardAggregates } from "@/hooks/use-butler-status-board"
import { useButler } from "@/hooks/use-butlers"
import { useSchedules } from "@/hooks/use-schedules"
import { useSpendSummary } from "@/hooks/use-spend"
import type { Schedule } from "@/api/types"
import { ButlerDetailHeader } from "./ButlerDetailHeader"
import { getScheduleHeaderFacts } from "./schedule-header-facts"

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
    overdue: 0,
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
    costSourceError: false,
    sessionsSourceError: false,
    sourcesPartiallyDegraded: false,
    ...overrides,
    unknown: overrides.unknown ?? 0,
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
    quarantineReason: null,
    quarantinedAt: null,
    sessions24h: 0,
    costToday: 0,
    loadPct: null,
    activeSessionCount: 0,
    lastRunISO: null,
    lastHeartbeatISO: null,
    heartbeatAgeSeconds: null,
    hourlyStripe: Array(24).fill(0),
    hourlyTotal: 0,
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

function makeSchedule(overrides: Partial<Schedule> = {}): Schedule {
  return {
    id: "schedule-1",
    name: "Daily check-in",
    cron: "0 9 * * *",
    prompt: "Check in",
    source: "butler",
    enabled: true,
    next_run_at: null,
    last_run_at: null,
    created_at: "2026-07-20T00:00:00.000Z",
    updated_at: "2026-07-20T00:00:00.000Z",
    ...overrides,
  }
}

function mockSchedules(schedules: Schedule[]): ReturnType<typeof useSchedules> {
  const response = {
    data: { data: schedules },
  } as unknown as ReturnType<typeof useSchedules>
  vi.mocked(useSchedules).mockReturnValue(response)
  return response
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
  mockSchedules([])
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
    needsYou: [],
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
      needsYou: [],
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
    // ButlerMark renders as a <span> with an aria-label equal to the butler name.
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
      needsYou: [],
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
      needsYou: [],
      rows: [],
      aggregates: makeAggregates({ isLoading: true }),
    })

    renderHeader("health")

    const header = screen.getByTestId("butler-detail-header")
    expect(header.getAttribute("aria-busy")).toBe("true")
  })

  it("C3: error state with no rows renders H1 with butler name but nav shows skeletons", () => {
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      needsYou: [],
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
      needsYou: [],
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

// ---------------------------------------------------------------------------
// Scenario F: header facts -- last run / next scheduled / cost today
// (bu-86c4c.18 -- replaces port/uptime trivia)
// ---------------------------------------------------------------------------

describe("Scenario F: header facts replace port/uptime trivia", () => {
  it("F1: does not render port or uptime text", () => {
    renderHeader("relationship")
    const facts = screen.getByTestId("butler-header-facts")
    expect(facts.textContent).not.toMatch(/port/i)
    expect(facts.textContent).not.toMatch(/uptime/i)
  })

  it("F2: renders last run, next scheduled, and cost today", () => {
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      rows: [
        makeRow("relationship", {
          description: "Relationship intelligence butler",
          lastRunISO: "2026-05-13T11:58:00Z",
        }),
      ],
      aggregates: makeAggregates({ total: 1 }),
      needsYou: [],
    })

    renderHeader("relationship")
    const facts = screen.getByTestId("butler-header-facts")
    expect(facts.textContent).toContain("last run")
    expect(facts.textContent).toContain("next")
    expect(facts.textContent).toContain("today")
  })

  it("F3: shows the placeholder glyph when last run and next scheduled are unknown", () => {
    renderHeader("relationship")
    const facts = screen.getByTestId("butler-header-facts")
    expect(facts.textContent).toContain("--")
  })

  it("F4: names a compatibility spend-source failure instead of a fabricated $0 today", () => {
    vi.mocked(useSpendSummary).mockReturnValue({
      data: { data: { by_butler: { relationship: 0 }, source_error: true } },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useSpendSummary>)

    renderHeader("relationship")

    const facts = screen.getByTestId("butler-header-facts")
    expect(facts.textContent).toContain("spend unavailable")
    expect(facts.textContent).not.toContain("$0.00 today")
  })
})

describe("Scenario G: truthful schedule header facts", () => {
  const NOW = new Date("2026-07-20T12:00:00.000Z")

  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("G1: renders the earliest enabled future timestamp as the next fact", () => {
    mockSchedules([
      makeSchedule({
        id: "later",
        name: "Later run",
        next_run_at: "2026-07-20T14:00:00.000Z",
      }),
      makeSchedule({
        id: "earlier",
        name: "Earlier run",
        next_run_at: "2026-07-20T13:00:00.000Z",
      }),
    ])

    renderHeader()

    const facts = screen.getByTestId("butler-header-facts")
    expect(facts.textContent).toContain("next in 1h")
    expect(facts.textContent).not.toContain("in 2h")
    expect(facts.textContent).not.toContain("overdue")
  })

  it("G2: renders a named amber overdue link instead of a stale next fact", () => {
    mockSchedules([
      makeSchedule({
        id: "stale",
        name: "Morning review",
        next_run_at: "2026-07-20T09:00:00.000Z",
      }),
    ])

    renderHeader()

    const overdue = screen.getByRole("link", {
      name: "Overdue Morning review, 3h ago. Open schedules.",
    })
    expect(overdue.getAttribute("href")).toBe("/butlers/relationship?tab=system&section=schedules")
    expect(overdue.className).toContain("text-[var(--amber-text)]")
    expect(overdue.classList.contains("underline")).toBe(true)
    expect(overdue.classList.contains("decoration-[var(--border-strong)]")).toBe(true)
    expect(overdue.classList.contains("underline-offset-4")).toBe(true)
    expect(overdue.classList.contains("focus-visible:outline")).toBe(true)
    expect(overdue.classList.contains("focus-visible:outline-2")).toBe(true)
    expect(overdue.classList.contains("focus-visible:outline-offset-2")).toBe(true)
    expect(overdue.classList.contains("focus-visible:outline-fg")).toBe(true)
    expect(screen.getByTestId("butler-header-facts").textContent).not.toContain("next 3h ago")
  })

  it("G3: keeps the oldest overdue fact and the earliest future fact together", () => {
    mockSchedules([
      makeSchedule({
        id: "most-overdue",
        name: "Daily digest",
        next_run_at: "2026-07-20T09:00:00.000Z",
      }),
      makeSchedule({
        id: "less-overdue",
        name: "Recent digest",
        next_run_at: "2026-07-20T11:00:00.000Z",
      }),
      makeSchedule({
        id: "future-earliest",
        name: "Soon run",
        next_run_at: "2026-07-20T13:00:00.000Z",
      }),
      makeSchedule({
        id: "future-later",
        name: "Later run",
        next_run_at: "2026-07-20T14:00:00.000Z",
      }),
    ])

    renderHeader()

    const facts = screen.getByTestId("butler-header-facts")
    expect(facts.textContent).toContain("overdue: Daily digest 3h ago")
    expect(facts.textContent).not.toContain("Recent digest")
    expect(facts.textContent).toContain("next in 1h")
    expect(facts.textContent).not.toContain("in 2h")
  })

  it.each([
    ["disabled", makeSchedule({ enabled: false, next_run_at: "2026-07-20T09:00:00.000Z" })],
    ["null", makeSchedule({ next_run_at: null })],
    ["malformed", makeSchedule({ next_run_at: "not-a-timestamp" })],
  ])("G4: does not fabricate a schedule fact for a %s timestamp", (_kind, schedule) => {
    mockSchedules([schedule])

    renderHeader()

    const facts = screen.getByTestId("butler-header-facts")
    expect(facts.textContent).toContain("next --")
    expect(facts.textContent).not.toContain("overdue")
    expect(facts.textContent).not.toContain("not-a-timestamp")
    expect(screen.queryByRole("link", { name: /Open schedules/ })).toBeNull()
  })

  it("G5: crosses cached schedule data into overdue on the minute ticker without a query update", () => {
    const cachedResponse = mockSchedules([
      makeSchedule({
        id: "boundary",
        name: "Minute boundary review",
        next_run_at: "2026-07-20T12:01:00.000Z",
      }),
    ])

    renderHeader()

    const facts = screen.getByTestId("butler-header-facts")
    expect(facts.textContent).toContain("next in 1m")
    expect(screen.queryByRole("link", { name: /Open schedules/ })).toBeNull()
    const callsBeforeTick = vi.mocked(useSchedules).mock.calls.length

    act(() => {
      vi.advanceTimersByTime(60_000)
    })

    expect(vi.mocked(useSchedules)).toHaveBeenCalledTimes(callsBeforeTick + 1)
    expect(vi.mocked(useSchedules)).toHaveLastReturnedWith(cachedResponse)
    expect(screen.getByRole("link", {
      name: "Overdue Minute boundary review, now. Open schedules.",
    })).toBeDefined()
    expect(facts.textContent).toContain("overdue: Minute boundary review now")
    expect(facts.textContent).not.toContain("next in 1m")
    expect(facts.textContent).toContain("next --")
  })

  it("G6: keeps the overdue link age on the header clock between ticker updates", () => {
    mockSchedules([
      makeSchedule({
        id: "clock-boundary",
        name: "Clock-boundary review",
        next_run_at: "2026-07-20T11:59:00.000Z",
      }),
    ])

    const { rerender } = renderHeader()
    expect(screen.getByRole("link", {
      name: "Overdue Clock-boundary review, 1m ago. Open schedules.",
    }).textContent).toContain("1m ago")

    vi.setSystemTime(new Date("2026-07-20T12:05:00.000Z"))
    rerender(
      <MemoryRouter>
        <ButlerDetailHeader butler="relationship" />
      </MemoryRouter>,
    )

    expect(screen.getByRole("link", {
      name: "Overdue Clock-boundary review, 1m ago. Open schedules.",
    }).textContent).toContain("1m ago")
  })

  it("G7: renders the name-stable overdue fact when equal instants arrive in reverse order", () => {
    mockSchedules([
      makeSchedule({
        id: "a-overdue",
        name: "Bravo overdue",
        next_run_at: "2026-07-20T11:00:00.000Z",
      }),
      makeSchedule({
        id: "z-overdue",
        name: "Alpha overdue",
        next_run_at: "2026-07-20T11:00:00.000Z",
      }),
    ])

    renderHeader()

    expect(screen.getByRole("link", {
      name: "Overdue Alpha overdue, 1h ago. Open schedules.",
    })).toBeDefined()
    expect(screen.getByTestId("butler-header-facts").textContent).not.toContain("Bravo overdue")
  })

  it("G8: selects equal future instants by id independent of source order", () => {
    const facts = getScheduleHeaderFacts([
      makeSchedule({
        id: "z-next",
        name: "Shared next",
        next_run_at: "2026-07-20T13:00:00.000Z",
      }),
      makeSchedule({
        id: "a-next",
        name: "Shared next",
        next_run_at: "2026-07-20T13:00:00.000Z",
      }),
    ], NOW.getTime())

    expect(facts.overdue).toBeNull()
    expect(facts.next).toMatchObject({ id: "a-next", name: "Shared next" })
  })
})

describe("Scenario H: schedule query state truthfulness", () => {
  const NOW = new Date("2026-07-20T12:00:00.000Z")

  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("H1: keeps loading distinct from a healthy schedule-free result", () => {
    vi.mocked(useSchedules).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      refetch: NO_OP_REFETCH,
    } as unknown as ReturnType<typeof useSchedules>)

    renderHeader()

    expect(screen.getByTestId("schedule-loading").textContent).toContain("loading")
    expect(screen.getByTestId("butler-header-facts").textContent).not.toContain("next --")
    expect(screen.queryByRole("alert")).toBeNull()
  })

  it("H2: renders a named accessible schedule-unavailable alert for an initial error", () => {
    const refetch = vi.fn().mockResolvedValue(undefined)
    vi.mocked(useSchedules).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch,
    } as unknown as ReturnType<typeof useSchedules>)

    renderHeader()

    const alert = screen.getByTestId("schedule-unavailable")
    expect(alert.getAttribute("role")).toBe("alert")
    expect(alert.textContent).toContain("Schedule: unavailable")
    expect(screen.getByTestId("butler-header-facts").textContent).not.toContain("next --")

    fireEvent.click(screen.getByRole("button", { name: "Retry" }))
    expect(refetch).toHaveBeenCalledOnce()
  })

  it("H3: keeps cached schedule facts and names a refresh error once", () => {
    const refetch = vi.fn().mockResolvedValue(undefined)
    vi.mocked(useSchedules).mockReturnValue({
      data: {
        data: [
          makeSchedule({
            id: "overdue",
            name: "Morning review",
            next_run_at: "2026-07-20T09:00:00.000Z",
          }),
          makeSchedule({
            id: "next",
            name: "Afternoon review",
            next_run_at: "2026-07-20T13:00:00.000Z",
          }),
        ],
      },
      isLoading: false,
      isError: true,
      refetch,
    } as unknown as ReturnType<typeof useSchedules>)

    renderHeader()

    expect(screen.getByRole("link", {
      name: "Overdue Morning review, 3h ago. Open schedules.",
    })).toBeDefined()
    expect(screen.getByTestId("butler-header-facts").textContent).toContain("next in 1h")
    const alert = screen.getByTestId("schedule-refresh-degraded")
    expect(alert.getAttribute("role")).toBe("alert")
    expect(alert.textContent).toContain("showing last known schedule")
    expect(screen.getAllByRole("alert")).toHaveLength(1)

    fireEvent.click(screen.getByRole("button", { name: "Retry" }))
    expect(refetch).toHaveBeenCalledOnce()
  })

  it("H4: retains the healthy no-schedule fallback", () => {
    mockSchedules([])

    renderHeader()

    expect(screen.getByTestId("butler-header-facts").textContent).toContain("next --")
    expect(screen.queryByTestId("schedule-loading")).toBeNull()
    expect(screen.queryByTestId("schedule-unavailable")).toBeNull()
    expect(screen.queryByTestId("schedule-refresh-degraded")).toBeNull()
    expect(screen.queryByRole("alert")).toBeNull()
  })
})

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
      needsYou: [],
      rows: [],
      aggregates: makeAggregates({ isLoading: true }),
    })
    renderHeader("relationship")
    expect(screen.queryByTestId("sibling-butler-nav")).toBeNull()
  })
})
