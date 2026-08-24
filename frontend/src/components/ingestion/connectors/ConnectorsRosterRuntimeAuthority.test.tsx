// @vitest-environment jsdom
/**
 * ConnectorsRoster — runtime-instance authority (bu-6jv4m.11).
 *
 * connector_registry holds two kinds of record: executable connector processes
 * and persisted checkpoint cursors. Google Health keeps one cursor per account
 * AND per resource, and those cursor rows never heartbeat — so the roster used
 * to present activity/sleep/HRV as separate OFFLINE listening connectors beside
 * the single genuinely-online account, each one pulling on fleet attention.
 *
 * Covers:
 *   - the parent-plus-subidentity shape: one roster row, cursors nested under it
 *   - multi-account isolation: two accounts never show each other's cursors
 *   - unclassified records get a named state, never active/healthy/offline
 *   - fleet KPIs count executable runtime instances only
 *   - an orphaned cursor stays visible instead of silently disappearing
 *   - source failure still degrades explicitly
 *
 * Uses mocked hooks to avoid QueryClient and network dependencies (same harness
 * as ConnectorsRoster.test.tsx).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router'

;(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true

// ---------------------------------------------------------------------------
// Mocks — must be declared before component imports
// ---------------------------------------------------------------------------

vi.mock('@/hooks/use-ingestion', () => ({
  useConnectorSummariesWithAggregates: vi.fn(),
  useAvailableConnectors: vi.fn(),
  useArchiveConnector: vi.fn(() => ({
    mutate: vi.fn(),
    isPending: false,
    isError: false,
    variables: undefined,
  })),
  useUnarchiveConnector: vi.fn(() => ({
    mutate: vi.fn(),
    isPending: false,
    isError: false,
    variables: undefined,
  })),
}))

import {
  useConnectorSummariesWithAggregates,
  useAvailableConnectors,
} from '@/hooks/use-ingestion'
import type { ConnectorCheckpointRecord, ConnectorSummary } from '@/api/types'
import { ConnectorsRoster } from './ConnectorsRoster'

// ---------------------------------------------------------------------------
// Test data — obviously synthetic, built here
// ---------------------------------------------------------------------------

const OWNER = 'owner@example.test'
const SECOND_OWNER = 'second@example.test'
const ACCOUNT = '00000000-0000-4000-8000-000000000001'
const SECOND_ACCOUNT = '00000000-0000-4000-8000-000000000002'

function checkpoint(
  parent: string,
  account: string,
  resource: string,
): ConnectorCheckpointRecord {
  return {
    connector_type: 'google_health',
    endpoint_identity: `${parent}:${account}:${resource}`,
    parent_endpoint_identity: parent,
    label: `${account}:${resource}`,
    checkpoint_cursor: `${resource}-cursor`,
    checkpoint_updated_at: new Date(Date.now() - 120_000).toISOString(),
    archived: false,
  }
}

function googleHealthAccount(owner: string, account: string): ConnectorSummary {
  const parent = `google_health:user:${owner}`
  return {
    connector_type: 'google_health',
    endpoint_identity: parent,
    liveness: 'online',
    state: 'healthy',
    error_message: null,
    version: '1.0',
    uptime_s: 3600,
    last_heartbeat_at: new Date(Date.now() - 60_000).toISOString(),
    first_seen_at: '2026-01-01T00:00:00Z',
    today: { messages_ingested: 12, messages_failed: 0, uptime_pct: 99.9 },
    hourly_events: Array(24).fill(0),
    operational_role: 'runtime_instance',
    checkpoints: [
      checkpoint(parent, account, 'activity'),
      checkpoint(parent, account, 'hrv'),
      checkpoint(parent, account, 'sleep_sessions'),
    ],
  }
}

const HEALTH_ACCOUNT = googleHealthAccount(OWNER, ACCOUNT)
const SECOND_HEALTH_ACCOUNT = googleHealthAccount(SECOND_OWNER, SECOND_ACCOUNT)

/** A registry record no producer has claimed — role never established. */
const UNCLASSIFIED_CONNECTOR: ConnectorSummary = {
  connector_type: 'steam',
  endpoint_identity: 'steam:synthetic-unconfigured',
  liveness: 'unclassified',
  state: 'unknown',
  error_message: null,
  version: null,
  uptime_s: null,
  last_heartbeat_at: null,
  first_seen_at: '2026-01-01T00:00:00Z',
  today: null,
  hourly_events: Array(24).fill(0),
  operational_role: 'unknown',
}

