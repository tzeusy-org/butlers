/**
 * ReauthCallout — prominent bordered banner for auth-broken or auth-expired connectors.
 *
 * Appears in the header band of the connector detail page when
 * auth status is 'needs_reauth' or 'expiring'. Bordered in --red for
 * needs_reauth, --amber for expiring.
 *
 * Contains: status dot + mono uppercase label, serif explanation text,
 * and a "re-authorize" action pill. When the connector does not need
 * reauthorization, renders null.
 *
 * Design: no card chrome — just a hairline border in the relevant state color.
 * State color used as border and foreground only, never as background fill.
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Reauth callout follows connector auth state"
 * Reference: docs/redesigns/ingestion-connector-detail.jsx §"reauth call-to-action"
 */

import type { DerivedAuthStatus } from './connector-auth'

interface ReauthCalloutProps {
  authStatus: DerivedAuthStatus
  /** Short human-readable reason for the auth issue. */
  authNote: string
  /** Connector type — e.g. "spotify" — for display in the callout text. */
  connectorType: string
  /** Called when the user clicks re-authorize. */
  onReauth?: () => void
}

/**
 * Bordered reauth callout for connector detail.
 *
 * Renders null when authStatus is 'ok' or 'unconfigured'.
 * For 'needs_reauth': red border + "reauth required" label.
 * For 'expiring': amber border + "expiring soon" label.
 */
export function ReauthCallout({ authStatus, authNote, connectorType, onReauth }: ReauthCalloutProps) {
  if (authStatus === 'ok' || authStatus === 'unconfigured') return null

  const isError = authStatus === 'needs_reauth'

  const borderClass = isError
    ? 'border-[color:var(--red,oklch(0.62_0.20_25))]'
    : 'border-[color:var(--amber,oklch(0.72_0.12_70))]'

  const dotColorClass = isError
    ? 'bg-[color:var(--red,oklch(0.62_0.20_25))]'
    : 'bg-[color:var(--amber,oklch(0.72_0.12_70))]'

  const textColorClass = isError
    ? 'text-[color:var(--red,oklch(0.62_0.20_25))]'
    : 'text-[color:var(--amber,oklch(0.72_0.12_70))]'

  const statusLabel = isError ? 'reauth required' : 'expiring soon'

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
        {authNote || `${connectorType} requires reauthorization to continue ingesting events.`}
      </p>

      {/* Actions */}
      <div className="mt-3.5 flex gap-2">
        {onReauth && (
          <button
            type="button"
            onClick={onReauth}
            data-testid="reauth-button"
            className="font-mono text-[11px] border border-foreground px-3 py-1.5 hover:bg-foreground hover:text-background transition-colors"
          >
            re-authorize
          </button>
        )}
      </div>
    </div>
  )
}
