// ---------------------------------------------------------------------------
// use-butler-status-board.test.ts (bu-86c4c.17)
//
// The hook is now a thin single-request adapter over GET /api/butlers/board:
// all activity/tone/eligibility/cadence/cost/load derivation moved
// server-side (covered by tests/api/test_butlers_board.py). These tests
// cover the wire-shape mapping (snake_case -> camelCase), stable row order
// passthrough, the needsYou strip, and aggregate/loading/error propagation.
// ---------------------------------------------------------------------------

import { describe, expect, it, vi, beforeEach } from "vitest"

import type { BoardResponse, BoardRow, BoardAggregates } from "@/api/types"

const mockGetButlersBoard = vi.fn()
const mockUseQuery = vi.fn()

vi.mock("@/api/index.ts", () => ({
  getButlersBoard: (...args: Parameters<typeof mockGetButlersBoard>) => mockGetButlersBoard(...args),
}))

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-query")>()
  return {
    ...actual,
    useQuery: (...args: Parameters<typeof mockUseQuery>) => mockUseQuery(...args),
  }
})

// React.useMemo must execute synchronously in tests (no React rendering context).
vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>()
  return {
    ...actual,
    useMemo: (fn: () => unknown) => fn(),
  }
})

// useButlerStatusBoard (via useButlersBoard) now calls useBusAwarePollInterval
// (bu-01r64.3), which reads the real EventBusProvider context via useContext
// -- invalid outside a React render. These tests call the hook directly (no
// renderHook), so mock the bus status as always "open".
vi.mock("@/lib/event-bus", () => ({
  useEventBus: () => ({ status: "open", lastEventAt: null, subscribe: vi.fn() }),
}))

import { useButlerStatusBoard } from "./use-butler-status-board"

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeRow(overrides: Partial<BoardRow> = {}): BoardRow {
  return {
    name: "finance",
    type: "butler",
    description: "Finance butler",
    status: "ok",
    activity: "idle",
    cell_tone: "neutral",
    eligibility: "active",
    quarantine_reason: null,
    quarantined_at: null,
    sessions_24h: 0,
    cost_today: null,
    load_pct: null,
    max_concurrent: null,
    active_session_count: 0,
    last_session_at: null,
    last_heartbeat_at: null,
    heartbeat_age_seconds: null,
    heartbeat_unavailable: false,
    schema_unreachable: false,
    hourly_stripe: Array(24).fill(0),
    hourly_total: 0,
    cadence_seconds: null,
    cadence_label: null,
    silence_seconds: null,
    cadence_status: "unknown",
    ...overrides,
  }
}

function makeAggregates(overrides: Partial<BoardAggregates> = {}): BoardAggregates {
  return {
    total: 0,
    butler_count: 0,
    staffer_count: 0,
    active: 0,
    offline: 0,
    quarantined: 0,
    overdue: 0,
    total_sessions_24h: 0,
    total_spend_today: 0,
    avg_load_pct: null,
    heartbeat_source_error: false,
    registry_source_error: false,
    cost_source_error: false,
    has_per_entry_errors: false,
    sources_partially_degraded: false,
    ...overrides,
  }
}

function makeBoardResponse(rows: BoardRow[], aggOverrides: Partial<BoardAggregates> = {}): BoardResponse {
  return {
    rows,
    aggregates: makeAggregates({
      total: rows.length,
      butler_count: rows.filter((r) => r.type === "butler").length,
      staffer_count: rows.filter((r) => r.type === "staffer").length,
      ...aggOverrides,
    }),
    generated_at: "2026-07-03T12:00:00Z",
  }
}

function mockQuerySuccess(data: BoardResponse, refetch = vi.fn()) {
  mockUseQuery.mockReturnValue({
    data: { data, meta: {} },
    isLoading: false,
    isError: false,
    error: null,
    refetch,
  })
}

beforeEach(() => {
  vi.clearAllMocks()
})

// ---------------------------------------------------------------------------
// Row mapping
// ---------------------------------------------------------------------------

