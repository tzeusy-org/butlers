/**
 * IngestionConnectorsPage — route component for /ingestion/connectors.
 *
 * Thin page wrapper around ConnectorsListPage, which is the extracted
 * first-class component for the connector roster list sub-route.
 *
 * NOTE: useConnectorDetail MUST NOT be mounted from this list view (§6.2).
 * Only summary-level data is shown here (per spec "Connector roster list
 * summary-only polling"). Detail data loads only on the connector detail page.
 *
 * Spec: openspec/changes/redesign-ingestion-dispatch-console/specs/
 *       ingestion-ui-information-architecture/spec.md §"Sub-route hierarchy"
 *       connector-base-spec/spec.md §"Dashboard Connector Page"
 *       tasks.md §3.4
 */

import { ConnectorsListPage } from '@/components/ingestion/ConnectorsListPage'

export default function IngestionConnectorsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Connectors</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Active ingestion connectors — status, health, and configuration.
        </p>
      </div>
      <ConnectorsListPage />
    </div>
  )
}
