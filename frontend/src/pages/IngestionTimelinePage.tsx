/**
 * IngestionTimelinePage — route component for /ingestion (Timeline root).
 *
 * Mounts under the INGESTION_DISPATCH_CONSOLE sub-route hierarchy when the
 * feature flag is on. The Timeline is the default landing view for the
 * ingestion surface.
 *
 * Uses Dispatch primitives (DispatchLayout, DispatchHeader) and the shared
 * IngestionSubNav for consistent navigation across all ingestion routes.
 * No legacy TabsTrigger shell — sub-nav replaces the old ?tab= switcher.
 *
 * Header aside: live status badge. Status is derived from the most-recent
 * event's received_at: "Live" when an event arrived within the last 60 s,
 * "Idle" otherwise.  TimelineTab reports freshness via onFreshnessChange
 * so the badge reflects real pipeline activity, not a wall-clock timer.
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Timeline route replaces legacy tab landing"
 *       §"Timeline Ledger" — header band with live freshness/status pill
 */

import { useCallback, useMemo, useState } from 'react'
import { IngestionSubNav } from '@/components/ingestion/IngestionSubNav'
import { DispatchLayout, DispatchHeader, DispatchSurface } from '@/components/ingestion/dispatch'
import { TimelineTab } from '@/components/ingestion/TimelineTab'

// ---------------------------------------------------------------------------
// LiveStatusBadge — driven by real event freshness
// ---------------------------------------------------------------------------

/** Freshness window: an event received within this many ms is "live". */
const LIVE_FRESHNESS_MS = 60_000

type LiveStatus = 'checking' | 'live' | 'idle'

interface LiveStatusBadgeProps {
  /**
   * ISO-8601 received_at of the most-recent ingestion event.
   * - undefined → initial loading state (before TimelineTab has completed its first fetch)
   * - null → pipeline is empty (query returned, no events) → "idle"
   * - string → has events; freshness determines "live" vs "idle"
   */
  latestReceivedAt: string | null | undefined
}

function deriveStatus(latestReceivedAt: string | null | undefined, now: number): LiveStatus {
  if (latestReceivedAt === undefined) return 'checking'
  if (latestReceivedAt === null) return 'idle'
  const date = new Date(latestReceivedAt)
  if (Number.isNaN(date.getTime())) return 'idle'
  const age = now - date.getTime()
  return age <= LIVE_FRESHNESS_MS ? 'live' : 'idle'
}

function LiveStatusBadge({ latestReceivedAt }: LiveStatusBadgeProps) {
  // Capture the current time once per render via useMemo to satisfy the
  // react-hooks/purity rule (no bare Date.now() calls in the render path).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const now = useMemo(() => Date.now(), [latestReceivedAt])
  const status = deriveStatus(latestReceivedAt, now)

  if (status === 'checking') {
    return (
      <span className="inline-flex items-center gap-1.5 font-mono text-[11px] tracking-[0.01em] text-muted-foreground">
        <span className="size-1.5 rounded-full bg-muted-foreground animate-pulse" />
        checking…
      </span>
    )
  }

  if (status === 'live') {
    return (
      <span
        className="inline-flex items-center gap-1.5 font-mono text-[11px] tracking-[0.01em]"
        style={{ color: 'var(--green, theme(colors.emerald.600))' }}
        data-testid="live-status-badge-live"
      >
        <span
          className="size-1.5 rounded-full animate-pulse"
          style={{ backgroundColor: 'var(--green, theme(colors.emerald.600))' }}
        />
        Live
      </span>
    )
  }

  return (
    <span
      className="inline-flex items-center gap-1.5 font-mono text-[11px] tracking-[0.01em] text-muted-foreground"
      data-testid="live-status-badge-idle"
    >
      <span className="size-1.5 rounded-full bg-muted-foreground/50" />
      Idle
    </span>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function IngestionTimelinePage() {
  // Freshness state: undefined until TimelineTab reports its first data fetch.
  // undefined = still loading; null = empty pipeline; string = has events.
  const [latestReceivedAt, setLatestReceivedAt] = useState<string | null | undefined>(undefined)

  const handleFreshnessChange = useCallback((ra: string | null) => {
    setLatestReceivedAt(ra)
  }, [])

  return (
    <DispatchLayout>
      <DispatchHeader
        eyebrow="Ingestion · timeline"
        headline="Today, in order of arrival."
        description="Every external signal the house received, with end-to-end pipeline detail behind each row."
        aside={<LiveStatusBadge latestReceivedAt={latestReceivedAt} />}
      />
      <IngestionSubNav />
      <DispatchSurface>
        <TimelineTab isActive={true} onFreshnessChange={handleFreshnessChange} />
      </DispatchSurface>
    </DispatchLayout>
  )
}
