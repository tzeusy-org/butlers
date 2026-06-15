// ---------------------------------------------------------------------------
// use-butler-status-board.test.ts — bu-hb7dh.5
//
// Tests the composite hook that powers the /butlers/ status-board page.
//
// Strategy:
//   - Mock all six input hooks before importing the module under test.
//   - Call useButlerStatusBoard() directly (useMemo is a synchronous passthrough).
//   - Configure mockUseQueries to return a stable per-butler result array.
// ---------------------------------------------------------------------------

import { describe, expect, it, vi, beforeEach } from "vitest"
import type { ButlerSummary } from "@/api/types"

// ---------------------------------------------------------------------------
// Mocks — must be defined before any imports from the module under test.
// ---------------------------------------------------------------------------

const mockUseButlers = vi.fn()
const mockUseRegistry = vi.fn()
const mockUseButlerHeartbeats = vi.fn()
const mockUseSpendSummary = vi.fn()
// mockUseQuery handles the sessions-24h query (and any other direct useQuery calls).
const mockUseQuery = vi.fn()
// useQueries returns an array of query-result objects; one per butler.
const mockUseQueries = vi.fn()

// A shared "loading, no data" default for secondary hooks.
const loadingNoData = { data: undefined, isLoading: true, isError: false, error: null }

/** Build a useQueries result array where every butler has max_concurrent = maxC. */
function runtimeResults(count: number, maxC: number | null): { data: { max_concurrent: number } | undefined; isLoading: boolean; isError: boolean }[] {
  return Array.from({ length: count }, () =>
    maxC === null
      ? { data: undefined, isLoading: false, isError: true }
      : { data: { max_concurrent: maxC }, isLoading: false, isError: false },
  )
}

/** Build a useQueries result array with per-index max_concurrent values (null = error). */
function runtimeResultsPerIndex(values: Array<number | null>): { data: { max_concurrent: number } | undefined; isLoading: boolean; isError: boolean }[] {
  return values.map((v) =>
    v === null
      ? { data: undefined, isLoading: false, isError: true }
      : { data: { max_concurrent: v }, isLoading: false, isError: false },
  )
}

// Default mocks — each test can override as needed.
function setDefaults() {
  mockUseButlers.mockReturnValue(butlersQueryResult([]))
  mockUseRegistry.mockReturnValue(registryQueryResult([]))
  mockUseButlerHeartbeats.mockReturnValue(heartbeatsQueryResult([]))
  mockUseSpendSummary.mockReturnValue(costQueryResult({}))
  // useQuery intercepts the sessions-24h query (PaginatedResponse, not ApiResponse)
  mockUseQuery.mockReturnValue({ data: { data: [], meta: { total: 0 } }, isLoading: false, isError: false, error: null })
  // useQueries returns an empty array when no butlers are present
  mockUseQueries.mockReturnValue([])
}

vi.mock("@/hooks/use-butlers", () => ({
  useButlers: (...args: Parameters<typeof mockUseButlers>) => mockUseButlers(...args),
}))

vi.mock("@/hooks/use-general", () => ({
  useRegistry: (...args: Parameters<typeof mockUseRegistry>) => mockUseRegistry(...args),
}))

vi.mock("@/hooks/use-system", () => ({
  useButlerHeartbeats: (...args: Parameters<typeof mockUseButlerHeartbeats>) => mockUseButlerHeartbeats(...args),
}))

vi.mock("@/hooks/use-spend", () => ({
  useSpendSummary: (...args: Parameters<typeof mockUseSpendSummary>) => mockUseSpendSummary(...args),
}))

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-query")>()
  return {
    ...actual,
    useQuery: (...args: Parameters<typeof mockUseQuery>) => mockUseQuery(...args),
    useQueries: (...args: Parameters<typeof mockUseQueries>) => mockUseQueries(...args),
  }
})

// React.useMemo must execute synchronously in tests (no React rendering context).
// We replace it with a passthrough so the derivation logic runs inline.
vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>()
  return {
    ...actual,
    useMemo: (fn: () => unknown) => fn(),
  }
})

