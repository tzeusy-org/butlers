// ---------------------------------------------------------------------------
// session-stripe-pagination.test.ts — bu-51str (keyset migration)
//
// Tests the single-request fetch logic in session-stripe-utils against the
// keyset list contract (no offset/total):
// - A single request is issued with limit=SESSIONS_HARD_CAP (no offset)
// - truncated=true when keyset meta.has_more=true (rows beyond the cap)
// - truncated=false when all sessions fit within SESSIONS_HARD_CAP
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
  limit: number,
  has_more: boolean,
) {
  return {
    data: sessions,
    meta: { limit, next_cursor: has_more ? "cursor-xyz" : null, has_more },
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

describe("session-stripe fetch — single request semantics (keyset)", () => {
  it("issues exactly one request with limit=SESSIONS_HARD_CAP and no offset", async () => {
    const sessions = Array.from({ length: 10 }, () => makeSession())
    mockGetSessions.mockResolvedValueOnce(makePage(sessions, SESSIONS_HARD_CAP, false))

    await invokeQueryFn()

    expect(mockGetSessions).toHaveBeenCalledTimes(1)
    const call = mockGetSessions.mock.calls[0][0]
    expect(call).toMatchObject({ limit: SESSIONS_HARD_CAP })
    expect(call.offset).toBeUndefined()
  })

  it("returns all sessions and truncated=false when there are no more rows", async () => {
    const sessions = Array.from({ length: 10 }, () => makeSession())
    mockGetSessions.mockResolvedValueOnce(makePage(sessions, SESSIONS_HARD_CAP, false))

    const result = await invokeQueryFn()

    expect(result.data).toHaveLength(10)
    expect(result.truncated).toBe(false)
  })

  it("sets truncated=true when keyset meta.has_more=true (rows beyond the cap)", async () => {
    const sessions = Array.from({ length: SESSIONS_HARD_CAP }, () => makeSession())
    mockGetSessions.mockResolvedValueOnce(makePage(sessions, SESSIONS_HARD_CAP, true))

    const result = await invokeQueryFn()

    expect(result.data).toHaveLength(SESSIONS_HARD_CAP)
    expect(result.truncated).toBe(true)
  })

  it("does NOT set truncated when has_more=false at exactly the cap", async () => {
    const sessions = Array.from({ length: SESSIONS_HARD_CAP }, () => makeSession())
    mockGetSessions.mockResolvedValueOnce(makePage(sessions, SESSIONS_HARD_CAP, false))

    const result = await invokeQueryFn()

    expect(result.data).toHaveLength(SESSIONS_HARD_CAP)
    expect(result.truncated).toBe(false)
  })
})

describe("session-stripe fetch — correct query parameters", () => {
  it("passes since/until/limit to getSessions correctly (no offset)", async () => {
    const sessions = Array.from({ length: 5 }, () => makeSession())
    mockGetSessions.mockResolvedValueOnce(makePage(sessions, SESSIONS_HARD_CAP, false))

    await invokeQueryFn()

    const call = mockGetSessions.mock.calls[0][0]
    expect(call).toMatchObject({ limit: SESSIONS_HARD_CAP })
    expect(call.offset).toBeUndefined()
    expect(typeof call.since).toBe("string")
    expect(typeof call.until).toBe("string")
  })
})
