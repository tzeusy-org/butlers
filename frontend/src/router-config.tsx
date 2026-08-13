/**
 * Browser router instance.
 *
 * This file owns the createBrowserRouter call and all route configuration.
 * It has no component definitions of its own — all components (including
 * redirect helpers) live in router.tsx or in their own page files.
 *
 * Separating the non-component `router` export into this file ensures that
 * router.tsx satisfies the react-refresh/only-export-components rule and
 * can be hot-reloaded by Vite without triggering a full page refresh.
 */

import { lazy, type ReactNode } from 'react'
import { createBrowserRouter, Navigate } from 'react-router'
import { RouteSuspense } from './components/layout/RouteSuspense.tsx'
import RootLayout from './layouts/RootLayout.tsx'
import { getShellCapability } from './lib/shell-capability'
import {
  ColumnsToPlexRedirect,
  ConnectorDetailRedirect,
  HopToPlexRedirect,
  IngestionTabRedirect,
  RelationshipContactRedirect,
  RelationshipEntityRedirect,
} from './router.tsx'

// Every page belongs to its own route chunk. The shell stays mounted while a
// chunk resolves, and routeElement gives every route the same in-place frame.
function shellPage(path: string) {
  const capability = getShellCapability(path)
  if (!capability) throw new Error(`Missing shell capability for ${path}`)
  return lazy(capability.loader)
}

const DashboardPage = shellPage('/')
const ButlersPage = shellPage('/butlers')
const ButlerDetailPage = shellPage('/butlers/:name')
const SessionsPage = shellPage('/sessions')
const SessionDetailPage = shellPage('/sessions/:id')
const TimelinePage = shellPage('/timeline')
const NotificationsPage = shellPage('/notifications')
const IssuesPage = shellPage('/issues')
const SpendPage = shellPage('/spend')
const MemoryPage = shellPage('/memory')
const FactDetailPage = shellPage('/memory/facts/:factId')
const RuleDetailPage = shellPage('/memory/rules/:ruleId')
const EpisodeDetailPage = shellPage('/memory/episodes/:episodeId')
const SettingsConsolePage = shellPage('/settings')
const SettingsPermissionsPage = shellPage('/settings/permissions')
const SettingsModelsPage = shellPage('/settings/models')
const AuditLogPage = shellPage('/audit-log')
const HealthOverviewPage = shellPage('/health')
const MeasurementsPage = shellPage('/health/measurements')
const MedicationsPage = shellPage('/health/medications')
const ConditionsPage = shellPage('/health/conditions')
const SymptomsPage = shellPage('/health/symptoms')
const MealsPage = shellPage('/health/meals')
const ResearchPage = shellPage('/health/research')
const ApprovalsPage = shellPage('/approvals')
const DecisionsPage = shellPage('/decisions')
const BeadDetailPage = shellPage('/beads/:beadId')
const SecretsPage = shellPage('/secrets')
const EducationPage = shellPage('/education')
const EntitiesIndexPage = shellPage('/entities/index')
const PlexPage = shellPage('/entities')
const EntityDetailPage = shellPage('/entities/:entityId')
const ConcentrationPage = shellPage('/entities/concentration')
const CirclesPage = shellPage('/entities/circles')
const IngestionConnectorsPage = shellPage('/ingestion/connectors')
const IngestionFiltersPage = shellPage('/ingestion/filters')
const ConnectorDetailPage = shellPage('/ingestion/connectors/:connectorType/:endpointIdentity')
const QaOverviewPage = shellPage('/qa')
const QaPatrolDetailPage = shellPage('/qa/patrols/:patrolId')
const QaInvestigationDetailPage = shellPage('/qa/investigations/:attemptId')
const CalendarWorkspacePage = shellPage('/calendar')
const ChroniclesPage = shellPage('/chronicles')
const SystemPage = shellPage('/system')

const _baseUrl = (import.meta.env.BASE_URL || '/').replace(/\/+$/, '') || '/'

function routeElement(page: ReactNode) {
  return <RouteSuspense>{page}</RouteSuspense>
}