// Import after mocks.
import { useButlerStatusBoard } from "./use-butler-status-board"
import { bucketSessionsByHour } from "@/lib/session-buckets"

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

/** Wrap a value in the ApiResponse envelope used by all list/detail endpoints. */
function apiResponse<T>(data: T): { data: T; meta: Record<string, unknown> } {
  return { data, meta: {} }
}

function makeButler(overrides: Partial<ButlerSummary> = {}): ButlerSummary {
  return {
    name: "test-butler",
    status: "healthy",
    port: 4000,
    type: "butler",
    description: null,
    sessions_24h: 0,
    ...overrides,
  }
}

/**
 * Build a mock return value for useButlers.
 * Wraps the butler array in an ApiResponse<ButlerSummary[]> envelope.
 */
function butlersQueryResult(
  butlers: ButlerSummary[],
  overrides: { isLoading?: boolean; isError?: boolean; error?: Error | null } = {},
) {
  return {
    data: apiResponse(butlers),
    isLoading: overrides.isLoading ?? false,
    isError: overrides.isError ?? false,
    error: overrides.error ?? null,
    refetch: vi.fn(),
  }
}

/**
 * Build a mock return value for useRegistry.
 * Wraps the entries in an ApiResponse<RegistryEntry[]> envelope.
 */
function registryQueryResult(entries: Array<{ name: string; eligibility_state: string }>) {
  return { data: apiResponse(entries), isLoading: false, isError: false, error: null }
}

function makeHeartbeats(entries: Array<{ name: string; active_session_count?: number; last_session_at?: string | null }>) {
  return {
    butlers: entries.map((e) => ({
      name: e.name,
      active_session_count: e.active_session_count ?? 0,
      last_session_at: e.last_session_at ?? null,
      heartbeat_age_seconds: 5,
      error: null,
    })),
  }
}

/**
 * Build a mock return value for useButlerHeartbeats.
 * Wraps the HeartbeatFacts in an ApiResponse envelope.
 */
function heartbeatsQueryResult(
  entries: Array<{ name: string; active_session_count?: number; last_session_at?: string | null }>,
  overrides: { isError?: boolean; error?: Error | null } = {},
) {
  return {
    data: apiResponse(makeHeartbeats(entries)),
    isLoading: false,
    isError: overrides.isError ?? false,
    error: overrides.error ?? null,
  }
}

/**
 * Build a mock return value for useSpendSummary.
 * Wraps the SpendSummary in an ApiResponse envelope.
 */
function costQueryResult(byButler: Record<string, number>) {
  return {
    data: apiResponse({ by_butler: byButler }),
    isLoading: false,
    isError: false,
    error: null,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  setDefaults()
})

// ---------------------------------------------------------------------------
// Activity verb derivation
// ---------------------------------------------------------------------------

