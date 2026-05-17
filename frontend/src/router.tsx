import { createBrowserRouter, Navigate, useParams, useSearchParams } from 'react-router'
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
import ContactsPage from './pages/ContactsPage.tsx'
import ContactDetailPage from './pages/ContactDetailPage.tsx'
import GroupsPage from './pages/GroupsPage.tsx'
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
import EntitiesPage from './pages/EntitiesPage.tsx'
import EntityDetailPage from './pages/EntityDetailPage.tsx'
import SocialMapPage from './pages/SocialMapPage.tsx'
import IngestionPage from './pages/IngestionPage.tsx'
import ConnectorDetailPage from './pages/ConnectorDetailPage.tsx'
import QaOverviewPage from './pages/QaOverviewPage.tsx'
import QaPatrolDetailPage from './pages/QaPatrolDetailPage.tsx'
import QaInvestigationDetailPage from './pages/QaInvestigationDetailPage.tsx'
import QaInvestigationsPage from './pages/QaInvestigationsPage.tsx'
import ChroniclesPage from './pages/ChroniclesPage.tsx'
import SystemPage from './pages/SystemPage.tsx'
const _baseUrl = (import.meta.env.BASE_URL || '/').replace(/\/+$/, '') || '/'

// Redirect /connectors/:connectorType/:endpointIdentity
// → /ingestion/connectors/:connectorType/:endpointIdentity
// Preserves relevant query string params (period, date filters) per spec section 3.3.
// eslint-disable-next-line react-refresh/only-export-components
function ConnectorDetailRedirect() {
  const { connectorType, endpointIdentity } = useParams()
  const [searchParams] = useSearchParams()
  const qs = searchParams.toString()
  const target = `/ingestion/connectors/${connectorType}/${endpointIdentity}${qs ? `?${qs}` : ''}`
  return <Navigate to={target} replace />
}

// Redirect /butlers/relationship/entities/:entityId → /entities/:entityId
// The relationship-scoped activity view has been folded into the unified
// entity detail page.
// eslint-disable-next-line react-refresh/only-export-components
function RelationshipEntityRedirect() {
  const { entityId } = useParams()
  return <Navigate to={`/entities/${entityId ?? ''}`} replace />
}

// Redirect /butlers/relationship/contacts/:id → /contacts/:contactId
// The legacy relationship-scoped contact path has been superseded by the
// canonical contact detail page per the detail-page-archetype spec.
// eslint-disable-next-line react-refresh/only-export-components
function RelationshipContactRedirect() {
  const { id } = useParams()
  return <Navigate to={`/contacts/${id ?? ''}`} replace />
}

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
        { path: '/contacts', element: <ContactsPage /> },
        { path: '/contacts/:contactId', element: <ContactDetailPage /> },
        { path: '/groups', element: <GroupsPage /> },
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
        { path: '/entities', element: <EntitiesPage /> },
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
        // Ingestion routes (spec section 3.1, 3.2)
        { path: '/ingestion', element: <IngestionPage /> },
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
