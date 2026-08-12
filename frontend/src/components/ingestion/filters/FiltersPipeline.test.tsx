// @vitest-environment jsdom
/**
 * FiltersPipeline — unit tests covering spec acceptance criteria:
 *
 * AC1: Filters route explains accept/dedupe/tier/route/execute gates.
 * AC2: Route gate distinguishes preserved-without-dispatch from drops.
 * AC3: Priority senders and channel defaults backed by API data, mutation
 *      errors visible.
 * AC4: Old card-based filter content is absent on the redesigned route.
 *
 * Additional coverage:
 * - 5-gate diagram renders all 5 gates with correct labels
 * - Proportional funnel widths reflect real counts (mocked)
 * - Route gate splits preserved-without-dispatch vs drops in funnel bar
 * - Rule rows render with condition + action
 * - Priority senders mutation surfaces error on API failure
 * - Channel defaults mutation surfaces error on API failure
 * - Archived rules section toggles open/closed
 * - Archived restore action triggers correct API call
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

;(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true

// ---------------------------------------------------------------------------
// Mocks — declared before component imports
// ---------------------------------------------------------------------------

const mockUsePipelineStats = vi.fn()
const mockUseIngestionRules = vi.fn()
const mockUpdateMutate = vi.fn()
const mockUpdateMutateAsync = vi.fn(() => Promise.resolve({ data: {} }))
const mockDeleteMutate = vi.fn()
const mockCreateMutateAsync = vi.fn(() => Promise.resolve({ data: {} }))
const mockTestMutateAsync = vi.fn(() =>
  Promise.resolve({
    data: {
      matched: true,
      decision: 'drop',
      target_butler: null,
      matched_rule_id: 'rule-001',
      matched_rule_type: 'sender_domain',
      reason: 'matched sender domain',
    },
  }),
)
const mockUsePriorityContacts = vi.fn()
const mockUseContacts = vi.fn()
const mockAddPriorityMutate = vi.fn()
const mockRemovePriorityMutate = vi.fn()
const mockUseChannelDefault = vi.fn()
const mockUpdateChannelDefaultMutate = vi.fn()

vi.mock('@/hooks/use-ingestion', () => ({
  usePipelineStats: () => mockUsePipelineStats(),
}))

vi.mock('@/hooks/use-channel-defaults', () => ({
  useChannelDefault: (channel: string, options?: { enabled?: boolean }) =>
    mockUseChannelDefault(channel, options),
  useUpdateChannelDefault: () => ({
    mutate: mockUpdateChannelDefaultMutate,
    isPending: false,
  }),
}))

vi.mock('@/hooks/use-ingestion-rules', () => ({
  useIngestionRules: (params?: { enabled?: boolean; archived?: boolean }) =>
    mockUseIngestionRules(params),
  useUpdateIngestionRule: () => ({
    mutate: mockUpdateMutate,
    mutateAsync: mockUpdateMutateAsync,
    isPending: false,
  }),
  useDeleteIngestionRule: () => ({ mutate: mockDeleteMutate }),
  useCreateIngestionRule: () => ({
    mutateAsync: mockCreateMutateAsync,
    isPending: false,
  }),
  useTestIngestionRule: () => ({
    mutateAsync: mockTestMutateAsync,
    isPending: false,
  }),
}))

vi.mock('@/hooks/use-priority-contacts', () => ({
  usePriorityContacts: () => mockUsePriorityContacts(),
  useAddPriorityContact: () => ({ mutate: mockAddPriorityMutate }),
  useRemovePriorityContact: () => ({ mutate: mockRemovePriorityMutate }),
}))

vi.mock('@/hooks/use-contacts', () => ({
  useContacts: () => mockUseContacts(),
}))

import type {
  PipelineStats,
  IngestionRule,
  PriorityContactEntry,
  ContactSummary,
} from '@/api/types'
import { ApiError } from '@/api/index.ts'
import { FiltersPipeline } from './FiltersPipeline'
import {
  PipelineGateDiagram,
  deriveGateCounts,
  groupRulesByGate,
} from './index'
import { ArchivedRulesSection } from './ArchivedRulesSection'
import { PrioritySendersBlock } from './PrioritySendersBlock'
import { ChannelDefaultsBlock } from './ChannelDefaultsBlock'

// ---------------------------------------------------------------------------
// Test data helpers
// ---------------------------------------------------------------------------

function makeStats(overrides: Partial<PipelineStats> = {}): PipelineStats {
  return {
    window: '24h',
    aggregates_available: true,
    ingested: 1000,
    filtered: 200,
    errored: 10,
    routed_by_butler: { general: 700, health: 250 },
    spark24h: Array(24).fill(40),
    rate1h: 12,
    routed_pct: 95,
    filtered24h: 200,
    failed_total: 0,
    replay_pending_total: 0,
    written_off_total: 0,
    backlog_available: true,
    ...overrides,
  }
}

function makeRule(overrides: Partial<IngestionRule> = {}): IngestionRule {
  return {
    id: 'rule-001',
    scope: 'email',
    rule_type: 'filter',
    condition: { source_channel: 'gmail' },
    action: 'drop',
    priority: 10,
    enabled: true,
    name: 'Drop spam',
    description: 'Drop known spam patterns',
    created_by: 'owner',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-02T00:00:00Z',
    deleted_at: null,
    ...overrides,
  }
}

function makeArchivedRule(overrides: Partial<IngestionRule> = {}): IngestionRule {
  return makeRule({
    id: 'rule-archived-001',
    enabled: false,
    name: 'Old block rule',
    deleted_at: '2026-04-01T00:00:00Z',
    ...overrides,
  })
}

function makeContactSummary(
  overrides: Partial<ContactSummary> = {},
): ContactSummary {
  return {
    id: 'contact-001',
    full_name: 'VIP Contact',
    first_name: 'VIP',
    last_name: 'Contact',
    nickname: null,
    email: 'vip@example.com',
    phone: null,
    labels: [],
    last_interaction_at: null,
    entity_id: null,
    ...overrides,
  }
}

function makePriorityContact(
  overrides: Partial<PriorityContactEntry> = {},
): PriorityContactEntry {
  return {
    contact_id: 'contact-001',
    added_at: '2026-01-01T00:00:00Z',
    added_by: 'dashboard',
    name: 'VIP Contact',
    contact_info_values: ['vip@example.com'],
    is_inert: false,
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------

function makeRoot(): { container: HTMLDivElement; root: Root } {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  return { container, root }
}

function cleanup(root: Root, container: HTMLDivElement) {
  act(() => root.unmount())
  container.remove()
  document.body.innerHTML = ''
}

function renderComponent(container: HTMLDivElement, root: Root, component: React.ReactElement) {
  act(() => { root.render(component) })
  return container
}

/**
 * Set a controlled <input>/<select> value the way React expects, using the
 * native value setter so React's change tracking fires onChange.
 */
function setInputValue(el: HTMLInputElement | HTMLSelectElement, value: string) {
  const proto =
    el instanceof HTMLSelectElement
      ? HTMLSelectElement.prototype
      : HTMLInputElement.prototype
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set
  setter?.call(el, value)
  el.dispatchEvent(new Event('input', { bubbles: true }))
  el.dispatchEvent(new Event('change', { bubbles: true }))
}

// ---------------------------------------------------------------------------
// Default mock setup
// ---------------------------------------------------------------------------