describe("activity verb derivation", () => {
  it("maps degraded status → paused, cellTone=red", () => {
    mockUseButlers.mockReturnValue(butlersQueryResult([makeButler({ name: "a", status: "degraded" })]))
    mockUseRegistry.mockReturnValue(registryQueryResult([{ name: "a", eligibility_state: "active" }]))
    mockUseButlerHeartbeats.mockReturnValue(heartbeatsQueryResult([{ name: "a", active_session_count: 2 }]))
    mockUseQueries.mockReturnValue(runtimeResults(1, 4))

    const { rows } = useButlerStatusBoard()
    expect(rows[0].activity).toBe("paused")
    expect(rows[0].cellTone).toBe("red")
  })

  it("maps status=waiting → awaiting, cellTone=amber", () => {
    mockUseButlers.mockReturnValue(butlersQueryResult([makeButler({ name: "a", status: "waiting" })]))
    mockUseRegistry.mockReturnValue(registryQueryResult([{ name: "a", eligibility_state: "active" }]))
    mockUseQueries.mockReturnValue(runtimeResults(1, 4))

    const { rows } = useButlerStatusBoard()
    expect(rows[0].activity).toBe("awaiting")
    expect(rows[0].cellTone).toBe("amber")
  })

  it("quarantined eligibility → quarantined activity, cellTone=red (even with active sessions)", () => {
    mockUseButlers.mockReturnValue(butlersQueryResult([makeButler({ name: "a", status: "healthy" })]))
    mockUseRegistry.mockReturnValue(registryQueryResult([{ name: "a", eligibility_state: "quarantined" }]))
    mockUseButlerHeartbeats.mockReturnValue(heartbeatsQueryResult([{ name: "a", active_session_count: 3 }]))
    mockUseQueries.mockReturnValue(runtimeResults(1, 4))

    const { rows } = useButlerStatusBoard()
    // Rule 2 fires before rule 3: quarantined wins over running
    expect(rows[0].activity).toBe("quarantined")
    expect(rows[0].cellTone).toBe("red")
  })

  it("active_session_count > 0 (healthy, active eligibility) → running, cellTone=green", () => {
    mockUseButlers.mockReturnValue(butlersQueryResult([makeButler({ name: "a", status: "healthy" })]))
    mockUseRegistry.mockReturnValue(registryQueryResult([{ name: "a", eligibility_state: "active" }]))
    mockUseButlerHeartbeats.mockReturnValue(heartbeatsQueryResult([{ name: "a", active_session_count: 1 }]))
    mockUseQueries.mockReturnValue(runtimeResults(1, 4))

    const { rows } = useButlerStatusBoard()
    expect(rows[0].activity).toBe("running")
    expect(rows[0].cellTone).toBe("green")
  })

  it("healthy, active, no active sessions → idle, cellTone=neutral", () => {
    mockUseButlers.mockReturnValue(butlersQueryResult([makeButler({ name: "a", status: "healthy" })]))
    mockUseRegistry.mockReturnValue(registryQueryResult([{ name: "a", eligibility_state: "active" }]))
    mockUseButlerHeartbeats.mockReturnValue(heartbeatsQueryResult([{ name: "a", active_session_count: 0 }]))
    mockUseQueries.mockReturnValue(runtimeResults(1, 4))

    const { rows } = useButlerStatusBoard()
    expect(rows[0].activity).toBe("idle")
    expect(rows[0].cellTone).toBe("neutral")
  })
})

// ---------------------------------------------------------------------------
// loadPct derivation
// ---------------------------------------------------------------------------

describe("loadPct derivation", () => {
  it("returns null when max_concurrent is unavailable (runtime-config error)", () => {
    mockUseButlers.mockReturnValue(butlersQueryResult([makeButler({ name: "a" })]))
    mockUseButlerHeartbeats.mockReturnValue(heartbeatsQueryResult([{ name: "a", active_session_count: 2 }]))
    // useQueries returns an error result for the one butler
    mockUseQueries.mockReturnValue(runtimeResults(1, null))

    const { rows } = useButlerStatusBoard()
    expect(rows[0].loadPct).toBeNull()
  })

  it("returns null when max_concurrent is 0", () => {
    mockUseButlers.mockReturnValue(butlersQueryResult([makeButler({ name: "a" })]))
    mockUseButlerHeartbeats.mockReturnValue(heartbeatsQueryResult([{ name: "a", active_session_count: 2 }]))
    mockUseQueries.mockReturnValue([{ data: { max_concurrent: 0 }, isLoading: false, isError: false }])

    const { rows } = useButlerStatusBoard()
    expect(rows[0].loadPct).toBeNull()
  })

  it("computes rounded loadPct when max_concurrent is known", () => {
    mockUseButlers.mockReturnValue(butlersQueryResult([makeButler({ name: "a" })]))
    mockUseButlerHeartbeats.mockReturnValue(heartbeatsQueryResult([{ name: "a", active_session_count: 2 }]))
    mockUseQueries.mockReturnValue(runtimeResults(1, 4))

    const { rows } = useButlerStatusBoard()
    expect(rows[0].loadPct).toBe(50) // 2/4 * 100 = 50
  })

  it("rounds fractional loadPct", () => {
    mockUseButlers.mockReturnValue(butlersQueryResult([makeButler({ name: "a" })]))
    mockUseButlerHeartbeats.mockReturnValue(heartbeatsQueryResult([{ name: "a", active_session_count: 1 }]))
    mockUseQueries.mockReturnValue(runtimeResults(1, 3))

    const { rows } = useButlerStatusBoard()
    expect(rows[0].loadPct).toBe(33) // round(1/3 * 100) = 33
  })
})

