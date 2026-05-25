/**
 * Connector auth/health helpers.
 *
 * Derives the Dispatch-language auth/health status from the real backend
 * ConnectorSummary shape (liveness + state + error_message). The backend
 * does not surface a dedicated `auth.status` field, so we infer it:
 *
 * - liveness "online"  + state "healthy"   → auth "ok",    health "ok"
 * - liveness "online"  + state "degraded"  → auth "ok",    health "degraded"
 * - liveness "stale"   + state "healthy"   → auth "ok",    health "degraded"
 * - liveness "offline" + state "healthy"   → auth "ok",    health "error" (connectivity issue)
 * - liveness *         + state "error"     → auth "needs_reauth", health "error"
 *                       error_message present → treat as auth / config issue
 *
 * These mappings are intentionally conservative: we only show "needs reauth"
 * when the backend state indicates an error. We do NOT fabricate auth errors.
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Connector with auth issue"
 */

import type { ConnectorSummary } from '@/api/types'

/** Derived auth status — maps onto the Dispatch design language. */
export type DerivedAuthStatus = 'ok' | 'expiring' | 'needs_reauth' | 'unconfigured'

/** Derived health — maps to the health dot on the roster row. */
export type DerivedHealth = 'ok' | 'degraded' | 'error' | 'off'

/** All derived Dispatch-model fields derived from a ConnectorSummary. */
export interface ConnectorDispatchInfo {
  authStatus: DerivedAuthStatus
  health: DerivedHealth
  /** Whether this connector needs operator attention. */
  needsAttention: boolean
  /** Short human-readable note for the attention strip / auth pill. */
  authNote: string
}

/**
 * Derive Dispatch-layer auth and health from a ConnectorSummary.
 *
 * This is the single source of truth for auth status across the roster,
 * attention strip, and connector detail. All three must read from this
 * function so the status label and color are consistent.
 */
export function deriveConnectorDispatchInfo(c: ConnectorSummary): ConnectorDispatchInfo {
  // Explicit error state takes priority regardless of liveness
  if (c.state === 'error') {
    const authNote = c.error_message
      ? truncate(c.error_message, 48)
      : 'connector error · check logs'
    return {
      authStatus: 'needs_reauth',
      health: 'error',
      needsAttention: true,
      authNote,
    }
  }

  // Offline + healthy state: connectivity issue (not auth)
  if (c.liveness === 'offline') {
    return {
      authStatus: 'ok',
      health: 'error',
      needsAttention: true,
      authNote: 'connector offline · check connectivity',
    }
  }

  // Stale: heartbeat missed but not failed
  if (c.liveness === 'stale') {
    return {
      authStatus: 'ok',
      health: 'degraded',
      needsAttention: true,
      authNote: 'heartbeat stale · check connector',
    }
  }

  // Degraded state but online
  if (c.state === 'degraded') {
    return {
      authStatus: 'ok',
      health: 'degraded',
      needsAttention: true,
      authNote: c.error_message ? truncate(c.error_message, 48) : 'degraded',
    }
  }

  // Healthy and online
  return {
    authStatus: 'ok',
    health: 'ok',
    needsAttention: false,
    authNote: 'oauth · authorized',
  }
}

/** Maps auth status to a display label (mono uppercase). */
export function authStatusLabel(status: DerivedAuthStatus): string {
  switch (status) {
    case 'ok':
      return 'authorized'
    case 'expiring':
      return 'expiring'
    case 'needs_reauth':
      return 'reauth'
    case 'unconfigured':
      return 'not set'
  }
}

/** Maps auth status to a Tailwind color token. */
export function authStatusColor(status: DerivedAuthStatus): string {
  switch (status) {
    case 'ok':
      return 'text-[color:var(--green,oklch(0.72_0.17_150))]'
    case 'expiring':
      return 'text-[color:var(--amber,oklch(0.72_0.12_70))]'
    case 'needs_reauth':
      return 'text-[color:var(--red,oklch(0.62_0.20_25))]'
    case 'unconfigured':
      return 'text-muted-foreground'
  }
}

/** Maps health to a Tailwind background color for the health dot. */
export function healthDotColor(health: DerivedHealth): string {
  switch (health) {
    case 'ok':
      return 'bg-[color:var(--green,oklch(0.72_0.17_150))]'
    case 'degraded':
      return 'bg-[color:var(--amber,oklch(0.72_0.12_70))]'
    case 'error':
      return 'bg-[color:var(--red,oklch(0.62_0.20_25))]'
    case 'off':
      return 'bg-muted-foreground/40'
  }
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + '…' : s
}
