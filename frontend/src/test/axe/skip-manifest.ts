/**
 * Route-registry-driven axe sweep — skip manifest (bu-qvnce.10, JARVIS
 * pursuit move 10).
 *
 * Burn-down list, NOT a gate-blocker: `route-sweep.a11y.test.tsx` asserts
 * that every path in `ALL_ROUTES` (src/lib/route-registry.ts) is either
 * axe-covered elsewhere (see `COVERED_ELSEWHERE` in that file) or has an
 * honest entry here explaining why it isn't yet. A route with NEITHER —
 * i.e. a newly added page nobody has made a decision about — fails the
 * sweep's completeness check. That's what "new pages get axe coverage
 * structurally" means in practice: silence is not an option, only an
 * explicit "covered" or "skipped, because X".
 *
 * To retire an entry: build it the way ButlersPage.a11y.test.tsx /
 * ButlerDetailPage.a11y.test.tsx / TimelineTab.a11y.test.tsx do — mock only
 * the page's data-hook layer (reuse the `vi.mock` list from its existing
 * `*.test.tsx`, referenced below where one exists), render the REAL page
 * component, run axe with `color-contrast` disabled (jsdom can't compute
 * it — see src/lib/contrast.test.ts for that coverage) — then delete the
 * entry here and add the route to `COVERED_ELSEWHERE`.
 */

export interface AxeSkipEntry {
  /** Exact path as it appears in ALL_ROUTES. */
  path: string;
  /** Honest, specific reason this route isn't swept yet. */
  reason: string;
}

export const AXE_SKIP_MANIFEST: AxeSkipEntry[] = [
  {
    path: "/",
    reason:
      "DashboardPage — not yet swept; needs its widget data-hook layer mocked (see DashboardPage.test.tsx for the existing hook list) before axe can render deterministically.",
  },
  {
    path: "/qa",
    reason: "QaOverviewPage — not yet swept; lift the hook-mock list from QaOverviewPage.test.tsx.",
  },
  {
    path: "/approvals",
    reason: "ApprovalsPage — not yet swept; lift the hook-mock list from ApprovalsPage.test.tsx.",
  },
  {
    path: "/memory",
    reason:
      "MemoryPage — no existing hook-mocking test to lift from either; needs a first pass identifying its data hooks.",
  },
  {
    path: "/entities",
    reason: "PlexPage — not yet swept; lift the hook-mock list from components/relationship/PlexPage.test.tsx.",
  },
  {
    path: "/secrets",
    reason: "SecretsPage — not yet swept; lift the hook-mock list from SecretsPage.test.tsx.",
  },
  {
    path: "/settings",
    reason: "SettingsConsolePage — not yet swept; lift the hook-mock list from SettingsConsolePage.test.tsx.",
  },
  {
    path: "/education",
    reason:
      "EducationPage — no existing hook-mocking test to lift from either; needs a first pass identifying its data hooks.",
  },
  {
    path: "/health",
    reason:
      "HealthOverviewPage — not yet swept; lift the hook-mock list from the \"Health Overview page\" describe block in HealthPages.view-only.test.tsx.",
  },
  {
    path: "/calendar",
    reason: "CalendarWorkspacePage — not yet swept; lift the hook-mock list from CalendarWorkspacePage.test.tsx.",
  },
  {
    path: "/chronicles",
    reason: "ChroniclesPage — not yet swept; lift the hook-mock list from ChroniclesPage.test.tsx.",
  },
  {
    path: "/notifications",
    reason: "NotificationsPage — not yet swept; lift the hook-mock list from NotificationsPage.test.tsx.",
  },
  {
    path: "/issues",
    reason:
      "IssuesPage — no existing hook-mocking test to lift from either; needs a first pass identifying its data hooks.",
  },
  {
    path: "/sessions",
    reason: "SessionsPage — not yet swept; lift the hook-mock list from SessionsPage.test.tsx.",
  },
  {
    path: "/spend",
    reason: "SpendPage — not yet swept; lift the hook-mock list from SpendPage.test.tsx.",
  },
  {
    path: "/audit-log",
    reason: "AuditLogPage — not yet swept; lift the hook-mock list from AuditLogPage.test.tsx.",
  },
  {
    path: "/system",
    reason: "SystemPage — not yet swept; lift the hook-mock list from SystemPage.test.tsx.",
  },
  {
    path: "/health/measurements",
    reason:
      "MeasurementsPage — not yet swept; lift the hook-mock list from the \"Measurements health page\" describe block in HealthPages.view-only.test.tsx.",
  },
  {
    path: "/health/medications",
    reason:
      "MedicationsPage — not yet swept; lift the hook-mock list from the \"Medications health page\" describe block in HealthPages.view-only.test.tsx.",
  },
  {
    path: "/health/conditions",
    reason:
      "ConditionsPage — not yet swept; lift the hook-mock list from the \"Conditions health page\" describe block in HealthPages.view-only.test.tsx.",
  },
  {
    path: "/health/symptoms",
    reason:
      "SymptomsPage — not yet swept; lift the hook-mock list from the \"Symptoms health page\" describe block in HealthPages.view-only.test.tsx.",
  },
  {
    path: "/health/meals",
    reason:
      "MealsPage — not yet swept; lift the hook-mock list from the \"Meals health page\" describe block in HealthPages.view-only.test.tsx.",
  },
  {
    path: "/health/research",
    reason:
      "ResearchPage — not yet swept; lift the hook-mock list from the \"Research health page\" describe block in HealthPages.view-only.test.tsx.",
  },
  {
    path: "/settings/permissions",
    reason: "SettingsPermissionsPage — not yet swept; lift the hook-mock list from SettingsPermissionsPage.test.tsx.",
  },
  {
    path: "/settings/models",
    reason: "SettingsModelsPage — not yet swept; lift the hook-mock list from SettingsModelsPage.test.tsx.",
  },
  {
    path: "/entities/index",
    reason:
      "EntitiesIndexPage — not yet swept; lift the hook-mock list from components/relationship/EntitiesIndexPage.test.tsx.",
  },
  {
    path: "/entities/concentration",
    reason:
      "ConcentrationPage — not yet swept; lift the hook-mock list from components/relationship/ConcentrationPage.test.tsx.",
  },
  {
    path: "/entities/circles",
    reason: "CirclesPage — not yet swept; lift the hook-mock list from components/relationship/CirclesPage.test.tsx.",
  },
  {
    path: "/entities/index?has=contact",
    reason:
      "EntitiesIndexPage (contact filter) — same component as /entities/index, filtered; covered by the same future pass.",
  },
];
