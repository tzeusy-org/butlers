/**
 * ArchivedConnectorsList — collapsed section of soft-archived connector identities.
 *
 * Renders below the active roster with an "archived · superseded" eyebrow.
 * Collapsed by default, expandable via a toggle. Each row shows the connector
 * type + endpoint identity and links to the connector detail route so the
 * identity's history (events, incidents) stays reachable.
 *
 * Archived identities are superseded/dead endpoints (bu-33dm2) that would
 * otherwise sit permanently "offline" in the active roster and drag the fleet
 * KPIs down. They are separated out here — excluded from the active roster's
 * attention strip and KPI band — but never deleted (ingestion history still
 * references them).
 *
 * Each row also offers a one-click "unarchive" action (bu-ep4ks.11 — the
 * safety envelope for consequential actions) that reuses the backend's
 * existing audit-logged unarchive endpoint. This closes the review queue's
 * missing UI path back: ArchiveCandidatesList's archive already had an
 * undo-window for the immediate mis-click, but there was no way to restore an
 * identity a human had archived deliberately, then reconsidered later.
 * Unarchiving is itself a restorative (not destructive) action, so it fires
 * directly on click rather than gaining its own confirm/undo layer.
 *
 * Spec: openspec/specs/dashboard-ingestion-dispatch-console/spec.md
 *       §"Connectors Roster" — Archived connectors section
 */

import { useState } from 'react'
import { Link } from 'react-router'
import type { ConnectorSummary } from '@/api/types'
import { useUnarchiveConnector } from '@/hooks/use-ingestion'

interface ArchivedConnectorsListProps {
  connectors: ConnectorSummary[]
}

/**
 * Collapsible list of soft-archived connector identities.
 *
 * Each row links to `/ingestion/connectors/<type>/<identity>` (the same detail
 * route as an active row) so the archived identity's history remains reachable.
 * Collapsed by default; toggled by clicking the eyebrow row.
 */
export function ArchivedConnectorsList({ connectors }: ArchivedConnectorsListProps) {
  const [expanded, setExpanded] = useState(false)
  const unarchive = useUnarchiveConnector()

  if (connectors.length === 0) return null

  const pendingKey =
    unarchive.isPending && unarchive.variables
      ? `${unarchive.variables.connectorType}:${unarchive.variables.endpointIdentity}`
      : null

  const sorted = [...connectors].sort(
    (a, b) =>
      a.connector_type.localeCompare(b.connector_type) ||
      a.endpoint_identity.localeCompare(b.endpoint_identity),
  )

  return (
    <div data-testid="archived-section" className="mt-9">
      {/* Eyebrow toggle header */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        data-testid="archived-toggle"
        aria-expanded={expanded}
        className="flex items-baseline gap-3 mb-2.5 cursor-pointer group w-full text-left"
      >
        <span className="font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground group-hover:text-foreground transition-colors">
          archived · superseded
        </span>
        <span className="font-mono text-[9.5px] text-muted-foreground/50">
          {connectors.length} identit{connectors.length !== 1 ? 'ies' : 'y'}
        </span>
        <span className="font-mono text-[11px] text-muted-foreground/50 ml-auto" aria-hidden="true">
          {expanded ? '−' : '+'}
        </span>
      </button>

      {expanded && (
        <div data-testid="archived-list">
          {sorted.map((c) => {
            const key = `${c.connector_type}:${c.endpoint_identity}`
            const detailPath = `/ingestion/connectors/${encodeURIComponent(
              c.connector_type,
            )}/${encodeURIComponent(c.endpoint_identity)}`
            const rowPending = pendingKey === key
            return (
              <div
                key={key}
                data-testid={`archived-row-${key}`}
                className="grid gap-x-4 py-3 border-b border-border/40 items-center"
                style={{ gridTemplateColumns: '14px 1fr auto auto' }}
              >
                {/* Off dot — archived identities read as dormant, not alarming */}
                <span
                  className="w-1.5 h-1.5 rounded-full bg-muted-foreground/30"
                  aria-hidden="true"
                />

                {/* Type + identity, linking to detail so history stays reachable */}
                <Link to={detailPath} className="min-w-0 group hover:bg-foreground/5 transition-colors">
                  <div className="text-[13.5px] text-muted-foreground font-medium capitalize truncate group-hover:underline">
                    {c.connector_type}
                  </div>
                  <div className="font-mono text-[10px] text-muted-foreground/50 truncate">
                    {c.endpoint_identity}
                  </div>
                </Link>

                {/* History affordance */}
                <Link
                  to={detailPath}
                  className="font-mono text-[11px] text-muted-foreground whitespace-nowrap hover:text-foreground"
                >
                  history →
                </Link>

                {/* Unarchive — restorative, so it fires directly (bu-ep4ks.11) */}
                <button
                  type="button"
                  data-testid={`unarchive-action-${key}`}
                  disabled={rowPending}
                  onClick={() =>
                    unarchive.mutate({
                      connectorType: c.connector_type,
                      endpointIdentity: c.endpoint_identity,
                    })
                  }
                  className="font-mono text-[11px] border border-border px-3 py-1.5 text-muted-foreground hover:border-foreground hover:text-foreground transition-colors disabled:opacity-40 disabled:cursor-not-allowed whitespace-nowrap"
                >
                  {rowPending ? 'unarchiving…' : 'unarchive'}
                </button>
              </div>
            )
          })}
        </div>
      )}

      {unarchive.isError && (
        <p
          data-testid="archived-connectors-error"
          className="font-mono text-[10.5px] text-[var(--red-text)] mt-2"
        >
          Unarchive failed. The identity is still archived. Try again.
        </p>
      )}
    </div>
  )
}
