/**
 * Tests for deriveConnectorDispatchInfo — the single source of truth for
 * connector auth/health status derivation.
 *
 * Key invariants:
 *  - state='error' always → needs_reauth regardless of liveness
 *  - state='degraded' + error_message 'api_forbidden' → needs_reauth
 *  - state='degraded' + error_message 'no_primary_account' → needs_primary_account
 *  - state='degraded' (other error_message) → ok (not an auth issue)
 *  - auth.status='unconfigured' is ignored (seen on healthy connectors too)
 *  - offline + healthy → ok auth, error health (connectivity, not auth)
 *  - stale → ok auth, degraded health
 *  - healthy + online → ok auth, ok health
 */

import { describe, it, expect } from 'vitest'
import { deriveConnectorDispatchInfo } from './connector-auth'
import type { ConnectorSummary } from '@/api/types'

const BASE: ConnectorSummary = {
  connector_type: 'gmail',
  endpoint_identity: 'user@example.com',
  liveness: 'online',
  state: 'healthy',
  error_message: null,
  version: '1.0.0',
  uptime_s: 3600,
  last_heartbeat_at: '2025-01-15T10:00:00Z',
  first_seen_at: '2025-01-01T00:00:00Z',
  today: { uptime_pct: 99.5, messages_ingested: 50, messages_failed: 0 },
  hourly_events: Array(24).fill(0),
}

// ---------------------------------------------------------------------------
// Healthy connector
// ---------------------------------------------------------------------------

describe('deriveConnectorDispatchInfo — healthy connector', () => {
  it('returns ok auth and ok health for online+healthy connector', () => {
    const result = deriveConnectorDispatchInfo(BASE)
    expect(result.authStatus).toBe('ok')
    expect(result.health).toBe('ok')
    expect(result.needsAttention).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// state='error' — always needs_reauth
// ---------------------------------------------------------------------------

describe('deriveConnectorDispatchInfo — state=error', () => {
  it('returns needs_reauth when state is error (no error_message)', () => {
    const c: ConnectorSummary = { ...BASE, state: 'error', error_message: null }
    const result = deriveConnectorDispatchInfo(c)
    expect(result.authStatus).toBe('needs_reauth')
    expect(result.health).toBe('error')
    expect(result.needsAttention).toBe(true)
  })

  it('returns needs_reauth with truncated error_message note when present', () => {
    const c: ConnectorSummary = {
      ...BASE,
      state: 'error',
      error_message: '401 Unauthorized — token expired',
    }
    const result = deriveConnectorDispatchInfo(c)
    expect(result.authStatus).toBe('needs_reauth')
    expect(result.authNote).toContain('401 Unauthorized')
  })

  it('returns needs_reauth even when liveness is online', () => {
    const c: ConnectorSummary = { ...BASE, liveness: 'online', state: 'error', error_message: null }
    const result = deriveConnectorDispatchInfo(c)
    expect(result.authStatus).toBe('needs_reauth')
  })
})

// ---------------------------------------------------------------------------
// state='degraded' + auth-flavored error_message
// ---------------------------------------------------------------------------

describe('deriveConnectorDispatchInfo — state=degraded with auth error_message', () => {
  it('returns needs_reauth when error_message contains api_forbidden', () => {
    const c: ConnectorSummary = {
      ...BASE,
      state: 'degraded',
      error_message: 'api_forbidden: 403 Forbidden',
    }
    const result = deriveConnectorDispatchInfo(c)
    expect(result.authStatus).toBe('needs_reauth')
    expect(result.health).toBe('degraded')
    expect(result.needsAttention).toBe(true)
  })

  it('returns needs_primary_account when error_message contains no_primary_account', () => {
    const c: ConnectorSummary = {
      ...BASE,
      state: 'degraded',
      error_message: 'no_primary_account',
    }
    const result = deriveConnectorDispatchInfo(c)
    expect(result.authStatus).toBe('needs_primary_account')
    expect(result.health).toBe('degraded')
    expect(result.needsAttention).toBe(true)
    expect(result.authNote).toContain('primary account')
  })

  it('does NOT treat degraded+other error_message as auth issue', () => {
    const c: ConnectorSummary = {
      ...BASE,
      state: 'degraded',
      error_message: 'rate_limit_exceeded: too many requests',
    }
    const result = deriveConnectorDispatchInfo(c)
    expect(result.authStatus).toBe('ok')
    expect(result.health).toBe('degraded')
    expect(result.needsAttention).toBe(true)
  })

  it('does NOT treat degraded+null error_message as auth issue', () => {
    const c: ConnectorSummary = { ...BASE, state: 'degraded', error_message: null }
    const result = deriveConnectorDispatchInfo(c)
    expect(result.authStatus).toBe('ok')
    expect(result.health).toBe('degraded')
  })

  // Critical regression guard: no_primary_account must NOT trigger needs_reauth
  it('no_primary_account routes to needs_primary_account, NOT needs_reauth', () => {
    const c: ConnectorSummary = {
      ...BASE,
      state: 'degraded',
      error_message: 'no_primary_account',
    }
    const result = deriveConnectorDispatchInfo(c)
    expect(result.authStatus).not.toBe('needs_reauth')
    expect(result.authStatus).toBe('needs_primary_account')
  })
})

// ---------------------------------------------------------------------------
// Liveness-driven paths
// ---------------------------------------------------------------------------

describe('deriveConnectorDispatchInfo — liveness', () => {
  it('returns error health with ok auth for offline+healthy (connectivity, not auth)', () => {
    const c: ConnectorSummary = { ...BASE, liveness: 'offline', state: 'healthy' }
    const result = deriveConnectorDispatchInfo(c)
    expect(result.authStatus).toBe('ok')
    expect(result.health).toBe('error')
    expect(result.needsAttention).toBe(true)
    expect(result.authNote).toContain('offline')
  })

  it('returns degraded health with ok auth for stale liveness', () => {
    const c: ConnectorSummary = { ...BASE, liveness: 'stale', state: 'healthy' }
    const result = deriveConnectorDispatchInfo(c)
    expect(result.authStatus).toBe('ok')
    expect(result.health).toBe('degraded')
    expect(result.needsAttention).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// auth.status='unconfigured' must NOT trigger needs_reauth
// ---------------------------------------------------------------------------

describe('deriveConnectorDispatchInfo — auth.status unconfigured is not mapped', () => {
  it('does NOT show needs_reauth for a healthy connector with auth.status unconfigured', () => {
    // ConnectorDetail extends ConnectorSummary with auth: ConnectorAuthBlock | null
    // The derivation only reads ConnectorSummary fields (liveness, state, error_message)
    // and must ignore auth.status to avoid false-flagging healthy connectors.
    const c = {
      ...BASE,
      state: 'healthy',
      liveness: 'online',
      error_message: null,
      // auth block present (ConnectorDetail extension) — should be ignored
      auth: { status: 'unconfigured', type: 'oauth', note: null, expires_at: null, required_scopes_version: null, manifest_version: null, alt_surface: null },
      instance_id: null,
      registered_via: 'auto',
      checkpoint: null,
      counters: null,
      settings: null,
      scopes: null,
    } as ConnectorSummary
    const result = deriveConnectorDispatchInfo(c)
    expect(result.authStatus).toBe('ok')
    expect(result.health).toBe('ok')
    expect(result.needsAttention).toBe(false)
  })
})