function setupDefaultMocks(
  statsOverrides: Partial<PipelineStats> = {},
  activeRules: IngestionRule[] = [],
  archivedRules: IngestionRule[] = [],
) {
  mockUsePipelineStats.mockReturnValue({
    data: makeStats(statsOverrides),
    isLoading: false,
  })

  // useIngestionRules is called twice per render — active rules (default params)
  // and archived rules ({ archived: true }). Switch on the PARAMS arg so the mock
  // survives re-renders (editor open/close triggers extra renders). The archived
  // view must request ?archived=true, NOT ?enabled=false (the original bug).
  mockUseIngestionRules.mockImplementation(
    (params?: { enabled?: boolean; archived?: boolean }) => ({
      data: { data: params?.archived === true ? archivedRules : activeRules },
      isLoading: false,
      isError: false,
    }),
  )

  mockUsePriorityContacts.mockReturnValue({
    data: { data: [] as PriorityContactEntry[] },
    isLoading: false,
    isError: false,
  })

  mockUseContacts.mockReturnValue({
    data: { contacts: [], total: 0 },
    isLoading: false,
  })

  mockUseChannelDefault.mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
  })
}

// ============================================================================
// Gate diagram tests
// ============================================================================

describe('PipelineGateDiagram — AC1: five gates render with correct labels', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => { ;({ container, root } = makeRoot()) })
  afterEach(() => cleanup(root, container))

  it('renders all 5 gate node labels', () => {
    const stats = makeStats()
    const counts = deriveGateCounts(stats)

    renderComponent(container, root, (
      <PipelineGateDiagram counts={counts} available={true} />
    ))

    const gateLabels = ['accept', 'dedupe', 'tier', 'route', 'execute']
    for (const label of gateLabels) {
      const node = container.querySelector(`[data-testid="gate-node-${label}"]`)
      expect(node, `gate node ${label} missing`).not.toBeNull()
    }
  })

  it('renders 5 funnel segments', () => {
    const counts = deriveGateCounts(makeStats())

    renderComponent(container, root, (
      <PipelineGateDiagram counts={counts} available={true} />
    ))

    const segments = container.querySelectorAll('[data-testid^="funnel-segment-"]')
    expect(segments.length).toBe(5)
  })

  it('names unavailable metrics instead of rendering zero-valued gate counts', () => {
    const counts = deriveGateCounts(makeStats({ aggregates_available: false }))

    renderComponent(container, root, (
      <PipelineGateDiagram counts={counts} available={false} />
    ))

    const unavailableNote = container.querySelector('[data-testid="pipeline-metrics-unavailable"]')
    expect(unavailableNote).not.toBeNull()
    expect(unavailableNote?.textContent).toContain('pipeline metrics')
    expect(container.querySelector('[data-testid^="gate-node-"]')).toBeNull()
    expect(container.textContent).not.toContain('received · 0')
  })

  it('keeps an initial metrics load distinct from an unavailable reader', () => {
    const counts = deriveGateCounts(makeStats())

    renderComponent(container, root, (
      <PipelineGateDiagram counts={counts} loading={true} available={false} />
    ))

    expect(container.querySelector('[data-testid="pipeline-metrics-loading"]')).not.toBeNull()
    expect(container.querySelector('[data-testid="pipeline-metrics-unavailable"]')).toBeNull()
    expect(container.querySelector('[data-testid^="gate-node-"]')).toBeNull()
  })
})

// ============================================================================
// Query failure states must not become zero metrics or code-policy-by-omission.
// ============================================================================

describe('FiltersPipeline reader failures (bu-xdjoq)', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
    setupDefaultMocks()
  })
  afterEach(() => cleanup(root, container))

  it('keeps successful neighbouring sections usable while metrics and rules readers are unavailable', () => {
    const retryMetrics = vi.fn()
    const retryRules = vi.fn()
    mockUsePipelineStats.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error('pipeline metrics offline'),
      refetch: retryMetrics,
    })
    mockUseIngestionRules.mockImplementation(
      (params?: { enabled?: boolean; archived?: boolean }) => ({
        data: params?.archived ? { data: [] } : undefined,
        isLoading: false,
        isError: !params?.archived,
        error: params?.archived ? null : new Error('rules reader offline'),
        refetch: retryRules,
      }),
    )
    mockUsePriorityContacts.mockReturnValue({
      data: { data: [makePriorityContact()] },
      isLoading: false,
      isError: false,
    })

    renderComponent(container, root, <FiltersPipeline />)

    expect(container.querySelector('[data-testid="pipeline-metrics-unavailable"]')).not.toBeNull()
    expect(container.querySelector('[data-testid="priority-sender-row-contact-001"]')).not.toBeNull()
    for (const key of ['accept', 'dedupe', 'tier', 'route', 'execute']) {
      const section = container.querySelector(`[data-testid="gate-section-${key}"]`)
      expect(section?.querySelector(`[data-testid="gate-rules-unavailable-${key}"]`)).not.toBeNull()
      expect(section?.textContent).not.toContain('Policy lives in code.')
      expect(section?.textContent).toContain('in —')
    }
    expect(container.querySelector('[data-testid="channel-defaults-error"]')).not.toBeNull()

    act(() => {
      ;(container.querySelector('[data-testid="pipeline-metrics-unavailable"] button') as HTMLButtonElement).click()
      ;(container.querySelector('[data-testid="gate-rules-unavailable-accept"] button') as HTMLButtonElement).click()
    })
    expect(retryMetrics).toHaveBeenCalledTimes(1)
    expect(retryRules).toHaveBeenCalledTimes(1)
  })
})

// ============================================================================
// Execution backlog — DB-backed counts independent of funnel metrics
// ============================================================================

describe('FiltersPipeline: execution backlog status', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
    setupDefaultMocks({
      failed_total: 7,
      replay_pending_total: 2,
      written_off_total: 11,
      backlog_available: true,
    })
  })
  afterEach(() => cleanup(root, container))

  it('shows unresolved and replay-pending work separately while excluding reviewed write-offs from active backlog', () => {
    renderComponent(container, root, <FiltersPipeline />)

    const backlog = container.querySelector('[data-testid="pipeline-execution-backlog"]')
    expect(backlog, 'execution backlog summary missing').not.toBeNull()
    expect(backlog?.textContent).toContain('9 active')
    expect(backlog?.textContent).toContain('7')
    expect(backlog?.textContent).toContain('unresolved failures')
    expect(backlog?.textContent).toContain('2')
    expect(backlog?.textContent).toContain('replay pending')
    expect(backlog?.textContent).toContain('11')
    expect(backlog?.textContent).toContain('reviewed write-offs')
    expect(backlog?.textContent).toContain('not active backlog')
    expect(backlog?.textContent).toContain('awaiting reconciliation')
  })

  it('shows backlog availability as unknown instead of displaying fabricated zero counts', () => {
    mockUsePipelineStats.mockReturnValue({
      data: makeStats({
        failed_total: null,
        replay_pending_total: null,
        written_off_total: null,
        backlog_available: false,
      }),
      isLoading: false,
    })

    renderComponent(container, root, <FiltersPipeline />)

    const unavailable = container.querySelector('[data-testid="pipeline-execution-backlog-unavailable"]')
    expect(unavailable, 'backlog unavailable state missing').not.toBeNull()
    expect(unavailable?.textContent).toContain('backlog unavailable')
    expect(container.querySelector('[data-testid="pipeline-execution-backlog"]')).toBeNull()
  })

  it('does not label cached execution counts as current after a failed stats refresh and retries', () => {
    const retry = vi.fn()
    mockUsePipelineStats.mockReturnValue({
      data: makeStats({
        failed_total: 7,
        replay_pending_total: 2,
        written_off_total: 11,
        backlog_available: true,
      }),
      isLoading: false,
      isError: true,
      error: new Error('pipeline metrics refresh failed'),
      refetch: retry,
    })

    renderComponent(container, root, <FiltersPipeline />)

    const unavailable = container.querySelector('[data-testid="pipeline-execution-backlog-unavailable"]')
    expect(unavailable, 'stale execution backlog must be named unavailable').not.toBeNull()
    expect(unavailable?.textContent).toContain('execution backlog')
    expect(unavailable?.textContent).toContain('unavailable')
    expect(container.querySelector('[data-testid="pipeline-execution-backlog"]')).toBeNull()
    expect(container.textContent).not.toContain('execution backlog · current ledger')

    act(() => {
      ;(unavailable?.querySelector('button') as HTMLButtonElement).click()
    })
    expect(retry).toHaveBeenCalledTimes(1)
  })
})