const ORPHAN_CHECKPOINT: ConnectorCheckpointRecord = {
  connector_type: 'google_health',
  endpoint_identity: `google_health:user:${OWNER}:${ACCOUNT}:spo2`,
  parent_endpoint_identity: null,
  label: `google_health:user:${OWNER}:${ACCOUNT}:spo2`,
  checkpoint_cursor: 'spo2-cursor',
  checkpoint_updated_at: null,
  archived: false,
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeResult<T>(data: T) {
  return { data, isLoading: false, isError: false }
}

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

function mockHooks(
  connectors: ConnectorSummary[],
  responseOverrides: {
    unparented_checkpoints?: ConnectorCheckpointRecord[]
    unclassified_count?: number
  } = {},
) {
  vi.mocked(useConnectorSummariesWithAggregates).mockReturnValue(
    makeResult({
      data: { connectors, ...responseOverrides },
    }) as ReturnType<typeof useConnectorSummariesWithAggregates>,
  )
  vi.mocked(useAvailableConnectors).mockReturnValue(
    makeResult({ data: [] }) as unknown as ReturnType<typeof useAvailableConnectors>,
  )
}

function renderRoster(container: HTMLDivElement, root: Root) {
  act(() => {
    root.render(
      <MemoryRouter>
        <ConnectorsRoster />
      </MemoryRouter>,
    )
  })
  return container
}

/** Read one KPI tile's value by its label. */
function kpiValue(container: HTMLDivElement, label: string): string | null {
  const footer = container.querySelector('[data-testid="connectors-kpi-footer"]')
  if (!footer) return null
  for (const tile of Array.from(footer.children)) {
    if (tile.firstElementChild?.textContent?.trim() === label) {
      return tile.lastElementChild?.textContent?.trim() ?? null
    }
  }
  return null
}

// ---------------------------------------------------------------------------
// Parent-plus-subidentity shape
// ---------------------------------------------------------------------------

describe('Google Health parent plus sub-identities', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
  })
  afterEach(() => cleanup(root, container))

  it('lists the account once, not once per checkpoint cursor', () => {
    mockHooks([HEALTH_ACCOUNT])
    renderRoster(container, root)

    const rows = container.querySelectorAll('[data-testid^="connector-row-"]')
    expect(rows.length).toBe(1)
    expect(rows[0].getAttribute('data-testid')).toBe('connector-row-google_health')
  })

  it('nests every cursor under its parent with a stream label', () => {
    mockHooks([HEALTH_ACCOUNT])
    renderRoster(container, root)

    const list = container.querySelector(
      '[data-testid="connector-checkpoints-google_health"]',
    )
    expect(list).not.toBeNull()
    const entries = list!.querySelectorAll('[data-testid^="connector-checkpoint-"]')
    expect(entries.length).toBe(3)
    expect(Array.from(entries).map((e) => e.textContent)).toEqual([
      expect.stringContaining(`${ACCOUNT}:activity`),
      expect.stringContaining(`${ACCOUNT}:hrv`),
      expect.stringContaining(`${ACCOUNT}:sleep_sessions`),
    ])
  })

  it('gives a checkpoint no health verdict of its own', () => {
    mockHooks([HEALTH_ACCOUNT])
    renderRoster(container, root)

    // Exactly one verdict on the row — the parent's. A cursor has no process,
    // so it must not acquire (or dilute) a liveness reading.
    const verdicts = container.querySelectorAll('[data-testid^="health-verdict-"]')
    expect(verdicts.length).toBe(1)
    expect(verdicts[0].textContent).toBe('online')
    const checkpointList = container.querySelector(
      '[data-testid="connector-checkpoints-google_health"]',
    )
    expect(
      checkpointList!.querySelectorAll('[data-testid^="health-verdict-"]').length,
    ).toBe(0)
  })

  it('keeps each account with its own cursors', () => {
    mockHooks([HEALTH_ACCOUNT, SECOND_HEALTH_ACCOUNT])
    renderRoster(container, root)

    const lists = container.querySelectorAll(
      '[data-testid="connector-checkpoints-google_health"]',
    )
    expect(lists.length).toBe(2)
    const texts = Array.from(lists).map((l) => l.textContent ?? '')
    const first = texts.find((t) => t.includes(ACCOUNT))!
    const second = texts.find((t) => t.includes(SECOND_ACCOUNT))!
    expect(first).not.toBe(second)
    expect(first).not.toContain(SECOND_ACCOUNT)
    expect(second).not.toContain(ACCOUNT)
  })

  it('counts the account, not its cursors, as one live connector', () => {
    mockHooks([HEALTH_ACCOUNT])
    renderRoster(container, root)

    expect(kpiValue(container, 'connectors · live')).toBe('1')
    expect(kpiValue(container, 'healthy')).toBe('1')
    expect(kpiValue(container, 'needs attention')).toBe('0')
  })

  it('does not raise the attention strip for a checkpoint cursor', () => {
    mockHooks([HEALTH_ACCOUNT])
    renderRoster(container, root)

    expect(container.querySelector('[data-testid="attention-strip"]')).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Unclassified records
// ---------------------------------------------------------------------------

describe('unclassified registry records', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
  })
  afterEach(() => cleanup(root, container))

  it('names the missing classification instead of guessing a verdict', () => {
    mockHooks([UNCLASSIFIED_CONNECTOR], { unclassified_count: 1 })
    renderRoster(container, root)

    const verdict = container.querySelector('[data-testid="health-verdict-steam"]')
    expect(verdict).not.toBeNull()
    expect(verdict!.textContent).toBe('unclassified')
    expect(verdict!.textContent).not.toBe('online')
    expect(verdict!.textContent).not.toBe('offline')
  })

  it('keeps an unclassified record out of the fleet-liveness KPIs', () => {
    mockHooks([HEALTH_ACCOUNT, UNCLASSIFIED_CONNECTOR], { unclassified_count: 1 })
    renderRoster(container, root)

    // One executable runtime instance; the unclassified record is not a second.
    expect(kpiValue(container, 'connectors · live')).toBe('1')
    expect(kpiValue(container, 'healthy')).toBe('1')
    // It is still an operator item, so the attention queue does count it.
    expect(kpiValue(container, 'needs attention')).toBe('1')
  })

  it('says out loud that the registry holds unclassified records', () => {
    mockHooks([UNCLASSIFIED_CONNECTOR], { unclassified_count: 1 })
    renderRoster(container, root)

    const note = container.querySelector('[data-testid="connectors-unclassified-note"]')
    expect(note).not.toBeNull()
    expect(note!.textContent).toContain('unclassified')
  })

  it('shows no unclassified note when every record is classified', () => {
    mockHooks([HEALTH_ACCOUNT], { unclassified_count: 0 })
    renderRoster(container, root)

    expect(container.querySelector('[data-testid="connectors-unclassified-note"]')).toBeNull()
  })

  it('still surfaces the record itself, rather than dropping it', () => {
    mockHooks([UNCLASSIFIED_CONNECTOR], { unclassified_count: 1 })
    renderRoster(container, root)

    expect(container.querySelector('[data-testid="connector-row-steam"]')).not.toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Orphaned checkpoints
// ---------------------------------------------------------------------------

describe('checkpoints with no resolvable owner', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
  })
  afterEach(() => cleanup(root, container))

  it('surfaces an orphaned cursor without making it a connector', () => {
    mockHooks([HEALTH_ACCOUNT], { unparented_checkpoints: [ORPHAN_CHECKPOINT] })
    renderRoster(container, root)

    expect(
      container.querySelector('[data-testid="unparented-checkpoints-section"]'),
    ).not.toBeNull()
    expect(container.querySelectorAll('[data-testid^="connector-row-"]').length).toBe(1)
    expect(kpiValue(container, 'connectors · live')).toBe('1')
  })

  it('reveals the orphaned cursor identity when expanded', () => {
    mockHooks([HEALTH_ACCOUNT], { unparented_checkpoints: [ORPHAN_CHECKPOINT] })
    renderRoster(container, root)

    expect(container.querySelector('[data-testid="unparented-checkpoints-list"]')).toBeNull()
    act(() => {
      ;(
        container.querySelector(
          '[data-testid="unparented-checkpoints-toggle"]',
        ) as HTMLButtonElement
      ).click()
    })

    const list = container.querySelector('[data-testid="unparented-checkpoints-list"]')
    expect(list).not.toBeNull()
    expect(list!.textContent).toContain(ORPHAN_CHECKPOINT.endpoint_identity)
  })

  it('renders no section when nothing is orphaned', () => {
    mockHooks([HEALTH_ACCOUNT], { unparented_checkpoints: [] })
    renderRoster(container, root)

    expect(container.querySelector('[data-testid="unparented-checkpoints-section"]')).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Compatibility and source failure
// ---------------------------------------------------------------------------

describe('older responses and source failure', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
  })
  afterEach(() => cleanup(root, container))

  it('counts connectors from a response carrying none of the new fields', () => {
    // An older cached response has no operational_role, checkpoints, or counts.
    // Absent must not be read as unclassified — that would empty the KPI band.
    const legacy: ConnectorSummary = {
      connector_type: 'gmail',
      endpoint_identity: 'gmail:synthetic@example.test',
      liveness: 'online',
      state: 'healthy',
      error_message: null,
      version: '1.0',
      uptime_s: 3600,
      last_heartbeat_at: new Date(Date.now() - 60_000).toISOString(),
      first_seen_at: '2026-01-01T00:00:00Z',
      today: { messages_ingested: 7, messages_failed: 0, uptime_pct: 99.9 },
      hourly_events: Array(24).fill(0),
    }
    mockHooks([legacy])
    renderRoster(container, root)

    expect(kpiValue(container, 'connectors · live')).toBe('1')
    expect(container.querySelector('[data-testid="connectors-unclassified-note"]')).toBeNull()
    expect(container.querySelector('[data-testid="unparented-checkpoints-section"]')).toBeNull()
  })

  it('degrades explicitly when the roster source failed', () => {
    vi.mocked(useConnectorSummariesWithAggregates).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useConnectorSummariesWithAggregates>)
    vi.mocked(useAvailableConnectors).mockReturnValue(
      makeResult({ data: [] }) as unknown as ReturnType<typeof useAvailableConnectors>,
    )
    renderRoster(container, root)

    expect(container.querySelector('[data-testid="connectors-roster-unavailable"]')).not.toBeNull()
    // No fabricated roster, no fabricated checkpoint sections.
    expect(container.querySelector('[data-testid="connectors-kpi-footer"]')).toBeNull()
    expect(container.querySelector('[data-testid="unparented-checkpoints-section"]')).toBeNull()
    expect(container.querySelector('[data-testid="connectors-unclassified-note"]')).toBeNull()
  })
})