// ---------------------------------------------------------------------------
// Sort order
// ---------------------------------------------------------------------------

describe("sort order", () => {
  it("sorts rows by sessions24h descending", () => {
    mockUseButlers.mockReturnValue(butlersQueryResult([
      makeButler({ name: "low", sessions_24h: 2 }),
      makeButler({ name: "high", sessions_24h: 10 }),
      makeButler({ name: "mid", sessions_24h: 5 }),
    ]))
    mockUseQueries.mockReturnValue(runtimeResults(3, 4))

    const { rows } = useButlerStatusBoard()
    expect(rows.map((r) => r.name)).toEqual(["high", "mid", "low"])
  })

  it("breaks ties by name ascending", () => {
    mockUseButlers.mockReturnValue(butlersQueryResult([
      makeButler({ name: "zara", sessions_24h: 5 }),
      makeButler({ name: "alice", sessions_24h: 5 }),
      makeButler({ name: "mike", sessions_24h: 5 }),
    ]))
    mockUseQueries.mockReturnValue(runtimeResults(3, 4))

    const { rows } = useButlerStatusBoard()
    expect(rows.map((r) => r.name)).toEqual(["alice", "mike", "zara"])
  })

  it("combined sort: sessions desc, then name asc for ties", () => {
    mockUseButlers.mockReturnValue(butlersQueryResult([
      makeButler({ name: "charlie", sessions_24h: 3 }),
      makeButler({ name: "alice", sessions_24h: 3 }),
      makeButler({ name: "zara", sessions_24h: 10 }),
      makeButler({ name: "bob", sessions_24h: 1 }),
    ]))
    mockUseQueries.mockReturnValue(runtimeResults(4, 4))

    const { rows } = useButlerStatusBoard()
    expect(rows.map((r) => r.name)).toEqual(["zara", "alice", "charlie", "bob"])
  })
})

// ---------------------------------------------------------------------------
// Partial failure tolerance
// ---------------------------------------------------------------------------

