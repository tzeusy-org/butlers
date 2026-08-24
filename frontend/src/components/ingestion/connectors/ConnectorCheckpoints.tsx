/**
 * ConnectorCheckpoints — persisted cursors listed under their parent connector.
 *
 * connector_registry has two producers: the heartbeat tool, which registers an
 * executable process, and cursor_store, which persists one restart-safe cursor
 * per stream. Google Health keeps a cursor per account AND per resource, so its
 * activity/sleep/HRV cursors each had their own registry row — rows that never
 * heartbeat. Before bu-6jv4m.11 the roster listed every one of them as its own
 * OFFLINE connector beside the single genuinely-online account, and they pulled
 * on fleet attention while they were at it.
 *
 * They belong here instead: nested under the runtime instance that owns them,
 * labelled by the stream they track, and deliberately carrying NO status. A
 * cursor has no process, so it has no liveness to report — that authority is
 * the parent row's alone. This list exists so the history stays inspectable,
 * not so it can be diagnosed.
 *
 * Spec: openspec/changes/connector-runtime-instance-authority/specs/
 *       dashboard-ingestion-dispatch-console/spec.md
 */

import { Time } from '@/components/ui/time'
import type { ConnectorCheckpointRecord } from '@/api/types'

interface ConnectorCheckpointsProps {
  checkpoints: ConnectorCheckpointRecord[]
  /** Owning connector_type — namespaces the test ids within a roster row. */
  connectorType: string
}

export function ConnectorCheckpoints({ checkpoints, connectorType }: ConnectorCheckpointsProps) {
  if (checkpoints.length === 0) return null

  return (
    <div
      className="flex flex-wrap items-center gap-x-4 gap-y-1 pb-1"
      style={{ gridColumn: '2 / -1' }}
      data-testid={`connector-checkpoints-${connectorType}`}
    >
      <span className="font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground/60">
        checkpoints
      </span>
      {checkpoints.map((cp) => (
        <div
          key={cp.endpoint_identity}
          className="flex items-baseline gap-1.5"
          data-testid={`connector-checkpoint-${cp.endpoint_identity}`}
        >
          <span className="font-mono text-[10px] tracking-[0.02em] text-muted-foreground/80">
            {cp.label}
          </span>
          {cp.checkpoint_updated_at ? (
            <span className="font-mono text-[10px] text-muted-foreground/60">
              saved · <Time value={cp.checkpoint_updated_at} mode="relative" className="inline" />
            </span>
          ) : (
            <span className="font-mono text-[10px] text-muted-foreground/40">saved · never</span>
          )}
        </div>
      ))}
    </div>
  )
}
