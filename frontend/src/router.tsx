import { createBrowserRouter, Navigate, useParams, useSearchParams } from 'react-router'
import RootLayout from './layouts/RootLayout.tsx'
import DashboardPage from './pages/DashboardPage.tsx'
import ButlersPage from './pages/ButlersPage.tsx'
import ButlerDetailPage from './pages/ButlerDetailPage.tsx'
import SessionsPage from './pages/SessionsPage.tsx'
import SessionDetailPage from './pages/SessionDetailPage.tsx'
import TracesPage from './pages/TracesPage.tsx'
import TraceDetailPage from './pages/TraceDetailPage.tsx'
import TimelinePage from './pages/TimelinePage.tsx'
import NotificationsPage from './pages/NotificationsPage.tsx'
import IssuesPage from './pages/IssuesPage.tsx'
import CostsPage from './pages/CostsPage.tsx'
import MemoryPage from './pages/MemoryPage.tsx'
import SettingsPage from './pages/SettingsPage.tsx'
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
import IngestionPage from './pages/IngestionPage.tsx'
import ConnectorDetailPage from './pages/ConnectorDetailPage.tsx'

const _baseUrl = (import.meta.env.BASE_URL || '/').replace(/\/+$/, '') || '/'

// Redirect /connectors/:connectorType/:endpointIdentity
// → /ingestion/connectors/:connectorType/:endpointIdentity
// Preserves relevant query string params (period, date filters) per spec section 3.3.
function ConnectorDetailRedirect() {
  const { connectorType, endpointIdentity } = useParams()
  const [searchParams] = useSearchParams()
  const qs = searchParams.toString()
  const target = `/ingestion/connectors/${connectorType}/${endpointIdentity}${qs ? `?${qs}` : ''}`
  return <Navigate to={target} replace />
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
        { path: '/traces', element: <TracesPage /> },
        { path: '/traces/:traceId', element: <TraceDetailPage /> },
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
        { path: '/settings', element: <SettingsPage /> },
        { path: '/secrets', element: <SecretsPage /> },
        // Ingestion routes (spec section 3.1, 3.2)
        { path: '/ingestion', element: <IngestionPage /> },
        {
          path: '/ingestion/connectors/:connectorType/:endpointIdentity',
          element: <ConnectorDetailPage />,
        },
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
