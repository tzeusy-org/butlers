/**
 * ReauthCallout — prominent bordered banner for auth-broken or auth-expired connectors.
 *
 * Appears in the header band of the connector detail page when auth status
 * requires operator action:
 *
 * - 'needs_reauth'          → error dot + recovery action when supported
 * - 'expiring'              → degraded dot + static status
 * - 'needs_primary_account' → degraded dot + static guidance
 *
 * Renders null when authStatus is 'ok' or 'unconfigured'.
 *
 * Design: a neutral hairline border and copy support one registry-resolved StateDot.
 *
 * Spec: openspec/specs/dashboard-ingestion-dispatch-console/spec.md
 *       §"Reauth callout follows connector auth state"
 * Reference: docs/redesigns/ingestion-connector-detail.jsx §"reauth call-to-action"
 */

import type { ConnectorRecovery, DerivedAuthStatus } from './connector-auth'
import { StateDot } from '@/components/ui/StateDot'

interface ReauthCalloutProps {
  authStatus: DerivedAuthStatus
  /** Short human-readable reason for the auth issue. */
  authNote: string
  /** Connector type — e.g. "spotify" — for display in the callout text. */
  connectorType: string
  /** Called when the user clicks a supported needs-reauth recovery action. */
  onReauth?: () => void
  /** Explicit connector recovery route; unsupported routes render as information. */
  recovery?: ConnectorRecovery
}

/**
 * Bordered recovery callout for connector detail.
 *
 * Renders null when authStatus is 'ok' or 'unconfigured'.
 * Only `needs_reauth` may surface an interactive recovery action. Unsupported
 * recovery is explicitly explained without a link or network request.
 */
export function ReauthCallout({
  authStatus,
  authNote,
  connectorType,
  onReauth,
  recovery,
}: ReauthCalloutProps) {
  if (authStatus === 'ok' || authStatus === 'unconfigured') return null

  const isPrimaryAccount = authStatus === 'needs_primary_account'
  const isError = authStatus === 'needs_reauth'
  const isUnsupportedRecovery = isError && recovery?.kind === 'unsupported'

  const statusLabel = isUnsupportedRecovery
    ? 'recovery unavailable'
    : isError
      ? 'reauth required'
    : isPrimaryAccount
      ? 'no primary account'
      : 'expiring soon'

  const explanation = isUnsupportedRecovery
    ? recovery.reason
    : authNote || (
        isPrimaryAccount
          ? `${connectorType} has no primary account. Set one in Secrets to resume ingestion.`
          : `${connectorType} requires reauthorization to continue ingesting events.`
      )

  return (
    <div
      data-testid="reauth-callout"
      className="border border-border px-5 py-4 min-w-[280px] max-w-sm"
    >
      {/* Status label */}
      <div className="flex items-center gap-2">
        <StateDot state={isError ? 'error' : 'degraded'} size={6} />
        <span
          className="font-mono text-[10px] tracking-[0.10em] uppercase text-muted-foreground"
        >
          {statusLabel}
        </span>
      </div>

      {/* Explanation */}
      <p className="mt-2.5 font-serif text-[14px] leading-[1.45] text-foreground">
        {explanation}
      </p>

      {/* Actions */}
      <div className="mt-3.5 flex gap-2">
        {isError && !isUnsupportedRecovery && onReauth && (
          <button
            type="button"
            onClick={onReauth}
            data-testid="reauth-button"
            className="font-mono text-[11px] border border-foreground px-3 py-1.5 hover:bg-foreground hover:text-background transition-colors"
          >
            {recovery?.kind === 'passport' && recovery.action === 'pair'
              ? 'open pairing'
              : 're-authorize'}
          </button>
        )}
      </div>
    </div>
  )
}
