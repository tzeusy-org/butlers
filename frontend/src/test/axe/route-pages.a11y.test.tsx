// @vitest-environment jsdom
/**
 * Real registered dashboard route accessibility coverage.
 *
 * This suite intentionally mounts production page components with their real
 * data hooks.  The fetch boundary is held pending so each route is inspected
 * in its real loading contract without replacing a page or a domain component
 * with a test double.  Dedicated page suites cover populated/error states
 * where those fixtures already exist; this route sweep ensures every
 * navigation-registered page has an axe pass through its own component tree.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { axe, toHaveNoViolations } from "jest-axe";

import { AppTimezoneProvider } from "@/components/ui/timezone-context";
import { EventBusProvider } from "@/lib/event-bus";
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

expect.extend(toHaveNoViolations);

const routes = [
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

async function checkRoute(path: string, Page: React.ComponentType): Promise<void> {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { container } = render(
    <QueryClientProvider client={client}>
      <AppTimezoneProvider timezone="UTC">
        <EventBusProvider>
          <MemoryRouter initialEntries={[path]}>
            <Page />
          </MemoryRouter>
        </EventBusProvider>
      </AppTimezoneProvider>
    </QueryClientProvider>,
  );
  expect(await axe(container, { rules: { "color-contrast": { enabled: false } } })).toHaveNoViolations();
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("a11y (real page): navigation-registered routes", () => {
  for (const [path, Page] of routes) {
    it(`${path} has zero axe violations in its real loading state`, async () => {
      vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
      await checkRoute(path, Page);
    });
  }
});
