/**
 * IngestionTimelinePage — route component for /ingestion (Timeline root).
 *
 * Mounts under the INGESTION_DISPATCH_CONSOLE sub-route hierarchy when the
 * feature flag is on. The Timeline is the default landing view for the
 * ingestion surface.
 *
 * Spec: openspec/changes/redesign-ingestion-dispatch-console/specs/
 *       ingestion-ui-information-architecture/spec.md §"Sub-route hierarchy"
 */

import { TimelineTab } from '@/components/ingestion/TimelineTab'

export default function IngestionTimelinePage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Ingestion</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Unified ingestion control surface: source visibility, routing policy, and historical replay.
        </p>
      </div>
      <TimelineTab isActive={true} />
    </div>
  )
}