describe("partial failure — secondary sources do not drop rows", () => {
  it("cost fetch failure: rows still render with costToday=0", () => {
    mockUseButlers.mockReturnValue(butlersQueryResult([makeButler({ name: "a" }), makeButler({ name: "b" })]))
    mockUseSpendSummary.mockReturnValue({ data: undefined, isLoading: false, isError: true, error: new Error("cost failed") })
    mockUseQueries.mockReturnValue(runtimeResults(2, 4))

    const { rows } = useButlerStatusBoard()
    expect(rows).toHaveLength(2)
    expect(rows.every((r) => r.costToday === 0)).toBe(true)
  })

  it("heartbeat fetch failure: rows render with lastRunISO=null", () => {
    mockUseButlers.mockReturnValue(butlersQueryResult([makeButler({ name: "a" })]))
    mockUseButlerHeartbeats.mockReturnValue({ data: undefined, isLoading: false, isError: true, error: new Error("hb failed") })
    mockUseQueries.mockReturnValue(runtimeResults(1, 4))

    const { rows } = useButlerStatusBoard()
    expect(rows).toHaveLength(1)
    expect(rows[0].lastRunISO).toBeNull()
  })

  it("sessions fetch failure: rows render with hourlyStripe=Array(24).fill(0)", () => {
    mockUseButlers.mockReturnValue(butlersQueryResult([makeButler({ name: "a" })]))
    // Override the useQuery mock to simulate sessions-24h fetch failure
    mockUseQuery.mockReturnValue({ data: undefined, isLoading: false, isError: true, error: new Error("sessions failed") })
    mockUseQueries.mockReturnValue(runtimeResults(1, 4))

    const { rows } = useButlerStatusBoard()
    expect(rows).toHaveLength(1)
    expect(rows[0].hourlyStripe).toEqual(Array(24).fill(0))
  })

  it("registry fetch failure: eligibility falls back to 'unavailable'", () => {
    mockUseButlers.mockReturnValue(butlersQueryResult([makeButler({ name: "a" })]))
    mockUseRegistry.mockReturnValue({ data: undefined, isLoading: false, isError: true, error: new Error("registry failed") })
    mockUseQueries.mockReturnValue(runtimeResults(1, 4))

    const { rows } = useButlerStatusBoard()
    expect(rows).toHaveLength(1)
    expect(rows[0].eligibility).toBe("unavailable")
  })

  it("all secondary sources fail: rows are still emitted with fallback values", () => {
    mockUseButlers.mockReturnValue(butlersQueryResult([makeButler({ name: "a" }), makeButler({ name: "b" })]))
    mockUseRegistry.mockReturnValue({ data: undefined, isLoading: false, isError: true, error: new Error("x") })
    mockUseButlerHeartbeats.mockReturnValue({ data: undefined, isLoading: false, isError: true, error: new Error("x") })
    mockUseSpendSummary.mockReturnValue({ data: undefined, isLoading: false, isError: true, error: new Error("x") })
    mockUseQuery.mockReturnValue({ data: undefined, isLoading: false, isError: true, error: new Error("x") })
    mockUseQueries.mockReturnValue(runtimeResults(2, null))

    const { rows, aggregates } = useButlerStatusBoard()
    expect(rows).toHaveLength(2)
    expect(rows.every((r) => r.costToday === 0)).toBe(true)
    expect(rows.every((r) => r.loadPct === null)).toBe(true)
    expect(rows.every((r) => r.lastRunISO === null)).toBe(true)
    expect(rows.every((r) => r.hourlyStripe.every((v) => v === 0))).toBe(true)
    expect(rows.every((r) => r.eligibility === "unavailable")).toBe(true)
    // aggregates must still be usable
    expect(aggregates.isError).toBe(false) // butlers list succeeded
    expect(aggregates.total).toBe(2)
  })
})

// ---------------------------------------------------------------------------
// Eligibility: 'unavailable' when registry has no entry for a name
// ---------------------------------------------------------------------------

describe("eligibility mapping", () => {
  it("returns 'unavailable' for a butler not in the registry", () => {
    mockUseButlers.mockReturnValue(butlersQueryResult([makeButler({ name: "unknown-butler" })]))
    mockUseRegistry.mockReturnValue(registryQueryResult([{ name: "other-butler", eligibility_state: "active" }]))
    mockUseQueries.mockReturnValue(runtimeResults(1, 4))

    const { rows } = useButlerStatusBoard()
    expect(rows[0].eligibility).toBe("unavailable")
  })

  it("passes through 'active', 'quarantined', 'stale' correctly", () => {
    mockUseButlers.mockReturnValue(butlersQueryResult([
      makeButler({ name: "a" }),
      makeButler({ name: "b" }),
      makeButler({ name: "c" }),
    ]))
    mockUseRegistry.mockReturnValue(registryQueryResult([
      { name: "a", eligibility_state: "active" },
      { name: "b", eligibility_state: "quarantined" },
      { name: "c", eligibility_state: "stale" },
    ]))
    mockUseQueries.mockReturnValue(runtimeResults(3, 4))

    const { rows } = useButlerStatusBoard()
    const byName = Object.fromEntries(rows.map((r) => [r.name, r.eligibility]))
    expect(byName["a"]).toBe("active")
    expect(byName["b"]).toBe("quarantined")
    expect(byName["c"]).toBe("stale")
  })
})

// ---------------------------------------------------------------------------
// Aggregation correctness
// ---------------------------------------------------------------------------

