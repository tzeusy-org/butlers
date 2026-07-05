/**
 * LiveIndicator — shell-level dot reflecting ACTUAL /api/events/stream socket
 * health (bu-86c4c.8, §JARVIS audit move 5).
 *
 * Renders one of three states, driven by useEventStream's connection status
 * (not by whether *any* data happens to be loaded):
 *   - connected    (status === "open")            — solid green
 *   - reconnecting (status === "reconnecting")     — amber
 *   - down         (status === "connecting" | "closed") — muted
 *
 * "connecting" (the very first attempt, before ever reaching open) reads as
 * "down" rather than "reconnecting" — there is nothing to reconnect *to*
 * yet, and the owner should not read a cold page load as a fleet problem.
 *
 * The dot never animates (bu-yykif, skeleton-pulse retirement) — color alone
 * carries the state, per the motion vocabulary in
 * openspec/specs/dashboard-design-language/spec.md § Motion Vocabulary.
 *
 * Renders on every viewport, including mobile (bu-qvnce.10) — it used to be
 * `hidden sm:inline-flex`, so the degraded-stream signal vanished entirely
 * below the `sm` breakpoint.
 */

import type { EventStreamStatus } from '@/hooks/use-event-stream'

export interface LiveIndicatorProps {
  status: EventStreamStatus
}

const STATE_META: Record<
  'connected' | 'reconnecting' | 'down',
  { label: string; dotClass: string; textClass: string }
> = {
  connected: {
    label: 'Live',
    dotClass: 'bg-[var(--green)]',
    textClass: 'text-muted-foreground',
  },
  reconnecting: {
    label: 'Reconnecting',
    dotClass: 'bg-[var(--amber)]',
    textClass: 'text-[var(--amber-text)]',
  },
  down: {
    label: 'Offline',
    dotClass: 'bg-muted-foreground/40',
    textClass: 'text-muted-foreground/70',
  },
}

function toDisplayState(status: EventStreamStatus): 'connected' | 'reconnecting' | 'down' {
  if (status === 'open') return 'connected'
  if (status === 'reconnecting') return 'reconnecting'
  return 'down' // "connecting" (first attempt) or "closed" (intentionally torn down)
}

export function LiveIndicator({ status }: LiveIndicatorProps) {
  const state = toDisplayState(status)
  const meta = STATE_META[state]

  return (
    <span
      className={`inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.08em] ${meta.textClass}`}
      title={`Fleet event stream: ${meta.label}`}
      data-testid="shell-live-indicator"
      data-live-state={state}
    >
      <span
        className={`size-1.5 rounded-full ${meta.dotClass}`}
        aria-hidden="true"
      />
      {meta.label}
    </span>
  )
}
