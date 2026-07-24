/**
 * ConnectorDetailPage — /ingestion/connectors/:connectorType/:endpointIdentity
 *
 * Adopts the <Page archetype="detail"> shell for loading, error, and empty
 * states per the detail-page-archetype spec (bu-1jh6i). The shell handles
 * chrome and state management; ConnectorDetailView owns the content body
 * (Dispatch-language two-zone editorial layout).
 *
 * Uses existing hooks:
 * - useConnectorDetail — full connector metadata (liveness, state, counters, etc.)
 * - useConnectorStats  — 24h timeseries for the histogram
 *
 * OAuth scopes are populated live from the backend via useConnectorDetail
 * and rendered by ScopeList. When the connector has no scopes the list
 * renders the explicit "unavailable" state (spec AC3).
 *
 * Auth status is derived from liveness + state via deriveConnectorDispatchInfo,
 * which is the same function used by the roster's AttentionStrip and row —
 * guaranteeing consistent auth label/color treatment across all three surfaces
 * (spec AC2).
 *
 * Recovery routing: the shared capability resolver preserves OAuth return
 * context for supported providers and routes WhatsApp pairing in-app through
 * Passport without constructing a provider URL from the connector type.
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Connector Detail"
 */

import { useCallback, useEffect } from 'react'
import { useParams, useSearchParams, useNavigate } from 'react-router'
import { toast } from 'sonner'
import { IngestionSubNav } from '@/components/ingestion/IngestionSubNav'
import { DispatchLayout, DispatchSurface } from '@/components/ingestion/dispatch'
import { ConnectorDetailView } from '@/components/ingestion/connectors/ConnectorDetailView'
import type { OAuthScope } from '@/components/ingestion/connectors/ScopeList'
import { BatchSettingsCard } from '@/components/ingestion/BatchSettingsCard'
import { BATCH_CONNECTOR_TYPES } from '@/components/ingestion/BatchSettingsCard.constants'
import {
  useConnectorDetail,
  useConnectorEvents,
  useConnectorIncidents,
  useConnectorRoutingRules,
  useConnectorStats,
  useUpdateConnectorSettings,
} from '@/hooks/use-ingestion'
import type { ConnectorScopeEntry } from '@/api/types'
import {
  deriveConnectorDispatchInfo,
  resolveConnectorRecovery,
} from '@/components/ingestion/connectors/connector-auth'
import { Page, type Breadcrumb } from '@/components/ui/page'

/** Map backend ConnectorScopeEntry[] to the OAuthScope[] shape ScopeList consumes. */
function _toOAuthScopes(scopes: ConnectorScopeEntry[] | null | undefined): OAuthScope[] | null {
  if (!scopes || scopes.length === 0) return null
  return scopes
    .filter((s) => s.status !== 'extra') // exclude extra-only scopes from the ScopeList display
    .map((s) => ({
      name: s.name,
      granted: s.status === 'ok',
      verdict: s.status === 'missing' ? 'denied' : s.status === 'ok' ? 'granted' : undefined,
      note: s.serif_note || undefined,
    }))
}

// ---------------------------------------------------------------------------
// ConnectorDetailPage
// ---------------------------------------------------------------------------

/** Breadcrumbs for the connector detail page shell states. */
const CONNECTOR_BREADCRUMBS: Breadcrumb[] = [
  { label: 'ingestion', href: '/ingestion' },
  { label: 'connectors', href: '/ingestion/connectors' },
]