describe("aggregate correctness", () => {
  it("counts butler vs staffer types correctly", () => {
    mockUseButlers.mockReturnValue(butlersQueryResult([
      makeButler({ name: "b1", type: "butler" }),
      makeButler({ name: "b2", type: "butler" }),
      makeButler({ name: "s1", type: "staffer" }),
    ]))
    mockUseQueries.mockReturnValue(runtimeResults(3, 4))

    const { aggregates } = useButlerStatusBoard()
    expect(aggregates.total).toBe(3)
    expect(aggregates.butlerCount).toBe(2)
    expect(aggregates.stafferCount).toBe(1)
  })

  it("sums totalSessions24h across all rows", () => {
    mockUseButlers.mockReturnValue(butlersQueryResult([
      makeButler({ name: "a", sessions_24h: 10 }),
      makeButler({ name: "b", sessions_24h: 5 }),
      makeButler({ name: "c", sessions_24h: 2 }),
    ]))
    mockUseQueries.mockReturnValue(runtimeResults(3, 4))

    const { aggregates } = useButlerStatusBoard()
    expect(aggregates.totalSessions24h).toBe(17)
  })

  it("sums totalSpendToday from by_butler cost data", () => {
    mockUseButlers.mockReturnValue(butlersQueryResult([
      makeButler({ name: "a" }),
      makeButler({ name: "b" }),
    ]))
    mockUseSpendSummary.mockReturnValue(costQueryResult({ a: 1.5, b: 2.25 }))
    mockUseQueries.mockReturnValue(runtimeResults(2, 4))

    const { aggregates } = useButlerStatusBoard()
    expect(aggregates.totalSpendToday).toBeCloseTo(3.75)
  })

  it("computes avgLoadPct ignoring null entries", () => {
    // a → max=4, active=2 → 50%; b → max=4, active=4 → 100%; c → no config → null
    mockUseButlers.mockReturnValue(butlersQueryResult([
      makeButler({ name: "a" }),
      makeButler({ name: "b" }),
      makeButler({ name: "c" }),
    ]))
    // runtimeConfigResults: a→4, b→4, c→null (error)
    mockUseQueries.mockReturnValue(runtimeResultsPerIndex([4, 4, null]))
    mockUseButlerHeartbeats.mockReturnValue(heartbeatsQueryResult([
      { name: "a", active_session_count: 2 },
      { name: "b", active_session_count: 4 },
      { name: "c", active_session_count: 0 },
    ]))

    const { aggregates } = useButlerStatusBoard()
    // avg of 50 and 100 = 75
    expect(aggregates.avgLoadPct).toBe(75)
  })

  it("returns avgLoadPct=null when no row has known load", () => {
    mockUseButlers.mockReturnValue(butlersQueryResult([makeButler({ name: "a" })]))
    mockUseQueries.mockReturnValue(runtimeResults(1, null))

    const { aggregates } = useButlerStatusBoard()
    expect(aggregates.avgLoadPct).toBeNull()
  })

  it("counts awaiting separately from quarantined in aggregates", () => {
    mockUseButlers.mockReturnValue(butlersQueryResult([
      makeButler({ name: "q", status: "healthy" }),   // quarantined
      makeButler({ name: "w", status: "waiting" }),   // awaiting
      makeButler({ name: "h", status: "healthy" }),   // idle
    ]))
    mockUseRegistry.mockReturnValue(registryQueryResult([
      { name: "q", eligibility_state: "quarantined" },
      { name: "w", eligibility_state: "active" },
      { name: "h", eligibility_state: "active" },
    ]))
    mockUseQueries.mockReturnValue(runtimeResults(3, 4))

    const { aggregates } = useButlerStatusBoard()
    expect(aggregates.quarantined).toBe(1)
    expect(aggregates.awaiting).toBe(1)
  })
})

// ---------------------------------------------------------------------------
// isLoading / isError propagation
// ---------------------------------------------------------------------------

