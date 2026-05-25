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

import type { ConnectorDetail } from '@/api/types'
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
  opts: { scopes?: OAuthScope[] | null; onReauth?: () => void } = {},
) {
  act(() => {
    root.render(
      <MemoryRouter>
        <ConnectorDetailView
          connector={connector}
          stats={undefined}
          oauthScopes={opts.scopes}
          onReauth={opts.onReauth}
        />
      </MemoryRouter>,
    )
  })
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