describe("row mapping (snake_case wire -> camelCase UI contract)", () => {
  it("maps every BoardRow field onto its StatusBoardRow counterpart", () => {
    const row = makeRow({
      name: "chronicler",
      activity: "overdue",
      cell_tone: "amber",
      eligibility: "quarantined",
      quarantine_reason: "3 missed heartbeats",
      quarantined_at: "2026-07-03T10:00:00Z",
      sessions_24h: 12,
      cost_today: 1.23,
      load_pct: 40,
      active_session_count: 3,
      last_session_at: "2026-07-03T09:00:00Z",
      last_heartbeat_at: "2026-07-03T08:55:00Z",
      heartbeat_age_seconds: 300,
      heartbeat_unavailable: true,
      schema_unreachable: true,
      hourly_stripe: [1, 2, 3, ...Array(21).fill(0)],
      hourly_total: 6,
      cadence_seconds: 86400,
      cadence_label: "daily",
      silence_seconds: 432000,
      cadence_status: "overdue",
    })
    mockQuerySuccess(makeBoardResponse([row]))

    const { rows } = useButlerStatusBoard()
    const mapped = rows[0]

    expect(mapped.name).toBe("chronicler")
    expect(mapped.activity).toBe("overdue")
    expect(mapped.cellTone).toBe("amber")
    expect(mapped.eligibility).toBe("quarantined")
    expect(mapped.quarantineReason).toBe("3 missed heartbeats")
    expect(mapped.quarantinedAt).toBe("2026-07-03T10:00:00Z")
    expect(mapped.sessions24h).toBe(12)
    expect(mapped.costToday).toBe(1.23)
    expect(mapped.loadPct).toBe(40)
    expect(mapped.activeSessionCount).toBe(3)
    expect(mapped.lastRunISO).toBe("2026-07-03T09:00:00Z")
    expect(mapped.lastHeartbeatISO).toBe("2026-07-03T08:55:00Z")
    expect(mapped.heartbeatAgeSeconds).toBe(300)
    expect(mapped.heartbeatUnavailable).toBe(true)
    expect(mapped.schemaUnreachable).toBe(true)
    expect(mapped.hourlyTotal).toBe(6)
    expect(mapped.cadenceSeconds).toBe(86400)
    expect(mapped.cadenceLabel).toBe("daily")
    expect(mapped.silenceSeconds).toBe(432000)
    expect(mapped.cadenceStatus).toBe("overdue")
    // Single-fetch board: no per-row loading stagger; per-row error mirrors
    // this butler's own schema_unreachable flag.
    expect(mapped.hourlyStripeLoading).toBe(false)
    expect(mapped.hourlyStripeError).toBe(true)
  })

  it("sets hourlyStripeError when stripe_source_error is true even if schema_unreachable is false", () => {
    // The hourly-activity query can fail independently of the session-count
    // queries that drive schema_unreachable -- both must gate the stripe.
    const row = makeRow({ schema_unreachable: false, stripe_source_error: true })
    mockQuerySuccess(makeBoardResponse([row]))

    const { rows } = useButlerStatusBoard()
    expect(rows[0].hourlyStripeError).toBe(true)
  })

  it("preserves server row order exactly (no client-side re-sort)", () => {
    mockQuerySuccess(
      makeBoardResponse([
        makeRow({ name: "zeta", sessions_24h: 0 }),
        makeRow({ name: "alpha", sessions_24h: 999 }),
        makeRow({ name: "mid", sessions_24h: 5 }),
      ]),
    )

    const { rows } = useButlerStatusBoard()
    expect(rows.map((r) => r.name)).toEqual(["zeta", "alpha", "mid"])
  })

  it("returns an empty row list before data has loaded", () => {
    mockUseQuery.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    })

    const { rows } = useButlerStatusBoard()
    expect(rows).toEqual([])
  })
})

// ---------------------------------------------------------------------------
// needsYou strip
// ---------------------------------------------------------------------------

describe("needsYou", () => {
  it("includes offline, quarantined, overdue, and unknown rows, excludes running/idle", () => {
    mockQuerySuccess(
      makeBoardResponse([
        makeRow({ name: "healthy-idle", activity: "idle" }),
        makeRow({ name: "healthy-running", activity: "running" }),
        makeRow({ name: "down", activity: "offline" }),
        makeRow({ name: "banned", activity: "quarantined" }),
        makeRow({ name: "late", activity: "overdue" }),
        // "unknown" liveness must surface here too -- a heartbeat-unavailable
        // butler is never confidently healthy (bu-qvnce.1).
        makeRow({ name: "degraded", activity: "unknown" }),
      ]),
    )

    const { needsYou } = useButlerStatusBoard()
    expect(needsYou.map((r) => r.name).sort()).toEqual(["banned", "degraded", "down", "late"])
  })

  it("is empty when the fleet is fully healthy", () => {
    mockQuerySuccess(
      makeBoardResponse([
        makeRow({ name: "a", activity: "running" }),
        makeRow({ name: "b", activity: "idle" }),
      ]),
    )

    const { needsYou } = useButlerStatusBoard()
    expect(needsYou).toEqual([])
  })

  it("preserves stable roster order within the strip", () => {
    mockQuerySuccess(
      makeBoardResponse([
        makeRow({ name: "zeta", activity: "overdue" }),
        makeRow({ name: "alpha", activity: "offline" }),
      ]),
    )

    const { needsYou } = useButlerStatusBoard()
    expect(needsYou.map((r) => r.name)).toEqual(["zeta", "alpha"])
  })
})

// ---------------------------------------------------------------------------
// Aggregates passthrough
// ---------------------------------------------------------------------------

