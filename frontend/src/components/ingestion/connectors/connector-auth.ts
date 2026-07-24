/**
 * Connector auth/health helpers.
 *
 * Derives the Dispatch-language auth/health status from the real backend
 * ConnectorSummary/ConnectorDetail shape (liveness + state + error_message).
 *
 * Auth-error signal comes from two places:
 *   1. `state === 'error'` — hard error; always auth/config issue.
 *   2. `state === 'degraded'` + auth-flavored error_message:
 *        - error_message contains "api_forbidden"    → needs_reauth
 *        - error_message contains "no_primary_account" → needs_primary_account
 *      Other degraded (heartbeat lag, transient, etc.) stays `ok` health-wise
 *      but is flagged as `needsAttention`.
 *
 * NOTE: `auth.status === 'unconfigured'` is NOT mapped to needs_reauth.
 * Live data shows unconfigured for ALL connectors (incl. healthy gmail) because
 * the backend's observed_scopes probe is not yet wired. Mapping it would
 * false-flag every connector. Key off error_message + state instead.
 *
 * Mappings:
 * - liveness "online"  + state "healthy"                          → auth "ok",                  health "ok"
 * - liveness "online"  + state "degraded" (no auth error_message) → auth "ok",                  health "degraded"
 * - liveness "online"  + state "degraded" + "api_forbidden"       → auth "needs_reauth",         health "degraded"
 * - liveness "online"  + state "degraded" + "no_primary_account"  → auth "needs_primary_account",health "degraded"
 * - liveness "stale"   + state "healthy"                          → auth "ok",                  health "degraded"
 * - liveness "offline" + state "healthy"                          → auth "ok",                  health "error" (connectivity)
 * - liveness *         + state "error"                            → auth "needs_reauth",         health "error"
 * - liveness "online"  + state "healthy" + any devices[].stale     → auth "ok",                  health "degraded"
 *   (bu-e16to: a multi-device connector_type's shared heartbeat only reflects ONE
 *   device, so a stale sibling device must still surface as needing attention.)
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Reauth callout follows connector auth state"
 */

import { getProviderOAuthStartUrl } from '@/api/client'
import type { ConnectorSummary } from '@/api/types'