// ============================================================================
// AC2: Route gate preserved-without-dispatch vs drops
// ============================================================================

describe('AC2: route gate distinguishes preserved-without-dispatch from drops', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => { ;({ container, root } = makeRoot()) })
  afterEach(() => cleanup(root, container))

  it('shows preserved segment in the route gate funnel bar', () => {
    const stats = makeStats({
      ingested: 1000,
      routed_by_butler: { general: 600 }, // 400 preserved
    })
    const counts = deriveGateCounts(stats)

    renderComponent(container, root, (
      <PipelineGateDiagram counts={counts} available={true} />
    ))

    const preservedSegment = container.querySelector('[data-testid="funnel-preserved-segment"]')
    expect(preservedSegment, 'preserved segment missing').not.toBeNull()
  })

  it('shows preserved badge on route gate node when events are preserved', () => {
    const stats = makeStats({
      ingested: 1000,
      routed_by_butler: { general: 600 }, // 400 preserved
    })
    const counts = deriveGateCounts(stats)

    renderComponent(container, root, (
      <PipelineGateDiagram counts={counts} available={true} />
    ))

    const preservedBadge = container.querySelector('[data-testid="gate-preserved-route"]')
    expect(preservedBadge, 'preserved badge on route gate missing').not.toBeNull()
  })

  it('shows drop segment in the accept gate funnel bar', () => {
    const stats = makeStats({
      ingested: 800,
      filtered: 200, // 200 hard drops at accept gate
    })
    const counts = deriveGateCounts(stats)

    renderComponent(container, root, (
      <PipelineGateDiagram counts={counts} available={true} />
    ))

    const droppedSegment = container.querySelector('[data-testid="funnel-dropped-segment"]')
    expect(droppedSegment, 'dropped segment missing').not.toBeNull()
  })
})

// ============================================================================
// Gate state helpers
// ============================================================================

describe('gate-state: groupRulesByGate', () => {
  it('buckets drop rules into accept gate', () => {
    const rule = makeRule({ action: 'drop', rule_type: 'filter' })
    const groups = groupRulesByGate([rule])
    expect(groups.accept).toHaveLength(1)
    expect(groups.dedupe).toHaveLength(0)
  })

  it('buckets tier rules into tier gate', () => {
    const rule = makeRule({ action: 'tier.priority', rule_type: 'tier' })
    const groups = groupRulesByGate([rule])
    expect(groups.tier).toHaveLength(1)
  })

  it('buckets route rules into route gate', () => {
    const rule = makeRule({ action: 'route general', rule_type: 'routing' })
    const groups = groupRulesByGate([rule])
    expect(groups.route).toHaveLength(1)
  })

  it('buckets preserve rules into accept gate', () => {
    const rule = makeRule({ action: 'preserve', rule_type: 'filter' })
    const groups = groupRulesByGate([rule])
    expect(groups.accept).toHaveLength(1)
  })
})

describe('gate-state: deriveGateCounts', () => {
  it('returns zeros when aggregates_available is false', () => {
    const stats = makeStats({ aggregates_available: false })
    const counts = deriveGateCounts(stats)
    for (const c of counts) {
      expect(c.in).toBe(0)
      expect(c.out).toBe(0)
    }
  })

  it('accept gate in = ingested + filtered', () => {
    const stats = makeStats({ ingested: 1000, filtered: 200 })
    const counts = deriveGateCounts(stats)
    const accept = counts.find((c) => c.key === 'accept')!
    expect(accept.in).toBe(1200)
    expect(accept.dropped).toBe(200)
  })

  it('route gate out = sum of routed_by_butler', () => {
    const stats = makeStats({
      ingested: 1000,
      routed_by_butler: { general: 600, health: 200 },
    })
    const counts = deriveGateCounts(stats)
    const route = counts.find((c) => c.key === 'route')!
    expect(route.out).toBe(800)
    expect(route.preserved).toBe(200) // 1000 - 800
  })

  // bu-95ido: dedupe and tier gates are passthrough estimates — mark them
  // so the diagram does not imply a measurement the backend doesn't provide.
  it('dedupe gate is marked estimated (no per-gate dedup count from API)', () => {
    const counts = deriveGateCounts(makeStats())
    const dedupe = counts.find((c) => c.key === 'dedupe')!
    expect(dedupe.estimated, 'dedupe gate should be marked estimated').toBe(true)
  })

  it('tier gate is marked estimated (tiering changes priority, not count)', () => {
    const counts = deriveGateCounts(makeStats())
    const tier = counts.find((c) => c.key === 'tier')!
    expect(tier.estimated, 'tier gate should be marked estimated').toBe(true)
  })

  it('accept, route, execute gates are NOT marked estimated', () => {
    const counts = deriveGateCounts(makeStats())
    for (const key of ['accept', 'route', 'execute'] as const) {
      const gate = counts.find((c) => c.key === key)!
      expect(gate.estimated, `${key} gate should not be marked estimated`).toBeFalsy()
    }
  })
})

describe('PipelineGateDiagram — estimated gate badges render (bu-95ido)', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => { ;({ container, root } = makeRoot()) })
  afterEach(() => cleanup(root, container))

  it('renders estimated badge on dedupe gate node', () => {
    const counts = deriveGateCounts(makeStats())
    renderComponent(container, root, (
      <PipelineGateDiagram counts={counts} available={true} />
    ))

    const badge = container.querySelector('[data-testid="gate-estimated-badge-dedupe"]')
    expect(badge, 'estimated badge missing on dedupe gate').not.toBeNull()
    expect(badge?.textContent).toContain('est.')
  })

  it('renders estimated badge on tier gate node', () => {
    const counts = deriveGateCounts(makeStats())
    renderComponent(container, root, (
      <PipelineGateDiagram counts={counts} available={true} />
    ))

    const badge = container.querySelector('[data-testid="gate-estimated-badge-tier"]')
    expect(badge, 'estimated badge missing on tier gate').not.toBeNull()
    expect(badge?.textContent).toContain('est.')
  })

  it('does NOT render estimated badge on accept, route, execute gates', () => {
    const counts = deriveGateCounts(makeStats())
    renderComponent(container, root, (
      <PipelineGateDiagram counts={counts} available={true} />
    ))

    for (const key of ['accept', 'route', 'execute']) {
      const badge = container.querySelector(`[data-testid="gate-estimated-badge-${key}"]`)
      expect(badge, `unexpected estimated badge on ${key} gate`).toBeNull()
    }
  })

  it('renders "~" prefix on estimated gate count display', () => {
    const counts = deriveGateCounts(makeStats())
    renderComponent(container, root, (
      <PipelineGateDiagram counts={counts} available={true} />
    ))

    const dedupeCountEl = container.querySelector('[data-testid="gate-count-estimated-dedupe"]')
    expect(dedupeCountEl, 'estimated count span missing on dedupe gate').not.toBeNull()
    expect(dedupeCountEl?.textContent).toMatch(/^~/)
  })
})

// ============================================================================
// Rule rows
// ============================================================================

