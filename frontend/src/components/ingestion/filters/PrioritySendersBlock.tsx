/**
 * PrioritySendersBlock — first-class priority senders data surface.
 *
 * Backed by real API data (IngestionRule with rule_type="priority_sender" or
 * similar). The section header shows the count; each row shows contact name,
 * handle, channel, target butler, added timestamp, last-seen.
 *
 * Mutations (add/remove) surface errors visibly via inline error state.
 * Errors are never silently swallowed.
 *
 * NOTE: The backend does not yet expose a dedicated priority-senders endpoint.
 * We use IngestionRule records with action="route.priority" or
 * rule_type="priority_sender" as a proxy. If the data is unavailable, we
 * render an explicit "unavailable" state rather than an empty list.
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Priority senders"
 * Reference: pr/overview/ingestion-redesign/ingestion-filters.jsx §PrioritySendersBlock
 */

import type { IngestionRule } from '@/api/types'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit' })
  } catch {
    return iso.slice(0, 10)
  }
}

/** Extract a human-readable sender identity from rule condition. */
function senderFromCondition(condition: Record<string, unknown>): string {
  if (typeof condition.sender_address === 'string') return condition.sender_address
  if (typeof condition.source_channel === 'string') return condition.source_channel
  return JSON.stringify(condition).slice(0, 40)
}

// ---------------------------------------------------------------------------
// PrioritySendersBlock
// ---------------------------------------------------------------------------

export interface PrioritySendersBlockProps {
  /** Rules identified as priority-sender rules. */
  rules: IngestionRule[]
  /** True when the data fetch has completed (regardless of result). */
  loaded: boolean
  /** Whether the fetch encountered an error. */
  error: boolean
  /** Mutation error from add/remove action. */
  mutationError?: string | null
  onAdd?: () => void
  onRemove?: (id: string) => void
}

export function PrioritySendersBlock({
  rules,
  loaded,
  error,
  mutationError,
  onAdd,
  onRemove,
}: PrioritySendersBlockProps) {
  return (
    <div data-testid="priority-senders-block">
      {/* Section header */}
      <div className="flex items-baseline gap-3 py-3 border-b border-border">
        <span className="font-mono text-[10px] tracking-[0.14em] uppercase text-muted-foreground">
          priority · senders
        </span>
        <span className="font-mono text-[10px] text-muted-foreground/60">
          {loaded ? `${rules.length} contacts · bypass batching` : '…'}
        </span>
        <span className="ml-auto" />
        <button
          type="button"
          className="font-mono text-[10px] border border-foreground/30 px-2.5 py-1 hover:bg-foreground/5 transition-colors"
          onClick={onAdd}
          data-testid="priority-senders-add"
        >
          + add
        </button>
      </div>

      {/* Gloss */}
      <p className="font-serif text-sm text-muted-foreground leading-[1.5] mt-3.5 max-w-[60ch]">
        Messages from these contacts skip the default tier. The system pings
        the named butler immediately rather than waiting for the next batch.
        This is the only place a person is first-class in filtering.
      </p>

      {/* Mutation error */}
      {mutationError && (
        <div
          className="mt-3 font-mono text-[11px] text-[color:var(--filter-red,oklch(0.62_0.20_25))] border border-[color:var(--filter-red,oklch(0.62_0.20_25))]/30 px-3 py-2"
          data-testid="priority-senders-mutation-error"
        >
          {mutationError}
        </div>
      )}

      {/* Loading skeleton */}
      {!loaded && (
        <div className="mt-4 space-y-2">
          {[1, 2].map((i) => (
            <div key={i} className="h-10 bg-foreground/5 animate-pulse" />
          ))}
        </div>
      )}

      {/* Error state */}
      {loaded && error && (
        <p
          className="font-serif italic text-sm text-muted-foreground py-5"
          data-testid="priority-senders-error"
        >
          Priority senders unavailable. Check connectivity and reload.
        </p>
      )}

      {/* Empty state */}
      {loaded && !error && rules.length === 0 && (
        <p
          className="font-serif italic text-sm text-muted-foreground py-5"
          data-testid="priority-senders-empty"
        >
          No priority senders configured.
        </p>
      )}

      {/* Table */}
      {loaded && !error && rules.length > 0 && (
        <div className="mt-4">
          {/* Column headers */}
          <div
            className="grid gap-3.5 py-2.5 border-b border-border/50 font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground/70"
            style={{ gridTemplateColumns: '1.4fr 1fr 90px 90px 24px' }}
          >
            <span>name · handle</span>
            <span>channel · routes to</span>
            <span>added</span>
            <span className="text-right">last seen</span>
            <span />
          </div>

          {rules.map((rule) => (
            <div
              key={rule.id}
              className="grid gap-3.5 py-3 border-b border-border/50 items-baseline"
              style={{ gridTemplateColumns: '1.4fr 1fr 90px 90px 24px' }}
              data-testid={`priority-sender-row-${rule.id}`}
            >
              {/* Name / handle */}
              <div className="min-w-0">
                <div className="text-[13.5px] font-medium tracking-[-0.005em] truncate">
                  {rule.name ?? senderFromCondition(rule.condition)}
                </div>
                <span className="block font-mono text-[10px] text-muted-foreground mt-0.5 truncate">
                  {senderFromCondition(rule.condition)}
                </span>
              </div>

              {/* Channel + butler */}
              <div className="font-mono text-[10.5px] text-muted-foreground truncate">
                {typeof rule.condition.source_channel === 'string'
                  ? rule.condition.source_channel
                  : rule.scope}
              </div>

              {/* Added */}
              <span className="font-mono text-[10px] text-muted-foreground">
                {formatDate(rule.created_at)}
              </span>

              {/* Last seen */}
              <span className="font-mono text-[10px] text-muted-foreground text-right">
                {formatDate(rule.updated_at)}
              </span>

              {/* Remove */}
              <button
                type="button"
                className="font-mono text-[12px] text-muted-foreground hover:text-[color:var(--filter-red,oklch(0.62_0.20_25))]"
                onClick={() => onRemove?.(rule.id)}
                aria-label={`Remove priority sender ${rule.name ?? rule.id}`}
                data-testid={`priority-sender-remove-${rule.id}`}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
