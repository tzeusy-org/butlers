// ---------------------------------------------------------------------------
// use-qa-badge — unit tests for badge count hooks [bu-k12cq]
//
// Coverage:
//   useApprovalsPendingBadge:
//     - loading state (data undefined): returns 0
//     - success with count > 0: returns total_pending
//     - success with count == 0: returns 0
//   useBadgeCounts:
//     - includes both qa-known-issues and approvals-pending keys
// ---------------------------------------------------------------------------

import { describe, expect, it, vi, beforeEach } from "vitest"

// Mocks must be declared before the module under test is imported.
vi.mock("./use-qa", () => ({
  useQaKnownIssues: vi.fn(() => ({ data: undefined })),
}))

vi.mock("./use-butlers", () => ({
  useButlers: vi.fn(() => ({ data: undefined })),
}))

vi.mock("./use-approvals", () => ({
  useApprovalMetrics: vi.fn(() => ({ data: undefined })),
}))

import { useApprovalMetrics } from "./use-approvals"
import { useApprovalsPendingBadge, useBadgeCounts } from "./use-qa-badge"

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

describe("useBadgeCounts", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("includes approvals-pending key in the returned map", () => {
    mockApprovalMetrics(3)
    const counts = useBadgeCounts()
    expect("approvals-pending" in counts).toBe(true)
    expect(counts["approvals-pending"]).toBe(3)
  })

  it("includes qa-known-issues key alongside approvals-pending", () => {
    mockApprovalMetrics(0)
    const counts = useBadgeCounts()
    expect("qa-known-issues" in counts).toBe(true)
    expect("approvals-pending" in counts).toBe(true)
  })

  it("approvals-pending is 0 when data is loading", () => {
    mockApprovalMetrics(undefined)
    expect(useBadgeCounts()["approvals-pending"]).toBe(0)
  })
})
