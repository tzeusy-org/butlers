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
 * OAuth reauth deep-link: when the user clicks re-authorize on this page, the
 * OAuth start URL includes connector_detail_path so the callback redirects back
 * to this specific connector detail page instead of the connectors roster.
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
import { getProviderOAuthStartUrl } from '@/api/client'
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

/**
 * Map a connector_type to its OAuth scope set name, if any.
 *
 * The backend ``GOOGLE_SCOPE_SETS`` registry maps named scope sets to actual
 * OAuth scopes.  Connectors that request non-default scopes must pass the
 * correct scope_set so the reauth grants the right permissions.
 *
 * - google_health → "health" (Google Fit / Health Connect scopes)
 * - other google_* → omitted (default Calendar/Drive/Gmail scope composition)
 * - non-google → omitted
 */
function _scopeSetForConnectorType(connectorType: string): string | undefined {
  if (connectorType === 'google_health') return 'health'
  return undefined
}

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

  const { data: statsResp, isLoading: statsLoading } = useConnectorStats(
    connectorType ?? null,
    endpointIdentity ?? null,
    '24h',
  )

  // Connector-scoped event, incident, and routing rule data [bu-5ywn2]
  const { data: eventsResp } = useConnectorEvents(
    connectorType ?? null,
    endpointIdentity ?? null,
    20,
  )
  const { data: incidentsResp } = useConnectorIncidents(
    connectorType ?? null,
    endpointIdentity ?? null,
    10,
  )
  const { data: routingRulesResp } = useConnectorRoutingRules(
    connectorType ?? null,
    endpointIdentity ?? null,
  )

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

  // Build the onReauth handler: initiates OAuth reauth for this connector's
  // provider (derived from connector_type) and carries connector_detail_path
  // so the callback deep-links back to this specific detail page.
  // scopeSet is passed so google_health requests health scopes, not the default
  // Calendar/Drive/Gmail scope composition — without it reauth would stay degraded.
  const handleReauth = useCallback(() => {
    if (!connectorType || !endpointIdentity) return
    // Derive the OAuth provider name from connector_type.  The backend registry
    // only accepts "google" and "spotify" as provider keys; connector_type values
    // like "google_health" or "google_drive" must be mapped to "google".
    const provider = connectorType.startsWith('google') ? 'google' : connectorType
    // connector_detail_path is "<type>/<identity>" — the backend validates the
    // format and silently ignores it if it doesn't match, falling back to the
    // roster.
    const connectorDetailPath = `${connectorType}/${endpointIdentity}`
    const url = getProviderOAuthStartUrl(provider, {
      pageOfOrigin: 'ingestion',
      connectorDetailPath,
      forceConsent: true,
      scopeSet: _scopeSetForConnectorType(connectorType),
    })
    window.location.href = url
  }, [connectorType, endpointIdentity])

  // Build the onSetPrimaryAccount handler: navigates to the Google account
  // management surface where the user can set a primary Google account.
  // no_primary_account is not a reauth issue — the connector has a valid
  // credential but no account is designated as primary. Per the
  // dashboard-google-accounts spec, any in-app cross-link to the Google account
  // management surface targets /secrets?focus=u:google (the canonical deep-link
  // that opens the PageGoogleAccounts passport page).
  const handleSetPrimaryAccount = useCallback(() => {
    navigate('/secrets?focus=u:google')
  }, [navigate])

  // --- Shell-owned states (loading, error, not-found) ----------------------
  // The Page shell handles these via its loading / error / empty props, replacing
  // the bespoke inline LoadingSkeleton / ErrorState / NotFoundState components.

  if (detailLoading || statsLoading) {
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
          oauthScopes={_toOAuthScopes(connector.scopes)}
          recentEvents={eventsResp ?? null}
          incidents={incidentsResp ?? null}
          routingRules={routingRulesResp ?? null}
          onReauth={handleReauth}
          onSetPrimaryAccount={handleSetPrimaryAccount}
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
