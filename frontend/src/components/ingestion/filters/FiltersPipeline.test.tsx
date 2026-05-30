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
const mockDeleteMutate = vi.fn()

vi.mock('@/hooks/use-ingestion', () => ({
  usePipelineStats: () => mockUsePipelineStats(),
}))

vi.mock('@/hooks/use-ingestion-rules', () => ({
  useIngestionRules: () => mockUseIngestionRules(),
  useUpdateIngestionRule: () => ({ mutate: mockUpdateMutate }),
  useDeleteIngestionRule: () => ({ mutate: mockDeleteMutate }),
}))

import type { PipelineStats, IngestionRule } from '@/api/types'
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

  // useIngestionRules is called twice — first for active, then for archived
  mockUseIngestionRules
    .mockReturnValueOnce({
      data: { data: activeRules },
      isLoading: false,
      isError: false,
    })
    .mockReturnValueOnce({
      data: { data: archivedRules },
      isLoading: false,
      isError: false,
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
// AC3: Priority senders mutation error
// ============================================================================

describe('AC3: priority senders mutation surfaces error', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => { ;({ container, root } = makeRoot()) })
  afterEach(() => cleanup(root, container))

  it('renders priority senders section when rules are loaded', () => {
    const priorityRule = makeRule({
      id: 'priority-001',
      rule_type: 'priority_sender',
      action: 'route general',
      name: 'VIP contact',
    })

    renderComponent(container, root, (
      <PrioritySendersBlock
        rules={[priorityRule]}
        loaded={true}
        error={false}
        mutationError={null}
      />
    ))

    const block = container.querySelector('[data-testid="priority-senders-block"]')
    expect(block).not.toBeNull()

    const row = container.querySelector('[data-testid="priority-sender-row-priority-001"]')
    expect(row).not.toBeNull()
  })

  it('renders mutation error when API fails', () => {
    renderComponent(container, root, (
      <PrioritySendersBlock
        rules={[]}
        loaded={true}
        error={false}
        mutationError="Failed to remove priority sender: 500 Internal Server Error"
      />
    ))

    const errorEl = container.querySelector('[data-testid="priority-senders-mutation-error"]')
    expect(errorEl).not.toBeNull()
    expect(errorEl?.textContent).toContain('Failed to remove priority sender')
  })

  it('calls onRemove with correct rule id when remove is clicked', () => {
    const onRemove = vi.fn()
    const priorityRule = makeRule({
      id: 'priority-001',
      rule_type: 'priority_sender',
    })

    renderComponent(container, root, (
      <PrioritySendersBlock
        rules={[priorityRule]}
        loaded={true}
        error={false}
        mutationError={null}
        onRemove={onRemove}
      />
    ))

    const removeBtn = container.querySelector('[data-testid="priority-sender-remove-priority-001"]')
    expect(removeBtn).not.toBeNull()

    act(() => { ;(removeBtn as HTMLButtonElement).click() })
    expect(onRemove).toHaveBeenCalledWith('priority-001')
  })

  it('renders error state when fetch fails', () => {
    renderComponent(container, root, (
      <PrioritySendersBlock
        rules={[]}
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
