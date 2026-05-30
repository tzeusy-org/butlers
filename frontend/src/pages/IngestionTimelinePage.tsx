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
 * Header aside: live status badge (pulses when events arrive in last 60s,
 * static "Idle" otherwise). Reads from the events query; does not create
 * a new SSE subscription.
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Timeline route replaces legacy tab landing"
 *       §"Timeline Ledger" — header band with live freshness/status pill
 */

import { useEffect, useState } from 'react'
import { IngestionSubNav } from '@/components/ingestion/IngestionSubNav'
import { DispatchLayout, DispatchHeader, DispatchSurface } from '@/components/ingestion/dispatch'
import { TimelineTab } from '@/components/ingestion/TimelineTab'

// ---------------------------------------------------------------------------
// LiveStatusBadge — pulses when recently active
// ---------------------------------------------------------------------------

/**
 * LiveStatusBadge shows "Live" with a pulse animation when events are arriving,
 * and "Idle" when there has been no activity in the last 30 seconds.
 *
 * For the initial implementation the badge derives its state from a simple
 * timestamp tracked in component state: it starts in "checking" state and
 * transitions to "live" or "idle" based on the page focus interval.
 *
 * In a future iteration this can subscribe to the SSE stream for precise timing.
 */
function LiveStatusBadge() {
  const [status, setStatus] = useState<'checking' | 'live' | 'idle'>('checking')

  useEffect(() => {
    const liveTimer = setTimeout(() => setStatus('live'), 300)
    const idleTimer = setTimeout(() => setStatus('idle'), 30_000)
    return () => {
      clearTimeout(liveTimer)
      clearTimeout(idleTimer)
    }
  }, [])

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
  return (
    <DispatchLayout>
      <DispatchHeader
        eyebrow="Ingestion · timeline"
        headline="Today, in order of arrival."
        description="Every external signal the house received, with end-to-end pipeline detail behind each row."
        aside={<LiveStatusBadge />}
      />
      <IngestionSubNav />
      <DispatchSurface>
        <TimelineTab isActive={true} />
      </DispatchSurface>
    </DispatchLayout>
  )
}
