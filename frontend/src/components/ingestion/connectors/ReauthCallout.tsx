/**
 * ReauthCallout — prominent bordered banner for auth-broken or auth-expired connectors.
 *
 * Appears in the header band of the connector detail page when auth status
 * requires operator action:
 *
 * - 'needs_reauth'          → red border + recovery action when supported
 * - 'expiring'              → amber border + static status
 * - 'needs_primary_account' → amber border + static guidance
 *
 * Renders null when authStatus is 'ok' or 'unconfigured'.
 *
 * Design: no card chrome — just a hairline border in the relevant state color.
 * State color used as border and foreground only, never as background fill.
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Reauth callout follows connector auth state"
 * Reference: docs/redesigns/ingestion-connector-detail.jsx §"reauth call-to-action"
 */

import type { ConnectorRecovery, DerivedAuthStatus } from './connector-auth'

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

  // Color: red for hard errors, amber for warnings (expiring / no primary account)
  const isRed = isError
  const borderClass = isRed
    ? 'border-[color:var(--red,oklch(0.62_0.20_25))]'
    : 'border-[color:var(--amber,oklch(0.72_0.12_70))]'
  const dotColorClass = isRed
    ? 'bg-[color:var(--red,oklch(0.62_0.20_25))]'
    : 'bg-[color:var(--amber,oklch(0.72_0.12_70))]'
  const textColorClass = isRed
    ? 'text-[color:var(--red,oklch(0.62_0.20_25))]'
    : 'text-[color:var(--amber,oklch(0.72_0.12_70))]'

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
      className={`border ${borderClass} px-5 py-4 min-w-[280px] max-w-sm`}
    >
      {/* Status label */}
      <div className="flex items-center gap-2">
        <span className={`w-1.5 h-1.5 rounded-full ${dotColorClass}`} aria-hidden="true" />
        <span
          className={`font-mono text-[10px] tracking-[0.10em] uppercase ${textColorClass}`}
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
