// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// StandingConditionsTile tests -- bu-ep4ks.3
//
// Coverage:
//   - Loading state: skeleton rendered, no content
//   - Error state: error message rendered
//   - Degraded state (conditions_available=false): degraded notice, distinct
//     from both error and empty -- never a fabricated all-clear
//   - Empty ledger (conditions_available=true, conditions=[]): empty-state
//   - Populated: source, escalation level, state, summary rendered
//   - Resolved condition: recovery provenance (resolved-relative + duration)
//   - Suppressed QA dispatch count: rendered when the fingerprint matches
//   - Suppression-count source degraded: never fabricates a 0 count
// ---------------------------------------------------------------------------

import { afterEach, describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import type { ApiResponse, ConditionsFacts } from "@/api/types"
import { StandingConditionsTile } from "./StandingConditionsTile"

type ConditionsHookResult = Partial<{
  isPending: boolean
  isError: boolean
  data: ApiResponse<ConditionsFacts>
}>

let mockConditionsResult: ConditionsHookResult = { isPending: false }
// null means "same as mockConditionsResult" -- most tests exercise a single
// ledger's shape and expect both the infra and owner useSystemConditions()
// calls to see it identically (mirrors production symmetry). Tests
// exercising partial degradation (one ledger down, the other healthy) set
// this explicitly.
let mockOwnerConditionsResult: ConditionsHookResult | null = null
let mockSuppressionCounts: Map<string, number> = new Map()
let mockSuppressionIsError = false

vi.mock("@/hooks/use-system", () => ({
  useSystemConditions: (params?: { ledger?: string }) =>
    params?.ledger === "owner" && mockOwnerConditionsResult !== null
      ? mockOwnerConditionsResult
      : mockConditionsResult,
}))

vi.mock("@/hooks/use-healing", () => ({
  useInfraConditionSuppressionCounts: () => ({
    counts: mockSuppressionCounts,
    isLoading: false,
    isError: mockSuppressionIsError,
  }),
}))

vi.mock("@/components/ui/time", () => ({
  Time: ({ value }: { value: string }) => <time dateTime={value}>{value}</time>,
}))

function makeFacts(overrides: Partial<ConditionsFacts> = {}): ApiResponse<ConditionsFacts> {
  return {
    data: {
      conditions: [],
      total: 0,
      conditions_available: true,
      ...overrides,
    },
    meta: {},
  }
}

function render(): string {
  return renderToStaticMarkup(<StandingConditionsTile />)
}

describe("StandingConditionsTile -- loading state", () => {
  it("renders skeleton when isPending=true", () => {
    mockConditionsResult = { isPending: true }
    expect(render()).toContain("standing-conditions-skeleton")
  })

  it("does not render content while loading", () => {
    mockConditionsResult = { isPending: true }
    const html = render()
    expect(html).not.toContain("standing-conditions-content")
    expect(html).not.toContain("standing-conditions-empty")
  })
})

describe("StandingConditionsTile -- error state", () => {
  it("renders error message when isError=true", () => {
    mockConditionsResult = { isPending: false, isError: true }
    expect(render()).toContain("standing-conditions-error")
  })

  it("does not render content when isError=true", () => {
    mockConditionsResult = { isPending: false, isError: true }
    expect(render()).not.toContain("standing-conditions-content")
  })
})

describe("StandingConditionsTile -- degraded state (never a fabricated all-clear)", () => {
  it("renders a degraded notice when conditions_available=false", () => {
    mockConditionsResult = {
      isPending: false,
      data: makeFacts({ conditions_available: false }),
    }
    expect(render()).toContain("standing-conditions-degraded")
  })

  it("degraded notice is distinct from the empty state", () => {
    mockConditionsResult = {
      isPending: false,
      data: makeFacts({ conditions_available: false }),
    }
    const html = render()
    expect(html).not.toContain("standing-conditions-empty")
    expect(html).toContain("unavailable")
  })
})

describe("StandingConditionsTile -- empty ledger", () => {
  it("renders empty-state when conditions_available=true and list is empty", () => {
    mockConditionsResult = { isPending: false, data: makeFacts() }
    expect(render()).toContain("standing-conditions-empty")
  })

  it("renders 'No standing conditions recorded' text", () => {
    mockConditionsResult = { isPending: false, data: makeFacts() }
    expect(render()).toContain("No standing conditions recorded")
  })
})

describe("StandingConditionsTile -- populated", () => {
  const openCondition = {
    ledger: "infra",
    id: "11111111-1111-1111-1111-111111111111",
    source: "infra_state",
    fingerprint: "a".repeat(64),
    episode: 1,
    state: "open",
    first_detected_at: "2026-07-25T10:00:00Z",
    last_confirmed_at: "2026-07-25T10:05:00Z",
    last_escalated_at: null,
    next_reescalate_at: null,
    escalation_level: "L1",
    resolved_at: null,
    recovered_after_s: null,
    summary: "backup source unreachable",
    metadata: null,
  }

  it("renders content container", () => {
    mockSuppressionCounts = new Map()
    mockConditionsResult = { isPending: false, data: makeFacts({ conditions: [openCondition], total: 1 }) }
    expect(render()).toContain("standing-conditions-content")
  })

  it("renders source, escalation level, and state", () => {
    mockSuppressionCounts = new Map()
    mockConditionsResult = { isPending: false, data: makeFacts({ conditions: [openCondition], total: 1 }) }
    const html = render()
    expect(html).toContain("infra_state")
    expect(html).toContain("L1")
    expect(html).toContain("open")
  })

  it("renders the summary text", () => {
    mockSuppressionCounts = new Map()
    mockConditionsResult = { isPending: false, data: makeFacts({ conditions: [openCondition], total: 1 }) }
    expect(render()).toContain("backup source unreachable")
  })

  it("renders a suppressed-count chip when the fingerprint has suppressions", () => {
    mockSuppressionCounts = new Map([[openCondition.fingerprint, 3]])
    mockConditionsResult = { isPending: false, data: makeFacts({ conditions: [openCondition], total: 1 }) }
    const html = render()
    expect(html).toContain("condition-suppressed-count")
    expect(html).toContain("3 QA dispatches suppressed")
  })

  it("omits the suppressed-count chip when there are none", () => {
    mockSuppressionCounts = new Map()
    mockConditionsResult = { isPending: false, data: makeFacts({ conditions: [openCondition], total: 1 }) }
    expect(render()).not.toContain("condition-suppressed-count")
  })

  it("renders a degraded note when the suppression-count source errors, instead of a fabricated 0", () => {
    mockSuppressionIsError = true
    mockSuppressionCounts = new Map()
    mockConditionsResult = { isPending: false, data: makeFacts({ conditions: [openCondition], total: 1 }) }
    const html = render()
    expect(html).toContain("standing-conditions-suppression-degraded")
    expect(html).not.toContain("condition-suppressed-count")
    mockSuppressionIsError = false
  })

  it("does not render the suppression-degraded note when the source is healthy", () => {
    mockSuppressionIsError = false
    mockSuppressionCounts = new Map()
    mockConditionsResult = { isPending: false, data: makeFacts({ conditions: [openCondition], total: 1 }) }
    expect(render()).not.toContain("standing-conditions-suppression-degraded")
  })
})

describe("StandingConditionsTile -- resolved condition (auto-resolve provenance)", () => {
  const resolvedCondition = {
    ledger: "infra",
    id: "22222222-2222-2222-2222-222222222222",
    source: "deploy_drift",
    fingerprint: "b".repeat(64),
    episode: 2,
    state: "resolved",
    first_detected_at: "2026-07-20T10:00:00Z",
    last_confirmed_at: "2026-07-20T12:00:00Z",
    last_escalated_at: null,
    next_reescalate_at: null,
    escalation_level: "L0",
    resolved_at: "2026-07-20T13:00:00Z",
    recovered_after_s: 3600,
    summary: null,
    metadata: null,
  }

  it("renders 'Resolved' provenance with recovery duration", () => {
    mockSuppressionCounts = new Map()
    mockSuppressionIsError = false
    mockConditionsResult = {
      isPending: false,
      data: makeFacts({ conditions: [resolvedCondition], total: 1 }),
    }
    const html = render()
    expect(html).toContain("Resolved")
    expect(html).toContain("recovered after 1h")
  })

  it("renders supersession rather than recovery for an identity-version successor", () => {
    mockSuppressionCounts = new Map()
    mockSuppressionIsError = false
    mockConditionsResult = {
      isPending: false,
      data: makeFacts({
        conditions: [
          {
            ...resolvedCondition,
            metadata: {
              identity_payload: {
                version: 1,
                resolution_reason: "superseded_by_identity_version_bump",
                successor: { fingerprint: "c".repeat(64), version: 2 },
              },
            },
          },
        ],
        total: 1,
      }),
    }

    const html = render()
    expect(html).toContain("Superseded by identity version v2")
    expect(html).not.toContain("recovered after 1h")
  })
})

// ---------------------------------------------------------------------------
// bu-ep4ks.6: both ledgers (infra + owner) merged onto one panel
// ---------------------------------------------------------------------------

describe("StandingConditionsTile -- ledger badge", () => {
  const infraCondition = {
    ledger: "infra",
    id: "33333333-3333-3333-3333-333333333333",
    source: "deploy_drift",
    fingerprint: "c".repeat(64),
    episode: 1,
    state: "open",
    first_detected_at: "2026-07-25T10:00:00Z",
    last_confirmed_at: "2026-07-25T10:00:00Z",
    last_escalated_at: null,
    next_reescalate_at: null,
    escalation_level: "L0",
    resolved_at: null,
    recovered_after_s: null,
    summary: null,
    metadata: null,
  }
  const ownerCondition = {
    ledger: "owner",
    id: "44444444-4444-4444-4444-444444444444",
    source: "finance:bill-overdue",
    fingerprint: "d".repeat(64),
    episode: 1,
    state: "open",
    first_detected_at: "2026-07-24T10:00:00Z",
    last_confirmed_at: "2026-07-24T10:00:00Z",
    last_escalated_at: null,
    next_reescalate_at: null,
    escalation_level: "L1",
    resolved_at: null,
    recovered_after_s: null,
    summary: "Bill overdue: Utility Co",
    metadata: null,
  }

  afterEach(() => {
    mockOwnerConditionsResult = null
  })

  it("merges rows from both ledgers into one list, most-recently-detected first", () => {
    mockSuppressionCounts = new Map()
    mockConditionsResult = {
      isPending: false,
      data: makeFacts({ conditions: [infraCondition], total: 1 }),
    }
    mockOwnerConditionsResult = {
      isPending: false,
      data: makeFacts({ conditions: [ownerCondition], total: 1 }),
    }
    const html = render()
    assert_ordered(html, "deploy_drift", "finance:bill-overdue")
  })

  it("labels an infra row 'Infra' and an owner row 'Owner'", () => {
    mockSuppressionCounts = new Map()
    mockConditionsResult = {
      isPending: false,
      data: makeFacts({ conditions: [infraCondition], total: 1 }),
    }
    mockOwnerConditionsResult = {
      isPending: false,
      data: makeFacts({ conditions: [ownerCondition], total: 1 }),
    }
    const html = render()
    expect(html).toContain("Infra")
    expect(html).toContain("Owner")
    expect(html).toContain("Bill overdue: Utility Co")
  })

  it("shows an infra-degraded note when only the owner ledger is available", () => {
    mockSuppressionCounts = new Map()
    mockConditionsResult = { isPending: false, data: makeFacts({ conditions_available: false }) }
    mockOwnerConditionsResult = {
      isPending: false,
      data: makeFacts({ conditions: [ownerCondition], total: 1 }),
    }
    const html = render()
    expect(html).toContain("standing-conditions-infra-degraded")
    expect(html).toContain("finance:bill-overdue")
  })

  it("shows an owner-degraded note when only the infra ledger is available", () => {
    mockSuppressionCounts = new Map()
    mockConditionsResult = {
      isPending: false,
      data: makeFacts({ conditions: [infraCondition], total: 1 }),
    }
    mockOwnerConditionsResult = { isPending: false, data: makeFacts({ conditions_available: false }) }
    const html = render()
    expect(html).toContain("standing-conditions-owner-degraded")
    expect(html).toContain("deploy_drift")
  })

  it("does not compute a suppression count for an owner-ledger row", () => {
    mockSuppressionCounts = new Map([[ownerCondition.fingerprint, 5]])
    mockConditionsResult = { isPending: false, data: makeFacts() }
    mockOwnerConditionsResult = {
      isPending: false,
      data: makeFacts({ conditions: [ownerCondition], total: 1 }),
    }
    const html = render()
    expect(html).not.toContain("condition-suppressed-count")
  })
})

function assert_ordered(html: string, first: string, second: string): void {
  const firstIndex = html.indexOf(first)
  const secondIndex = html.indexOf(second)
  expect(firstIndex).toBeGreaterThanOrEqual(0)
  expect(secondIndex).toBeGreaterThanOrEqual(0)
  expect(firstIndex).toBeLessThan(secondIndex)
}
