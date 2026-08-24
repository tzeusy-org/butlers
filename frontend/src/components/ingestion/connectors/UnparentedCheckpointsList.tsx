/**
 * UnparentedCheckpointsList — cursors whose owning connector cannot be resolved.
 *
 * A checkpoint row records which runtime instance it belongs to. When that
 * parent is missing — never recorded, or its registry row is gone — the cursor
 * has nowhere to nest. It is surfaced here rather than dropped: an orphaned
 * cursor is a real condition (a renamed identity, a deleted account, a
 * connector that checkpoints under a key no process registers), and silently
 * hiding it would trade the bug bu-6jv4m.11 fixed for a quieter one.
 *
 * Like every checkpoint record, these carry no liveness and no health. They are
 * storage state with an unresolved owner, presented as exactly that.
 *
 * Spec: openspec/changes/connector-runtime-instance-authority/specs/
 *       dashboard-ingestion-dispatch-console/spec.md
 */

import { useState } from 'react'
import { Time } from '@/components/ui/time'
import type { ConnectorCheckpointRecord } from '@/api/types'

interface UnparentedCheckpointsListProps {
  checkpoints: ConnectorCheckpointRecord[]
}

export function UnparentedCheckpointsList({ checkpoints }: UnparentedCheckpointsListProps) {
  const [expanded, setExpanded] = useState(false)

  if (checkpoints.length === 0) return null

  return (
    <div data-testid="unparented-checkpoints-section" className="mt-9">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        data-testid="unparented-checkpoints-toggle"
        aria-expanded={expanded}
        className="flex items-baseline gap-3 mb-2.5 cursor-pointer group w-full text-left"
      >
        <span className="font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground group-hover:text-foreground transition-colors">
          checkpoints · owner unresolved
        </span>
        <span className="font-mono text-[9.5px] text-muted-foreground/50">
          {checkpoints.length} record{checkpoints.length !== 1 ? 's' : ''}
        </span>
        <span className="font-mono text-[11px] text-muted-foreground/50 ml-auto" aria-hidden="true">
          {expanded ? '−' : '+'}
        </span>
      </button>

      {expanded && (
        <div data-testid="unparented-checkpoints-list">
          {checkpoints.map((cp) => (
            <div
              key={`${cp.connector_type}:${cp.endpoint_identity}`}
              data-testid={`unparented-checkpoint-${cp.endpoint_identity}`}
              className="grid gap-x-4 py-3 border-b border-border/40 items-center"
              style={{ gridTemplateColumns: '14px 1fr auto' }}
            >
              {/* Muted dot — storage state, never a health signal */}
              <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground/30" aria-hidden="true" />
              <div className="min-w-0">
                <div className="text-[13.5px] text-muted-foreground font-medium capitalize truncate">
                  {cp.connector_type.replace(/_/g, ' ')}
                </div>
                <div className="font-mono text-[10px] text-muted-foreground/50 truncate">
                  {cp.endpoint_identity}
                </div>
              </div>
              <span className="font-mono text-[10px] text-muted-foreground/60 whitespace-nowrap">
                {cp.checkpoint_updated_at ? (
                  <>
                    saved ·{' '}
                    <Time value={cp.checkpoint_updated_at} mode="relative" className="inline" />
                  </>
                ) : (
                  'saved · never'
                )}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