describe("aggregates passthrough", () => {
  it("maps every BoardAggregates field onto its camelCase counterpart", () => {
    mockQuerySuccess(
      makeBoardResponse([makeRow()], {
        total: 5,
        butler_count: 4,
        staffer_count: 1,
        active: 2,
        offline: 1,
        quarantined: 1,
        overdue: 1,
        total_sessions_24h: 42,
        total_spend_today: 3.75,
        avg_load_pct: 60,
        heartbeat_source_error: true,
        registry_source_error: true,
        cost_source_error: true,
        has_per_entry_errors: true,
        sources_partially_degraded: true,
      }),
    )

    const { aggregates } = useButlerStatusBoard()
    expect(aggregates.total).toBe(5)
    expect(aggregates.butlerCount).toBe(4)
    expect(aggregates.stafferCount).toBe(1)
    expect(aggregates.active).toBe(2)
    expect(aggregates.offline).toBe(1)
    expect(aggregates.quarantined).toBe(1)
    expect(aggregates.overdue).toBe(1)
    expect(aggregates.totalSessions24h).toBe(42)
    expect(aggregates.totalSpendToday).toBeCloseTo(3.75)
    expect(aggregates.avgLoadPct).toBe(60)
    expect(aggregates.heartbeatSourceError).toBe(true)
    expect(aggregates.registrySourceError).toBe(true)
    expect(aggregates.costSourceError).toBe(true)
    expect(aggregates.hasPerEntryErrors).toBe(true)
    expect(aggregates.sourcesPartiallyDegraded).toBe(true)
  })

  it("maps sessions_source_error onto sessionsSourceError", () => {
    mockQuerySuccess(makeBoardResponse([makeRow()], { sessions_source_error: true }))
    const { aggregates } = useButlerStatusBoard()
    expect(aggregates.sessionsSourceError).toBe(true)
  })

  it("defaults sessionsSourceError to false when the backend omits the field", () => {
    mockQuerySuccess(makeBoardResponse([makeRow()]))
    const { aggregates } = useButlerStatusBoard()
    expect(aggregates.sessionsSourceError).toBe(false)
  })

  it("computes eligibilityUnavailable client-side from mapped rows", () => {
    mockQuerySuccess(
      makeBoardResponse([
        makeRow({ name: "a", eligibility: "unavailable" }),
        makeRow({ name: "b", eligibility: "active" }),
        makeRow({ name: "c", eligibility: "unavailable" }),
      ]),
    )

    const { aggregates } = useButlerStatusBoard()
    expect(aggregates.eligibilityUnavailable).toBe(2)
  })

  it("computes unknown from canonical activity rather than registry eligibility", () => {
    mockQuerySuccess(
      makeBoardResponse([
        makeRow({ name: "unknown-but-active", activity: "unknown", eligibility: "active" }),
        makeRow({ name: "available-but-idle", activity: "idle", eligibility: "unavailable" }),
        makeRow({ name: "unknown-and-unavailable", activity: "unknown", eligibility: "unavailable" }),
      ]),
    )

    const { aggregates } = useButlerStatusBoard()
    expect(aggregates.unknown).toBe(2)
    expect(aggregates.eligibilityUnavailable).toBe(2)
  })

  it("returns zeroed aggregates and avgLoadPct=null before data has loaded", () => {
    mockUseQuery.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    })

    const { aggregates } = useButlerStatusBoard()
    expect(aggregates.total).toBe(0)
    expect(aggregates.avgLoadPct).toBeNull()
    expect(aggregates.totalSpendToday).toBe(0)
  })
})

// ---------------------------------------------------------------------------
// Loading / error propagation
// ---------------------------------------------------------------------------

describe("loading and error propagation", () => {
  it("aggregates.isLoading=true only when the board query is loading with no cached data", () => {
    mockUseQuery.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    })

    const { aggregates } = useButlerStatusBoard()
    expect(aggregates.isLoading).toBe(true)
  })

  it("aggregates.isLoading=false when the board query is loading but has cached data", () => {
    mockUseQuery.mockReturnValue({
      data: { data: makeBoardResponse([makeRow()]), meta: {} },
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    })

    const { aggregates } = useButlerStatusBoard()
    expect(aggregates.isLoading).toBe(false)
  })

  it("aggregates.isError=true only when the board query errors with no cached data", () => {
    const err = new Error("network failure")
    mockUseQuery.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: err,
      refetch: vi.fn(),
    })

    const { aggregates } = useButlerStatusBoard()
    expect(aggregates.isError).toBe(true)
    expect(aggregates.error).toBe(err)
  })

  it("aggregates.isError=false when the query errors but cached rows still exist", () => {
    const err = new Error("refresh failed")
    mockUseQuery.mockReturnValue({
      data: { data: makeBoardResponse([makeRow()]), meta: {} },
      isLoading: false,
      isError: true,
      error: err,
      refetch: vi.fn(),
    })

    const { rows, aggregates } = useButlerStatusBoard()
    expect(rows).toHaveLength(1)
    expect(aggregates.isError).toBe(false)
    expect(aggregates.error).toBe(err)
  })

  it("exposes the query's refetch function", () => {
    const refetch = vi.fn()
    mockQuerySuccess(makeBoardResponse([]), refetch)

    const { aggregates } = useButlerStatusBoard()
    expect(aggregates.refetch).toBe(refetch)
  })
})
