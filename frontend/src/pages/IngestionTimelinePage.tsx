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
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Timeline route replaces legacy tab landing"
 */

import { IngestionSubNav } from '@/components/ingestion/IngestionSubNav'
import { DispatchLayout, DispatchHeader, DispatchSurface } from '@/components/ingestion/dispatch'
import { TimelineTab } from '@/components/ingestion/TimelineTab'

export default function IngestionTimelinePage() {
  return (
    <DispatchLayout>
      <DispatchHeader
        eyebrow="Ingestion · timeline"
        headline="Ingestion"
        description="Unified ingestion control surface: source visibility, routing policy, and historical replay."
      />
      <IngestionSubNav />
      <DispatchSurface>
        <TimelineTab isActive={true} />
      </DispatchSurface>
    </DispatchLayout>
  )
}
