/**
 * DormantList — available-but-unconnected connectors section.
 *
 * Renders below the active roster with an "available · not connected" eyebrow.
 * Collapsed by default, expandable via a toggle. Each row shows the connector
 * type name, a serif italic description gloss, and a "connect →" link to /secrets
 * where the actual credential setup lives.
 *
 * Design: hairline-divided rows, serif italic description, no card chrome.
 * The dormant dot is muted (off-color) to distinguish from active connectors.
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Dormant connectors"
 * Reference: (ingestion dispatch redesign, graduated) ingestion-connectors-a.jsx §"Dormant section"
 */

import { useState } from 'react'
import { Link } from 'react-router'
import type { ConnectorProfile } from '@/api/types'

interface DormantListProps {
  profiles: ConnectorProfile[]
}

/**
 * Collapsible list of available-but-unconnected connector profiles.
 *
 * Each "connect →" link goes to /secrets?focus=u:<provider> where the
 * DirectionPassport credential page for that provider lives.
 * Collapsed by default; toggled by clicking the eyebrow row.
 */
export function DormantList({ profiles }: DormantListProps) {
  const [expanded, setExpanded] = useState(false)

  if (profiles.length === 0) return null

  return (
    <div data-testid="dormant-section" className="mt-9">
      {/* Eyebrow toggle header */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        data-testid="dormant-toggle"
        aria-expanded={expanded}
        className="flex items-baseline gap-3 mb-2.5 cursor-pointer group w-full text-left"
      >
        <span className="font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground group-hover:text-foreground transition-colors">
          available · not connected
        </span>
        <span className="font-mono text-[9.5px] text-muted-foreground/50">
          {profiles.length} connector{profiles.length !== 1 ? 's' : ''}
        </span>
        <span className="font-mono text-[11px] text-muted-foreground/50 ml-auto" aria-hidden="true">
          {expanded ? '−' : '+'}
        </span>
      </button>

      {expanded && (
        <div data-testid="dormant-list">
          {profiles.map((profile) => (
            <div
              key={profile.connector_type}
              data-testid={`dormant-row-${profile.connector_type}`}
              className="grid gap-x-4 py-3 border-b border-border/40 items-center"
              style={{ gridTemplateColumns: '14px 180px 1fr auto' }}
            >
              {/* Off dot */}
              <span
                className="w-1.5 h-1.5 rounded-full bg-muted-foreground/30"
                aria-hidden="true"
              />

              {/* Name + channel */}
              <div className="min-w-0">
                <div className="text-[13.5px] text-muted-foreground font-medium capitalize truncate">
                  {profile.display_name}
                </div>
                <div className="font-mono text-[10px] text-muted-foreground/50 capitalize">
                  {profile.channel}
                </div>
              </div>

              {/* Description gloss — serif italic */}
              <div className="font-serif italic text-[13px] text-muted-foreground/60 leading-snug min-w-0 truncate">
                {profile.display_name} connector: not yet configured.
              </div>

              {/* Connect action */}
              <Link
                to="/secrets"
                data-testid={`dormant-connect-${profile.connector_type}`}
                className="font-mono text-[11px] text-foreground border border-border px-2.5 py-1 hover:bg-foreground/5 transition-colors whitespace-nowrap"
              >
                connect →
              </Link>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
