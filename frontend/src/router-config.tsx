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

import { createBrowserRouter, Navigate } from 'react-router'
import RootLayout from './layouts/RootLayout.tsx'
import DashboardPage from './pages/DashboardPage.tsx'
import ButlersPage from './pages/ButlersPage.tsx'
import ButlerDetailPage from './pages/ButlerDetailPage.tsx'
import SessionsPage from './pages/SessionsPage.tsx'
import SessionDetailPage from './pages/SessionDetailPage.tsx'
import TimelinePage from './pages/TimelinePage.tsx'
import NotificationsPage from './pages/NotificationsPage.tsx'
import IssuesPage from './pages/IssuesPage.tsx'
import CostsPage from './pages/CostsPage.tsx'
import MemoryPage from './pages/MemoryPage.tsx'
import FactDetailPage from './pages/FactDetailPage.tsx'
import RuleDetailPage from './pages/RuleDetailPage.tsx'
import EpisodeDetailPage from './pages/EpisodeDetailPage.tsx'
import SettingsConsolePage from './pages/SettingsConsolePage.tsx'
import SettingsSpendPage from './pages/SettingsSpendPage.tsx'
import SettingsPermissionsPage from './pages/SettingsPermissionsPage.tsx'
import SettingsModelsPage from './pages/SettingsModelsPage.tsx'
import AuditLogPage from './pages/AuditLogPage.tsx'
import GroupsPage from './pages/GroupsPage.tsx'
import HealthOverviewPage from './pages/HealthOverviewPage.tsx'
import MeasurementsPage from './pages/MeasurementsPage.tsx'
import MedicationsPage from './pages/MedicationsPage.tsx'
import ConditionsPage from './pages/ConditionsPage.tsx'
import SymptomsPage from './pages/SymptomsPage.tsx'
import MealsPage from './pages/MealsPage.tsx'
import ResearchPage from './pages/ResearchPage.tsx'
import ApprovalsPage from './pages/ApprovalsPage.tsx'
import ApprovalRulesPage from './pages/ApprovalRulesPage.tsx'
import SecretsPage from './pages/SecretsPage.tsx'
import CalendarWorkspacePage from './pages/CalendarWorkspacePage.tsx'
import EducationPage from './pages/EducationPage.tsx'
import { EntitiesIndexPage } from './components/relationship/EntitiesIndexPage.tsx'
import EntityDetailPage from './pages/EntityDetailPage.tsx'
import HopPage from './components/relationship/HopPage.tsx'
import ColumnsPage from './components/relationship/ColumnsPage.tsx'
import ConcentrationPage from './components/relationship/ConcentrationPage.tsx'
import SocialMapPage from './pages/SocialMapPage.tsx'
import IngestionPage from './pages/IngestionPage.tsx'
import IngestionConnectorsPage from './pages/IngestionConnectorsPage.tsx'
import IngestionFiltersPage from './pages/IngestionFiltersPage.tsx'
import IngestionHistoryPage from './pages/IngestionHistoryPage.tsx'
import ConnectorDetailPage from './pages/ConnectorDetailPage.tsx'
import QaOverviewPage from './pages/QaOverviewPage.tsx'
import QaPatrolDetailPage from './pages/QaPatrolDetailPage.tsx'
import QaInvestigationDetailPage from './pages/QaInvestigationDetailPage.tsx'
import QaInvestigationsPage from './pages/QaInvestigationsPage.tsx'
import ChroniclesPage from './pages/ChroniclesPage.tsx'
import SystemPage from './pages/SystemPage.tsx'
import {
  ConnectorDetailRedirect,
  ContactEntityRedirect,
  IngestionTabRedirect,
  RelationshipContactRedirect,
  RelationshipEntityRedirect,
} from './router.tsx'
import { INGESTION_DISPATCH_CONSOLE } from './lib/feature-flags.ts'

const _baseUrl = (import.meta.env.BASE_URL || '/').replace(/\/+$/, '') || '/'

