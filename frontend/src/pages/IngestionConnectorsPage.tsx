/**
 * IngestionConnectorsPage — route component for /ingestion/connectors.
 *
 * Thin page wrapper around ConnectorsListPage, which is the extracted
 * first-class component for the connector roster list sub-route.
 *
 * Uses Dispatch primitives and IngestionSubNav for consistent navigation.
 * No legacy TabsTrigger shell.
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
import { ConnectorsListPage } from '@/components/ingestion/ConnectorsListPage'

export default function IngestionConnectorsPage() {
  return (
    <DispatchLayout>
      <DispatchHeader
        eyebrow="Ingestion · connectors"
        headline="Connectors"
        description="Active ingestion connectors — status, health, and configuration."
      />
      <IngestionSubNav />
      <DispatchSurface>
        <ConnectorsListPage />
      </DispatchSurface>
    </DispatchLayout>
  )
}
