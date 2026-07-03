/**
 * LiveIndicator — shell-level dot reflecting ACTUAL /api/events/stream socket
 * health (bu-86c4c.8, §JARVIS audit move 5).
 *
 * Renders one of three states, driven by useEventStream's connection status
 * (not by whether *any* data happens to be loaded):
 *   - connected    (status === "open")            — solid green, pulsing
 *   - reconnecting (status === "reconnecting")     — amber, pulsing
 *   - down         (status === "connecting" | "closed") — muted, static
 *
 * "connecting" (the very first attempt, before ever reaching open) reads as
 * "down" rather than "reconnecting" — there is nothing to reconnect *to*
 * yet, and the owner should not read a cold page load as a fleet problem.
 */

import type { EventStreamStatus } from '@/hooks/use-event-stream'

export interface LiveIndicatorProps {
  status: EventStreamStatus
}

const STATE_META: Record<
  'connected' | 'reconnecting' | 'down',
  { label: string; dotClass: string; textClass: string; pulse: boolean }
> = {
  connected: {
    label: 'Live',
    dotClass: 'bg-green-500',
    textClass: 'text-muted-foreground',
    pulse: true,
  },
  reconnecting: {
    label: 'Reconnecting',
    dotClass: 'bg-amber-500',
    textClass: 'text-amber-600 dark:text-amber-400',
    pulse: true,
  },
  down: {
    label: 'Offline',
    dotClass: 'bg-muted-foreground/40',
    textClass: 'text-muted-foreground/70',
    pulse: false,
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
      className={`hidden items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.08em] sm:inline-flex ${meta.textClass}`}
      title={`Fleet event stream: ${meta.label}`}
      data-testid="shell-live-indicator"
      data-live-state={state}
    >
      <span
        className={`size-1.5 rounded-full ${meta.dotClass} ${meta.pulse ? 'animate-pulse' : ''}`}
        aria-hidden="true"
      />
      {meta.label}
    </span>
  )
}
