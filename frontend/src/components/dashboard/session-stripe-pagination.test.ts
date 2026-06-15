// ---------------------------------------------------------------------------
// session-stripe-pagination.test.ts — bu-51str
//
// Tests the single-request fetch logic in session-stripe-utils:
// - A single request is issued with limit=SESSIONS_HARD_CAP
// - truncated=true when backend has_more=true (total > SESSIONS_HARD_CAP)
// - truncated=false when all sessions fit within SESSIONS_HARD_CAP
// - truncated=false when total equals exactly SESSIONS_HARD_CAP (has_more=false)
// ---------------------------------------------------------------------------

import { describe, expect, it, vi, beforeEach } from "vitest"

// ---------------------------------------------------------------------------
// Mock getSessions before importing the module under test
// ---------------------------------------------------------------------------

const mockGetSessions = vi.fn()

vi.mock("@/api/index.ts", () => ({
  getSessions: (...args: Parameters<typeof mockGetSessions>) => mockGetSessions(...args),
}))

// Must import after mock registration
import { SESSIONS_HARD_CAP } from "./session-stripe-utils"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeSession(butler = "home") {
  return { butler, started_at: new Date().toISOString() }
}

function makePage(
  sessions: Array<{ butler: string; started_at: string }>,
  total: number,
  offset: number,
  limit: number,
) {
  const has_more = offset + limit < total
  return {
    data: sessions,
    meta: { total, offset, limit, has_more },
  }
}

// ---------------------------------------------------------------------------
// We test the fetch logic by mocking @tanstack/react-query and inspecting
// what queryFn does when called directly.
// ---------------------------------------------------------------------------

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const original = await importOriginal<typeof import("@tanstack/react-query")>()
  return {
    ...original,
    useQuery: vi.fn(({ queryFn }) => {
      // Store the queryFn for tests to invoke directly
      ;(globalThis as Record<string, unknown>).__lastQueryFn = queryFn
      return { data: undefined, isLoading: false, isError: false }
    }),
  }
})

// Trigger useSessionStripeData so the queryFn is registered
import { useSessionStripeData } from "./session-stripe-utils"

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks()
  mockGetSessions.mockReset()
  // Trigger registration of the queryFn
  useSessionStripeData(24, false)
})

async function invokeQueryFn() {
  const fn = (globalThis as Record<string, unknown>).__lastQueryFn
  if (typeof fn !== "function") throw new Error("queryFn not registered")
  return fn()
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("session-stripe fetch — single request semantics", () => {
  it("issues exactly one request with limit=SESSIONS_HARD_CAP", async () => {
    const sessions = Array.from({ length: 10 }, () => makeSession())
    mockGetSessions.mockResolvedValueOnce(makePage(sessions, 10, 0, SESSIONS_HARD_CAP))

    await invokeQueryFn()

    expect(mockGetSessions).toHaveBeenCalledTimes(1)
    expect(mockGetSessions).toHaveBeenCalledWith(
      expect.objectContaining({ limit: SESSIONS_HARD_CAP, offset: 0 }),
    )
  })

  it("returns all sessions and truncated=false when total < SESSIONS_HARD_CAP", async () => {
    const sessions = Array.from({ length: 10 }, () => makeSession())
    mockGetSessions.mockResolvedValueOnce(makePage(sessions, 10, 0, SESSIONS_HARD_CAP))

    const result = await invokeQueryFn()

    expect(result.data).toHaveLength(10)
    expect(result.truncated).toBe(false)
  })

  it("sets truncated=true when backend has_more=true (total > SESSIONS_HARD_CAP)", async () => {
    // Simulate backend with 1200 total sessions; limit=1000 leaves 200 beyond the cap
    const sessions = Array.from({ length: SESSIONS_HARD_CAP }, () => makeSession())
    mockGetSessions.mockResolvedValueOnce(makePage(sessions, 1200, 0, SESSIONS_HARD_CAP))

    const result = await invokeQueryFn()

    expect(result.data).toHaveLength(SESSIONS_HARD_CAP)
    expect(result.truncated).toBe(true)
  })

  it("does NOT set truncated when total equals exactly SESSIONS_HARD_CAP", async () => {
    // Exactly 1000 sessions — has_more=false because offset(0)+limit(1000) == total(1000)
    const sessions = Array.from({ length: SESSIONS_HARD_CAP }, () => makeSession())
    mockGetSessions.mockResolvedValueOnce(makePage(sessions, SESSIONS_HARD_CAP, 0, SESSIONS_HARD_CAP))

    const result = await invokeQueryFn()

    expect(result.data).toHaveLength(SESSIONS_HARD_CAP)
    expect(result.truncated).toBe(false)
  })
})

describe("session-stripe fetch — correct query parameters", () => {
  it("passes since/until/limit/offset to getSessions correctly", async () => {
    const sessions = Array.from({ length: 5 }, () => makeSession())
    mockGetSessions.mockResolvedValueOnce(makePage(sessions, 5, 0, SESSIONS_HARD_CAP))

    await invokeQueryFn()

    const call = mockGetSessions.mock.calls[0][0]
    expect(call).toMatchObject({
      limit: SESSIONS_HARD_CAP,
      offset: 0,
    })
    expect(typeof call.since).toBe("string")
    expect(typeof call.until).toBe("string")
  })
})