describe("loading and error propagation", () => {
  it("aggregates.isLoading=true only when butlers list is loading with no cached data", () => {
    mockUseButlers.mockReturnValue({ data: undefined, isLoading: true, isError: false, error: null, refetch: vi.fn() })

    const { aggregates } = useButlerStatusBoard()
    expect(aggregates.isLoading).toBe(true)
  })

  it("aggregates.isLoading=false when butlers list is loading but has cached data", () => {
    mockUseButlers.mockReturnValue(butlersQueryResult([makeButler({ name: "a" })], { isLoading: true }))
    mockUseQueries.mockReturnValue(runtimeResults(1, 4))

    const { aggregates } = useButlerStatusBoard()
    expect(aggregates.isLoading).toBe(false)
  })

  it("aggregates.isError=true only when butlers list errors with no cached data", () => {
    const err = new Error("network failure")
    mockUseButlers.mockReturnValue({ data: undefined, isLoading: false, isError: true, error: err, refetch: vi.fn() })

    const { aggregates } = useButlerStatusBoard()
    expect(aggregates.isError).toBe(true)
    expect(aggregates.error).toBe(err)
  })

  it("secondary source loading does not block row render (aggregates.isLoading=false)", () => {
    mockUseButlers.mockReturnValue(butlersQueryResult([makeButler({ name: "a" })]))
    mockUseSpendSummary.mockReturnValue(loadingNoData)
    mockUseButlerHeartbeats.mockReturnValue(loadingNoData)
    mockUseQueries.mockReturnValue(runtimeResults(1, 4))

    const { rows, aggregates } = useButlerStatusBoard()
    expect(aggregates.isLoading).toBe(false)
    expect(rows).toHaveLength(1)
  })
})

// ---------------------------------------------------------------------------
// bucketSessionsByHour (session-buckets util)
// ---------------------------------------------------------------------------

