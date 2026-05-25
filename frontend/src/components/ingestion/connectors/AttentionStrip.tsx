/**
 * AttentionStrip — compact strip surfacing connectors that need operator attention.
 *
 * Appears above the connector roster table only when one or more connectors
 * have auth issues, degraded health, or are offline. Each entry is a clickable
 * link to the affected connector detail page. The count badge shows the total
 * number of connectors needing attention.
 *
 * Design: hairline top/bottom borders, no card chrome. Links use underline with
 * border color, not heavy color fills. Auth tone shown as mono uppercase label
 * in the appropriate foreground color.
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Connector with auth issue"
 * Reference: pr/overview/ingestion-redesign/ingestion-connectors-a.jsx §"Attention strip"
 */

import { Link } from 'react-router'
import type { ConnectorSummary } from '@/api/types'
import {
  deriveConnectorDispatchInfo,
  authStatusLabel,
  authStatusColor,
} from './connector-auth'

interface AttentionStripProps {
  connectors: ConnectorSummary[]
}

/**
 * Attention strip — renders only when at least one connector needs attention.
 *
 * Each item links to /ingestion/connectors/:type/:identity, which is the
 * connector detail route. The auth status label matches the one shown on the
 * connector row and detail page (consistent per spec AC2).
 */
export function AttentionStrip({ connectors }: AttentionStripProps) {
  const issues = connectors.filter((c) => deriveConnectorDispatchInfo(c).needsAttention)

  if (issues.length === 0) return null

  return (
    <div
      data-testid="attention-strip"
      className="py-3 border-t border-b border-border flex flex-wrap items-baseline gap-x-4 gap-y-2"
    >
      {/* Label + count badge */}
      <div className="flex items-baseline gap-2 shrink-0">
        <span className="font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground">
          needs attention
        </span>
        <span
          data-testid="attention-count"
          className="font-mono text-[10px] tabular-nums bg-[color:var(--red,oklch(0.62_0.20_25))] text-white rounded-full px-1.5 py-0.5 leading-none"
        >
          {issues.length}
        </span>
      </div>

      {/* Issue links */}
      <div className="flex flex-wrap gap-x-4 gap-y-1.5">
        {issues.map((c) => {
          const info = deriveConnectorDispatchInfo(c)
          const label = authStatusLabel(info.authStatus)
          const colorClass = authStatusColor(info.authStatus)
          const displayName = formatConnectorName(c)

          return (
            <Link
              key={`${c.connector_type}:${c.endpoint_identity}`}
              to={`/ingestion/connectors/${encodeURIComponent(c.connector_type)}/${encodeURIComponent(c.endpoint_identity)}`}
              data-testid={`attention-item-${c.connector_type}`}
              className="inline-flex items-baseline gap-1.5 text-foreground underline decoration-border underline-offset-[3px] hover:decoration-foreground transition-colors"
            >
              <span className="text-[13px] tracking-[-0.005em]">{displayName}</span>
              <span className={`font-mono text-[10px] tracking-[0.06em] uppercase ${colorClass}`}>
                {label}
              </span>
            </Link>
          )
        })}
      </div>
    </div>
  )
}

/** Produce a short display name for a connector. */
function formatConnectorName(c: ConnectorSummary): string {
  const type = c.connector_type.replace(/_/g, ' ')
  return c.endpoint_identity && c.endpoint_identity !== 'default'
    ? `${type} · ${c.endpoint_identity}`
    : type
}
