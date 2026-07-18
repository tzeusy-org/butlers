import DashboardPage from "@/pages/DashboardPage";
import QaOverviewPage from "@/pages/QaOverviewPage";
import ApprovalsPage from "@/pages/ApprovalsPage";
import MemoryPage from "@/pages/MemoryPage";
import PlexPage from "@/components/relationship/PlexPage";
import SecretsPage from "@/pages/SecretsPage";
import SettingsConsolePage from "@/pages/SettingsConsolePage";
import EducationPage from "@/pages/EducationPage";
import HealthOverviewPage from "@/pages/HealthOverviewPage";
import CalendarWorkspacePage from "@/pages/CalendarWorkspacePage";
import ChroniclesPage from "@/pages/ChroniclesPage";
import NotificationsPage from "@/pages/NotificationsPage";
import IssuesPage from "@/pages/IssuesPage";
import SessionsPage from "@/pages/SessionsPage";
import SpendPage from "@/pages/SpendPage";
import AuditLogPage from "@/pages/AuditLogPage";
import SystemPage from "@/pages/SystemPage";
import MeasurementsPage from "@/pages/MeasurementsPage";
import MedicationsPage from "@/pages/MedicationsPage";
import ConditionsPage from "@/pages/ConditionsPage";
import SymptomsPage from "@/pages/SymptomsPage";
import MealsPage from "@/pages/MealsPage";
import ResearchPage from "@/pages/ResearchPage";
import SettingsPermissionsPage from "@/pages/SettingsPermissionsPage";
import SettingsModelsPage from "@/pages/SettingsModelsPage";
import { EntitiesIndexPage } from "@/components/relationship/EntitiesIndexPage";
import ConcentrationPage from "@/components/relationship/ConcentrationPage";
import CirclesPage from "@/components/relationship/CirclesPage";

/** Real page components exercised by route-pages.a11y.test.tsx. */
export const ROUTE_AXE_CASES = [
  ["/", DashboardPage], ["/qa", QaOverviewPage], ["/approvals", ApprovalsPage],
  ["/memory", MemoryPage], ["/entities", PlexPage], ["/secrets", SecretsPage],
  ["/settings", SettingsConsolePage], ["/education", EducationPage], ["/health", HealthOverviewPage],
  ["/calendar", CalendarWorkspacePage], ["/chronicles", ChroniclesPage], ["/notifications", NotificationsPage],
  ["/issues", IssuesPage], ["/sessions", SessionsPage], ["/spend", SpendPage], ["/audit-log", AuditLogPage],
  ["/system", SystemPage], ["/health/measurements", MeasurementsPage], ["/health/medications", MedicationsPage],
  ["/health/conditions", ConditionsPage], ["/health/symptoms", SymptomsPage], ["/health/meals", MealsPage],
  ["/health/research", ResearchPage], ["/settings/permissions", SettingsPermissionsPage], ["/settings/models", SettingsModelsPage],
  ["/entities/index", EntitiesIndexPage], ["/entities/concentration", ConcentrationPage], ["/entities/circles", CirclesPage],
  ["/entities/index?has=contact", EntitiesIndexPage],
] as const;

/** Paths with real page-level axe rendering, derived from ROUTE_AXE_CASES. */
export const ROUTE_AXE_PATHS = ROUTE_AXE_CASES.map(([path]) => path);
