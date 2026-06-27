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
 * OAuth error handling: when a reauth redirect lands here with ?oauth_error=,
 * we surface a toast and strip the param. no_primary_account gets its own
 * "set primary account" guidance (not a generic auth error message).
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Connectors Roster"
 */

import { useEffect } from 'react'
import { useSearchParams } from 'react-router'
import { toast } from 'sonner'
import { IngestionSubNav } from '@/components/ingestion/IngestionSubNav'
import { DispatchLayout, DispatchHeader, DispatchSurface } from '@/components/ingestion/dispatch'
import { ConnectorsRoster } from '@/components/ingestion/connectors/ConnectorsRoster'

export default function IngestionConnectorsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const oauthError = searchParams.get('oauth_error')

  // Surface ?oauth_error= from a failed reauth callback and strip it from the URL.
  // no_primary_account → guide user to set a primary account in Secrets.
  // Other errors → generic "try re-authorizing" guidance.
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

  return (
    <DispatchLayout>
      <DispatchHeader
        eyebrow="Ingestion · connectors"
        headline="Where signals come from."
        description="Every channel the house listens on: status, health, and credential state."
      />
      <IngestionSubNav />
      <DispatchSurface>
        <ConnectorsRoster />
      </DispatchSurface>
    </DispatchLayout>
  )
}
