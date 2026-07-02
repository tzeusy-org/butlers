/**
 * ChannelDefaultsBlock — per-channel default routing policy.
 *
 * The row list shows what happens to unmatched events for each channel,
 * derived from IngestionRule records with scope="channel_default". The
 * "edit" affordance opens an inline editor that reads/writes the actual
 * runtime policy document at GET/PATCH /api/ingestion/channel-defaults/:channel
 * (public.channel_defaults) — a distinct store from the rule rows above it.
 * There is no DELETE surface (the backend always 405s that verb).
 *
 * Labels use the runtime priority-action vocabulary (pass_through / block /
 * skip / metadata_only / low_priority_queue) rather than the retired DSL
 * verbs (drop / preserve / tier / route) — bu-4utdw.9.
 *
 * Mutation errors are visible (inline error state). Edits validate the
 * per-channel schema before mutation and do NOT optimistically hide the
 * previous policy on failure.
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Channel defaults"
 * Reference: (ingestion dispatch redesign, graduated) ingestion-filters.jsx §ChannelDefaultsBlock
 */

import { useState } from 'react'
import type { IngestionRule } from '@/api/types'
import type { ChannelDefaultPolicy, ChannelDefaultPriorityAction } from '@/api/index.ts'

// ---------------------------------------------------------------------------
// Runtime vocabulary
// ---------------------------------------------------------------------------

const PRIORITY_ACTIONS: { value: ChannelDefaultPriorityAction; label: string }[] = [
  { value: 'pass_through', label: 'pass through' },
  { value: 'block', label: 'block' },
  { value: 'skip', label: 'skip' },
  { value: 'metadata_only', label: 'metadata only' },
  { value: 'low_priority_queue', label: 'low priority queue' },
]

/** Map a stored action (runtime verb, or a retired DSL alias) to a display label. */
function policyLabel(action: string): string {
  const verb = action.toLowerCase().split(/[ :.]/)[0]
  switch (verb) {
    case 'pass_through':
    case 'preserve':
    case 'allow':
      return 'pass through'
    case 'block':
    case 'drop':
      return 'block'
    case 'skip':
      return 'skip'
    case 'metadata_only':
      return 'metadata only'
    case 'low_priority_queue':
    case 'tier':
      return 'low priority queue'
    case 'route':
    case 'route_to':
      return 'route → butler'
    default:
      return action
  }
}