/** Derived auth status — maps onto the Dispatch design language. */
export type DerivedAuthStatus =
  | 'ok'
  | 'expiring'
  | 'needs_reauth'
  | 'needs_primary_account'
  | 'unconfigured'

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
 * Derive Dispatch-layer auth and health from a ConnectorSummary (or ConnectorDetail).
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

  // Degraded state but online: check error_message for auth-flavored signals
  if (c.state === 'degraded') {
    const msg = c.error_message ?? ''
    if (msg.includes('no_primary_account')) {
      return {
        authStatus: 'needs_primary_account',
        health: 'degraded',
        needsAttention: true,
        authNote: 'no primary account · set a primary account to continue',
      }
    }
    if (msg.includes('api_forbidden')) {
      return {
        authStatus: 'needs_reauth',
        health: 'degraded',
        needsAttention: true,
        authNote: truncate(msg, 48),
      }
    }
    // Other degraded reasons (transient, heartbeat lag, etc.) — not an auth issue
    return {
      authStatus: 'ok',
      health: 'degraded',
      needsAttention: true,
      authNote: msg ? truncate(msg, 48) : 'degraded',
    }
  }

  // Healthy and online, but check for stale sibling devices (bu-e16to). The
  // connector-level heartbeat only ever reflects ONE device on a multi-device
  // connector_type (e.g. OwnTracks), so a stale device here is the only signal
  // that a household device has gone silent — it must not be swallowed by an
  // otherwise-healthy connector-level verdict.
  const staleDevices = c.devices?.filter((d) => d.stale) ?? []
  if (staleDevices.length > 0) {
    return {
      authStatus: 'ok',
      health: 'degraded',
      needsAttention: true,
      authNote:
        staleDevices.length === 1
          ? `device silent · ${staleDevices[0].sender_identity}`
          : `${staleDevices.length} devices silent`,
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
    case 'needs_primary_account':
      return 'no primary'
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
    case 'needs_primary_account':
      return 'text-[color:var(--amber,oklch(0.72_0.12_70))]'
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

/** Maps health to a Tailwind foreground text color — same palette as {@link healthDotColor}. */
export function healthTextColor(health: DerivedHealth): string {
  switch (health) {
    case 'ok':
      return 'text-[color:var(--green,oklch(0.72_0.17_150))]'
    case 'degraded':
      return 'text-[color:var(--amber,oklch(0.72_0.12_70))]'
    case 'error':
      return 'text-[color:var(--red,oklch(0.62_0.20_25))]'
    case 'off':
      return 'text-muted-foreground/40'
  }
}

/**
 * Derive the single roster verdict word, folding the derived health axis
 * (degraded/error) onto the raw liveness read (online/stale/offline).
 *
 * Replaces the old two-dot display (a bare liveness dot stacked on a bare
 * state dot) that required memorizing which axis was which. `health` is
 * `ok` only when liveness is `online` (see {@link deriveConnectorDispatchInfo}),
 * so `info.health === 'ok'` is reported as "online" without needing the raw
 * liveness value.
 */
export function healthVerdictWord(c: ConnectorSummary, info: ConnectorDispatchInfo): string {
  if (info.health === 'error') return c.liveness === 'offline' ? 'offline' : 'error'
  if (info.health === 'degraded') return c.liveness === 'stale' ? 'stale' : 'degraded'
  if (info.health === 'off') return 'offline'
  return 'online'
}

/** Context carried into a recovery action from an ingestion connector surface. */
export interface ConnectorRecoveryOptions {
  /** Route fragment to restore after an OAuth callback. */
  connectorDetailPath?: string
  /** Ask the OAuth provider to re-prompt for consent when recovery requires it. */
  forceConsent?: boolean
}

/** A recovery route chosen from an explicit, supported connector capability. */
export type ConnectorRecovery =
  | { kind: 'oauth'; href: string }
  | { kind: 'passport'; to: '/secrets?focus=u:whatsapp' }
  | { kind: 'unsupported'; reason: string }

const GOOGLE_OAUTH_CONNECTOR_TYPES = new Set([
  'google',
  'gmail',
  'google_calendar',
  'google_drive',
  'google_health',
])

const WHATSAPP_CONNECTOR_TYPES = new Set(['whatsapp', 'whatsapp_user_client'])

const UNSUPPORTED_RECOVERY_REASON =
  'Recovery is not available because this connector has no supported OAuth or Passport flow in the dashboard.'

/**
 * Resolve a connector's recovery route from known capabilities.
 *
 * This is deliberately an allowlist. Connector type is registry data, not an
 * OAuth provider identifier: unknown types fail closed instead of becoming a
 * fabricated `/api/oauth/<connector-type>/start` URL. Every OAuth route keeps
 * the ingestion origin, while Google Health additionally keeps its restricted
 * Health scope set.
 */
export function resolveConnectorRecovery(
  connectorType: string,
  options: ConnectorRecoveryOptions = {},
): ConnectorRecovery {
  if (GOOGLE_OAUTH_CONNECTOR_TYPES.has(connectorType)) {
    return {
      kind: 'oauth',
      href: getProviderOAuthStartUrl('google', {
        pageOfOrigin: 'ingestion',
        connectorDetailPath: options.connectorDetailPath,
        forceConsent: options.forceConsent,
        scopeSet: connectorType === 'google_health' ? 'health' : undefined,
      }),
    }
  }

  if (connectorType === 'spotify') {
    return {
      kind: 'oauth',
      href: getProviderOAuthStartUrl('spotify', {
        pageOfOrigin: 'ingestion',
        connectorDetailPath: options.connectorDetailPath,
        forceConsent: options.forceConsent,
      }),
    }
  }

  if (WHATSAPP_CONNECTOR_TYPES.has(connectorType)) {
    return { kind: 'passport', to: '/secrets?focus=u:whatsapp' }
  }

  return { kind: 'unsupported', reason: UNSUPPORTED_RECOVERY_REASON }
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + '…' : s
}
