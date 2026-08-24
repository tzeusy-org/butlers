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
//
// bu-jyd6e adds the commitment-class panel (RFC 0026). A commitment is an
// owner_conditions row whose metadata.class is "commitment"; everything else
// on this panel must keep rendering through the exact path it did before,
// which is why every commitment-absence assertion below is paired with a
// positive control asserting the non-commitment row still rendered.
//   - Commitment fields: counterparty name, direction, kind badge, deadline
//   - Direction indicators: outgoing / incoming / circular
//   - Overdue deadline: warning styling, and only while still standing
//   - Confidence: surfaced only at >= 0.8
//   - Unresolved counterparty id: never fabricated into a name
//   - Class filter: all / commitments / non-commitments (RTL, real clicks)
//   - Deadline-proximity ordering for deadline-bearing commitments
//   - Commitments-only empty state, with the tile chrome still around it
// ---------------------------------------------------------------------------

import { afterEach, describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"
// The filter is the one behaviour on this panel that cannot be observed in
// static markup -- it needs a real click -- so those specs (and only those)
// mount through RTL alongside the existing renderToStaticMarkup suites.
import { cleanup, fireEvent, render as renderDom, screen } from "@testing-library/react"

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

// Counterparty name resolution (bu-jyd6e): the tile hydrates
// metadata.counterparty_entity_id through the existing relationship-entity
// lookup. Empty by default so the "never fabricate a name" path is the
// default, and tests that want a name opt in.
let mockEntityItems: { id: string; canonical_name: string }[] = []

vi.mock("@/hooks/use-entities", () => ({
  useRelationshipEntitiesByIds: () => ({
    data: {
      items: mockEntityItems,
      total: mockEntityItems.length,
      limit: 50,
      offset: 0,
    },
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
              // bu-o4i4j: the terminal reason is top-level for every
              // resolution path; only the successor lineage stays nested.
              resolution_reason: "superseded_by_identity_version_bump",
              identity_payload: {
                version: 1,
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

// ---------------------------------------------------------------------------
// bu-jyd6e: commitment-class conditions (RFC 0026)
//
// Deadlines in these fixtures are deliberately far past (2020) or far future
// (2098/2099) so "overdue" is decided by the fixture, not by when the suite
// runs.
// ---------------------------------------------------------------------------

const SAM_ENTITY_ID = "55555555-5555-5555-5555-555555555555"

/** An outgoing, high-confidence promise with a future deadline. */
const commitmentCondition = {
  ledger: "owner",
  id: "55555555-0000-0000-0000-000000000001",
  source: "relationship:commitment",
  fingerprint: "e".repeat(64),
  episode: 1,
  state: "open",
  first_detected_at: "2026-07-26T10:00:00Z",
  last_confirmed_at: "2026-07-26T10:00:00Z",
  last_escalated_at: null,
  next_reescalate_at: null,
  escalation_level: "L0",
  resolved_at: null,
  recovered_after_s: null,
  summary: "Send Sam the book",
  metadata: {
    class: "commitment",
    kind: "promise",
    direction: "owner_to_other",
    counterparty_entity_id: SAM_ENTITY_ID,
    confidence: 0.9,
    evidence_opened: { source: "conversation_extraction" },
    deadline: "2099-01-01T00:00:00Z",
  },
}

/** A plain (non-commitment) owner condition -- the regression control. */
const plainOwnerCondition = {
  ledger: "owner",
  id: "55555555-0000-0000-0000-000000000002",
  source: "finance:bill-overdue",
  fingerprint: "f".repeat(64),
  episode: 1,
  state: "open",
  first_detected_at: "2026-07-30T10:00:00Z",
  last_confirmed_at: "2026-07-30T10:00:00Z",
  last_escalated_at: null,
  next_reescalate_at: null,
  escalation_level: "L1",
  resolved_at: null,
  recovered_after_s: null,
  summary: "Bill overdue: Utility Co",
  metadata: null,
}

function resetCommitmentMocks(): void {
  mockSuppressionCounts = new Map()
  mockSuppressionIsError = false
  mockOwnerConditionsResult = null
  mockEntityItems = [{ id: SAM_ENTITY_ID, canonical_name: "Sam Rivera" }]
}

function showConditions(conditions: unknown[]): void {
  mockConditionsResult = {
    isPending: false,
    data: makeFacts({
      conditions: conditions as ConditionsFacts["conditions"],
      total: conditions.length,
    }),
  }
}

describe("StandingConditionsTile -- commitment fields", () => {
  afterEach(resetCommitmentMocks)

  it("renders counterparty name, direction, kind badge, and deadline", () => {
    resetCommitmentMocks()
    showConditions([commitmentCondition])
    const html = render()
    expect(html).toContain('data-testid="commitment-detail"')
    expect(html).toContain("Sam Rivera")
    expect(html).toContain('data-direction="owner_to_other"')
    expect(html).toContain('data-kind="promise"')
    expect(html).toContain("Promise")
    expect(html).toContain('data-testid="commitment-deadline"')
    expect(html).toContain("2099-01-01T00:00:00Z")
  })

  it("keeps the shared row chrome on a commitment row", () => {
    resetCommitmentMocks()
    showConditions([commitmentCondition])
    const html = render()
    expect(html).toContain("relationship:commitment")
    expect(html).toContain("Send Sam the book")
    expect(html).toContain('data-testid="standing-condition-ledger-badge"')
    expect(html).toContain("L0")
  })

  it("renders the high-confidence indicator at 0.9", () => {
    resetCommitmentMocks()
    showConditions([commitmentCondition])
    expect(render()).toContain('data-testid="commitment-confidence"')
  })

  it("omits the confidence indicator below the surfacing threshold", () => {
    resetCommitmentMocks()
    showConditions([
      { ...commitmentCondition, metadata: { ...commitmentCondition.metadata, confidence: 0.7 } },
    ])
    const html = render()
    // Positive control: the commitment itself still renders -- an absent
    // confidence chip must not be able to mean "nothing rendered".
    expect(html).toContain('data-testid="commitment-detail"')
    expect(html).toContain("Send Sam the book")
    expect(html).not.toContain('data-testid="commitment-confidence"')
  })

  it("never fabricates a name for a counterparty the lookup did not return", () => {
    resetCommitmentMocks()
    mockEntityItems = []
    showConditions([commitmentCondition])
    const html = render()
    expect(html).toContain('data-testid="commitment-counterparty-unresolved"')
    expect(html).toContain("Counterparty unresolved")
    expect(html).not.toContain('data-testid="commitment-counterparty"')
  })

  it("omits the counterparty slot entirely for a self-commitment", () => {
    resetCommitmentMocks()
    showConditions([
      {
        ...commitmentCondition,
        metadata: {
          ...commitmentCondition.metadata,
          direction: "self",
          counterparty_entity_id: null,
        },
      },
    ])
    const html = render()
    // Positive control: the commitment detail line is present, so the two
    // absence assertions below are about the counterparty slot only.
    expect(html).toContain('data-testid="commitment-detail"')
    expect(html).not.toContain('data-testid="commitment-counterparty"')
    expect(html).not.toContain('data-testid="commitment-counterparty-unresolved"')
  })

  it("degrades gracefully when the metadata carries no kind or deadline", () => {
    resetCommitmentMocks()
    showConditions([
      {
        ...commitmentCondition,
        metadata: {
          class: "commitment",
          direction: "other_to_owner",
          counterparty_entity_id: SAM_ENTITY_ID,
          confidence: 0.9,
        },
      },
    ])
    const html = render()
    expect(html).toContain('data-direction="other_to_owner"')
    expect(html).toContain("Sam Rivera")
    expect(html).not.toContain('data-testid="commitment-kind-badge"')
    expect(html).not.toContain('data-testid="commitment-deadline"')
  })
})

describe("StandingConditionsTile -- direction indicators", () => {
  afterEach(resetCommitmentMocks)

  const cases: { direction: string; glyph: string }[] = [
    { direction: "owner_to_other", glyph: "→" },
    { direction: "other_to_owner", glyph: "←" },
    { direction: "self", glyph: "↺" },
  ]

  for (const { direction, glyph } of cases) {
    it(`renders the ${direction} indicator`, () => {
      resetCommitmentMocks()
      showConditions([
        { ...commitmentCondition, metadata: { ...commitmentCondition.metadata, direction } },
      ])
      const html = render()
      expect(html).toContain(`data-direction="${direction}"`)
      expect(html).toContain(glyph)
    })
  }

  it("renders no indicator for a direction outside the vocabulary", () => {
    resetCommitmentMocks()
    showConditions([
      {
        ...commitmentCondition,
        metadata: { ...commitmentCondition.metadata, direction: "sideways" },
      },
    ])
    const html = render()
    // Positive control: the row is still a commitment and still renders.
    expect(html).toContain('data-testid="commitment-detail"')
    expect(html).toContain("Send Sam the book")
    expect(html).not.toContain('data-testid="commitment-direction"')
  })
})

describe("StandingConditionsTile -- overdue deadlines", () => {
  afterEach(resetCommitmentMocks)

  it("marks a past deadline overdue with warning styling", () => {
    resetCommitmentMocks()
    showConditions([
      {
        ...commitmentCondition,
        metadata: { ...commitmentCondition.metadata, deadline: "2020-01-01T00:00:00Z" },
      },
    ])
    const html = render()
    expect(html).toContain('data-overdue="true"')
    expect(html).toContain("Overdue")
    expect(html).toContain("text-destructive")
  })

  it("does not mark a future deadline overdue", () => {
    resetCommitmentMocks()
    showConditions([commitmentCondition])
    const html = render()
    expect(html).toContain('data-overdue="false"')
    expect(html).toContain("Due ")
    expect(html).not.toContain('data-overdue="true"')
  })

  it("does not mark a resolved commitment overdue", () => {
    resetCommitmentMocks()
    showConditions([
      {
        ...commitmentCondition,
        state: "resolved",
        resolved_at: "2026-07-27T10:00:00Z",
        metadata: { ...commitmentCondition.metadata, deadline: "2020-01-01T00:00:00Z" },
      },
    ])
    const html = render()
    // Positive control: the resolved commitment row (and its deadline) render.
    expect(html).toContain("Resolved")
    expect(html).toContain('data-testid="commitment-deadline"')
    expect(html).toContain('data-overdue="false"')
    expect(html).not.toContain('data-overdue="true"')
  })
})

describe("StandingConditionsTile -- non-commitment rows are untouched", () => {
  afterEach(resetCommitmentMocks)

  it("renders a plain owner condition with no commitment chrome", () => {
    resetCommitmentMocks()
    showConditions([plainOwnerCondition])
    const html = render()
    // Positive control: this row's own source and summary still render.
    expect(html).toContain("finance:bill-overdue")
    expect(html).toContain("Bill overdue: Utility Co")
    expect(html).toContain('data-testid="standing-condition-row"')
    expect(html).not.toContain('data-testid="commitment-detail"')
  })

  it("does not treat non-commitment metadata as a commitment", () => {
    resetCommitmentMocks()
    showConditions([
      {
        ...plainOwnerCondition,
        metadata: { anomaly_kind: "spend_spike", magnitude: 3 },
      },
    ])
    const html = render()
    // Positive control: the row renders in full, metadata and all.
    expect(html).toContain("finance:bill-overdue")
    expect(html).toContain('data-testid="standing-condition-row"')
    expect(html).not.toContain('data-testid="commitment-detail"')
  })

  it("keeps the infra supersession path intact alongside a commitment", () => {
    resetCommitmentMocks()
    showConditions([
      commitmentCondition,
      {
        ledger: "infra",
        id: "55555555-0000-0000-0000-000000000003",
        source: "deploy_drift",
        fingerprint: "0".repeat(64),
        episode: 2,
        state: "resolved",
        first_detected_at: "2026-07-19T10:00:00Z",
        last_confirmed_at: "2026-07-19T12:00:00Z",
        last_escalated_at: null,
        next_reescalate_at: null,
        escalation_level: "L0",
        resolved_at: "2026-07-19T13:00:00Z",
        recovered_after_s: null,
        summary: null,
        metadata: {
          resolution_reason: "superseded_by_identity_version_bump",
          identity_payload: { version: 1, successor: { fingerprint: "1".repeat(64), version: 2 } },
        },
      },
    ])
    const html = render()
    // Positive control pair: the infra row keeps its supersession provenance
    // while the commitment row renders its own structured fields.
    expect(html).toContain("deploy_drift")
    expect(html).toContain("Superseded by identity version v2")
    expect(html).toContain('data-testid="commitment-detail"')
  })
})

describe("StandingConditionsTile -- deadline-proximity ordering", () => {
  afterEach(resetCommitmentMocks)

  const soonerCommitment = {
    ...commitmentCondition,
    id: "55555555-0000-0000-0000-000000000004",
    source: "relationship:sooner",
    fingerprint: "2".repeat(64),
    // Detected EARLIER than the other commitment, so detection order alone
    // would put it second -- only deadline proximity floats it first.
    first_detected_at: "2026-07-20T10:00:00Z",
    summary: "Reply to Sam",
    metadata: { ...commitmentCondition.metadata, deadline: "2098-01-01T00:00:00Z" },
  }

  it("sorts deadline-bearing commitments soonest-first", () => {
    resetCommitmentMocks()
    showConditions([commitmentCondition, soonerCommitment])
    const html = render()
    assert_ordered(html, "relationship:sooner", "relationship:commitment")
  })

  it("floats the deadline group above the most recently detected row", () => {
    resetCommitmentMocks()
    showConditions([plainOwnerCondition, commitmentCondition, soonerCommitment])
    const html = render()
    // plainOwnerCondition is the most recently detected row, so this also
    // proves the deadline group floats above the detection-ordered tail.
    assert_ordered(html, "relationship:sooner", "finance:bill-overdue")
    assert_ordered(html, "Send Sam the book", "finance:bill-overdue")
  })
})

// ---------------------------------------------------------------------------
// Class filter -- RTL, because a toggle has to be clicked to be proven
// ---------------------------------------------------------------------------

describe("StandingConditionsTile -- class filter", () => {
  afterEach(() => {
    cleanup()
    resetCommitmentMocks()
  })

  function mountBoth(): void {
    resetCommitmentMocks()
    showConditions([commitmentCondition, plainOwnerCondition])
    renderDom(<StandingConditionsTile />)
  }

  it("defaults to showing both classes", () => {
    mountBoth()
    expect(screen.getByTestId("standing-conditions-filter")).toBeTruthy()
    expect(screen.getByTestId("commitment-detail")).toBeTruthy()
    expect(screen.getByText("Bill overdue: Utility Co")).toBeTruthy()
    expect(screen.getByRole("button", { name: "All" }).getAttribute("aria-pressed")).toBe("true")
  })

  it("commitments-only hides non-commitment rows", () => {
    mountBoth()
    fireEvent.click(screen.getByRole("button", { name: "Commitments" }))
    // Positive control: the commitment row is still on screen.
    expect(screen.getByText("Send Sam the book")).toBeTruthy()
    expect(screen.getByTestId("commitment-detail")).toBeTruthy()
    expect(screen.queryByText("Bill overdue: Utility Co")).toBeNull()
  })

  it("non-commitments-only hides commitment rows", () => {
    mountBoth()
    fireEvent.click(screen.getByRole("button", { name: "Other" }))
    // Positive control: the plain condition is still on screen.
    expect(screen.getByText("Bill overdue: Utility Co")).toBeTruthy()
    expect(screen.queryByTestId("commitment-detail")).toBeNull()
    expect(screen.queryByText("Send Sam the book")).toBeNull()
  })

  it("returns to both classes when All is re-selected", () => {
    mountBoth()
    fireEvent.click(screen.getByRole("button", { name: "Commitments" }))
    fireEvent.click(screen.getByRole("button", { name: "All" }))
    expect(screen.getByText("Send Sam the book")).toBeTruthy()
    expect(screen.getByText("Bill overdue: Utility Co")).toBeTruthy()
  })

  it("shows a no-commitments empty state, with the tile still around it", () => {
    resetCommitmentMocks()
    showConditions([plainOwnerCondition])
    renderDom(<StandingConditionsTile />)
    fireEvent.click(screen.getByRole("button", { name: "Commitments" }))
    expect(screen.getByTestId("standing-conditions-filtered-empty").textContent).toBe(
      "No commitments recorded.",
    )
    // Positive controls: a crashed tile cannot pass as an empty state.
    expect(screen.getByText("Standing Conditions")).toBeTruthy()
    expect(screen.getByTestId("standing-conditions-filter")).toBeTruthy()
    expect(screen.getByRole("button", { name: "Commitments" }).getAttribute("aria-pressed")).toBe(
      "true",
    )
  })

  it("does not offer the filter when the whole ledger is empty", () => {
    resetCommitmentMocks()
    mockConditionsResult = { isPending: false, data: makeFacts() }
    renderDom(<StandingConditionsTile />)
    // Positive control: the honest all-clear empty state is what rendered.
    expect(screen.getByText("No standing conditions recorded.")).toBeTruthy()
    expect(screen.queryByTestId("standing-conditions-filter")).toBeNull()
  })
})
