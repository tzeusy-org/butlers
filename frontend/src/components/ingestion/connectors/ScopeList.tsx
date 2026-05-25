/**
 * ScopeList — OAuth scope list for connector detail.
 *
 * Consumes connector-oauth-scope-surface fields when available. Each scope
 * shows a status dot (green=granted, red=mismatch/denied), the mono scope
 * name, and a mono uppercase verdict. A trailing serif italic note appears
 * when reauth is needed.
 *
 * When the connector-oauth-scope-surface data is absent (null / undefined /
 * empty), renders an explicit "unavailable" state — NOT hidden, per spec AC3.
 * The unavailable state shows a serif italic sentence explaining that scope
 * data requires reauthorization or is not yet available.
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Scope list consumes the OAuth scope capability"
 * Reference: pr/overview/ingestion-redesign/ingestion-connector-detail.jsx §Scopes
 */

/** One OAuth scope entry from connector-oauth-scope-surface. */
export interface OAuthScope {
  name: string
  /** Whether the scope is currently granted. */
  granted: boolean
  /** Optional: 'mismatch' | 'denied' | 'granted' — overrides `granted` for display. */
  verdict?: string
  /** Optional explanatory note. */
  note?: string
}

interface ScopeListProps {
  /** OAuth scopes from connector-oauth-scope-surface. Null/undefined = unavailable. */
  scopes: OAuthScope[] | null | undefined
  /** Whether reauth is currently in play (shows a trailing italic note). */
  reauthRequired?: boolean
  /** Short connector name for the "unavailable" message. */
  connectorType: string
}

/**
 * OAuth scope list for connector detail right rail.
 *
 * Renders granted/denied status per scope with consistent color treatment.
 * When scopes are unavailable, renders an explicit unavailable state.
 */
export function ScopeList({ scopes, reauthRequired, connectorType }: ScopeListProps) {
  const hasScopes = Array.isArray(scopes) && scopes.length > 0

  return (
    <div>
      <div className="font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground mb-2.5">
        oauth scopes{hasScopes ? ` · ${scopes.length}` : ''}
      </div>

      {!hasScopes ? (
        /* Unavailable state — explicit, not hidden (spec AC3) */
        <div data-testid="scopes-unavailable">
          <p
            className="font-serif italic text-[13px] text-muted-foreground leading-[1.5]"
          >
            {reauthRequired
              ? `Scope data unavailable. Reauthorize ${connectorType} to surface current granted scopes.`
              : `OAuth scope data not yet available for ${connectorType}.`}
          </p>
        </div>
      ) : (
        <div data-testid="scopes-list">
          {scopes.map((scope, i) => {
            const isBroken = !scope.granted || scope.verdict === 'mismatch' || scope.verdict === 'denied'
            const verdict = scope.verdict ?? (scope.granted ? 'granted' : 'denied')

            const dotClass = isBroken
              ? 'bg-[color:var(--red,oklch(0.62_0.20_25))]'
              : 'bg-[color:var(--green,oklch(0.72_0.17_150))]'
            const nameClass = isBroken
              ? 'text-[color:var(--red,oklch(0.62_0.20_25))]'
              : 'text-foreground'

            return (
              <div
                key={i}
                className="grid gap-x-2.5 py-2.5 border-b border-border/50 items-baseline"
                style={{ gridTemplateColumns: '12px 1fr auto' }}
                data-testid={`scope-row-${scope.name}`}
              >
                <span
                  className={`w-1.5 h-1.5 rounded-full ${dotClass} mt-1 self-start`}
                  aria-hidden="true"
                />
                <span className={`font-mono text-[11.5px] ${nameClass} break-all`}>
                  {scope.name}
                </span>
                <span className="font-mono text-[10px] text-muted-foreground whitespace-nowrap">
                  {verdict}
                </span>
              </div>
            )
          })}

          {reauthRequired && (
            <p className="mt-2.5 font-serif italic text-[12.5px] text-muted-foreground leading-[1.5]">
              Reauthorising will request the updated scopes and resume ingestion.
            </p>
          )}
        </div>
      )}
    </div>
  )
}
