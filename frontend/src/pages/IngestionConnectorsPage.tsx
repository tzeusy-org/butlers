/**
 * IngestionConnectorsPage — route component for /ingestion/connectors.
 *
 * Thin page wrapper around ConnectorsRoster, the dense hairline-divided
 * connector register for the first-class /ingestion/connectors sub-route.
 *
 * Uses Dispatch primitives and IngestionSubNav for consistent navigation.
 * No legacy TabsTrigger shell. No card chrome — hairlines and rhythm only.
 *
 * NOTE: useConnectorDetail MUST NOT be mounted from this list view (§6.2).
 * Only summary-level data is shown here (per spec "Connector roster list
 * summary-only polling"). Detail data loads only on the connector detail page.
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Connectors Roster"
 */

import { IngestionSubNav } from '@/components/ingestion/IngestionSubNav'
import { DispatchLayout, DispatchHeader, DispatchSurface } from '@/components/ingestion/dispatch'
import { ConnectorsRoster } from '@/components/ingestion/connectors/ConnectorsRoster'

export default function IngestionConnectorsPage() {
  return (
    <DispatchLayout>
      <DispatchHeader
        eyebrow="Ingestion · connectors"
        headline="Where signals come from."
        description="Every channel the house listens on — status, health, and credential state."
      />
      <IngestionSubNav />
      <DispatchSurface>
        <ConnectorsRoster />
      </DispatchSurface>
    </DispatchLayout>
  )
}
