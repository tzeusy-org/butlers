import { Link, Navigate, useParams, useSearchParams } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import IngestionTimelinePage from './pages/IngestionTimelinePage.tsx'
import { resolveContactEntity } from './api/client.ts'
import { EmptyState } from './components/ui/empty-state.tsx'

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
// canonical contact detail page per the detail-page-archetype spec.
export function RelationshipContactRedirect() {
  const { id } = useParams()
  return <Navigate to={`/contacts/${id ?? ''}`} replace />
}

// Redirect /contacts/:contactId → /entities/:entityId when the contact has a
// linked entity.  When the contact exists but has no entity_id, renders a
// recovery state pointing users to the entities index.  When the contact does
// not exist (API 404) or any other error occurs, renders the same recovery
// state so the user is never left at a broken URL.
//
// Spec: openspec/changes/decommission-contact-detail-page/tasks.md §4
export function ContactEntityRedirect() {
  const { contactId } = useParams<{ contactId: string }>()
  const { data, isLoading, isError } = useQuery({
    queryKey: ['contact-entity-resolve', contactId],
    queryFn: () => resolveContactEntity(contactId!),
    enabled: !!contactId,
    retry: false,
  })

  if (isLoading) return null

  if (!isError && data?.entity_id) {
    return <Navigate to={`/entities/${data.entity_id}`} replace />
  }

  // Either unlinked (no entity_id) or contact not found (ApiError 404).
  return (
    <EmptyState
      title="Contact not linked to an entity"
      description="This contact has not been migrated to an entity yet."
      action={
        <Link
          to="/entities?has=contact"
          className="text-sm text-primary hover:underline"
        >
          Browse entities
        </Link>
      }
    />
  )
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