describe("bucketSessionsByHour", () => {
  it("returns an array of 24 zeroes when no sessions match", () => {
    const stripe = bucketSessionsByHour([], "my-butler")
    expect(stripe).toHaveLength(24)
    expect(stripe.every((v) => v === 0)).toBe(true)
  })

  it("counts sessions for the correct butler only", () => {
    const now = new Date()
    const oneHourAgo = new Date(now.getTime() - 60 * 60 * 1000)
    const sessions = [
      { butler: "my-butler", started_at: oneHourAgo.toISOString() },
      { butler: "other-butler", started_at: oneHourAgo.toISOString() },
    ]
    const stripe = bucketSessionsByHour(sessions, "my-butler")
    expect(stripe.reduce((s, v) => s + v, 0)).toBe(1)
  })

  it("places sessions in the correct hour slot (oldest=slot 0)", () => {
    const now = new Date()
    // UTC-floor to avoid edge-case issues with current hour boundary
    const windowEnd = Math.floor(now.getTime() / (3600 * 1000)) * (3600 * 1000) + 3600 * 1000
    const windowStart = windowEnd - 24 * 3600 * 1000

    // Session at slot 0 (oldest = first hour of the window)
    const slot0Time = new Date(windowStart + 1000) // 1 second into slot 0
    // Session at slot 23 (newest = last hour of the window)
    const slot23Time = new Date(windowEnd - 1000) // 1 second before window end

    const sessions = [
      { butler: "b", started_at: slot0Time.toISOString() },
      { butler: "b", started_at: slot23Time.toISOString() },
    ]
    const stripe = bucketSessionsByHour(sessions, "b", "UTC", now)
    expect(stripe[0]).toBe(1)
    expect(stripe[23]).toBe(1)
    // All other slots are 0
    for (let i = 1; i < 23; i++) {
      expect(stripe[i]).toBe(0)
    }
  })

  it("ignores sessions outside the 24h window", () => {
    const now = new Date()
    const old = new Date(now.getTime() - 25 * 60 * 60 * 1000)
    const sessions = [{ butler: "b", started_at: old.toISOString() }]
    const stripe = bucketSessionsByHour(sessions, "b", "UTC", now)
    expect(stripe.reduce((s, v) => s + v, 0)).toBe(0)
  })

  it("ignores sessions with unparseable started_at", () => {
    const sessions = [{ butler: "b", started_at: "not-a-date" }]
    const stripe = bucketSessionsByHour(sessions, "b")
    expect(stripe.every((v) => v === 0)).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

describe("empty state", () => {
  it("returns empty rows and zero aggregates when butlers list is empty", () => {
    // Default mock returns empty butler list
    const { rows, aggregates } = useButlerStatusBoard()
    expect(rows).toHaveLength(0)
    expect(aggregates.total).toBe(0)
    expect(aggregates.totalSessions24h).toBe(0)
    expect(aggregates.avgLoadPct).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Quarantined wins over running (priority ordering)
// ---------------------------------------------------------------------------

describe("quarantined activity dominates running", () => {
  it("butler with active sessions AND quarantined eligibility gets quarantined activity", () => {
    mockUseButlers.mockReturnValue(butlersQueryResult([makeButler({ name: "a", status: "healthy" })]))
    mockUseRegistry.mockReturnValue(registryQueryResult([{ name: "a", eligibility_state: "quarantined" }]))
    mockUseButlerHeartbeats.mockReturnValue(heartbeatsQueryResult([{ name: "a", active_session_count: 5 }]))
    mockUseQueries.mockReturnValue(runtimeResults(1, 4))

    const { rows } = useButlerStatusBoard()
    expect(rows[0].activity).toBe("quarantined")
    expect(rows[0].cellTone).toBe("red")
  })
})

// ---------------------------------------------------------------------------
// Rule ordering: degraded wins over quarantined
// ---------------------------------------------------------------------------

describe("rule ordering", () => {
  it("degraded status wins over quarantined eligibility (rule 1 fires before rule 2)", () => {
    mockUseButlers.mockReturnValue(butlersQueryResult([makeButler({ name: "a", status: "degraded" })]))
    mockUseRegistry.mockReturnValue(registryQueryResult([{ name: "a", eligibility_state: "quarantined" }]))
    mockUseQueries.mockReturnValue(runtimeResults(1, 4))

    const { rows } = useButlerStatusBoard()
    expect(rows[0].activity).toBe("paused")
    expect(rows[0].cellTone).toBe("red")
  })
})

// ---------------------------------------------------------------------------
// Staffers folded into the same sorted list
// ---------------------------------------------------------------------------

describe("staffers and butlers in the same list", () => {
  it("staffers appear in the sorted list alongside butlers", () => {
    mockUseButlers.mockReturnValue(butlersQueryResult([
      makeButler({ name: "butler-a", type: "butler", sessions_24h: 1 }),
      makeButler({ name: "staffer-x", type: "staffer", sessions_24h: 5 }),
    ]))
    mockUseQueries.mockReturnValue(runtimeResults(2, 4))

    const { rows, aggregates } = useButlerStatusBoard()
    // staffer-x has more sessions so sorts first
    expect(rows[0].name).toBe("staffer-x")
    expect(rows[0].type).toBe("staffer")
    expect(aggregates.stafferCount).toBe(1)
    expect(aggregates.butlerCount).toBe(1)
  })
})

// ---------------------------------------------------------------------------
// lastRunISO propagation
// ---------------------------------------------------------------------------

describe("lastRunISO", () => {
  it("reflects heartbeat.last_session_at", () => {
    const ts = "2026-05-10T08:00:00Z"
    mockUseButlers.mockReturnValue(butlersQueryResult([makeButler({ name: "a" })]))
    mockUseButlerHeartbeats.mockReturnValue(heartbeatsQueryResult([{ name: "a", last_session_at: ts }]))
    mockUseQueries.mockReturnValue(runtimeResults(1, 4))

    const { rows } = useButlerStatusBoard()
    expect(rows[0].lastRunISO).toBe(ts)
  })

  it("returns null when butler has no heartbeat entry", () => {
    mockUseButlers.mockReturnValue(butlersQueryResult([makeButler({ name: "a" })]))
    mockUseButlerHeartbeats.mockReturnValue(heartbeatsQueryResult([]))
    mockUseQueries.mockReturnValue(runtimeResults(1, 4))

    const { rows } = useButlerStatusBoard()
    expect(rows[0].lastRunISO).toBeNull()
  })
})
