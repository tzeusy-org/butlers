// ---------------------------------------------------------------------------
// use-qa-badge — unit tests for badge count hooks [bu-k12cq]
//
// Coverage:
//   useQaEscalationsBadge:
//     - loading state (data undefined): returns 0
//     - success with count > 0: returns active_breakdown.escalated_open_cases
//     - success with count == 0: returns 0
//   useApprovalsPendingBadge:
//     - loading state (data undefined): returns 0
//     - success with count > 0: returns total_pending
//     - success with count == 0: returns 0
//   useDecisionsOpenBadge:
//     - loading state (data undefined): returns 0
//     - success with count > 0: returns data.data.length
//     - meta.decisions_available === false: returns 0 (never a fabricated count)
//   useBadgeCounts:
//     - includes qa-escalations, approvals-pending, and decisions-open keys
// ---------------------------------------------------------------------------

import { describe, expect, it, vi, beforeEach } from "vitest"

// Mocks must be declared before the module under test is imported.
vi.mock("./use-qa", () => ({
  useQaSummary: vi.fn(() => ({ data: undefined })),
}))

vi.mock("./use-butlers", () => ({
  useButlers: vi.fn(() => ({
    data: { data: [{ name: "qa" }], meta: {} },
  })),
}))

vi.mock("./use-approvals", () => ({
  useApprovalMetrics: vi.fn(() => ({ data: undefined })),
}))

vi.mock("./use-decisions", () => ({
  useDecisions: vi.fn(() => ({ data: undefined })),
}))

import { useQaSummary } from "./use-qa"
import { useApprovalMetrics } from "./use-approvals"
import { useDecisions } from "./use-decisions"
import {
  useApprovalsPendingBadge,
  useDecisionsOpenBadge,
  useQaEscalationsBadge,
  useBadgeCounts,
} from "./use-qa-badge"

function mockQaSummary(escalatedOpenCases: number | undefined) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const result: any =
    escalatedOpenCases === undefined
      ? { data: undefined }
      : {
          data: {
            data: {
              active_breakdown: {
                awaiting_ci: 0,
                escalated_open_cases: escalatedOpenCases,
              },
            },
            meta: {},
          },
        }
  vi.mocked(useQaSummary).mockReturnValue(result)
}

function mockApprovalMetrics(totalPending: number | undefined) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const result: any =
    totalPending === undefined
      ? { data: undefined }
      : {
          data: {
            data: {
              total_pending: totalPending,
              total_approved_today: 0,
              total_rejected_today: 0,
              total_auto_approved_today: 0,
              total_expired_today: 0,
              avg_decision_latency_seconds: null,
              auto_approval_rate: 0,
              rejection_rate: 0,
              failure_count_today: 0,
              active_rules_count: 0,
            },
            meta: {},
          },
        }
  vi.mocked(useApprovalMetrics).mockReturnValue(result)
}

describe("useQaEscalationsBadge", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("returns 0 when data is still loading (undefined)", () => {
    mockQaSummary(undefined)
    expect(useQaEscalationsBadge()).toBe(0)
  })

  it("returns the escalated open-case count when greater than 0", () => {
    mockQaSummary(4)
    expect(useQaEscalationsBadge()).toBe(4)
  })

  it("returns 0 when the escalated open-case count is 0", () => {
    mockQaSummary(0)
    expect(useQaEscalationsBadge()).toBe(0)
  })
})

describe("useApprovalsPendingBadge", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("returns 0 when data is still loading (undefined)", () => {
    mockApprovalMetrics(undefined)
    expect(useApprovalsPendingBadge()).toBe(0)
  })

  it("returns the pending count when count is greater than 0", () => {
    mockApprovalMetrics(5)
    expect(useApprovalsPendingBadge()).toBe(5)
  })

  it("returns 0 when count is 0", () => {
    mockApprovalMetrics(0)
    expect(useApprovalsPendingBadge()).toBe(0)
  })
})

function mockDecisions(
  rows: Array<{ id: string }> | undefined,
  decisionsAvailable = true,
) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const result: any =
    rows === undefined
      ? { data: undefined }
      : { data: { data: rows, meta: { decisions_available: decisionsAvailable } } }
  vi.mocked(useDecisions).mockReturnValue(result)
}

describe("useDecisionsOpenBadge", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("returns 0 when data is still loading (undefined)", () => {
    mockDecisions(undefined)
    expect(useDecisionsOpenBadge()).toBe(0)
  })

  it("returns the open-decisions count when greater than 0", () => {
    mockDecisions([{ id: "bu-a" }, { id: "bu-b" }])
    expect(useDecisionsOpenBadge()).toBe(2)
  })

  it("returns 0 when the digest is genuinely empty", () => {
    mockDecisions([])
    expect(useDecisionsOpenBadge()).toBe(0)
  })

  it("returns 0 (never a fabricated count) when decisions_available is false", () => {
    mockDecisions([{ id: "bu-a" }], false)
    expect(useDecisionsOpenBadge()).toBe(0)
  })

  // Regression: a permissive catch-all mock/proxy (e.g. Playwright's
  // `page.route("**/api/**", () => ({ data: [] }))` used by unrelated e2e
  // specs) can return `{ data: [...] }` with `meta` entirely absent rather
  // than `{ decisions_available: true }`. `data.meta.decisions_available`
  // (no optional chaining) throws in that shape and crashes the sidebar —
  // rendered on every route — taking down unrelated pages. Locks in the
  // `data.meta?.decisions_available` guard.
  it("does not throw and returns 0 when meta is entirely absent", () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    vi.mocked(useDecisions).mockReturnValue({ data: { data: [] } } as any)
    expect(() => useDecisionsOpenBadge()).not.toThrow()
    expect(useDecisionsOpenBadge()).toBe(0)
  })
})

describe("useBadgeCounts", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("includes approvals-pending key in the returned map", () => {
    mockApprovalMetrics(3)
    mockDecisions(undefined)
    const counts = useBadgeCounts()
    expect("approvals-pending" in counts).toBe(true)
    expect(counts["approvals-pending"]).toBe(3)
  })

  it("includes qa-escalations key alongside approvals-pending", () => {
    mockQaSummary(2)
    mockApprovalMetrics(0)
    mockDecisions(undefined)
    const counts = useBadgeCounts()
    expect("qa-escalations" in counts).toBe(true)
    expect(counts["qa-escalations"]).toBe(2)
    expect("approvals-pending" in counts).toBe(true)
  })

  it("includes decisions-open key alongside the others", () => {
    mockApprovalMetrics(0)
    mockDecisions([{ id: "bu-a" }])
    const counts = useBadgeCounts()
    expect("decisions-open" in counts).toBe(true)
    expect(counts["decisions-open"]).toBe(1)
  })

  it("approvals-pending is 0 when data is loading", () => {
    mockApprovalMetrics(undefined)
    mockDecisions(undefined)
    expect(useBadgeCounts()["approvals-pending"]).toBe(0)
  })
})
