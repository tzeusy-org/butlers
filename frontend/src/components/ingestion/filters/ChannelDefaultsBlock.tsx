/**
 * ChannelDefaultsBlock — per-channel default routing policy.
 *
 * Shows what happens to unmatched events for each channel. Backed by
 * IngestionRule records with scope="channel_default" or derived from
 * the scope field of rules.
 *
 * Mutation errors are visible (inline error state). Edits validate the
 * per-channel schema before mutation and do NOT optimistically hide the
 * previous policy on failure.
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Channel defaults"
 * Reference: pr/overview/ingestion-redesign/ingestion-filters.jsx §ChannelDefaultsBlock
 */

import type { IngestionRule } from '@/api/types'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function policyLabel(action: string): string {
  const verb = action.toLowerCase().split(' ')[0]
  switch (verb) {
    case 'route': return 'route → butler'
    case 'drop': return 'drop'
    case 'preserve': return 'preserve'
    case 'tier': return 'tier'
    default: return action
  }
}

function policyColor(action: string): string {
  const verb = action.toLowerCase().split(' ')[0]
  if (verb === 'drop') return 'text-[color:var(--filter-red,oklch(0.62_0.20_25))]'
  if (verb === 'preserve') return 'text-[color:var(--filter-amber,oklch(0.72_0.12_70))]'
  return 'text-foreground'
}

/** Group rules by channel scope. */
function groupByChannel(rules: IngestionRule[]): Record<string, IngestionRule[]> {
  const result: Record<string, IngestionRule[]> = {}
  for (const rule of rules) {
    const channel = rule.scope ?? 'unknown'
    if (!result[channel]) result[channel] = []
    result[channel].push(rule)
  }
  return result
}

// ---------------------------------------------------------------------------
// ChannelDefaultsBlock
// ---------------------------------------------------------------------------

export interface ChannelDefaultsBlockProps {
  rules: IngestionRule[]
  loaded: boolean
  error: boolean
  mutationError?: string | null
  onEdit?: (id: string) => void
}

export function ChannelDefaultsBlock({
  rules,
  loaded,
  error,
  mutationError,
  onEdit,
}: ChannelDefaultsBlockProps) {
  const channelGroups = groupByChannel(rules)
  const channels = Object.keys(channelGroups).sort()

  return (
    <div data-testid="channel-defaults-block">
      {/* Header */}
      <div className="flex items-baseline gap-3 py-3 border-b border-border">
        <span className="font-mono text-[10px] tracking-[0.14em] uppercase text-muted-foreground">
          channel · defaults
        </span>
        <span className="font-mono text-[10px] text-muted-foreground/60">
          fallback policy per connector
        </span>
      </div>

      {/* Gloss */}
      <p className="font-serif text-sm text-muted-foreground leading-[1.5] mt-3.5 max-w-[46ch]">
        When no rule matches, this is what the channel does. Most channels
        route to a butler; some channels are preserve-only when the volume
        is too high to dispatch on by default.
      </p>

      {/* Mutation error */}
      {mutationError && (
        <div
          className="mt-3 font-mono text-[11px] text-[color:var(--filter-red,oklch(0.62_0.20_25))] border border-[color:var(--filter-red,oklch(0.62_0.20_25))]/30 px-3 py-2"
          data-testid="channel-defaults-mutation-error"
        >
          {mutationError}
        </div>
      )}

      {/* Loading */}
      {!loaded && (
        <div className="mt-4 space-y-2">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-9 bg-foreground/5 animate-pulse" />
          ))}
        </div>
      )}

      {/* Error */}
      {loaded && error && (
        <p
          className="font-serif italic text-sm text-muted-foreground py-5"
          data-testid="channel-defaults-error"
        >
          Channel defaults unavailable. Check connectivity and reload.
        </p>
      )}

      {/* Empty */}
      {loaded && !error && channels.length === 0 && (
        <p
          className="font-serif italic text-sm text-muted-foreground py-5"
          data-testid="channel-defaults-empty"
        >
          No channel defaults configured.
        </p>
      )}

      {/* Rows */}
      {loaded && !error && channels.length > 0 && (
        <div className="mt-4">
          {channels.map((channel) => {
            const channelRules = channelGroups[channel]
            const primary = channelRules[0]
            return (
              <div
                key={channel}
                className="grid gap-3.5 py-3 border-b border-border/50 items-baseline"
                style={{ gridTemplateColumns: '140px 180px 1fr 40px' }}
                data-testid={`channel-default-row-${channel}`}
              >
                {/* Channel name */}
                <div className="flex items-center gap-2">
                  <span className="font-mono text-[11.5px]">{channel}</span>
                </div>

                {/* Policy */}
                <span
                  className={`font-mono text-[11px] ${policyColor(primary.action)}`}
                  data-testid={`channel-default-policy-${channel}`}
                >
                  {policyLabel(primary.action)}
                </span>

                {/* Note */}
                <span className="font-serif italic text-[12.5px] text-muted-foreground leading-snug">
                  {primary.description ?? `${channelRules.length} rule${channelRules.length !== 1 ? 's' : ''}`}
                </span>

                {/* Edit */}
                <button
                  type="button"
                  className="font-mono text-[10px] text-muted-foreground hover:text-foreground underline underline-offset-2 decoration-muted-foreground/30"
                  onClick={() => onEdit?.(primary.id)}
                  aria-label={`Edit default for ${channel}`}
                  data-testid={`channel-default-edit-${channel}`}
                >
                  edit
                </button>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
