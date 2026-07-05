/**
 * ConnectorDeviceBadges — per-device liveness row for multi-device connectors.
 *
 * Some connector_types have several distinct physical devices posting through
 * one shared connector (e.g. OwnTracks household phones). connector_registry
 * only ever tracks a single heartbeat identity for the whole connector_type
 * (whichever device most recently resolved it), so a silently-dead sibling
 * device is otherwise invisible behind a healthy connector-level verdict —
 * this was the root cause of bu-e16to (2 of 3 OwnTracks devices dead for 10
 * weeks with no dashboard signal).
 *
 * Renders one badge per distinct sender_identity with its last-seen relative
 * time; a device stale beyond the backend's threshold (48h) is called out in
 * the same red tone as the rest of the Dispatch state language.
 *
 * Spec: bu-e16to
 */

import { Time } from '@/components/ui/time'
import type { ConnectorDeviceLiveness } from '@/api/types'

interface ConnectorDeviceBadgesProps {
  devices: ConnectorDeviceLiveness[]
}

export function ConnectorDeviceBadges({ devices }: ConnectorDeviceBadgesProps) {
  if (devices.length === 0) return null

  return (
    <div
      className="flex flex-wrap items-center gap-x-4 gap-y-1 pb-1"
      style={{ gridColumn: '2 / -1' }}
      data-testid="connector-devices"
    >
      {devices.map((d) => (
        <div
          key={d.sender_identity}
          className="flex items-center gap-1.5"
          data-testid={`connector-device-${d.sender_identity}`}
        >
          <span
            aria-hidden="true"
            className={`w-1 h-1 rounded-full shrink-0 ${
              d.stale
                ? 'bg-[color:var(--red,oklch(0.62_0.20_25))]'
                : 'bg-[color:var(--green,oklch(0.72_0.17_150))]'
            }`}
          />
          <span className="font-mono text-[10px] tracking-[0.02em] text-muted-foreground/80">
            {d.sender_identity}
          </span>
          <span
            className={`font-mono text-[10px] ${
              d.stale
                ? 'text-[color:var(--red,oklch(0.62_0.20_25))]'
                : 'text-muted-foreground/60'
            }`}
            data-testid={`connector-device-lastseen-${d.sender_identity}`}
          >
            {d.stale ? 'stale · ' : 'last · '}
            <Time value={d.last_seen_at} mode="relative" className="inline" />
          </span>
        </div>
      ))}
    </div>
  )
}
