/**
 * IngestionTimelinePage — route component for /ingestion (Timeline root).
 *
 * Mounts under the /ingestion sub-route hierarchy. The Timeline is the
 * default landing view for the ingestion surface.
 *
 * Uses Dispatch primitives (DispatchLayout, DispatchHeader) and the shared
 * IngestionSubNav for consistent navigation across all ingestion routes.
 * No legacy TabsTrigger shell — sub-nav replaces the old ?tab= switcher.
 *
 * Header aside: live status badge. Status is derived from the most-recent
 * event's received_at: "Live" when an event arrived within the last 60 s,
 * "Idle" otherwise.  TimelineTab reports freshness via onFreshnessChange
 * so the badge reflects real pipeline activity — but "now" itself ticks on
 * a wall clock (bu-86c4c.8, move 5) so the badge decays to "Idle" on its
 * own once the freshness window elapses, even if no new event ever arrives
 * to trigger a re-render. Before this fix "now" was only recomputed when
 * latestReceivedAt changed, so a pipeline that went quiet kept showing a
 * stale "Live" forever.
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Timeline route replaces legacy tab landing"
 *       §"Timeline Ledger" — header band with live freshness/status pill
 */

import { useCallback, useEffect, useState } from 'react'
import { IngestionSubNav } from '@/components/ingestion/IngestionSubNav'
import { DispatchLayout, DispatchHeader, DispatchSurface } from '@/components/ingestion/dispatch'
import { TimelineTab, type IngestionRange } from '@/components/ingestion/TimelineTab'

// ---------------------------------------------------------------------------
// Range-driven headline (bu-4utdw.4 honesty fix — replaces the hardcoded
// "Today, in order of arrival." with copy that reflects the active range
// picker selection, reported up via TimelineTab's onRangeReport).
// ---------------------------------------------------------------------------

const RANGE_HEADLINE: Record<IngestionRange, string> = {
  '1h': 'Last 1 hour, newest first.',
  '24h': 'Last 24 hours, newest first.',
  '7d': 'Last 7 days, newest first.',
}

// ---------------------------------------------------------------------------
// LiveStatusBadge — driven by real event freshness
// ---------------------------------------------------------------------------

/** Freshness window: an event received within this many ms is "live". */
const LIVE_FRESHNESS_MS = 60_000

/** How often the badge re-evaluates its own age against the wall clock. */
const CLOCK_TICK_MS = 5_000

/**
 * A `now` timestamp that ticks on a wall clock rather than only advancing
 * when its caller re-renders for some other reason. Used so freshness
 * badges decay to "stale" on their own instead of staying frozen at
 * whatever `now` happened to be at the last data-driven render.
 */
function useTickingNow(intervalMs: number = CLOCK_TICK_MS): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(id)
  }, [intervalMs])
  return now
}

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
  // `now` ticks on a wall clock (not just when latestReceivedAt changes) so
  // the badge decays from "Live" to "Idle" on its own once the freshness
  // window elapses, even if the pipeline goes quiet and never reports a
  // new timestamp.
  const now = useTickingNow()
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

  // Range-driven headline: defaults to TimelineTab's own default ("24h")
  // until the first onRangeReport call confirms the actual active range.
  const [activeRange, setActiveRange] = useState<IngestionRange>('24h')
  const handleRangeReport = useCallback((r: IngestionRange) => {
    setActiveRange(r)
  }, [])

  return (
    <DispatchLayout>
      <DispatchHeader
        eyebrow="Ingestion · timeline"
        headline={RANGE_HEADLINE[activeRange]}
        description="Every external signal the house received, with end-to-end pipeline detail behind each row."
        aside={<LiveStatusBadge latestReceivedAt={latestReceivedAt} />}
      />
      <IngestionSubNav />
      <DispatchSurface>
        <TimelineTab
          isActive={true}
          onFreshnessChange={handleFreshnessChange}
          onRangeReport={handleRangeReport}
        />
      </DispatchSurface>
    </DispatchLayout>
  )
}