describe('GateSection + RuleRow: renders condition and action', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => { ;({ container, root } = makeRoot()) })
  afterEach(() => cleanup(root, container))

  it('renders rule name and action', () => {
    setupDefaultMocks({}, [
      makeRule({
        id: 'rule-001',
        name: 'Drop spam',
        action: 'drop',
        condition: { source_channel: 'gmail' },
      }),
    ])

    renderComponent(container, root, <FiltersPipeline />)

    // Rule row should appear
    const row = container.querySelector('[data-testid="rule-row-rule-001"]')
    expect(row, 'rule row missing').not.toBeNull()

    // Action should show 'drop'
    const action = container.querySelector('[data-testid="rule-action-rule-001"]')
    expect(action?.textContent?.toLowerCase()).toContain('drop')
  })
})

// ============================================================================
// Rule editor wiring — '+ add rule' / 'edit' / 'open DSL'
// ============================================================================

describe('FiltersPipeline: rule editor wiring', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
    mockCreateMutateAsync.mockClear()
    mockUpdateMutateAsync.mockClear()
    mockTestMutateAsync.mockClear()
  })
  afterEach(() => cleanup(root, container))

  it("'+ add rule' opens a create form", () => {
    setupDefaultMocks()
    renderComponent(container, root, <FiltersPipeline />)

    expect(container.querySelector('[data-testid="rule-editor"]')).toBeNull()

    const addBtn = container.querySelector('[data-testid="filters-add-rule"]')
    act(() => { ;(addBtn as HTMLButtonElement).click() })

    const editor = container.querySelector('[data-testid="rule-editor"]')
    expect(editor, 'editor should open on + add rule').not.toBeNull()
    // Create mode shows the "create rule" save label.
    const save = container.querySelector('[data-testid="rule-editor-save"]')
    expect(save?.textContent?.toLowerCase()).toContain('create')
  })

  it('submitting the create form calls useCreateIngestionRule', async () => {
    setupDefaultMocks()
    renderComponent(container, root, <FiltersPipeline />)

    act(() => {
      ;(container.querySelector('[data-testid="filters-add-rule"]') as HTMLButtonElement).click()
    })

    // Fill the required condition field (sender_domain default).
    const domain = container.querySelector(
      '[data-testid="rule-editor-condition-domain"]',
    ) as HTMLInputElement
    act(() => { setInputValue(domain, 'spam.example.com') })

    await act(async () => {
      ;(container.querySelector('[data-testid="rule-editor-save"]') as HTMLButtonElement).click()
    })

    expect(mockCreateMutateAsync).toHaveBeenCalledTimes(1)
    const body = (mockCreateMutateAsync.mock.calls[0] as unknown[])[0] as Record<string, unknown>
    expect(body.rule_type).toBe('sender_domain')
    // The editor now emits a runtime-valid action ('skip'), not the old inert
    // 'drop' verdict the policy engine never matched (bu-4rt0h).
    expect(body.action).toBe('skip')
    expect((body.condition as Record<string, unknown>).domain).toBe('spam.example.com')
  })

  it('blocks create when the required condition field is empty', async () => {
    setupDefaultMocks()
    renderComponent(container, root, <FiltersPipeline />)

    act(() => {
      ;(container.querySelector('[data-testid="filters-add-rule"]') as HTMLButtonElement).click()
    })

    await act(async () => {
      ;(container.querySelector('[data-testid="rule-editor-save"]') as HTMLButtonElement).click()
    })

    expect(mockCreateMutateAsync).not.toHaveBeenCalled()
    expect(container.querySelector('[data-testid="rule-editor-error"]')).not.toBeNull()
  })

  it("per-rule 'edit' opens a prefilled edit form and calls update on save", async () => {
    setupDefaultMocks({}, [
      makeRule({
        id: 'rule-001',
        name: 'Drop spam',
        action: 'drop',
        rule_type: 'sender_domain',
        condition: { domain: 'spam.example.com', match: 'exact' },
      }),
    ])
    renderComponent(container, root, <FiltersPipeline />)

    const editBtn = container.querySelector('[data-testid="rule-edit-rule-001"]')
    expect(editBtn, 'per-rule edit affordance missing').not.toBeNull()
    act(() => { ;(editBtn as HTMLButtonElement).click() })

    const editor = container.querySelector('[data-testid="rule-editor"]')
    expect(editor).not.toBeNull()
    // Prefilled name.
    const nameInput = container.querySelector(
      '[data-testid="rule-editor-name"]',
    ) as HTMLInputElement
    expect(nameInput.value).toBe('Drop spam')
    // Edit mode shows the "save changes" label.
    const save = container.querySelector('[data-testid="rule-editor-save"]')
    expect(save?.textContent?.toLowerCase()).toContain('save')

    await act(async () => { ;(save as HTMLButtonElement).click() })

    expect(mockUpdateMutateAsync).toHaveBeenCalledTimes(1)
    const arg = (mockUpdateMutateAsync.mock.calls[0] as unknown[])[0] as { id: string }
    expect(arg.id).toBe('rule-001')
  })

  it("'open DSL' opens the editor with the DSL test panel and runs a test", async () => {
    setupDefaultMocks()
    renderComponent(container, root, <FiltersPipeline />)

    const dslBtn = container.querySelector('[data-testid="filters-open-dsl"]')
    act(() => { ;(dslBtn as HTMLButtonElement).click() })

    const panel = container.querySelector('[data-testid="rule-editor-dsl-panel"]')
    expect(panel, 'DSL test panel should be visible in dsl mode').not.toBeNull()

    const sender = container.querySelector(
      '[data-testid="rule-editor-test-sender"]',
    ) as HTMLInputElement
    act(() => { setInputValue(sender, 'alerts@spam.example.com') })

    await act(async () => {
      ;(container.querySelector('[data-testid="rule-editor-test-run"]') as HTMLButtonElement).click()
    })

    expect(mockTestMutateAsync).toHaveBeenCalledTimes(1)
    const result = container.querySelector('[data-testid="rule-editor-test-result"]')
    expect(result, 'test result should render').not.toBeNull()
    expect(result?.textContent?.toLowerCase()).toContain('decision')
  })
})

// ============================================================================
// AC3: Priority senders mutation error
// ============================================================================

