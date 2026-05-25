/**
 * ConnectorDetailPage — /ingestion/connectors/:connectorType/:endpointIdentity
 *
 * Dispatch-language connector detail page. Wires data and renders the
 * ConnectorDetailView layout (two-zone editorial: header band + left narrative
 * + right index column).
 *
 * Uses existing hooks:
 * - useConnectorDetail — full connector metadata (liveness, state, counters, etc.)
 * - useConnectorStats  — 24h timeseries for the histogram
 *
 * OAuth scopes surface: not yet available from the real backend.
 * The ScopeList renders the explicit "unavailable" state (per spec AC3 —
 * unsupported/unavailable state must be rendered explicitly, not hidden).
 *
 * Auth status is derived from liveness + state via deriveConnectorDispatchInfo,
 * which is the same function used by the roster's AttentionStrip and row —
 * guaranteeing consistent auth label/color treatment across all three surfaces
 * (spec AC2).
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Connector Detail"
 */

import { useParams } from 'react-router'
import { IngestionSubNav } from '@/components/ingestion/IngestionSubNav'
import { DispatchLayout, DispatchSurface } from '@/components/ingestion/dispatch'
import { ConnectorDetailView } from '@/components/ingestion/connectors/ConnectorDetailView'
import {
  useConnectorDetail,
  useConnectorStats,
} from '@/hooks/use-ingestion'

// ---------------------------------------------------------------------------
// ConnectorDetailPage
// ---------------------------------------------------------------------------

export default function ConnectorDetailPage() {
  const { connectorType, endpointIdentity } = useParams<{
    connectorType: string
    endpointIdentity: string
  }>()

  const {
    data: detailResp,
    isLoading: detailLoading,
    error: detailError,
  } = useConnectorDetail(connectorType ?? null, endpointIdentity ?? null)

  const { data: statsResp, isLoading: statsLoading } = useConnectorStats(
    connectorType ?? null,
    endpointIdentity ?? null,
    '24h',
  )

  const connector = detailResp?.data
  const stats = statsResp?.data

  return (
    <DispatchLayout>
      <IngestionSubNav />
      <DispatchSurface>
        {detailLoading || statsLoading ? (
          <LoadingSkeleton />
        ) : detailError ? (
          <ErrorState connectorType={connectorType} error={detailError} />
        ) : connector ? (
          <ConnectorDetailView
            connector={connector}
            stats={stats}
            // oauthScopes is intentionally null here — the connector-oauth-scope-surface
            // backend capability is not yet implemented. ScopeList renders the
            // explicit "unavailable" state (spec AC3 compliance).
            oauthScopes={null}
          />
        ) : (
          <NotFoundState connectorType={connectorType} endpointIdentity={endpointIdentity} />
        )}
      </DispatchSurface>
    </DispatchLayout>
  )
}

// ---------------------------------------------------------------------------
// Sub-states
// ---------------------------------------------------------------------------

function LoadingSkeleton() {
  return (
    <div className="space-y-4 animate-pulse" data-testid="detail-loading">
      <div className="h-20 bg-foreground/5 rounded" />
      <div className="h-40 bg-foreground/5 rounded" />
      <div className="h-60 bg-foreground/5 rounded" />
    </div>
  )
}

function ErrorState({
  connectorType,
  error,
}: {
  connectorType: string | undefined
  error: Error
}) {
  return (
    <div data-testid="detail-error" className="py-8">
      <p className="font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground mb-2">
        error
      </p>
      <p className="font-serif italic text-[14px] text-muted-foreground">
        Failed to load connector{connectorType ? ` ${connectorType}` : ''}: {error.message}
      </p>
    </div>
  )
}

function NotFoundState({
  connectorType,
  endpointIdentity,
}: {
  connectorType: string | undefined
  endpointIdentity: string | undefined
}) {
  return (
    <div data-testid="detail-not-found" className="py-8">
      <p className="font-serif italic text-[14px] text-muted-foreground">
        Connector not found
        {connectorType ? `: ${connectorType}/${endpointIdentity ?? ''}` : ''}.
      </p>
    </div>
  )
}
