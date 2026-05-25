// @vitest-environment jsdom
/**
 * ConnectorsRoster — unit tests covering spec acceptance criteria:
 *
 * AC1: Connectors route is a roster, not a card grid
 * AC2: Auth issues appear consistently in attention strip, row, and detail
 *      (focus here: strip count matches auth-needed connectors; row shows same label)
 * Dormant section toggles open/closed (spec requirement)
 *
 * Uses mocked hooks to avoid QueryClient and network dependencies.
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
  useConnectorSummaries: vi.fn(),
  useAvailableConnectors: vi.fn(),
}))

import {
  useConnectorSummaries,
  useAvailableConnectors,
} from '@/hooks/use-ingestion'
import type { ConnectorSummary, ConnectorProfile } from '@/api/types'
import { ConnectorsRoster } from './ConnectorsRoster'

// ---------------------------------------------------------------------------
// Mock result helpers (match pattern used by ConnectorsListPage.test.tsx)
// ---------------------------------------------------------------------------

function makeResult<T>(data: T) {
  return { data, isLoading: false, isError: false }
}

// ---------------------------------------------------------------------------
// Test data
// ---------------------------------------------------------------------------

const HEALTHY_CONNECTOR: ConnectorSummary = {
  connector_type: 'gmail',
  endpoint_identity: 'user@example.com',
  liveness: 'online',
  state: 'healthy',
  error_message: null,
  version: '1.0',
  uptime_s: 3600,
  last_heartbeat_at: new Date(Date.now() - 60_000).toISOString(),
  first_seen_at: '2026-01-01T00:00:00Z',
  today: { messages_ingested: 42, messages_failed: 0, uptime_pct: 99.9 },
}

const REAUTH_CONNECTOR: ConnectorSummary = {
  connector_type: 'spotify',
  endpoint_identity: 'me',
  liveness: 'offline',
  state: 'error',
  error_message: '401 Unauthorized — token expired',
  version: null,
  uptime_s: null,
  last_heartbeat_at: new Date(Date.now() - 3_600_000).toISOString(),
  first_seen_at: '2026-01-01T00:00:00Z',
  today: { messages_ingested: 0, messages_failed: 8, uptime_pct: null },
}

const STALE_CONNECTOR: ConnectorSummary = {
  connector_type: 'telegram',
  endpoint_identity: 'bot_123',
  liveness: 'stale',
  state: 'healthy',
  error_message: null,
  version: '2.0',
  uptime_s: 7200,
  last_heartbeat_at: new Date(Date.now() - 900_000).toISOString(), // 15 min ago
  first_seen_at: '2026-01-01T00:00:00Z',
  today: { messages_ingested: 5, messages_failed: 0, uptime_pct: 85 },
}

const DORMANT_PROFILE: ConnectorProfile = {
  connector_type: 'home_assistant',
  channel: 'long-poll',
  provider: 'homeassistant',
  display_name: 'Home Assistant',
  supports_backfill: false,
}

// ---------------------------------------------------------------------------
// Helpers
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

function mockHooks(
  connectors: ConnectorSummary[],
  profiles: ConnectorProfile[] = [],
) {
  vi.mocked(useConnectorSummaries).mockReturnValue(
    makeResult({ data: connectors }) as ReturnType<typeof useConnectorSummaries>,
  )

  vi.mocked(useAvailableConnectors).mockReturnValue(
    makeResult({ data: profiles }) as ReturnType<typeof useAvailableConnectors>,
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

// ---------------------------------------------------------------------------
// §AC1: Dense roster, not a card grid
// ---------------------------------------------------------------------------

describe('AC1: dense roster layout', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
  })
  afterEach(() => cleanup(root, container))

  it('renders a roster container (not a grid of cards)', () => {
    mockHooks([HEALTHY_CONNECTOR])
    renderRoster(container, root)

    const roster = container.querySelector('[data-testid="connectors-roster"]')
    expect(roster).not.toBeNull()

    // Must NOT render shadcn Card elements (card grid rejected by spec)
    const cards = container.querySelectorAll('[data-slot="card"]')
    expect(cards.length).toBe(0)
  })

  it('renders roster rows for each connector', () => {
    mockHooks([HEALTHY_CONNECTOR, REAUTH_CONNECTOR])
    renderRoster(container, root)

    const rows = container.querySelectorAll('[data-testid^="connector-row-"]')
    expect(rows.length).toBe(2)
  })

  it('renders the auth-needed connector row at the top (sorted first)', () => {
    // Both connectors present; reauth should sort before healthy
    mockHooks([HEALTHY_CONNECTOR, REAUTH_CONNECTOR])
    renderRoster(container, root)

    const rosterRows = container.querySelector('[data-testid="roster-rows"]')
    const rows = rosterRows?.querySelectorAll('[data-testid^="connector-row-"]')
    expect(rows).not.toBeNull()
    expect(rows!.length).toBeGreaterThan(0)
    // First row should be the reauth connector (sorted by attention score)
    expect(rows![0]?.getAttribute('data-testid')).toBe('connector-row-spotify')
  })

  it('renders empty state serif italic when no connectors', () => {
    mockHooks([])
    renderRoster(container, root)

    const empty = container.querySelector('p.font-serif')
    expect(empty?.textContent).toMatch(/no connectors/i)
  })
})

// ---------------------------------------------------------------------------
// §AC2: Auth issues consistent in attention strip and row
// ---------------------------------------------------------------------------

describe('AC2: auth issues appear consistently in attention strip and row', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
  })
  afterEach(() => cleanup(root, container))

  it('renders the attention strip when auth-error connector is present', () => {
    mockHooks([HEALTHY_CONNECTOR, REAUTH_CONNECTOR])
    renderRoster(container, root)

    const strip = container.querySelector('[data-testid="attention-strip"]')
    expect(strip).not.toBeNull()
  })

  it('attention strip count badge equals number of attention-needed connectors', () => {
    // REAUTH + STALE both need attention; HEALTHY does not
    mockHooks([HEALTHY_CONNECTOR, REAUTH_CONNECTOR, STALE_CONNECTOR])
    renderRoster(container, root)

    const badge = container.querySelector('[data-testid="attention-count"]')
    expect(badge?.textContent?.trim()).toBe('2')
  })

  it('attention strip does NOT render when all connectors are healthy', () => {
    mockHooks([HEALTHY_CONNECTOR])
    renderRoster(container, root)

    const strip = container.querySelector('[data-testid="attention-strip"]')
    expect(strip).toBeNull()
  })

  it('attention strip has one item per attention-needed connector', () => {
    mockHooks([HEALTHY_CONNECTOR, REAUTH_CONNECTOR, STALE_CONNECTOR])
    renderRoster(container, root)

    const items = container.querySelectorAll('[data-testid^="attention-item-"]')
    expect(items.length).toBe(2)
  })

  it('auth status label on the roster row matches attention strip label', () => {
    mockHooks([REAUTH_CONNECTOR])
    renderRoster(container, root)

    // Auth status on row
    const rowAuthLabel = container.querySelector('[data-testid="auth-status-spotify"]')
    const rowText = rowAuthLabel?.textContent?.trim().toLowerCase()

    // Auth label in strip
    const stripItem = container.querySelector('[data-testid="attention-item-spotify"]')
    const stripText = stripItem?.textContent?.toLowerCase()

    // Both should contain 'reauth' (the consistent label for needs_reauth status)
    expect(rowText).toContain('reauth')
    expect(stripText).toContain('reauth')
  })
})

// ---------------------------------------------------------------------------
// Dormant section toggles
// ---------------------------------------------------------------------------

describe('Dormant section toggle', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
  })
  afterEach(() => cleanup(root, container))

  it('dormant section is collapsed by default', () => {
    mockHooks([HEALTHY_CONNECTOR], [DORMANT_PROFILE])
    renderRoster(container, root)

    const dormantSection = container.querySelector('[data-testid="dormant-section"]')
    expect(dormantSection).not.toBeNull()

    // Collapsed: list is not rendered
    const dormantList = container.querySelector('[data-testid="dormant-list"]')
    expect(dormantList).toBeNull()
  })

  it('clicking the toggle expands the dormant section', () => {
    mockHooks([HEALTHY_CONNECTOR], [DORMANT_PROFILE])
    renderRoster(container, root)

    const toggle = container.querySelector('[data-testid="dormant-toggle"]')
    expect(toggle).not.toBeNull()

    act(() => {
      ;(toggle as HTMLButtonElement).click()
    })

    const dormantList = container.querySelector('[data-testid="dormant-list"]')
    expect(dormantList).not.toBeNull()
  })

  it('clicking the toggle again collapses the dormant section', () => {
    mockHooks([HEALTHY_CONNECTOR], [DORMANT_PROFILE])
    renderRoster(container, root)

    const toggle = container.querySelector('[data-testid="dormant-toggle"]')

    // Expand
    act(() => {
      ;(toggle as HTMLButtonElement).click()
    })
    expect(container.querySelector('[data-testid="dormant-list"]')).not.toBeNull()

    // Collapse
    act(() => {
      ;(toggle as HTMLButtonElement).click()
    })
    expect(container.querySelector('[data-testid="dormant-list"]')).toBeNull()
  })

  it('does not render dormant section when no dormant profiles', () => {
    mockHooks([HEALTHY_CONNECTOR], [])
    renderRoster(container, root)

    const dormantSection = container.querySelector('[data-testid="dormant-section"]')
    expect(dormantSection).toBeNull()
  })
})
