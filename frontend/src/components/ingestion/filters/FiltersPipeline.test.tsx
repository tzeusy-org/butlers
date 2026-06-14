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

vi.mock('@/hooks/use-ingestion', () => ({
  usePipelineStats: () => mockUsePipelineStats(),
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
    butler: 'gmail',
    added_at: '2026-01-01T00:00:00Z',
    added_by: 'dashboard',
    name: 'VIP Contact',
    contact_info_values: ['vip@example.com'],
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

  it('shows metrics unavailable label when aggregates_available is false', () => {
    const counts = deriveGateCounts(makeStats({ aggregates_available: false }))

    renderComponent(container, root, (
      <PipelineGateDiagram counts={counts} available={false} />
    ))

    const unavailableNote = container.querySelector('[data-testid="funnel-bar-unavailable"]')
    expect(unavailableNote).not.toBeNull()
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