export const router = createBrowserRouter(
  [
    {
      element: <RootLayout />,
      children: [
        { path: '/', element: <DashboardPage /> },
        { path: '/butlers', element: <ButlersPage /> },
        { path: '/butlers/:name', element: <ButlerDetailPage /> },
        { path: '/sessions', element: <SessionsPage /> },
        { path: '/sessions/:id', element: <SessionDetailPage /> },
        { path: '/timeline', element: <TimelinePage /> },
        { path: '/notifications', element: <NotificationsPage /> },
        { path: '/issues', element: <IssuesPage /> },
        { path: '/audit-log', element: <AuditLogPage /> },
        { path: '/approvals', element: <ApprovalsPage /> },
        { path: '/approvals/rules', element: <ApprovalRulesPage /> },
        { path: '/calendar', element: <CalendarWorkspacePage /> },
        // /contacts → /entities?has=contact (§8.10 entity-redesign redirect)
        { path: '/contacts', element: <Navigate to="/entities?has=contact" replace /> },
        // /contacts/:contactId → /entities/:entityId via entity-id lookup.
        // Renders a recovery state for unlinked or missing contacts.
        // Spec: openspec/changes/decommission-contact-detail-page/tasks.md §4
        { path: '/contacts/:contactId', element: <ContactEntityRedirect /> },
        { path: '/groups', element: <GroupsPage /> },
        { path: '/health', element: <HealthOverviewPage /> },
        { path: '/health/measurements', element: <MeasurementsPage /> },
        { path: '/health/medications', element: <MedicationsPage /> },
        { path: '/health/conditions', element: <ConditionsPage /> },
        { path: '/health/symptoms', element: <SymptomsPage /> },
        { path: '/health/meals', element: <MealsPage /> },
        { path: '/health/research', element: <ResearchPage /> },
        { path: '/costs', element: <CostsPage /> },
        { path: '/memory', element: <MemoryPage /> },
        { path: '/memory/facts/:factId', element: <FactDetailPage /> },
        { path: '/memory/rules/:ruleId', element: <RuleDetailPage /> },
        { path: '/memory/episodes/:episodeId', element: <EpisodeDetailPage /> },
        { path: '/entities', element: <EntitiesIndexPage /> },
        { path: '/entities/hop', element: <HopPage /> },
        { path: '/entities/columns', element: <ColumnsPage /> },
        { path: '/entities/concentration', element: <ConcentrationPage /> },
        { path: '/entities/social-map', element: <SocialMapPage /> },
        { path: '/entities/:entityId', element: <EntityDetailPage /> },
        { path: '/settings', element: <SettingsConsolePage /> },
        { path: '/settings/spend', element: <SettingsSpendPage /> },
        { path: '/settings/permissions', element: <SettingsPermissionsPage /> },
        { path: '/settings/models', element: <SettingsModelsPage /> },
        { path: '/secrets', element: <SecretsPage /> },
        { path: '/education', element: <EducationPage /> },
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
        { path: '/chronicles', element: <ChroniclesPage /> },
        // QA Staffer routes
        { path: '/qa', element: <QaOverviewPage /> },
        { path: '/qa/patrols/:patrolId', element: <QaPatrolDetailPage /> },
        { path: '/qa/investigations', element: <QaInvestigationsPage /> },
        { path: '/qa/investigations/:attemptId', element: <QaInvestigationDetailPage /> },
        // Ingestion routes — behaviour depends on INGESTION_DISPATCH_CONSOLE flag.
        //
        // Flag ON (default in dev): first-class sub-routes + 301-equivalent redirects
        //   from legacy ?tab= URLs per dashboard-ingestion-dispatch-console spec.
        // Flag OFF (default in prod): legacy single-route IngestionPage with ?tab= param.
        //
        // Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
        //       dashboard-ingestion-dispatch-console/spec.md
        //       dashboard-shell/spec.md
        //
        // Route hierarchy (flag ON):
        //   /ingestion                                    Timeline ledger (default)
        //   /ingestion/connectors                         Connectors roster
        //   /ingestion/connectors/:connectorType/:id      Connector detail
        //   /ingestion/filters                            Filters pipeline
        //
        // Legacy compat (flag ON):
        //   ?tab=connectors  → /ingestion/connectors
        //   ?tab=filters     → /ingestion/filters
        //   ?tab=history     → /ingestion (Timeline; no /ingestion/history primary route)
        //   ?tab=timeline    → /ingestion (strips param)
        //   /ingestion/history → /ingestion (redirect; route retained for bookmark compat)
        ...(INGESTION_DISPATCH_CONSOLE
          ? [
              // Root /ingestion: redirect ?tab= params → sub-routes; else Timeline.
              { path: '/ingestion', element: <IngestionTabRedirect /> },
              // First-class sub-routes
              { path: '/ingestion/connectors', element: <IngestionConnectorsPage /> },
              { path: '/ingestion/filters', element: <IngestionFiltersPage /> },
              // /ingestion/history: bookmark compat redirect → Timeline
              // There is no primary redesigned /ingestion/history route.
              { path: '/ingestion/history', element: <Navigate to="/ingestion" replace /> },
            ]
          : [
              // Legacy single-route with ?tab= param (prod-safe fallback)
              { path: '/ingestion', element: <IngestionPage /> },
              // Retain history sub-route in legacy mode
              { path: '/ingestion/history', element: <IngestionHistoryPage /> },
            ]),
        {
          path: '/ingestion/connectors/:connectorType/:endpointIdentity',
          element: <ConnectorDetailPage />,
        },
        // System page
        { path: '/system', element: <SystemPage /> },
        // Legacy /connectors redirects → /ingestion equivalents (spec section 3.3)
        {
          path: '/connectors',
          element: <Navigate to="/ingestion?tab=connectors" replace />,
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