export default function ConnectorDetailPage() {
  const { connectorType, endpointIdentity } = useParams<{
    connectorType: string
    endpointIdentity: string
  }>()
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()

  const {
    data: detailResp,
    isLoading: detailLoading,
    error: detailError,
  } = useConnectorDetail(connectorType ?? null, endpointIdentity ?? null)

  const {
    data: statsResp,
    isLoading: statsLoading,
    isError: statsError,
    refetch: refetchStats,
  } = useConnectorStats(connectorType ?? null, endpointIdentity ?? null, '24h')

  // Connector-scoped event, incident, and routing rule data [bu-5ywn2]
  const {
    data: eventsResp,
    isLoading: eventsLoading,
    isError: eventsError,
    refetch: refetchEvents,
  } = useConnectorEvents(connectorType ?? null, endpointIdentity ?? null, 20)
  const {
    data: incidentsResp,
    isLoading: incidentsLoading,
    isError: incidentsError,
    refetch: refetchIncidents,
  } = useConnectorIncidents(connectorType ?? null, endpointIdentity ?? null, 10)
  const {
    data: routingRulesResp,
    isLoading: routingRulesLoading,
    isError: routingRulesError,
    refetch: refetchRoutingRules,
  } = useConnectorRoutingRules(connectorType ?? null, endpointIdentity ?? null)

  const connector = detailResp?.data
  const stats = statsResp?.data

  // Surface ?oauth_error= from a failed reauth redirect and strip it from the URL.
  // no_primary_account is NOT an auth error — it needs "set primary account" guidance.
  // Other oauth_error values indicate a failed reauth attempt.
  const oauthError = searchParams.get('oauth_error')
  useEffect(() => {
    if (!oauthError) return
    setSearchParams(
      (prev) => {
        const params = new URLSearchParams(prev)
        params.delete('oauth_error')
        return params
      },
      { replace: true },
    )
    if (oauthError === 'no_primary_account') {
      toast.warning('No primary account set. Go to Secrets to set a primary account.')
    } else {
      toast.warning(`OAuth error: ${oauthError.replace(/_/g, ' ')}. Try re-authorizing.`)
    }
  }, [oauthError, setSearchParams])

  // Mutation for batch settings (flush_interval_s).  Only called when the
  // connector type is in BATCH_CONNECTOR_TYPES, but the hook is always
  // initialised here to keep hook call order unconditional.
  const settingsMutation = useUpdateConnectorSettings(
    connectorType ?? '',
    endpointIdentity ?? '',
  )

  const recoveryConnectorType = connector?.connector_type ?? connectorType ?? ''
  const recoveryEndpointIdentity = connector?.endpoint_identity ?? endpointIdentity
  const connectorDetailPath = recoveryEndpointIdentity
    ? `${recoveryConnectorType}/${recoveryEndpointIdentity}`
    : undefined
  const recovery = resolveConnectorRecovery(recoveryConnectorType, {
    connectorDetailPath,
    forceConsent: true,
  })
  const canRecover =
    connector != null &&
    deriveConnectorDispatchInfo(connector).authStatus === 'needs_reauth' &&
    recovery.kind !== 'unsupported'

  // The resolver owns provider capability selection. OAuth needs a full-page
  // redirect, while Passport pairing remains in-app; unsupported recovery is
  // intentionally never an interactive action.
  const handleReauth = useCallback(() => {
    if (recovery.kind === 'oauth') {
      window.location.href = recovery.href
    } else if (recovery.kind === 'passport') {
      navigate(recovery.to)
    }
  }, [navigate, recovery])

  // --- Shell-owned states (loading, error, not-found) ----------------------
  // The Page shell handles these via its loading / error / empty props, replacing
  // the bespoke inline LoadingSkeleton / ErrorState / NotFoundState components.

  if (detailLoading) {
    return (
      <Page
        archetype="detail"
        title={connectorType ?? 'Connector'}
        breadcrumbs={CONNECTOR_BREADCRUMBS}
        loading
      >
        {null}
      </Page>
    )
  }

  if (detailError) {
    return (
      <Page
        archetype="detail"
        title={connectorType ?? 'Connector'}
        breadcrumbs={CONNECTOR_BREADCRUMBS}
        error={detailError}
      >
        {null}
      </Page>
    )
  }

  if (!connector) {
    return (
      <Page
        archetype="detail"
        title="Connector not found"
        breadcrumbs={CONNECTOR_BREADCRUMBS}
        empty={{
          title: 'Connector not found',
          description: connectorType
            ? `No connector found for ${connectorType}/${endpointIdentity ?? ''}.`
            : 'Connector not found.',
        }}
      >
        {null}
      </Page>
    )
  }

  // --- Normal state: Dispatch-language layout (ConnectorDetailView) ---------
  return (
    <DispatchLayout>
      <IngestionSubNav />
      <DispatchSurface>
        <ConnectorDetailView
          connector={connector}
          stats={stats}
          statsReader={{
            isLoading: statsLoading,
            isError: statsError,
            onRetry: () => void refetchStats(),
          }}
          oauthScopes={_toOAuthScopes(connector.scopes)}
          recentEvents={eventsResp ?? null}
          recentEventsReader={{
            isLoading: eventsLoading,
            isError: eventsError,
            onRetry: () => void refetchEvents(),
          }}
          incidents={incidentsResp ?? null}
          incidentsReader={{
            isLoading: incidentsLoading,
            isError: incidentsError,
            onRetry: () => void refetchIncidents(),
          }}
          routingRules={routingRulesResp ?? null}
          routingRulesReader={{
            isLoading: routingRulesLoading,
            isError: routingRulesError,
            onRetry: () => void refetchRoutingRules(),
          }}
          recovery={recovery}
          onReauth={canRecover ? handleReauth : undefined}
        />
        {BATCH_CONNECTOR_TYPES.has(connector.connector_type) && (
          <div className="mt-8" data-testid="batch-settings-section">
            <BatchSettingsCard
              connector={connector}
              settingsMutation={settingsMutation}
            />
          </div>
        )}
      </DispatchSurface>
    </DispatchLayout>
  )
}