function policyColor(action: string): string {
  const verb = action.toLowerCase().split(/[ :.]/)[0]
  if (verb === 'block' || verb === 'drop') return 'text-[var(--red)]'
  if (verb === 'low_priority_queue' || verb === 'tier') return 'text-[var(--amber)]'
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
// Inline editor
// ---------------------------------------------------------------------------

const labelCls =
  'block font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground/70 mb-1'
const inputCls =
  'w-full bg-transparent border border-border px-2 py-1 font-mono text-[11px] focus:outline-none focus:border-foreground'

export interface ChannelDefaultEditorState {
  loading: boolean
  /** True when the channel has no configured policy yet (GET 404). */
  notFound: boolean
  /** True on a real fetch failure (not a 404). */
  error: boolean
  policy: ChannelDefaultPolicy | null
}

function ChannelDefaultEditor({
  channel,
  state,
  saving,
  onSave,
  onCancel,
}: {
  channel: string
  state: ChannelDefaultEditorState
  saving: boolean
  onSave: (policy: ChannelDefaultPolicy) => void
  onCancel: () => void
}) {
  const initial = state.policy ?? { priority_action: 'pass_through' as ChannelDefaultPriorityAction }
  const [priorityAction, setPriorityAction] = useState<ChannelDefaultPriorityAction>(
    initial.priority_action,
  )
  const [maxAgeDays, setMaxAgeDays] = useState<string>(
    initial.max_age_days !== undefined ? String(initial.max_age_days) : '',
  )
  const [localError, setLocalError] = useState<string | null>(null)

  const isEmail = channel === 'email'

  function handleSave() {
    setLocalError(null)
    const policy: ChannelDefaultPolicy = { priority_action: priorityAction }
    if (isEmail && maxAgeDays.trim() !== '') {
      const parsed = Number(maxAgeDays)
      if (!Number.isFinite(parsed) || parsed <= 0 || !Number.isInteger(parsed)) {
        setLocalError('Max age must be a positive integer.')
        return
      }
      policy.max_age_days = parsed
    }
    onSave(policy)
  }

  if (state.loading) {
    return (
      <div
        className="col-span-3 py-2 font-mono text-[10.5px] text-muted-foreground"
        data-testid={`channel-default-editor-loading-${channel}`}
      >
        loading current policy…
      </div>
    )
  }

  if (state.error) {
    return (
      <div
        className="col-span-3 py-2 font-mono text-[10.5px] text-[var(--red)]"
        data-testid={`channel-default-editor-error-${channel}`}
      >
        Failed to load current policy for '{channel}'.{' '}
        <button
          type="button"
          className="underline underline-offset-2"
          onClick={onCancel}
        >
          cancel
        </button>
      </div>
    )
  }

  return (
    <div className="col-span-3 py-3" data-testid={`channel-default-editor-${channel}`}>
      {state.notFound && (
        <p
          className="mb-2 font-mono text-[9.5px] tracking-[0.04em] text-muted-foreground/60"
          data-testid={`channel-default-editor-notfound-${channel}`}
        >
          no policy configured yet — showing defaults
        </p>
      )}
      <div
        className="grid gap-3 items-end"
        style={{ gridTemplateColumns: isEmail ? '1fr 140px auto' : '1fr auto' }}
      >
        <label className="block">
          <span className={labelCls}>policy</span>
          <select
            className={inputCls}
            value={priorityAction}
            onChange={(e) => setPriorityAction(e.target.value as ChannelDefaultPriorityAction)}
            data-testid={`channel-default-editor-policy-${channel}`}
          >
            {PRIORITY_ACTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </label>

        {isEmail && (
          <label className="block">
            <span className={labelCls}>max age (days)</span>
            <input
              type="number"
              min={1}
              className={inputCls}
              value={maxAgeDays}
              onChange={(e) => setMaxAgeDays(e.target.value)}
              placeholder="e.g. 30"
              data-testid={`channel-default-editor-max-age-${channel}`}
            />
          </label>
        )}

        <div className="flex items-center gap-2">
          <button
            type="button"
            className="font-mono text-[10px] border border-foreground px-2.5 py-1 hover:bg-foreground hover:text-background transition-colors disabled:opacity-50"
            onClick={handleSave}
            disabled={saving}
            data-testid={`channel-default-editor-save-${channel}`}
          >
            {saving ? 'saving…' : 'save'}
          </button>
          <button
            type="button"
            className="font-mono text-[10px] text-muted-foreground hover:text-foreground"
            onClick={onCancel}
            disabled={saving}
            data-testid={`channel-default-editor-cancel-${channel}`}
          >
            cancel
          </button>
        </div>
      </div>
      {localError && (
        <p
          className="mt-2 font-mono text-[10.5px] text-[var(--red)]"
          data-testid={`channel-default-editor-local-error-${channel}`}
        >
          {localError}
        </p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// ChannelDefaultsBlock
// ---------------------------------------------------------------------------

export interface ChannelDefaultsBlockProps {
  rules: IngestionRule[]
  loaded: boolean
  error: boolean
  mutationError?: string | null
  /** Channel currently open for editing (null = none). */
  editingChannel?: string | null
  /** Fetch state for the channel currently open for editing. */
  editingState?: ChannelDefaultEditorState
  /** True while a save is in flight. */
  saving?: boolean
  onEdit?: (channel: string) => void
  onSaveEdit?: (channel: string, policy: ChannelDefaultPolicy) => void
  onCancelEdit?: () => void
}

export function ChannelDefaultsBlock({
  rules,
  loaded,
  error,
  mutationError,
  editingChannel = null,
  editingState,
  saving = false,
  onEdit,
  onSaveEdit,
  onCancelEdit,
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
        pass events through to routing; some are metadata-only or
        low-priority when the volume is too high to dispatch on by default.
      </p>

      {/* Mutation error */}
      {mutationError && (
        <div
          className="mt-3 font-mono text-[11px] text-[var(--red)] border border-[var(--red)]/30 px-3 py-2"
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
            const isEditing = editingChannel === channel
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

                {isEditing && editingState ? (
                  <ChannelDefaultEditor
                    channel={channel}
                    state={editingState}
                    saving={saving}
                    onSave={(policy) => onSaveEdit?.(channel, policy)}
                    onCancel={() => onCancelEdit?.()}
                  />
                ) : (
                  <>
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
                      onClick={() => onEdit?.(channel)}
                      aria-label={`Edit default for ${channel}`}
                      data-testid={`channel-default-edit-${channel}`}
                    >
                      edit
                    </button>
                  </>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