export const router = createBrowserRouter(
  [
    {
      element: <RootLayout />,
      children: [
        { path: '/', element: routeElement(<DashboardPage />) },
        { path: '/butlers', element: routeElement(<ButlersPage />) },
        { path: '/butlers/:name', element: routeElement(<ButlerDetailPage />) },
        { path: '/sessions', element: routeElement(<SessionsPage />) },
        { path: '/sessions/:id', element: routeElement(<SessionDetailPage />) },
        { path: '/timeline', element: routeElement(<TimelinePage />) },
        { path: '/notifications', element: routeElement(<NotificationsPage />) },
        { path: '/issues', element: routeElement(<IssuesPage />) },
        { path: '/audit-log', element: routeElement(<AuditLogPage />) },
        { path: '/approvals', element: routeElement(<ApprovalsPage />) },
        { path: '/approvals/:id', element: routeElement(<ApprovalsPage />) },
        { path: '/decisions', element: routeElement(<DecisionsPage />) },
        { path: '/beads/:beadId', element: routeElement(<BeadDetailPage />) },
        {
          path: '/calendar',
          element: routeElement(<CalendarWorkspacePage />),
        },
        // /contacts → /entities?has=contact (§8.10 entity-redesign redirect)
        { path: '/contacts', element: <Navigate to="/entities/index?has=contact" replace /> },
        // /contacts/:contactId → /entities?has=contact compatibility redirect.
        // public.contacts was dropped (core_134) and the per-contact resolver
        // endpoint no longer exists, so legacy contact bookmarks forward to the
        // entity index filter instead of resolving an individual entity.
        {
          path: '/contacts/:contactId',
          element: <Navigate to="/entities/index?has=contact" replace />,
        },
        { path: '/health', element: routeElement(<HealthOverviewPage />) },
        { path: '/health/measurements', element: routeElement(<MeasurementsPage />) },
        { path: '/health/medications', element: routeElement(<MedicationsPage />) },
        { path: '/health/conditions', element: routeElement(<ConditionsPage />) },
        { path: '/health/symptoms', element: routeElement(<SymptomsPage />) },
        { path: '/health/meals', element: routeElement(<MealsPage />) },
        { path: '/health/research', element: routeElement(<ResearchPage />) },
        // One Spend surface (JARVIS audit move 8, bu-86c4c.11): /costs and
        // /settings/spend merged into a single nav-visible /spend page.
        // Legacy bookmarks forward to it.
        { path: '/spend', element: routeElement(<SpendPage />) },
        { path: '/costs', element: <Navigate to="/spend" replace /> },
        { path: '/memory', element: routeElement(<MemoryPage />) },
        { path: '/memory/facts/:factId', element: routeElement(<FactDetailPage />) },
        { path: '/memory/rules/:ruleId', element: routeElement(<RuleDetailPage />) },
        { path: '/memory/episodes/:episodeId', element: routeElement(<EpisodeDetailPage />) },
        { path: '/entities', element: routeElement(<PlexPage />) },
        { path: '/entities/index', element: routeElement(<EntitiesIndexPage />) },
        // Hop and Columns were absorbed by the Plex; deep links carry over.
        { path: '/entities/hop', element: <HopToPlexRedirect /> },
        { path: '/entities/columns', element: <ColumnsToPlexRedirect /> },
        { path: '/entities/social-map', element: <Navigate to="/entities" replace /> },
        { path: '/entities/concentration', element: routeElement(<ConcentrationPage />) },
        // Circles (JARVIS audit move 14): retires the standalone /groups page
        // into an entities lens — see CirclesPage.tsx.
        { path: '/entities/circles', element: routeElement(<CirclesPage />) },
        // Legacy /groups bookmarks forward to the new home.
        { path: '/groups', element: <Navigate to="/entities/circles" replace /> },
        { path: '/entities/:entityId', element: routeElement(<EntityDetailPage />) },
        { path: '/settings', element: routeElement(<SettingsConsolePage />) },
        { path: '/settings/spend', element: <Navigate to="/spend" replace /> },
        { path: '/settings/permissions', element: routeElement(<SettingsPermissionsPage />) },
        { path: '/settings/models', element: routeElement(<SettingsModelsPage />) },
        { path: '/secrets', element: routeElement(<SecretsPage />) },
        { path: '/education', element: routeElement(<EducationPage />) },
        // Relationship butler: legacy paths redirect into unified canonical pages.
        {
          path: '/butlers/relationship/entities/:entityId',
          element: <RelationshipEntityRedirect />,
        },
        {
          path: '/butlers/relationship/contacts/:id',
          element: <RelationshipContactRedirect />,
        },
        // Chronicler routes
        {
          path: '/chronicles',
          element: routeElement(<ChroniclesPage />),
        },
        // QA Staffer routes
        { path: '/qa', element: routeElement(<QaOverviewPage />) },
        { path: '/qa/patrols/:patrolId', element: routeElement(<QaPatrolDetailPage />) },
        // The flat /qa/investigations index was folded into /qa itself, whose
        // filters (severity/since/state/butler) are now URL-persisted —
        // bu-86c4c.19 (JARVIS audit move 14, "one canonical case index").
        // Legacy bookmarks forward to the merged page.
        { path: '/qa/investigations', element: <Navigate to="/qa" replace /> },
        { path: '/qa/investigations/:attemptId', element: routeElement(<QaInvestigationDetailPage />) },
        // Ingestion routes — first-class sub-routes in the Dispatch visual
        // language, with 301-equivalent redirects from legacy ?tab= URLs.
        //
        // Spec: openspec/specs/dashboard-ingestion-dispatch-console/spec.md
        //       dashboard-shell/spec.md
        //
        // Route hierarchy:
        //   /ingestion                                    Timeline ledger (default)
        //   /ingestion/connectors                         Connectors roster
        //   /ingestion/connectors/:connectorType/:id      Connector detail
        //   /ingestion/filters                            Filters pipeline
        //
        // Legacy compat:
        //   ?tab=connectors  → /ingestion/connectors
        //   ?tab=filters     → /ingestion/filters
        //   ?tab=history     → /ingestion (Timeline; no /ingestion/history primary route)
        //   ?tab=timeline    → /ingestion (strips param)
        //   /ingestion/history → /ingestion (redirect; route retained for bookmark compat)
        //
        // Root /ingestion: redirect ?tab= params → sub-routes; else Timeline.
        { path: '/ingestion', element: <IngestionTabRedirect /> },
        // First-class sub-routes
        { path: '/ingestion/connectors', element: routeElement(<IngestionConnectorsPage />) },
        { path: '/ingestion/filters', element: routeElement(<IngestionFiltersPage />) },
        // /ingestion/history: bookmark compat redirect → Timeline
        // There is no primary redesigned /ingestion/history route.
        { path: '/ingestion/history', element: <Navigate to="/ingestion" replace /> },
        {
          path: '/ingestion/connectors/:connectorType/:endpointIdentity',
          element: routeElement(<ConnectorDetailPage />),
        },
        // System page
        {
          path: '/system',
          element: routeElement(<SystemPage />),
        },
        // Legacy /connectors redirects → /ingestion equivalents (spec section 3.3)
        {
          path: '/connectors',
          element: <Navigate to="/ingestion/connectors" replace />,
        },
        {
          path: '/connectors/:connectorType/:endpointIdentity',
          element: <ConnectorDetailRedirect />,
        },
      ],
    },
  ],
  { basename: _baseUrl },
)
