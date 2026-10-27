// ---------------------------------------------------------------------------
// use-qa-badge — unit tests for badge count hooks [bu-k12cq]
//
// Coverage:
//   useQaEscalationsBadge:
//     - loading state (data undefined): returns 0
//     - success with count > 0: returns active_breakdown.escalated_open_cases
//     - success with count == 0: returns 0
//   useApprovalsPendingBadge:
//     - loading state (data undefined): returns a quiet zero count state
//     - success with count > 0: returns total_pending
//     - success with count == 0: returns a truthful zero count state
//     - query or pending-actions aggregate failure: returns unavailable
//   useBadgeCounts:
//     - includes only qa-escalations and approvals-pending keys
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
  pendingApprovalMetricSourcesDegraded: (
    response: { meta?: { pending_actions_sources_degraded?: string[] } } | undefined,
  ) => response?.meta?.pending_actions_sources_degraded ?? [],
}))

import { useQaSummary } from "./use-qa"
import { useApprovalMetrics } from "./use-approvals"
import {
  useApprovalsPendingBadge,
  useQaEscalationsBadge,
  useBadgeCounts,
} from "./use-qa-badge"

function mockQaSummary(escalatedOpenCases: number | undefined, isError = false) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const result: any =
    escalatedOpenCases === undefined
      ? { data: undefined, isError }
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

function mockApprovalMetrics(
  totalPending: number | undefined,
  pendingActionsSourcesDegraded: string[] = [],
  isError = false,
) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const result: any =
    totalPending === undefined
      ? { data: undefined, isError }
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
            meta: { pending_actions_sources_degraded: pendingActionsSourcesDegraded },
          },
          isError,
        }
  vi.mocked(useApprovalMetrics).mockReturnValue(result)
}

describe("useQaEscalationsBadge", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("returns 0 when data is still loading (undefined)", () => {
    mockQaSummary(undefined)
    expect(useQaEscalationsBadge()).toEqual({ kind: "count", count: 0 })
  })

  it("reports unavailable instead of a calm zero when the summary fetch fails", () => {
    mockQaSummary(undefined, true)
    expect(useQaEscalationsBadge()).toEqual({ kind: "unavailable" })
  })

  it("returns the escalated open-case count when greater than 0", () => {
    mockQaSummary(4)
    expect(useQaEscalationsBadge()).toEqual({ kind: "count", count: 4 })
  })

  it("returns 0 when the escalated open-case count is 0", () => {
    mockQaSummary(0)
    expect(useQaEscalationsBadge()).toEqual({ kind: "count", count: 0 })
  })
})

describe("useApprovalsPendingBadge", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("returns a quiet zero count state when data is still loading (undefined)", () => {
    mockApprovalMetrics(undefined)
    expect(useApprovalsPendingBadge()).toEqual({ kind: "count", count: 0 })
  })

  it("returns the pending count when count is greater than 0", () => {
    mockApprovalMetrics(5)
    expect(useApprovalsPendingBadge()).toEqual({ kind: "count", count: 5 })
  })

  it("returns a truthful zero count state when count is 0", () => {
    mockApprovalMetrics(0)
    expect(useApprovalsPendingBadge()).toEqual({ kind: "count", count: 0 })
  })

  it("returns unavailable when pending-actions metrics are partial", () => {
    mockApprovalMetrics(0, ["home"])
    expect(useApprovalsPendingBadge()).toEqual({ kind: "unavailable" })
  })

  it("returns unavailable when the metrics query errors directly", () => {
    mockApprovalMetrics(undefined, [], true)
    expect(useApprovalsPendingBadge()).toEqual({ kind: "unavailable" })
  })
})

describe("useBadgeCounts", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("includes approvals-pending key in the returned map", () => {
    mockApprovalMetrics(3)
    const counts = useBadgeCounts()
    expect(Object.keys(counts).sort()).toEqual(["approvals-pending", "qa-escalations"])
    expect(counts["approvals-pending"]).toEqual({ kind: "count", count: 3 })
  })

  it("includes qa-escalations key alongside approvals-pending", () => {
    mockQaSummary(2)
    mockApprovalMetrics(0)
    const counts = useBadgeCounts()
    expect("qa-escalations" in counts).toBe(true)
    expect(counts["qa-escalations"]).toEqual({ kind: "count", count: 2 })
    expect("approvals-pending" in counts).toBe(true)
  })

  it("approvals-pending keeps a quiet zero count state while loading", () => {
    mockApprovalMetrics(undefined)
    expect(useBadgeCounts()["approvals-pending"]).toEqual({ kind: "count", count: 0 })
  })
})
