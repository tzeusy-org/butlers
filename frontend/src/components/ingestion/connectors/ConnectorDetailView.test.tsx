// @vitest-environment jsdom
/**
 * ConnectorDetailView — unit tests covering spec acceptance criteria:
 *
 * AC2: Auth issues appear consistently — ReauthCallout uses same status/color
 *      as the roster row (both derived from deriveConnectorDispatchInfo)
 * AC3: Scope list shows unavailable state when connector-oauth-scope-surface
 *      data is missing/null
 * AC4: Reauth callout appears when auth status is broken/expired
 *
 * Also covers: header band renders connector name, KPI strip renders,
 * scope list with data shows correct verdicts.
 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router'

;(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true

import type {
  ConnectorDetail,
  ConnectorEventsResponse,
  ConnectorIncidentsResponse,
  ConnectorRoutingRulesResponse,
} from '@/api/types'
import { ConnectorDetailView } from './ConnectorDetailView'
import type { OAuthScope } from './ScopeList'

// ---------------------------------------------------------------------------
// Test data
// ---------------------------------------------------------------------------

const BASE_CONNECTOR: ConnectorDetail = {
  connector_type: 'spotify',
  endpoint_identity: 'me',
  liveness: 'online',
  state: 'healthy',
  error_message: null,
  version: '1.2.3',
  uptime_s: 3600,
  last_heartbeat_at: new Date(Date.now() - 60_000).toISOString(),
  first_seen_at: '2026-01-01T00:00:00Z',
  today: { messages_ingested: 24, messages_failed: 0, uptime_pct: 99 },
  hourly_events: Array(24).fill(0),
  instance_id: 'inst-abc',
  registered_via: 'auto',
  checkpoint: { cursor: 'tok-xyz', updated_at: '2026-05-25T10:00:00Z' },
  counters: {
    messages_ingested: 1000,
    messages_failed: 2,
    source_api_calls: 500,
    checkpoint_saves: 100,
    dedupe_accepted: 50,
  },
  settings: null,
}

const REAUTH_CONNECTOR: ConnectorDetail = {
  ...BASE_CONNECTOR,
  liveness: 'offline',
  state: 'error',
  error_message: '401 Unauthorized — oauth token expired',
  today: { messages_ingested: 0, messages_failed: 8, uptime_pct: null },
}

const MOCK_SCOPES: OAuthScope[] = [
  { name: 'user-read-recently-played', granted: false, verdict: 'mismatch' },
  { name: 'user-library-read', granted: true, verdict: 'granted' },
  { name: 'user-top-read', granted: true, verdict: 'granted' },
]

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

function renderDetail(
  root: Root,
  connector: ConnectorDetail,
  opts: {
    scopes?: OAuthScope[] | null
    onReauth?: () => void
    recentEvents?: ConnectorEventsResponse | null
    incidents?: ConnectorIncidentsResponse | null
    routingRules?: ConnectorRoutingRulesResponse | null
  } = {},
) {
  act(() => {
    root.render(
      <MemoryRouter>
        <ConnectorDetailView
          connector={connector}
          stats={undefined}
          oauthScopes={opts.scopes}
          onReauth={opts.onReauth}
          recentEvents={opts.recentEvents}
          incidents={opts.incidents}
          routingRules={opts.routingRules}
        />
      </MemoryRouter>,
    )
  })
}

// ---------------------------------------------------------------------------
// Test data for new sections [bu-5ywn2]
// ---------------------------------------------------------------------------

const MOCK_EVENTS: ConnectorEventsResponse = {
  events: [
    {
      id: 'evt-001',
      received_at: new Date(Date.now() - 120_000).toISOString(),
      source_channel: 'spotify',
      source_sender_identity: null,
      status: 'ingested',
      filter_reason: null,
      error_detail: null,
    },
    {
      id: 'evt-002',
      received_at: new Date(Date.now() - 300_000).toISOString(),
      source_channel: 'spotify',
      source_sender_identity: null,
      status: 'failed',
      filter_reason: null,
      error_detail: 'Connection timeout',
    },
  ],
  connector_type: 'spotify',
  endpoint_identity: 'me',
  total_returned: 2,
}

const MOCK_INCIDENTS: ConnectorIncidentsResponse = {
  incidents: [
    {
      id: 'inc-001',
      received_at: new Date(Date.now() - 600_000).toISOString(),
      source_channel: 'spotify',
      status: 'failed',
      error_detail: 'Rate limit exceeded',
      filter_reason: null,
    },
  ],
  connector_type: 'spotify',
  endpoint_identity: 'me',
  total_returned: 1,
}

const MOCK_RULES: ConnectorRoutingRulesResponse = {
  rules: [
    {
      id: 'rule-001',
      scope: 'connector:spotify:me',
      rule_type: 'substring',
      condition: { pattern: 'spam' },
      action: 'block',
      priority: 1,
      enabled: true,
      name: 'Block spam',
      description: null,
      created_by: 'dashboard',
      created_at: '2025-01-01T00:00:00Z',
      updated_at: '2025-01-01T00:00:00Z',
    },
  ],
  connector_type: 'spotify',
  endpoint_identity: 'me',
  total_returned: 1,
  filter_note: null,
}

// ---------------------------------------------------------------------------
// Header band
// ---------------------------------------------------------------------------

describe('Header band', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
  })
  afterEach(() => cleanup(root, container))

  it('renders connector type as the display headline', () => {
    renderDetail(root, BASE_CONNECTOR)
    const h1 = container.querySelector('h1')
    expect(h1?.textContent?.toLowerCase()).toContain('spotify')
  })

  it('renders the endpoint identity in the meta line', () => {
    renderDetail(root, BASE_CONNECTOR)
    // endpoint_identity 'me' should appear in the mono meta line
    expect(container.textContent).toContain('me')
  })

  it('renders liveness status in the meta line', () => {
    renderDetail(root, BASE_CONNECTOR)
    expect(container.textContent).toContain('online')
  })
})

// ---------------------------------------------------------------------------
// KPI strip
// ---------------------------------------------------------------------------

describe('KPI strip', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
  })
  afterEach(() => cleanup(root, container))

  it('renders the KPI strip with today events count', () => {
    renderDetail(root, BASE_CONNECTOR)
    const strip = container.querySelector('[data-testid="kpi-strip"]')
    expect(strip).not.toBeNull()
    // today.messages_ingested = 24
    expect(strip?.textContent).toContain('24')
  })
})

// ---------------------------------------------------------------------------
// AC4: Reauth callout
// ---------------------------------------------------------------------------

describe('AC4: ReauthCallout appears when auth is broken/expired', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
  })
  afterEach(() => cleanup(root, container))

  it('renders ReauthCallout when auth status is needs_reauth', () => {
    renderDetail(root, REAUTH_CONNECTOR, { onReauth: () => {} })

    const callout = container.querySelector('[data-testid="reauth-callout"]')
    expect(callout).not.toBeNull()
  })

  it('does NOT render ReauthCallout when auth is ok', () => {
    renderDetail(root, BASE_CONNECTOR)

    const callout = container.querySelector('[data-testid="reauth-callout"]')
    expect(callout).toBeNull()
  })

  it('reauth callout contains reauth button when onReauth is provided', () => {
    renderDetail(root, REAUTH_CONNECTOR, { onReauth: () => {} })

    const button = container.querySelector('[data-testid="reauth-button"]')
    expect(button).not.toBeNull()
  })

  it('reauth callout text is consistent with needs_reauth status', () => {
    renderDetail(root, REAUTH_CONNECTOR)

    const callout = container.querySelector('[data-testid="reauth-callout"]')
    // Should show "reauth required" label — same label used by roster row and attention strip
    expect(callout?.textContent?.toLowerCase()).toContain('reauth required')
  })
})

// ---------------------------------------------------------------------------
// AC3: Scope list — unavailable state
// ---------------------------------------------------------------------------

describe('AC3: Scope list renders unavailable state when data is missing', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
  })
  afterEach(() => cleanup(root, container))

  it('renders unavailable state when scopes is null', () => {
    renderDetail(root, BASE_CONNECTOR, { scopes: null })

    const unavailable = container.querySelector('[data-testid="scopes-unavailable"]')
    expect(unavailable).not.toBeNull()
  })

  it('renders unavailable state when scopes is undefined', () => {
    renderDetail(root, BASE_CONNECTOR, { scopes: undefined })

    const unavailable = container.querySelector('[data-testid="scopes-unavailable"]')
    expect(unavailable).not.toBeNull()
  })

  it('renders unavailable state when scopes array is empty', () => {
    renderDetail(root, BASE_CONNECTOR, { scopes: [] })

    const unavailable = container.querySelector('[data-testid="scopes-unavailable"]')
    expect(unavailable).not.toBeNull()
  })

  it('does NOT render unavailable state when scope data is present', () => {
    renderDetail(root, BASE_CONNECTOR, { scopes: MOCK_SCOPES })

    const unavailable = container.querySelector('[data-testid="scopes-unavailable"]')
    expect(unavailable).toBeNull()
  })

  it('renders scope rows when scope data is present', () => {
    renderDetail(root, BASE_CONNECTOR, { scopes: MOCK_SCOPES })

    const scopesList = container.querySelector('[data-testid="scopes-list"]')
    expect(scopesList).not.toBeNull()

    const rows = container.querySelectorAll('[data-testid^="scope-row-"]')
    expect(rows.length).toBe(MOCK_SCOPES.length)
  })

  it('mismatch scope row has correct scope name', () => {
    renderDetail(root, BASE_CONNECTOR, { scopes: MOCK_SCOPES })

    const mismatchRow = container.querySelector(
      '[data-testid="scope-row-user-read-recently-played"]',
    )
    expect(mismatchRow).not.toBeNull()
    expect(mismatchRow?.textContent).toContain('mismatch')
  })

  it('granted scope row shows verdict "granted"', () => {
    renderDetail(root, BASE_CONNECTOR, { scopes: MOCK_SCOPES })

    const grantedRow = container.querySelector('[data-testid="scope-row-user-library-read"]')
    expect(grantedRow).not.toBeNull()
    expect(grantedRow?.textContent).toContain('granted')
  })

  it('unavailable message mentions reauth when reauthRequired is true', () => {
    renderDetail(root, REAUTH_CONNECTOR, { scopes: null })

    const unavailable = container.querySelector('[data-testid="scopes-unavailable"]')
    // When reauth is needed, the unavailable note should mention reauthorization
    expect(unavailable?.textContent?.toLowerCase()).toContain('reauthorize')
  })
})

// ---------------------------------------------------------------------------
// [bu-5ywn2] Recent events section
// ---------------------------------------------------------------------------

describe('[bu-5ywn2] Recent events section', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
  })
  afterEach(() => cleanup(root, container))

  it('renders the section container regardless of data', () => {
    renderDetail(root, BASE_CONNECTOR)
    const section = container.querySelector('[data-testid="recent-events-section"]')
    expect(section).not.toBeNull()
  })

  it('shows empty state when recentEvents is null', () => {
    renderDetail(root, BASE_CONNECTOR, { recentEvents: null })
    const empty = container.querySelector('[data-testid="recent-events-empty"]')
    expect(empty).not.toBeNull()
    expect(container.querySelector('[data-testid="recent-events-list"]')).toBeNull()
  })

  it('shows empty state when recentEvents has no events', () => {
    const emptyEvents: ConnectorEventsResponse = {
      events: [],
      connector_type: 'spotify',
      endpoint_identity: 'me',
      total_returned: 0,
    }
    renderDetail(root, BASE_CONNECTOR, { recentEvents: emptyEvents })
    const empty = container.querySelector('[data-testid="recent-events-empty"]')
    expect(empty).not.toBeNull()
  })

  it('renders event rows when populated', () => {
    renderDetail(root, BASE_CONNECTOR, { recentEvents: MOCK_EVENTS })
    const list = container.querySelector('[data-testid="recent-events-list"]')
    expect(list).not.toBeNull()
    expect(container.querySelector('[data-testid="recent-events-empty"]')).toBeNull()
  })

  it('renders event count matching the data', () => {
    renderDetail(root, BASE_CONNECTOR, { recentEvents: MOCK_EVENTS })
    // MOCK_EVENTS has 2 events — each renders a row inside the list
    const list = container.querySelector('[data-testid="recent-events-list"]')
    // rows are direct children of the list div
    const rows = list?.querySelectorAll('div') ?? []
    expect(rows.length).toBeGreaterThanOrEqual(MOCK_EVENTS.events.length)
  })

  it('renders view-all link when events present', () => {
    renderDetail(root, BASE_CONNECTOR, { recentEvents: MOCK_EVENTS })
    const section = container.querySelector('[data-testid="recent-events-section"]')
    const link = section?.querySelector('a')
    expect(link).not.toBeNull()
    expect(link?.getAttribute('href')).toContain('/ingestion')
  })
})

// ---------------------------------------------------------------------------
// [bu-5ywn2] Incident list section
// ---------------------------------------------------------------------------

describe('[bu-5ywn2] Incident list section', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
  })
  afterEach(() => cleanup(root, container))

  it('renders the section container regardless of data', () => {
    renderDetail(root, BASE_CONNECTOR)
    const section = container.querySelector('[data-testid="incident-list-section"]')
    expect(section).not.toBeNull()
  })

  it('shows empty state when incidents is null', () => {
    renderDetail(root, BASE_CONNECTOR, { incidents: null })
    const empty = container.querySelector('[data-testid="incident-list-empty"]')
    expect(empty).not.toBeNull()
    expect(container.querySelector('[data-testid="incident-list"]')).toBeNull()
  })

  it('shows empty state when incidents has no entries', () => {
    const emptyIncidents: ConnectorIncidentsResponse = {
      incidents: [],
      connector_type: 'spotify',
      endpoint_identity: 'me',
      total_returned: 0,
    }
    renderDetail(root, BASE_CONNECTOR, { incidents: emptyIncidents })
    const empty = container.querySelector('[data-testid="incident-list-empty"]')
    expect(empty).not.toBeNull()
  })

  it('renders incident rows when populated', () => {
    renderDetail(root, BASE_CONNECTOR, { incidents: MOCK_INCIDENTS })
    const list = container.querySelector('[data-testid="incident-list"]')
    expect(list).not.toBeNull()
    expect(container.querySelector('[data-testid="incident-list-empty"]')).toBeNull()
  })

  it('shows error detail text for populated incidents', () => {
    renderDetail(root, BASE_CONNECTOR, { incidents: MOCK_INCIDENTS })
    expect(container.textContent).toContain('Rate limit exceeded')
  })
})

// ---------------------------------------------------------------------------
// [bu-5ywn2] Routing rules section
// ---------------------------------------------------------------------------

describe('[bu-5ywn2] Routing rules section', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
  })
  afterEach(() => cleanup(root, container))

  it('renders the section container regardless of data', () => {
    renderDetail(root, BASE_CONNECTOR)
    const section = container.querySelector('[data-testid="routing-rules-section"]')
    expect(section).not.toBeNull()
  })

  it('shows empty state when routingRules is null', () => {
    renderDetail(root, BASE_CONNECTOR, { routingRules: null })
    const empty = container.querySelector('[data-testid="routing-rules-empty"]')
    expect(empty).not.toBeNull()
    expect(container.querySelector('[data-testid="routing-rules-list"]')).toBeNull()
  })

  it('shows empty state when routing rules list is empty', () => {
    const emptyRules: ConnectorRoutingRulesResponse = {
      rules: [],
      connector_type: 'spotify',
      endpoint_identity: 'me',
      total_returned: 0,
      filter_note: null,
    }
    renderDetail(root, BASE_CONNECTOR, { routingRules: emptyRules })
    const empty = container.querySelector('[data-testid="routing-rules-empty"]')
    expect(empty).not.toBeNull()
    expect(empty?.textContent).toContain('No routing rules reference this connector')
  })

  it('renders rule rows when populated', () => {
    renderDetail(root, BASE_CONNECTOR, { routingRules: MOCK_RULES })
    const list = container.querySelector('[data-testid="routing-rules-list"]')
    expect(list).not.toBeNull()
    expect(container.querySelector('[data-testid="routing-rules-empty"]')).toBeNull()
  })

  it('renders rule name and action for populated rules', () => {
    renderDetail(root, BASE_CONNECTOR, { routingRules: MOCK_RULES })
    expect(container.textContent).toContain('Block spam')
    expect(container.textContent).toContain('block')
  })

  it('rule rows link to /ingestion/filters', () => {
    renderDetail(root, BASE_CONNECTOR, { routingRules: MOCK_RULES })
    const list = container.querySelector('[data-testid="routing-rules-list"]')
    const link = list?.querySelector('a')
    expect(link).not.toBeNull()
    expect(link?.getAttribute('href')).toBe('/ingestion/filters')
  })
})
