import { Navigate, useParams, useSearchParams } from 'react-router'
import IngestionTimelinePage from './pages/IngestionTimelinePage.tsx'

// ---------------------------------------------------------------------------
// Private redirect helpers
// ---------------------------------------------------------------------------
// These components are only used inside the route config (router-config.tsx).
// They are exported so router-config.tsx can import them without circular deps,
// but they are not part of the public API.

// Redirect /connectors/:connectorType/:endpointIdentity
// → /ingestion/connectors/:connectorType/:endpointIdentity
// Preserves relevant query string params (period, date filters) per spec section 3.3.
export function ConnectorDetailRedirect() {
  const { connectorType, endpointIdentity } = useParams()
  const [searchParams] = useSearchParams()
  const qs = searchParams.toString()
  const target = `/ingestion/connectors/${connectorType}/${endpointIdentity}${qs ? `?${qs}` : ''}`
  return <Navigate to={target} replace />
}

// Redirect /butlers/relationship/entities/:entityId → /entities/:entityId
// The relationship-scoped activity view has been folded into the unified
// entity detail page.
export function RelationshipEntityRedirect() {
  const { entityId } = useParams()
  return <Navigate to={`/entities/${entityId ?? ''}`} replace />
}

// Redirect /butlers/relationship/contacts/:id → /contacts/:contactId
// The legacy relationship-scoped contact path has been superseded by the
// canonical contact compatibility redirect (which forwards to the entity index).
export function RelationshipContactRedirect() {
  const { id } = useParams()
  return <Navigate to={`/contacts/${id ?? ''}`} replace />
}

// ---------------------------------------------------------------------------
// IngestionTabRedirect — public component
// ---------------------------------------------------------------------------

// Redirect /ingestion?tab=connectors|filters|history → matching sub-route.
// Preserves filter query-string params (period, channel, status) so deep links
// and bookmarks continue to resolve after the tab-param → sub-route migration.
// Unrecognized or absent ?tab= values redirect to /ingestion (Timeline root),
// stripping the unknown tab param from the URL.
//
// React Router does not issue a real HTTP 301; this is the SPA equivalent:
// a permanent client-side replace() navigation, which is functionally identical
// for bookmark resolution and browser history.
//
// Spec: dashboard-ingestion-dispatch-console §"Legacy connectors tab normalizes to roster route"
//       "History tab normalizes to Timeline state"
// Note: ?tab=history normalises to /ingestion (Timeline), NOT /ingestion/history.
//       There is no primary redesigned /ingestion/history route per the new spec.
// Exported so tests can import the component directly without duplicating its logic.
export function IngestionTabRedirect() {
  const [searchParams] = useSearchParams()
  const tab = searchParams.get('tab')

  // Strip the 'tab' key; preserve all other filter params
  const filtered = new URLSearchParams(searchParams)
  filtered.delete('tab')
  const qs = filtered.toString()

  if (tab === 'connectors') {
    return <Navigate to={`/ingestion/connectors${qs ? `?${qs}` : ''}`} replace />
  }
  if (tab === 'filters') {
    return <Navigate to={`/ingestion/filters${qs ? `?${qs}` : ''}`} replace />
  }
  // history normalises to Timeline (no separate /ingestion/history route in the redesign)
  // Spec: "history SHALL map to the Timeline route … it SHALL NOT remain a fourth redesigned tab"
  if (tab === 'history' || tab === 'timeline') {
    return <Navigate to={`/ingestion${qs ? `?${qs}` : ''}`} replace />
  }

  // Unrecognized ?tab= value: redirect to Timeline root, stripping the unknown
  // tab param so stale bookmarks do not keep an invalid ?tab= in the URL.
  // No ?tab= at all: render Timeline directly (no redirect needed, avoids loop).
  if (tab !== null) {
    return <Navigate to={`/ingestion${qs ? `?${qs}` : ''}`} replace />
  }
  return <IngestionTimelinePage />
}
