// @vitest-environment jsdom
/**
 * ConnectorsRoster — unit tests covering spec acceptance criteria:
 *
 * AC1: Connectors route is a roster, not a card grid
 * AC2: Auth issues appear consistently in attention strip, row, and detail
 *      (focus here: strip count matches auth-needed connectors; row shows same label)
 * AC3: Roster rows render a single health verdict (one dot + word), folding
 *      liveness and health into one signal instead of two unlabeled dots
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

const archiveMutate = vi.fn()
const unarchiveMutate = vi.fn()

vi.mock('@/hooks/use-ingestion', () => ({
  useConnectorSummariesWithAggregates: vi.fn(),
  useAvailableConnectors: vi.fn(),
  // ArchiveCandidatesList (bu-u19yv) calls this unconditionally; return a stable
  // idle mutation so the roster mounts without a real QueryClient.
  useArchiveConnector: vi.fn(() => ({
    mutate: archiveMutate,
    isPending: false,
    isError: false,
    variables: undefined,
  })),
  // ArchivedConnectorsList (bu-ep4ks.11) calls this unconditionally too.
  useUnarchiveConnector: vi.fn(() => ({
    mutate: unarchiveMutate,
    isPending: false,
    isError: false,
    variables: undefined,
  })),
}))

import {
  useConnectorSummariesWithAggregates,
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
  hourly_events: Array(24).fill(0),
}

// Live (online) + state=error: a genuine auth/config failure — needs_reauth.
// bu-14gso: liveness must be 'online' here, not 'offline' — an offline connector's
// frozen error label is a connectivity issue, not a live auth diagnosis (see
// OFFLINE_FROZEN_ERROR_CONNECTOR below for that case).
const REAUTH_CONNECTOR: ConnectorSummary = {
  connector_type: 'spotify',
  endpoint_identity: 'me',
  liveness: 'online',
  state: 'error',
  error_message: '401 Unauthorized — token expired',
  version: null,
  uptime_s: null,
  last_heartbeat_at: new Date(Date.now() - 60_000).toISOString(),
  first_seen_at: '2026-01-01T00:00:00Z',
  today: { messages_ingested: 0, messages_failed: 8, uptime_pct: null },
  hourly_events: Array(24).fill(0),
}

// bu-14gso: offline connector whose last heartbeat froze state='error' before the
// process died. Must report as a connectivity problem, NOT needs_reauth.
const OFFLINE_FROZEN_ERROR_CONNECTOR: ConnectorSummary = {
  ...REAUTH_CONNECTOR,
  liveness: 'offline',
  last_heartbeat_at: new Date(Date.now() - 3_600_000).toISOString(),
}

const GMAIL_REAUTH_CONNECTOR: ConnectorSummary = {
  ...REAUTH_CONNECTOR,
  connector_type: 'gmail',
  endpoint_identity: 'user@example.com',
}

const WHATSAPP_REAUTH_CONNECTOR: ConnectorSummary = {
  ...REAUTH_CONNECTOR,
  connector_type: 'whatsapp_user_client',
  endpoint_identity: '+65 5555 0100',
}

const UNSUPPORTED_REAUTH_CONNECTOR: ConnectorSummary = {
  ...REAUTH_CONNECTOR,
  connector_type: 'steam',
  endpoint_identity: 'owner',
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
  hourly_events: Array(24).fill(0),
}

const SPARSE_OWNTRACKS_CONNECTOR: ConnectorSummary = {
  connector_type: 'owntracks',
  endpoint_identity: 'owntracks:phone',
  liveness: 'online',
  state: 'healthy',
  error_message: null,
  version: '1.0',
  uptime_s: 3600,
  last_heartbeat_at: new Date(Date.now() - 60_000).toISOString(),
  first_seen_at: '2026-01-01T00:00:00Z',
  today: { messages_ingested: 3, messages_failed: 0, uptime_pct: 99.9 },
  hourly_events: Array(24).fill(0),
  operational_warnings: [
    'Only 3 OwnTracks location points were recorded in the last 24 hours. The operational baseline is 24; use Move mode during waking hours.',
  ],
}

const DORMANT_PROFILE: ConnectorProfile = {
  connector_type: 'home_assistant',
  channel: 'long-poll',
  provider: 'homeassistant',
  display_name: 'Home Assistant',
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
  responseOverrides: {
    hourly_events_available?: boolean
    device_liveness_available?: boolean
    owntracks_cadence_available?: boolean
  } = {},
) {
  // The endpoint returns { connectors: [...] } (all fields DB-sourced),
  // wrapped in ApiResponse<ConnectorSummariesResponse>: { data: { connectors } }
  vi.mocked(useConnectorSummariesWithAggregates).mockReturnValue(
    makeResult({
      data: { connectors, ...responseOverrides },
    }) as ReturnType<typeof useConnectorSummariesWithAggregates>,
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

  it('uses the registered Google OAuth route for Gmail reauth', () => {
    mockHooks([GMAIL_REAUTH_CONNECTOR])
    renderRoster(container, root)

    const action = container.querySelector('[data-testid="auth-status-gmail"]')
    expect(action?.getAttribute('href')).toContain('/api/oauth/google/start')
    expect(action?.getAttribute('href')).toContain('page_of_origin=ingestion')
  })

  it('routes WhatsApp reauth to Passport pairing instead of OAuth', () => {
    mockHooks([WHATSAPP_REAUTH_CONNECTOR])
    renderRoster(container, root)

    const action = container.querySelector('[data-testid="auth-status-whatsapp_user_client"]')
    expect(action?.getAttribute('href')).toBe('/secrets?focus=u:whatsapp')
  })

  it('does not create a recovery link for an unsupported connector', () => {
    mockHooks([UNSUPPORTED_REAUTH_CONNECTOR])
    renderRoster(container, root)

    const status = container.querySelector('[data-testid="auth-status-steam"]')
    expect(status?.tagName).toBe('SPAN')
    expect(status?.textContent).toContain('unavailable')
    expect(container.textContent).toMatch(/not available/i)
  })

  it('surfaces a sparse OwnTracks cadence warning without changing transport health', () => {
    mockHooks([SPARSE_OWNTRACKS_CONNECTOR])
    renderRoster(container, root)

    expect(
      container.querySelector('[data-testid="attention-item-owntracks"]')?.textContent,
    ).toContain('cadence sparse')
    expect(
      container.querySelector('[data-testid="connector-warning-owntracks"]')?.textContent,
    ).toContain('The operational baseline is 24')
    expect(
      container.querySelector('[data-testid="connector-warning-owntracks"]')?.className,
    ).toContain('text-[var(--amber-text)]')
    expect(
      container.querySelector('[data-testid="health-verdict-owntracks"]')?.textContent?.trim(),
    ).toBe('online')

    const attentionKpiLabel = Array.from(container.querySelectorAll('div')).find(
      (element) => element.textContent?.trim() === 'needs attention',
    )
    expect(attentionKpiLabel?.parentElement?.lastElementChild?.textContent?.trim()).toBe('1')
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

  it('the connect link carries ?focus=u:<provider> from the catalog, not a bare /secrets link', () => {
    mockHooks([HEALTHY_CONNECTOR], [DORMANT_PROFILE])
    renderRoster(container, root)

    act(() => {
      ;(container.querySelector('[data-testid="dormant-toggle"]') as HTMLButtonElement).click()
    })

    const connectLink = container.querySelector(
      '[data-testid="dormant-connect-home_assistant"]',
    )
    expect(connectLink?.getAttribute('href')).toBe('/secrets?focus=u:homeassistant')
  })
})

// ---------------------------------------------------------------------------
// §AC3: Single health verdict (one dot + word) per row
//
// Supersedes the old two-dot (liveness + state) assertions: the binding spec
// ("rows with health dot" — singular) already called for one indicator; the
// old stacked-dot markup had drifted from it. bu-4utdw.10 brings the code
// back in line with the spec.
// ---------------------------------------------------------------------------

describe('AC3: single health verdict per row (dot + word)', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
  })
  afterEach(() => cleanup(root, container))

  it('renders exactly one health-verdict element for a healthy online connector', () => {
    mockHooks([HEALTHY_CONNECTOR])
    renderRoster(container, root)

    const verdict = container.querySelector('[data-testid="health-verdict-gmail"]')
    expect(verdict).not.toBeNull()
    expect(verdict?.textContent?.trim()).toBe('online')

    // No leftover two-dot markup
    expect(container.querySelector('[data-testid="liveness-dot-gmail"]')).toBeNull()
    expect(container.querySelector('[data-testid="state-dot-gmail"]')).toBeNull()
  })

  it('stale connector reports the "stale" verdict word', () => {
    // STALE_CONNECTOR: liveness=stale, state=healthy
    mockHooks([STALE_CONNECTOR])
    renderRoster(container, root)

    const verdict = container.querySelector('[data-testid="health-verdict-telegram"]')
    expect(verdict?.textContent?.trim()).toBe('stale')
  })

  it('offline+error connector reports the "offline" verdict word', () => {
    // OFFLINE_FROZEN_ERROR_CONNECTOR: liveness=offline, state=error
    mockHooks([OFFLINE_FROZEN_ERROR_CONNECTOR])
    renderRoster(container, root)

    const verdict = container.querySelector('[data-testid="health-verdict-spotify"]')
    expect(verdict?.textContent?.trim()).toBe('offline')
  })
})

// ---------------------------------------------------------------------------
// bu-14gso: offline connector with a frozen error label is a connectivity
// problem, not needs_reauth
// ---------------------------------------------------------------------------

describe('bu-14gso: offline connector with frozen error state', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
  })
  afterEach(() => cleanup(root, container))

  it('does not render the reauth pill for an offline connector with a frozen error label', () => {
    mockHooks([OFFLINE_FROZEN_ERROR_CONNECTOR])
    renderRoster(container, root)

    const status = container.querySelector('[data-testid="auth-status-spotify"]')
    // Not a reauth link — plain-text 'authorized'/ok rendering, same as a healthy connector.
    expect(status?.tagName).not.toBe('A')
    expect(status?.textContent?.toLowerCase()).not.toContain('reauth')
  })

  it('still reports the "offline" verdict word (connectivity), not "error"', () => {
    mockHooks([OFFLINE_FROZEN_ERROR_CONNECTOR])
    renderRoster(container, root)

    const verdict = container.querySelector('[data-testid="health-verdict-spotify"]')
    expect(verdict?.textContent?.trim()).toBe('offline')
  })
})

// ---------------------------------------------------------------------------
// Whole-row navigation (row click + keyboard focus)
// ---------------------------------------------------------------------------

describe('whole-row navigation', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
  })
  afterEach(() => cleanup(root, container))

  it('the row link spans the row and targets connector detail', () => {
    mockHooks([HEALTHY_CONNECTOR])
    renderRoster(container, root)

    const rowLink = container.querySelector('[data-testid="row-link-gmail"]')
    expect(rowLink).not.toBeNull()
    expect(rowLink?.tagName).toBe('A')
    expect(rowLink?.getAttribute('href')).toBe(
      '/ingestion/connectors/gmail/user%40example.com',
    )
  })

  it('the row link is keyboard-focusable (no explicit negative tabindex)', () => {
    mockHooks([HEALTHY_CONNECTOR])
    renderRoster(container, root)

    const rowLink = container.querySelector('[data-testid="row-link-gmail"]')
    expect(rowLink?.getAttribute('tabindex')).not.toBe('-1')
  })

  it('the disclosure chevron is decorative, not its own link', () => {
    mockHooks([HEALTHY_CONNECTOR])
    renderRoster(container, root)

    const row = container.querySelector('[data-testid="connector-row-gmail"]')
    const links = row?.querySelectorAll('a') ?? []
    // Exactly one anchor per healthy row: the stretched row link.
    expect(links.length).toBe(1)
    expect(links[0].getAttribute('data-testid')).toBe('row-link-gmail')
  })
})

// ---------------------------------------------------------------------------
// Reauth pill is the reauth action
// ---------------------------------------------------------------------------

describe('reauth pill is the reauth action', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
  })
  afterEach(() => cleanup(root, container))

  it('links the auth pill to the Spotify Passport card when needs_reauth', () => {
    // Spotify recovers through its Passport card (the connector PKCE drawer),
    // not the generalized /oauth/spotify/start dance — that entry is a
    // confidential-client flow whose app credentials were never provisioned.
    // The pill keeps the "reauth" wording of the attention strip; only
    // WhatsApp's Passport recovery reads "pair".
    mockHooks([REAUTH_CONNECTOR])
    renderRoster(container, root)

    const pill = container.querySelector('[data-testid="auth-status-spotify"]')
    expect(pill?.tagName).toBe('A')
    expect(pill?.getAttribute('href')).toBe('/secrets?focus=u:spotify')
    expect(pill?.textContent).toContain('reauth')
    expect(pill?.getAttribute('aria-label')).toContain('Re-authorize')
  })

  it('renders the auth pill as plain text (not a link) for a healthy connector', () => {
    mockHooks([HEALTHY_CONNECTOR])
    renderRoster(container, root)

    const pill = container.querySelector('[data-testid="auth-status-gmail"]')
    expect(pill?.tagName).not.toBe('A')
  })
})

// ---------------------------------------------------------------------------
// Honest metadata: kind only when known from the catalog; function column
// shows endpoint identity instead of duplicating the channel column.
// ---------------------------------------------------------------------------

describe('honest metadata', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
  })
  afterEach(() => cleanup(root, container))

  const GMAIL_PROFILE: ConnectorProfile = {
    connector_type: 'gmail',
    channel: 'email',
    provider: 'google',
    display_name: 'Gmail',
  }

  it('shows the real catalog channel when the connector_type is in the catalog', () => {
    mockHooks([HEALTHY_CONNECTOR], [GMAIL_PROFILE])
    renderRoster(container, root)

    const row = container.querySelector('[data-testid="connector-row-gmail"]')
    expect(row?.textContent).toContain('email')
  })

  it('does not fabricate a kind when the connector_type is absent from the catalog', () => {
    // STALE_CONNECTOR's type ("telegram") has no catalog entry in this fixture set.
    mockHooks([STALE_CONNECTOR], [])
    renderRoster(container, root)

    const row = container.querySelector('[data-testid="connector-row-telegram"]')
    // No fabricated "poll"/"webhook"/"imap" guess anywhere in the row.
    expect(row?.textContent).not.toMatch(/\b(poll|webhook|imap|long-poll)\b/)
  })

  it('the function column shows endpoint identity, not a repeat of the channel name', () => {
    mockHooks([HEALTHY_CONNECTOR], [])
    renderRoster(container, root)

    const row = container.querySelector('[data-testid="connector-row-gmail"]')
    expect(row?.textContent).toContain(HEALTHY_CONNECTOR.endpoint_identity)
    // "gmail" appears once (channel column) — endpoint identity shouldn't
    // re-prefix it with the connector type like the old "gmail · user@..." gloss.
    expect(row?.textContent).not.toContain(`gmail · ${HEALTHY_CONNECTOR.endpoint_identity}`)
  })
})

// ---------------------------------------------------------------------------
// Per-device liveness for multi-device connectors (bu-e16to)
//
// Regression coverage for: OwnTracks devices 'tz' and 'el' went silent for
// 10 weeks with zero dashboard signal because connector_registry only ever
// tracks ONE shared heartbeat identity per connector_type ('th' stayed
// healthy). The `devices` field surfaces every distinct sender_identity so a
// silent sibling device is visible on the roster row itself.
// ---------------------------------------------------------------------------

const OWNTRACKS_WITH_STALE_DEVICE: ConnectorSummary = {
  connector_type: 'owntracks',
  endpoint_identity: 'owntracks:th',
  liveness: 'online',
  state: 'healthy',
  error_message: null,
  version: '1.0',
  uptime_s: 3600,
  last_heartbeat_at: new Date(Date.now() - 60_000).toISOString(),
  first_seen_at: '2026-04-24T17:55:51Z',
  today: { messages_ingested: 100, messages_failed: 0, uptime_pct: 99.9 },
  hourly_events: Array(24).fill(0),
  devices: [
    {
      sender_identity: 'owntracks:th',
      last_seen_at: new Date(Date.now() - 60_000).toISOString(),
      stale: false,
    },
    {
      sender_identity: 'owntracks:el',
      last_seen_at: new Date(Date.now() - 70 * 24 * 3600_000).toISOString(),
      stale: true,
    },
    {
      sender_identity: 'owntracks:tz',
      last_seen_at: new Date(Date.now() - 71 * 24 * 3600_000).toISOString(),
      stale: true,
    },
  ],
}

describe('per-device liveness (bu-e16to)', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
  })
  afterEach(() => cleanup(root, container))

  it('renders a device badge for every distinct sender_identity', () => {
    mockHooks([OWNTRACKS_WITH_STALE_DEVICE])
    renderRoster(container, root)

    expect(
      container.querySelector('[data-testid="connector-device-owntracks:th"]'),
    ).not.toBeNull()
    expect(
      container.querySelector('[data-testid="connector-device-owntracks:el"]'),
    ).not.toBeNull()
    expect(
      container.querySelector('[data-testid="connector-device-owntracks:tz"]'),
    ).not.toBeNull()
  })

  it('marks stale devices distinctly from the fresh device', () => {
    mockHooks([OWNTRACKS_WITH_STALE_DEVICE])
    renderRoster(container, root)

    const fresh = container.querySelector(
      '[data-testid="connector-device-lastseen-owntracks:th"]',
    )
    const stale = container.querySelector(
      '[data-testid="connector-device-lastseen-owntracks:el"]',
    )
    expect(fresh?.textContent).toMatch(/^last ·/)
    expect(stale?.textContent).toMatch(/^stale ·/)
  })

  it('does not render a devices section for a single-device connector', () => {
    mockHooks([HEALTHY_CONNECTOR])
    renderRoster(container, root)

    expect(container.querySelector('[data-testid="connector-devices"]')).toBeNull()
  })

  it('a stale sibling device surfaces the connector in the attention strip', () => {
    // The connector itself is liveness=online/state=healthy -- only a stale
    // device distinguishes it from HEALTHY_CONNECTOR. Without the devices-aware
    // check in deriveConnectorDispatchInfo this would NOT appear in the strip,
    // reproducing the exact bu-e16to invisibility bug.
    mockHooks([OWNTRACKS_WITH_STALE_DEVICE])
    renderRoster(container, root)

    const strip = container.querySelector('[data-testid="attention-strip"]')
    expect(strip).not.toBeNull()
    expect(
      container.querySelector('[data-testid="attention-item-owntracks"]'),
    ).not.toBeNull()
  })

  it('a stale sibling device downgrades the row health verdict to degraded', () => {
    mockHooks([OWNTRACKS_WITH_STALE_DEVICE])
    renderRoster(container, root)

    const verdict = container.querySelector('[data-testid="health-verdict-owntracks"]')
    expect(verdict?.textContent?.trim()).toBe('degraded')
  })
})

// ---------------------------------------------------------------------------
// hourly_events_available degraded note (bu-scyro)
//
// The combined ingested+filtered hourly query failing must never render as an
// honest "quiet 24h" -- the degraded source has to be named inline, not
// suppressed behind all-zero sparklines and a "0" events · 24h stat.
// ---------------------------------------------------------------------------

describe('hourly_events_available degraded note (bu-scyro)', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
  })
  afterEach(() => cleanup(root, container))

  it('does not render a degraded note when hourly_events_available is true', () => {
    mockHooks([HEALTHY_CONNECTOR], [], { hourly_events_available: true })
    renderRoster(container, root)

    expect(container.textContent).not.toMatch(/hourly event source unavailable/)
  })

  it('does not render a degraded note when hourly_events_available is absent (older cached response)', () => {
    mockHooks([HEALTHY_CONNECTOR], [])
    renderRoster(container, root)

    expect(container.textContent).not.toMatch(/hourly event source unavailable/)
  })

  it('renders a degraded note when hourly_events_available is false', () => {
    mockHooks([HEALTHY_CONNECTOR], [], { hourly_events_available: false })
    renderRoster(container, root)

    expect(container.textContent).toMatch(/24h activity/)
    expect(container.textContent).toMatch(/hourly event source unavailable/)
  })
})

// ---------------------------------------------------------------------------
// device_liveness_available degraded note (bu-fm3my)
//
// The per-device liveness query failing must never render as an honest
// "no multi-device connectors" -- devices:null on genuine failure is
// indistinguishable from that case and would hide a silently-dead sibling
// device (bu-e16to). Same shape as the hourly_events_available note above.
// ---------------------------------------------------------------------------

describe('device_liveness_available degraded note (bu-fm3my)', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
  })
  afterEach(() => cleanup(root, container))

  it('does not render a degraded note when device_liveness_available is true', () => {
    mockHooks([HEALTHY_CONNECTOR], [], { device_liveness_available: true })
    renderRoster(container, root)

    expect(container.textContent).not.toMatch(/device liveness/)
    expect(container.textContent).not.toMatch(/per-device liveness source unavailable/)
  })

  it('does not render a degraded note when device_liveness_available is absent (older cached response)', () => {
    mockHooks([HEALTHY_CONNECTOR], [])
    renderRoster(container, root)

    expect(container.textContent).not.toMatch(/per-device liveness source unavailable/)
  })

  it('renders a degraded note when device_liveness_available is false', () => {
    mockHooks([HEALTHY_CONNECTOR], [], { device_liveness_available: false })
    renderRoster(container, root)

    expect(container.textContent).toMatch(/device liveness/)
    expect(container.textContent).toMatch(/per-device liveness source unavailable/)
  })
})

describe('owntracks_cadence_available degraded note', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
  })
  afterEach(() => cleanup(root, container))

  it('does not render a degraded note when the flag is absent (older cached response)', () => {
    mockHooks([HEALTHY_CONNECTOR])
    renderRoster(container, root)

    expect(container.textContent).not.toMatch(/durable location-point cadence unavailable/)
  })

  it('renders a degraded note when the cadence query failed', () => {
    mockHooks([HEALTHY_CONNECTOR], [], {
      owntracks_cadence_available: false,
    })
    renderRoster(container, root)

    expect(container.textContent).toMatch(/OwnTracks cadence/)
    expect(container.textContent).toMatch(/durable location-point cadence unavailable/)
  })
})

// ---------------------------------------------------------------------------
// Archived / superseded connector identities (bu-33dm2)
//
// Dead endpoint identities are returned in the same summaries list but flagged
// `archived`. They must be split out of the active roster (no rows, no KPI, no
// attention) into a collapsed "archived" section whose rows link to detail so
// their history stays reachable.
// ---------------------------------------------------------------------------

const ARCHIVED_CONNECTOR: ConnectorSummary = {
  connector_type: 'google_health',
  endpoint_identity: 'degraded',
  liveness: 'offline',
  state: 'degraded',
  error_message: null,
  version: null,
  uptime_s: null,
  last_heartbeat_at: null,
  first_seen_at: '2026-01-01T00:00:00Z',
  today: { messages_ingested: 0, messages_failed: 0, uptime_pct: null },
  hourly_events: Array(24).fill(0),
  archived: true,
  archived_at: '2026-06-07T00:00:00Z',
}

// ---------------------------------------------------------------------------
// Archive review queue (bu-u19yv)
//
// An active identity the backend flags `archive_candidate` (offline >30d + a
// newer online sibling) is surfaced as a review-queue row with a one-click
// archive. It is a SUGGESTION overlay: the identity ALSO stays in the active
// roster with its true (offline) liveness — never filed as "just a candidate".
// ---------------------------------------------------------------------------

const CANDIDATE_CONNECTOR: ConnectorSummary = {
  connector_type: 'google_health',
  endpoint_identity: 'dead-placeholder',
  liveness: 'offline',
  state: 'degraded',
  error_message: null,
  version: null,
  uptime_s: null,
  last_heartbeat_at: new Date(Date.now() - 45 * 24 * 3600_000).toISOString(),
  first_seen_at: '2026-01-01T00:00:00Z',
  today: { messages_ingested: 0, messages_failed: 0, uptime_pct: null },
  hourly_events: Array(24).fill(0),
  archive_candidate: true,
}

describe('archive review queue (bu-u19yv)', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
    archiveMutate.mockClear()
  })
  afterEach(() => cleanup(root, container))

  it('renders a review-queue section listing the candidate', () => {
    mockHooks([HEALTHY_CONNECTOR, CANDIDATE_CONNECTOR])
    renderRoster(container, root)

    expect(
      container.querySelector('[data-testid="archive-candidates-section"]'),
    ).not.toBeNull()
    expect(
      container.querySelector(
        '[data-testid="archive-candidate-row-google_health:dead-placeholder"]',
      ),
    ).not.toBeNull()
  })

  it('does not render the review queue when there are no candidates', () => {
    mockHooks([HEALTHY_CONNECTOR])
    renderRoster(container, root)

    expect(
      container.querySelector('[data-testid="archive-candidates-section"]'),
    ).toBeNull()
  })

  it('keeps the candidate in the active roster (suggestion, not a filter)', () => {
    mockHooks([HEALTHY_CONNECTOR, CANDIDATE_CONNECTOR])
    renderRoster(container, root)

    // The candidate still renders as an active roster row with its true state.
    expect(
      container.querySelector('[data-testid="connector-row-google_health"]'),
    ).not.toBeNull()
  })

  it('archive fires the archive mutation with the identity once the undo window elapses (no dead onClick)', () => {
    mockHooks([HEALTHY_CONNECTOR, CANDIDATE_CONNECTOR])
    renderRoster(container, root)

    const btn = container.querySelector(
      '[data-testid="archive-candidate-action-google_health:dead-placeholder"]',
    )
    expect(btn).not.toBeNull()
    vi.useFakeTimers()
    try {
      act(() => {
        ;(btn as HTMLButtonElement).click()
      })
      expect(archiveMutate).not.toHaveBeenCalled()
      act(() => {
        vi.advanceTimersByTime(5_000)
      })
      expect(archiveMutate).toHaveBeenCalledTimes(1)
      expect(archiveMutate).toHaveBeenCalledWith({
        connectorType: 'google_health',
        endpointIdentity: 'dead-placeholder',
      })
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('archived connectors section (bu-33dm2)', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
  })
  afterEach(() => cleanup(root, container))

  it('renders a collapsed archived section when an archived identity is present', () => {
    mockHooks([HEALTHY_CONNECTOR, ARCHIVED_CONNECTOR])
    renderRoster(container, root)

    expect(container.querySelector('[data-testid="archived-section"]')).not.toBeNull()
    // Collapsed: list not rendered until toggled
    expect(container.querySelector('[data-testid="archived-list"]')).toBeNull()
  })

  it('does not render the archived section when there are no archived identities', () => {
    mockHooks([HEALTHY_CONNECTOR])
    renderRoster(container, root)

    expect(container.querySelector('[data-testid="archived-section"]')).toBeNull()
  })

  it('excludes archived identities from the active roster rows', () => {
    mockHooks([HEALTHY_CONNECTOR, ARCHIVED_CONNECTOR])
    renderRoster(container, root)

    // Only the live connector renders as an active roster row.
    const rosterRows = container.querySelector('[data-testid="roster-rows"]')
    const rows = rosterRows?.querySelectorAll('[data-testid^="connector-row-"]') ?? []
    expect(rows.length).toBe(1)
    expect(rows[0].getAttribute('data-testid')).toBe('connector-row-gmail')
    // The archived google_health identity is NOT an active row.
    expect(
      container.querySelector('[data-testid="connector-row-google_health"]'),
    ).toBeNull()
  })

  it('does not drag an archived (offline) identity into the attention strip', () => {
    // Only a healthy connector + an archived offline one: nothing needs attention.
    mockHooks([HEALTHY_CONNECTOR, ARCHIVED_CONNECTOR])
    renderRoster(container, root)

    expect(container.querySelector('[data-testid="attention-strip"]')).toBeNull()
  })

  it('expands to show archived rows that link to connector detail (history reachable)', () => {
    mockHooks([HEALTHY_CONNECTOR, ARCHIVED_CONNECTOR])
    renderRoster(container, root)

    const toggle = container.querySelector('[data-testid="archived-toggle"]')
    expect(toggle).not.toBeNull()
    act(() => {
      ;(toggle as HTMLButtonElement).click()
    })

    const row = container.querySelector(
      '[data-testid="archived-row-google_health:degraded"]',
    )
    expect(row).not.toBeNull()
    // The row itself is a container (bu-ep4ks.11 added an unarchive button as
    // a sibling), but it still links to detail so history stays reachable.
    const link = row?.querySelector('a')
    expect(link).not.toBeNull()
    expect(link?.getAttribute('href')).toBe('/ingestion/connectors/google_health/degraded')
  })

  it('unarchive button fires the unarchive mutation with the identity', () => {
    mockHooks([HEALTHY_CONNECTOR, ARCHIVED_CONNECTOR])
    renderRoster(container, root)

    act(() => {
      ;(container.querySelector('[data-testid="archived-toggle"]') as HTMLButtonElement).click()
    })

    const btn = container.querySelector(
      '[data-testid="unarchive-action-google_health:degraded"]',
    ) as HTMLButtonElement
    expect(btn).not.toBeNull()
    act(() => btn.click())

    expect(unarchiveMutate).toHaveBeenCalledWith({
      connectorType: 'google_health',
      endpointIdentity: 'degraded',
    })
  })
})

// ---------------------------------------------------------------------------
// Query failures must not fall through to the same calm states as a genuinely
// empty roster or catalog (bu-xdjoq).
// ---------------------------------------------------------------------------

describe('connector roster source failures are named and retryable', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
  })
  afterEach(() => cleanup(root, container))

  it('keeps a successful dormant catalog visible when the active roster reader fails', () => {
    const retryRoster = vi.fn()
    vi.mocked(useConnectorSummariesWithAggregates).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error('connector summaries offline'),
      refetch: retryRoster,
    } as unknown as ReturnType<typeof useConnectorSummariesWithAggregates>)
    vi.mocked(useAvailableConnectors).mockReturnValue(
      makeResult({ data: [DORMANT_PROFILE] }) as ReturnType<typeof useAvailableConnectors>,
    )

    renderRoster(container, root)

    expect(container.querySelector('[data-testid="connectors-roster-unavailable"]')).not.toBeNull()
    expect(container.textContent).toContain('connector roster')
    expect(container.textContent).not.toContain('No connectors registered.')
    expect(container.querySelector('[data-testid="dormant-section"]')).not.toBeNull()
    expect(container.querySelector('[data-testid="connectors-kpi-footer"]')).toBeNull()

    act(() => {
      ;(
        container.querySelector('[data-testid="connectors-roster-unavailable"] button') as HTMLButtonElement
      ).click()
    })
    expect(retryRoster).toHaveBeenCalledTimes(1)
  })

  it('does not present a failed available-catalog reader as an empty dormant catalog', () => {
    const retryCatalog = vi.fn()
    vi.mocked(useConnectorSummariesWithAggregates).mockReturnValue(
      makeResult({ data: { connectors: [HEALTHY_CONNECTOR] } }) as ReturnType<
        typeof useConnectorSummariesWithAggregates
      >,
    )
    vi.mocked(useAvailableConnectors).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error('connector catalog offline'),
      refetch: retryCatalog,
    } as unknown as ReturnType<typeof useAvailableConnectors>)

    renderRoster(container, root)

    expect(container.querySelector('[data-testid="connector-row-gmail"]')).not.toBeNull()
    expect(container.querySelector('[data-testid="connector-catalog-unavailable"]')).not.toBeNull()
    expect(container.querySelector('[data-testid="dormant-section"]')).toBeNull()

    act(() => {
      ;(
        container.querySelector('[data-testid="connector-catalog-unavailable"] button') as HTMLButtonElement
      ).click()
    })
    expect(retryCatalog).toHaveBeenCalledTimes(1)
  })
})