describe('AC3: priority senders read + mutation (public.priority_contacts)', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => { ;({ container, root } = makeRoot()) })
  afterEach(() => cleanup(root, container))

  it('renders a row per priority contact (read from priority-contacts API)', () => {
    const entry = makePriorityContact({
      contact_id: 'contact-001',
      name: 'VIP contact',
      contact_info_values: ['vip@example.com'],
    })

    renderComponent(container, root, (
      <PrioritySendersBlock
        contacts={[entry]}
        loaded={true}
        error={false}
        mutationError={null}
      />
    ))

    const block = container.querySelector('[data-testid="priority-senders-block"]')
    expect(block).not.toBeNull()

    const row = container.querySelector('[data-testid="priority-sender-row-contact-001"]')
    expect(row).not.toBeNull()
    // Channel identifier from contact_info_values is shown.
    expect(row?.textContent).toContain('vip@example.com')
  })

  it('renders added dates in the owner timezone', () => {
    const entry = makePriorityContact({
      contact_id: 'contact-boundary',
      added_at: '2025-12-31T17:00:00Z',
    })

    renderComponent(container, root, (
      <PrioritySendersBlock
        contacts={[entry]}
        loaded={true}
        error={false}
        mutationError={null}
      />
    ))

    const row = container.querySelector('[data-testid="priority-sender-row-contact-boundary"]')
    expect(row?.textContent).toContain('Jan 1, 26')
    expect(row?.querySelector('time')?.getAttribute('datetime')).toBe('2025-12-31T17:00:00.000Z')
  })

  it('renders mutation error when API fails', () => {
    renderComponent(container, root, (
      <PrioritySendersBlock
        contacts={[]}
        loaded={true}
        error={false}
        mutationError="Failed to remove priority sender: 500 Internal Server Error"
      />
    ))

    const errorEl = container.querySelector('[data-testid="priority-senders-mutation-error"]')
    expect(errorEl).not.toBeNull()
    expect(errorEl?.textContent).toContain('Failed to remove priority sender')
  })

  it('calls onRemove with the contact id when remove is clicked', () => {
    const onRemove = vi.fn()
    const entry = makePriorityContact({ contact_id: 'contact-001' })

    renderComponent(container, root, (
      <PrioritySendersBlock
        contacts={[entry]}
        loaded={true}
        error={false}
        mutationError={null}
        onRemove={onRemove}
      />
    ))

    const removeBtn = container.querySelector('[data-testid="priority-sender-remove-contact-001"]')
    expect(removeBtn).not.toBeNull()

    act(() => { ;(removeBtn as HTMLButtonElement).click() })
    expect(onRemove).toHaveBeenCalledWith('contact-001')
  })

  it('opens the add picker and calls onAdd with the selected contact id', () => {
    const onAdd = vi.fn()
    const candidates = [
      makeContactSummary({ id: 'c-1', full_name: 'Alice', email: 'alice@example.com' }),
      makeContactSummary({ id: 'c-2', full_name: 'Bob', email: 'bob@example.com' }),
    ]

    renderComponent(container, root, (
      <PrioritySendersBlock
        contacts={[]}
        loaded={true}
        error={false}
        mutationError={null}
        addCandidates={candidates}
        onAdd={onAdd}
      />
    ))

    // Picker is hidden until "+ add" is clicked.
    expect(container.querySelector('[data-testid="priority-senders-add-picker"]')).toBeNull()

    const addBtn = container.querySelector('[data-testid="priority-senders-add"]')
    act(() => { ;(addBtn as HTMLButtonElement).click() })

    const select = container.querySelector(
      '[data-testid="priority-senders-contact-select"]',
    ) as HTMLSelectElement
    expect(select).not.toBeNull()

    act(() => {
      select.value = 'c-2'
      select.dispatchEvent(new Event('change', { bubbles: true }))
    })

    expect(onAdd).toHaveBeenCalledWith('c-2')
  })

  it('renders error state when fetch fails', () => {
    renderComponent(container, root, (
      <PrioritySendersBlock
        contacts={[]}
        loaded={true}
        error={true}
        mutationError={null}
      />
    ))

    const errEl = container.querySelector('[data-testid="priority-senders-error"]')
    expect(errEl).not.toBeNull()
  })

  it('shows inert warning badge when is_inert=true', () => {
    const inertEntry = makePriorityContact({
      contact_id: 'contact-inert',
      name: 'No Entity',
      contact_info_values: [],
      is_inert: true,
    })

    renderComponent(container, root, (
      <PrioritySendersBlock
        contacts={[inertEntry]}
        loaded={true}
        error={false}
        mutationError={null}
      />
    ))

    const badge = container.querySelector('[data-testid="priority-sender-inert-contact-inert"]')
    expect(badge, 'inert warning badge should be present').not.toBeNull()
    expect(badge?.textContent).toContain('no email fact')
  })

  it('does not show inert warning badge when is_inert=false', () => {
    const activeEntry = makePriorityContact({
      contact_id: 'contact-active',
      name: 'Active VIP',
      contact_info_values: ['vip@example.com'],
      is_inert: false,
    })

    renderComponent(container, root, (
      <PrioritySendersBlock
        contacts={[activeEntry]}
        loaded={true}
        error={false}
        mutationError={null}
      />
    ))

    const badge = container.querySelector('[data-testid="priority-sender-inert-contact-active"]')
    expect(badge, 'inert warning badge should NOT be present for active contact').toBeNull()
  })

  it('shows inert badge on inert row but not on active row in mixed list', () => {
    const inertEntry = makePriorityContact({
      contact_id: 'contact-inert',
      is_inert: true,
      contact_info_values: [],
    })
    const activeEntry = makePriorityContact({
      contact_id: 'contact-active',
      is_inert: false,
      contact_info_values: ['active@example.com'],
    })

    renderComponent(container, root, (
      <PrioritySendersBlock
        contacts={[inertEntry, activeEntry]}
        loaded={true}
        error={false}
        mutationError={null}
      />
    ))

    expect(container.querySelector('[data-testid="priority-sender-inert-contact-inert"]')).not.toBeNull()
    expect(container.querySelector('[data-testid="priority-sender-inert-contact-active"]')).toBeNull()
  })
})

// ============================================================================
// AC3: Channel defaults mutation error
// ============================================================================

describe('AC3: channel defaults mutation surfaces error', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => { ;({ container, root } = makeRoot()) })
  afterEach(() => cleanup(root, container))

  it('renders channel defaults rows', () => {
    const channelRule = makeRule({
      id: 'ch-001',
      scope: 'gmail',
      rule_type: 'channel_default',
      action: 'route general',
      description: 'Default for Gmail',
    })

    renderComponent(container, root, (
      <ChannelDefaultsBlock
        rules={[channelRule]}
        loaded={true}
        error={false}
        mutationError={null}
      />
    ))

    const row = container.querySelector('[data-testid="channel-default-row-gmail"]')
    expect(row, 'channel default row missing').not.toBeNull()
  })

  it('renders mutation error when channel default update fails', () => {
    renderComponent(container, root, (
      <ChannelDefaultsBlock
        rules={[]}
        loaded={true}
        error={false}
        mutationError="Failed to update channel default: validation error"
      />
    ))

    const errEl = container.querySelector('[data-testid="channel-defaults-mutation-error"]')
    expect(errEl).not.toBeNull()
    expect(errEl?.textContent).toContain('Failed to update channel default')
  })

  it('renders error state when fetch fails', () => {
    renderComponent(container, root, (
      <ChannelDefaultsBlock
        rules={[]}
        loaded={true}
        error={true}
        mutationError={null}
      />
    ))

    const errEl = container.querySelector('[data-testid="channel-defaults-error"]')
    expect(errEl).not.toBeNull()
  })
})

// ============================================================================
// Archived rules section
// ============================================================================

describe('ArchivedRulesSection: toggles open/closed', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => { ;({ container, root } = makeRoot()) })
  afterEach(() => cleanup(root, container))

  it('is collapsed by default', () => {
    const rule = makeArchivedRule()

    renderComponent(container, root, (
      <ArchivedRulesSection rules={[rule]} />
    ))

    const section = container.querySelector('[data-testid="archived-rules-section"]')
    expect(section, 'section missing').not.toBeNull()

    const list = container.querySelector('[data-testid="archived-rules-list"]')
    expect(list, 'should be collapsed initially').toBeNull()
  })

  it('expands when toggle is clicked', () => {
    const rule = makeArchivedRule()

    renderComponent(container, root, (
      <ArchivedRulesSection rules={[rule]} />
    ))

    const toggle = container.querySelector('[data-testid="archived-rules-toggle"]')
    expect(toggle).not.toBeNull()

    act(() => { ;(toggle as HTMLButtonElement).click() })

    const list = container.querySelector('[data-testid="archived-rules-list"]')
    expect(list, 'should be expanded after click').not.toBeNull()
  })

  it('collapses again when toggle is clicked twice', () => {
    const rule = makeArchivedRule()

    renderComponent(container, root, (
      <ArchivedRulesSection rules={[rule]} />
    ))

    const toggle = container.querySelector('[data-testid="archived-rules-toggle"]')

    act(() => { ;(toggle as HTMLButtonElement).click() })
    act(() => { ;(toggle as HTMLButtonElement).click() })

    const list = container.querySelector('[data-testid="archived-rules-list"]')
    expect(list).toBeNull()
  })

  it('renders a row for each archived rule when expanded', () => {
    const rules = [makeArchivedRule({ id: 'arch-001' }), makeArchivedRule({ id: 'arch-002' })]

    renderComponent(container, root, (
      <ArchivedRulesSection rules={rules} />
    ))

    const toggle = container.querySelector('[data-testid="archived-rules-toggle"]')
    act(() => { ;(toggle as HTMLButtonElement).click() })

    const rows = container.querySelectorAll('[data-testid^="archived-rule-row-"]')
    expect(rows.length).toBe(2)
  })

  it('shows count in header', () => {
    const rules = [makeArchivedRule({ id: 'arch-001' }), makeArchivedRule({ id: 'arch-002' })]

    renderComponent(container, root, (
      <ArchivedRulesSection rules={rules} />
    ))

    const count = container.querySelector('[data-testid="archived-rules-count"]')
    expect(count?.textContent).toContain('2')
  })

  it('does not render when rules list is empty', () => {
    renderComponent(container, root, (
      <ArchivedRulesSection rules={[]} />
    ))

    const section = container.querySelector('[data-testid="archived-rules-section"]')
    expect(section).toBeNull()
  })
})

