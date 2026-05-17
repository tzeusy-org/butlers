/**
 * IngestionConnectorsPage — route component for /ingestion/connectors.
 *
 * Thin wrapper around the existing ConnectorsTab, promoted to a first-class
 * sub-route under the INGESTION_DISPATCH_CONSOLE flag.
 *
 * NOTE: useConnectorDetail MUST NOT be mounted from this list view.
 * Only summary-level data is shown here (per spec "Connector roster list
 * summary-only polling"). Detail data loads only on the connector detail page.
 *
 * Spec: openspec/changes/redesign-ingestion-dispatch-console/specs/
 *       ingestion-ui-information-architecture/spec.md §"Sub-route hierarchy"
 */

import { ConnectorsTab } from '@/components/ingestion/ConnectorsTab'

export default function IngestionConnectorsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Connectors</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Active ingestion connectors — status, health, and configuration.
        </p>
      </div>
      <ConnectorsTab isActive={true} />
    </div>
  )
}
