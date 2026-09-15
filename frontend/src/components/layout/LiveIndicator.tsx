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
 *
 * Client-link honesty (bu-8cdl1.13): `status` above is fleet/backend health
 * and says nothing about whether THIS BROWSER has a network at all. A
 * dropped LTE link closes the socket exactly like a real fleet outage does,
 * so without `clientLink` this component would render "Offline" -- read by
 * an owner as "the fleet is down" -- when the true fact is "you lost your
 * own connection". When `clientLink` is not "online" it takes over the
 * entire display, naming the client (not the fleet) as the thing that
 * dropped, with a visible (not hover-only, per the Viewport and Modality
 * Contract's no-hover-only-facts rule -- this indicator is reached from
 * phone approval deep links) data-age stamp from `lastEventAt`.
 */

import type { EventBusHealth, EventStreamStatus } from '@/hooks/use-event-stream'
import type { ClientLinkStatus } from '@/hooks/use-client-link'
import { formatDurationCompact } from '@/lib/format-duration'

export interface LiveIndicatorProps {
  status: EventStreamStatus | EventBusHealth
  /** This browser's own network link, distinct from `status`. Omitted (or
   *  "online") falls through to the fleet-driven display below unchanged. */
  clientLink?: ClientLinkStatus
  /** Wall-clock ms of the last event actually received, for the visible
   *  "data as of" age stamp shown while the client link itself is down. */
  lastEventAt?: number | null
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

function toDisplayState(status: EventStreamStatus | EventBusHealth): 'connected' | 'reconnecting' | 'down' {
  if (status === 'healthy') return 'connected'
  if (status === 'late') return 'reconnecting'
  if (status === 'open') return 'connected'
  if (status === 'reconnecting') return 'reconnecting'
  return 'down' // "connecting" (first attempt) or "closed" (intentionally torn down)
}

function formatDataAge(lastEventAt: number | null | undefined): string | null {
  if (lastEventAt == null) return null
  const age = Date.now() - lastEventAt
  if (age < 0) return null
  return formatDurationCompact(age)
}

export function LiveIndicator({ status, clientLink, lastEventAt }: LiveIndicatorProps) {
  if (clientLink && clientLink !== 'online') {
    const isOffline = clientLink === 'offline'
    const state = isOffline ? 'client-offline' : 'client-reconnecting'
    const age = formatDataAge(lastEventAt)

    return (
      <span
        className={`inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.08em] ${
          isOffline ? 'text-muted-foreground/70' : 'text-[var(--amber-text)]'
        }`}
        title={isOffline ? 'Your device lost its network connection.' : 'Your connection is reconnecting.'}
        data-testid="shell-live-indicator"
        data-live-state={state}
      >
        <span
          className={`size-1.5 rounded-full ${isOffline ? 'bg-muted-foreground/40' : 'bg-[var(--amber)]'}`}
          aria-hidden="true"
        />
        {isOffline ? "You're offline" : 'Reconnecting (you)'}
        {isOffline && age && <span className="opacity-70">&nbsp;· {age} old</span>}
      </span>
    )
  }

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