describe('ArchivedRulesSection: restore action', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => { ;({ container, root } = makeRoot()) })
  afterEach(() => cleanup(root, container))

  it('calls onRestore with correct id when restore is clicked', () => {
    const onRestore = vi.fn()
    const rule = makeArchivedRule({ id: 'arch-001' })

    renderComponent(container, root, (
      <ArchivedRulesSection rules={[rule]} onRestore={onRestore} />
    ))

    // Expand first
    const toggle = container.querySelector('[data-testid="archived-rules-toggle"]')
    act(() => { ;(toggle as HTMLButtonElement).click() })

    const restoreBtn = container.querySelector('[data-testid="archived-rule-restore-arch-001"]')
    expect(restoreBtn).not.toBeNull()

    act(() => { ;(restoreBtn as HTMLButtonElement).click() })
    expect(onRestore).toHaveBeenCalledWith('arch-001')
  })

  it('shows restore error when provided', () => {
    const rule = makeArchivedRule({ id: 'arch-001' })

    renderComponent(container, root, (
      <ArchivedRulesSection
        rules={[rule]}
        restoreError="Failed to restore: network error"
      />
    ))

    const errEl = container.querySelector('[data-testid="archived-rules-restore-error"]')
    expect(errEl).not.toBeNull()
    expect(errEl?.textContent).toContain('Failed to restore')
  })
})

// ============================================================================
// Regression: archived view queries ?archived=true (was ?enabled=false)
// bu-rnljv.3 — the archived-rules call must pass { archived: true } as the
// hook's PARAMS argument (query string), not { enabled: false } (which is
// neither the right param nor a react-query option), so the soft-deleted rules
// are actually fetched and rendered.
// ============================================================================

describe('FiltersPipeline: archived view requests archived=true', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
    mockUseIngestionRules.mockClear()
  })
  afterEach(() => cleanup(root, container))

  it('calls useIngestionRules with { archived: true } and never { enabled: false }', () => {
    setupDefaultMocks({}, [], [makeArchivedRule({ id: 'arch-001' })])

    renderComponent(container, root, <FiltersPipeline />)

    const paramCalls = mockUseIngestionRules.mock.calls.map((c) => c[0])

    // The archived view must request archived=true.
    expect(paramCalls).toContainEqual({ archived: true })

    // The buggy { enabled: false } params shape must never be used.
    for (const params of paramCalls) {
      expect(params).not.toEqual({ enabled: false })
    }
  })

  it('renders archived rows returned by the archived=true query', () => {
    setupDefaultMocks(
      {},
      [],
      [
        makeArchivedRule({ id: 'arch-001', name: 'Old block rule' }),
        makeArchivedRule({ id: 'arch-002', name: 'Retired routing rule' }),
      ],
    )

    renderComponent(container, root, <FiltersPipeline />)

    // Section renders with the archived rows (count of 2 in the header).
    const count = container.querySelector('[data-testid="archived-rules-count"]')
    expect(count?.textContent).toContain('2')

    // Expand to confirm the rows are the archived rules.
    const toggle = container.querySelector('[data-testid="archived-rules-toggle"]')
    act(() => { ;(toggle as HTMLButtonElement).click() })

    expect(
      container.querySelector('[data-testid="archived-rule-row-arch-001"]'),
      'archived row arch-001 missing',
    ).not.toBeNull()
    expect(
      container.querySelector('[data-testid="archived-rule-row-arch-002"]'),
      'archived row arch-002 missing',
    ).not.toBeNull()
  })

  it('renders an empty archived section when no rules are archived', () => {
    setupDefaultMocks({}, [], [])

    renderComponent(container, root, <FiltersPipeline />)

    // No archived rules -> section is not rendered at all.
    expect(
      container.querySelector('[data-testid="archived-rules-section"]'),
    ).toBeNull()
  })
})

// ============================================================================
// AC4: Old card-based filter content is absent
// ============================================================================

describe('AC4: old card-based filter content is absent', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => { ;({ container, root } = makeRoot()) })
  afterEach(() => cleanup(root, container))

  it('does not render shadcn Card elements (old filter UI rejected by spec)', () => {
    setupDefaultMocks()

    renderComponent(container, root, <FiltersPipeline />)

    const cards = container.querySelectorAll('[data-slot="card"]')
    expect(cards.length).toBe(0)
  })

  it('renders the filters pipeline container', () => {
    setupDefaultMocks()

    renderComponent(container, root, <FiltersPipeline />)

    const pipeline = container.querySelector('[data-testid="filters-pipeline"]')
    expect(pipeline).not.toBeNull()
  })
})

// ============================================================================
// bu-4utdw.9: --filter-* tokens never existed in index.css (silent fallback,
// no light/dark parity). The real tokens --red / --amber / --green DO exist
// with dark-mode overrides. Regression: rendered output must reference the
// real tokens and must never reference the retired --filter- prefix.
// ============================================================================

describe('bu-4utdw.9: color tokens resolve to real --red/--amber/--green', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => { ;({ container, root } = makeRoot()) })
  afterEach(() => cleanup(root, container))

  it('renders no --filter-* token anywhere on the filters pipeline surface', () => {
    setupDefaultMocks(
      { ingested: 800, filtered: 200 },
      [
        makeRule({ id: 'rule-drop', action: 'drop', name: 'Drop rule' }),
        makeRule({ id: 'rule-tier', action: 'tier.priority', name: 'Tier rule' }),
      ],
      [makeArchivedRule({ id: 'arch-001' })],
    )

    renderComponent(container, root, <FiltersPipeline />)

    expect(container.innerHTML).not.toContain('--filter-red')
    expect(container.innerHTML).not.toContain('--filter-amber')
    expect(container.innerHTML).not.toContain('--filter-green')
  })

  it('the accept gate drop badge references the real --red token', () => {
    setupDefaultMocks({ ingested: 800, filtered: 200 })

    renderComponent(container, root, <FiltersPipeline />)

    expect(container.innerHTML).toContain('var(--red)')
  })

  it('the route gate preserved badge references the real --amber token', () => {
    setupDefaultMocks({ ingested: 1000, routed_by_butler: { general: 600 } })

    renderComponent(container, root, <FiltersPipeline />)

    expect(container.innerHTML).toContain('var(--amber)')
  })

  it('an enabled rule row dot references the real --green token', () => {
    setupDefaultMocks({}, [makeRule({ id: 'rule-001', enabled: true })])

    renderComponent(container, root, <FiltersPipeline />)

    const dot = container.querySelector('[aria-label="enabled"]')
    expect(dot?.className).toContain('var(--green)')
  })
})

// ============================================================================
// bu-4utdw.9: RuleRow toggle uses the shared shadcn Switch
// ============================================================================

