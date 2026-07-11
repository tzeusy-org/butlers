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
 * onFreshnessChange also carries an `isDown` flag (the events head poll's
 * isError), threaded into the badge's distinct "Down" state so a dead API
 * no longer decays into the same muted "Idle" dot a genuinely quiet
 * pipeline gets — the dead-API-impersonates-idle defect bu-qvnce.2 fixed on
 * /timeline, now closed on the badge's original home (bu-jad4j.5).
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Timeline route replaces legacy tab landing"
 *       §"Timeline Ledger" — header band with live freshness/status pill
 */

import { useCallback, useState } from 'react'
import { IngestionSubNav } from '@/components/ingestion/IngestionSubNav'
import { DispatchLayout, DispatchHeader, DispatchSurface } from '@/components/ingestion/dispatch'
import { TimelineTab, type IngestionRange } from '@/components/ingestion/TimelineTab'
import { LiveStatusBadge } from '@/components/ui/live-status-badge'

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
// Page
// ---------------------------------------------------------------------------

export default function IngestionTimelinePage() {
  // Freshness state: undefined until TimelineTab reports its first data fetch.
  // undefined = still loading; null = empty pipeline; string = has events.
  const [latestReceivedAt, setLatestReceivedAt] = useState<string | null | undefined>(undefined)

  // True whenever TimelineTab's events head poll is currently erroring. Kept
  // separate from freshness so a dead API renders the badge's distinct "Down"
  // state instead of the muted "Idle" dot a genuinely quiet pipeline gets —
  // the exact dead-API-impersonates-idle defect bu-qvnce.2 fixed on /timeline,
  // now closed on the badge's original home (bu-jad4j.5).
  const [isLiveFeedDown, setIsLiveFeedDown] = useState(false)

  const handleFreshnessChange = useCallback((ra: string | null, isDown: boolean) => {
    setLatestReceivedAt(ra)
    setIsLiveFeedDown(isDown)
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
        aside={<LiveStatusBadge latestReceivedAt={latestReceivedAt} isDown={isLiveFeedDown} />}
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