describe('bu-4utdw.9: RuleRow uses the shared Switch component', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => { ;({ container, root } = makeRoot()) })
  afterEach(() => cleanup(root, container))

  it('renders a shadcn Switch (data-slot="switch") for the enable toggle', () => {
    setupDefaultMocks({}, [makeRule({ id: 'rule-001' })])

    renderComponent(container, root, <FiltersPipeline />)

    const toggle = container.querySelector('[data-testid="rule-toggle-rule-001"]')
    expect(toggle, 'toggle missing').not.toBeNull()
    expect(toggle?.getAttribute('data-slot')).toBe('switch')
    // No more hand-rolled inline oklch background style on the toggle itself.
    expect((toggle as HTMLElement).getAttribute('style')).toBeNull()
  })

  it('clicking the toggle calls handleToggleRule via useUpdateIngestionRule', () => {
    setupDefaultMocks({}, [makeRule({ id: 'rule-001', enabled: true })])
    mockUpdateMutate.mockClear()

    renderComponent(container, root, <FiltersPipeline />)

    const toggle = container.querySelector('[data-testid="rule-toggle-rule-001"]') as HTMLButtonElement
    act(() => { toggle.click() })

    expect(mockUpdateMutate).toHaveBeenCalledTimes(1)
    const [arg] = mockUpdateMutate.mock.calls[0] as [{ id: string; body: { enabled: boolean } }]
    expect(arg.id).toBe('rule-001')
    expect(arg.body.enabled).toBe(false)
  })
})

// ============================================================================
// bu-4utdw.9: RuleRow delete is a two-step inline confirm, not one click
// ============================================================================

describe('bu-4utdw.9: RuleRow delete requires inline confirmation', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
    mockDeleteMutate.mockReset()
  })
  afterEach(() => cleanup(root, container))

  it('does not delete on the first click — shows a confirm affordance instead', () => {
    setupDefaultMocks({}, [makeRule({ id: 'rule-001' })])

    renderComponent(container, root, <FiltersPipeline />)

    const deleteBtn = container.querySelector('[data-testid="rule-delete-rule-001"]') as HTMLButtonElement
    act(() => { deleteBtn.click() })

    expect(mockDeleteMutate).not.toHaveBeenCalled()
    expect(
      container.querySelector('[data-testid="rule-delete-confirm-rule-001"]'),
      'confirm affordance should appear after first click',
    ).not.toBeNull()
  })

  it('deletes only after the confirm click', () => {
    setupDefaultMocks({}, [makeRule({ id: 'rule-001' })])

    renderComponent(container, root, <FiltersPipeline />)

    act(() => {
      ;(container.querySelector('[data-testid="rule-delete-rule-001"]') as HTMLButtonElement).click()
    })
    act(() => {
      ;(container.querySelector('[data-testid="rule-delete-confirm-rule-001"]') as HTMLButtonElement).click()
    })

    expect(mockDeleteMutate).toHaveBeenCalledTimes(1)
    expect(mockDeleteMutate.mock.calls[0][0]).toBe('rule-001')
  })

  it('cancel reverts to the single delete affordance without deleting', () => {
    setupDefaultMocks({}, [makeRule({ id: 'rule-001' })])

    renderComponent(container, root, <FiltersPipeline />)

    act(() => {
      ;(container.querySelector('[data-testid="rule-delete-rule-001"]') as HTMLButtonElement).click()
    })
    act(() => {
      ;(container.querySelector('[data-testid="rule-delete-cancel-rule-001"]') as HTMLButtonElement).click()
    })

    expect(mockDeleteMutate).not.toHaveBeenCalled()
    expect(container.querySelector('[data-testid="rule-delete-rule-001"]')).not.toBeNull()
    expect(container.querySelector('[data-testid="rule-delete-confirm-rule-001"]')).toBeNull()
  })
})

// ============================================================================
// bu-4utdw.9: copy dedup — condition summary once, gate gloss in one place
// ============================================================================

describe('bu-4utdw.9: RuleRow renders the condition summary exactly once', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => { ;({ container, root } = makeRoot()) })
  afterEach(() => cleanup(root, container))

  it('shows the condition summary text a single time in the row', () => {
    setupDefaultMocks({}, [
      makeRule({ id: 'rule-001', condition: { source_channel: 'gmail' } }),
    ])

    renderComponent(container, root, <FiltersPipeline />)

    const row = container.querySelector('[data-testid="rule-row-rule-001"]') as HTMLElement
    const occurrences = row.textContent?.split('source_channel: gmail').length ?? 1
    // split() on N occurrences yields N+1 parts.
    expect(occurrences - 1).toBe(1)
  })
})

describe('bu-4utdw.9: gate gloss renders in exactly one place', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => { ;({ container, root } = makeRoot()) })
  afterEach(() => cleanup(root, container))

  it('the accept gate gloss text appears once on the page, not duplicated in the diagram', () => {
    setupDefaultMocks()

    renderComponent(container, root, <FiltersPipeline />)

    const glossFragment = 'First contact: channel authentication'
    const occurrences =
      (container.textContent?.split(glossFragment).length ?? 1) - 1
    expect(occurrences).toBe(1)
  })

  it('PipelineGateDiagram gate nodes do not render any gloss text', () => {
    setupDefaultMocks()

    renderComponent(container, root, <FiltersPipeline />)

    const diagram = container.querySelector('[data-testid="pipeline-gate-diagram"]') as HTMLElement
    expect(diagram.textContent).not.toContain('First contact: channel authentication')
  })
})

// ============================================================================
// bu-4utdw.9: archived-rule restore round-trips through the backend contract
// (PATCH {enabled: true} on a soft-deleted rule clears deleted_at server-side
// — see roster/switchboard/api/router.py update_ingestion_rule / bu-rnljv.3).
// The frontend must send exactly that shape; it must not attempt to clear
// deleted_at itself client-side.
// ============================================================================

describe('bu-4utdw.9: restore round-trips via PATCH {enabled: true}', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
    mockUpdateMutate.mockReset()
  })
  afterEach(() => cleanup(root, container))

  it('clicking restore on an archived rule sends {id, body: {enabled: true}} and nothing else', () => {
    setupDefaultMocks({}, [], [makeArchivedRule({ id: 'arch-001' })])

    renderComponent(container, root, <FiltersPipeline />)

    act(() => {
      ;(container.querySelector('[data-testid="archived-rules-toggle"]') as HTMLButtonElement).click()
    })
    act(() => {
      ;(container.querySelector('[data-testid="archived-rule-restore-arch-001"]') as HTMLButtonElement).click()
    })

    expect(mockUpdateMutate).toHaveBeenCalledTimes(1)
    const [arg] = mockUpdateMutate.mock.calls[0] as [{ id: string; body: Record<string, unknown> }]
    expect(arg.id).toBe('arch-001')
    // Exactly {enabled: true} — no client-side deleted_at manipulation. The
    // backend is solely responsible for clearing deleted_at on restore.
    expect(arg.body).toEqual({ enabled: true })
  })
})

// ============================================================================
// bu-4utdw.9: channel defaults inline editor wires GET/PATCH
// /api/ingestion/channel-defaults/:channel (public.channel_defaults)
// ============================================================================

describe('bu-4utdw.9: channel defaults inline editor', () => {
  let container: HTMLDivElement
  let root: Root

  function channelRule(overrides: Partial<IngestionRule> = {}) {
    return makeRule({
      id: 'ch-email',
      scope: 'email',
      rule_type: 'channel_default',
      action: 'pass_through',
      description: 'Default for email',
      ...overrides,
    })
  }

  beforeEach(() => {
    ;({ container, root } = makeRoot())
    mockUpdateChannelDefaultMutate.mockReset()
    mockUseChannelDefault.mockReset()
  })
  afterEach(() => cleanup(root, container))

  it('no longer shows the "not yet available" apology on edit', () => {
    setupDefaultMocks({}, [channelRule()])

    renderComponent(container, root, <FiltersPipeline />)

    act(() => {
      ;(container.querySelector('[data-testid="channel-default-edit-email"]') as HTMLButtonElement).click()
    })

    expect(container.textContent).not.toContain('not yet available')
    expect(
      container.querySelector('[data-testid="channel-default-editor-email"]'),
      'inline editor should render',
    ).not.toBeNull()
  })

  it('requests the current policy for the channel being edited via useChannelDefault', () => {
    setupDefaultMocks({}, [channelRule()])

    renderComponent(container, root, <FiltersPipeline />)

    act(() => {
      ;(container.querySelector('[data-testid="channel-default-edit-email"]') as HTMLButtonElement).click()
    })

    const calls = mockUseChannelDefault.mock.calls
    const enabledCall = calls.find((c) => c[1]?.enabled === true)
    expect(enabledCall?.[0]).toBe('email')
  })

  it('treats a 404 (no policy configured yet) as an empty form, not an error', () => {
    setupDefaultMocks({}, [channelRule()])
    mockUseChannelDefault.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError('NOT_FOUND', 'No channel defaults found', 404),
    })

    renderComponent(container, root, <FiltersPipeline />)

    act(() => {
      ;(container.querySelector('[data-testid="channel-default-edit-email"]') as HTMLButtonElement).click()
    })

    expect(container.querySelector('[data-testid="channel-default-editor-error-email"]')).toBeNull()
    expect(container.querySelector('[data-testid="channel-default-editor-policy-email"]')).not.toBeNull()
    expect(
      container.querySelector('[data-testid="channel-default-editor-notfound-email"]'),
      'should note that no policy is configured yet',
    ).not.toBeNull()
  })

  it('shows an error state on a genuine fetch failure (non-404)', () => {
    setupDefaultMocks({}, [channelRule()])
    mockUseChannelDefault.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError('SERVER_ERROR', 'boom', 500),
    })

    renderComponent(container, root, <FiltersPipeline />)

    act(() => {
      ;(container.querySelector('[data-testid="channel-default-edit-email"]') as HTMLButtonElement).click()
    })

    expect(container.querySelector('[data-testid="channel-default-editor-error-email"]')).not.toBeNull()
  })

  it('save calls useUpdateChannelDefault with the selected policy and max_age_days', async () => {
    setupDefaultMocks({}, [channelRule()])
    mockUseChannelDefault.mockReturnValue({
      data: {
        channel: 'email',
        default_policy_json: { priority_action: 'pass_through' },
        updated_at: '2026-01-01T00:00:00Z',
        updated_by: 'dashboard',
      },
      isLoading: false,
      isError: false,
      error: null,
    })

    renderComponent(container, root, <FiltersPipeline />)

    act(() => {
      ;(container.querySelector('[data-testid="channel-default-edit-email"]') as HTMLButtonElement).click()
    })

    const select = container.querySelector(
      '[data-testid="channel-default-editor-policy-email"]',
    ) as HTMLSelectElement
    act(() => { setInputValue(select, 'metadata_only') })

    const maxAge = container.querySelector(
      '[data-testid="channel-default-editor-max-age-email"]',
    ) as HTMLInputElement
    expect(maxAge, 'email channel should show max_age_days field').not.toBeNull()
    act(() => { setInputValue(maxAge, '45') })

    await act(async () => {
      ;(
        container.querySelector('[data-testid="channel-default-editor-save-email"]') as HTMLButtonElement
      ).click()
    })

    expect(mockUpdateChannelDefaultMutate).toHaveBeenCalledTimes(1)
    const [arg] = mockUpdateChannelDefaultMutate.mock.calls[0] as [
      { channel: string; body: { default_policy_json: Record<string, unknown>; updated_by: string } },
    ]
    expect(arg.channel).toBe('email')
    expect(arg.body.default_policy_json.priority_action).toBe('metadata_only')
    expect(arg.body.default_policy_json.max_age_days).toBe(45)
  })

  it('rejects a non-positive-integer max_age_days without saving', async () => {
    setupDefaultMocks({}, [channelRule()])
    mockUseChannelDefault.mockReturnValue({
      data: {
        channel: 'email',
        default_policy_json: { priority_action: 'pass_through' },
        updated_at: '2026-01-01T00:00:00Z',
        updated_by: 'dashboard',
      },
      isLoading: false,
      isError: false,
      error: null,
    })

    renderComponent(container, root, <FiltersPipeline />)

    act(() => {
      ;(container.querySelector('[data-testid="channel-default-edit-email"]') as HTMLButtonElement).click()
    })

    const maxAge = container.querySelector(
      '[data-testid="channel-default-editor-max-age-email"]',
    ) as HTMLInputElement
    act(() => { setInputValue(maxAge, '-5') })

    await act(async () => {
      ;(
        container.querySelector('[data-testid="channel-default-editor-save-email"]') as HTMLButtonElement
      ).click()
    })

    expect(mockUpdateChannelDefaultMutate).not.toHaveBeenCalled()
    const errorEl = container.querySelector('[data-testid="channel-default-editor-local-error-email"]')
    expect(errorEl, 'validation error should be rendered').not.toBeNull()
    expect(errorEl?.textContent).toMatch(/positive integer/i)
  })

  it('does not show a max_age_days field for non-email channels', () => {
    setupDefaultMocks({}, [
      channelRule({ id: 'ch-telegram', scope: 'telegram', action: 'skip' }),
    ])

    renderComponent(container, root, <FiltersPipeline />)

    act(() => {
      ;(
        container.querySelector('[data-testid="channel-default-edit-telegram"]') as HTMLButtonElement
      ).click()
    })

    expect(
      container.querySelector('[data-testid="channel-default-editor-max-age-telegram"]'),
    ).toBeNull()
  })

  it('cancel closes the editor without saving', () => {
    setupDefaultMocks({}, [channelRule()])

    renderComponent(container, root, <FiltersPipeline />)

    act(() => {
      ;(container.querySelector('[data-testid="channel-default-edit-email"]') as HTMLButtonElement).click()
    })
    act(() => {
      ;(
        container.querySelector('[data-testid="channel-default-editor-cancel-email"]') as HTMLButtonElement
      ).click()
    })

    expect(mockUpdateChannelDefaultMutate).not.toHaveBeenCalled()
    expect(container.querySelector('[data-testid="channel-default-editor-email"]')).toBeNull()
    expect(container.querySelector('[data-testid="channel-default-edit-email"]')).not.toBeNull()
  })

  it('reconciles the retired legacy verb labels to the runtime vocabulary', () => {
    setupDefaultMocks({}, [
      channelRule({ id: 'ch-legacy', scope: 'discord', action: 'preserve' }),
    ])

    renderComponent(container, root, <FiltersPipeline />)

    const policyLabel = container.querySelector('[data-testid="channel-default-policy-discord"]')
    expect(policyLabel?.textContent?.toLowerCase()).toContain('pass through')
    expect(policyLabel?.textContent?.toLowerCase()).not.toBe('preserve')
  })

  it('footer DSL gloss uses the runtime verdict vocabulary, not the retired DSL verbs', () => {
    setupDefaultMocks()

    renderComponent(container, root, <FiltersPipeline />)

    const footer = container.querySelector('[data-testid="filters-footer"]') as HTMLElement
    expect(footer.textContent).toContain('pass_through')
    expect(footer.textContent).toContain('skip')
    expect(footer.textContent).not.toMatch(/\bdrop\b/)
    expect(footer.textContent).not.toMatch(/\bpreserve\b/)
  })
})
