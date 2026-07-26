/**
 * Sidebar path -> route JS-chunk loader map (bu-ep4ks.15).
 *
 * Every dashboard page is its own `React.lazy(() => import(...))` chunk (see
 * router-config.tsx's header comment) -- clicking a Sidebar item that has
 * never been visited this session pays a network round-trip for that chunk
 * before the route can render. This is a DIFFERENT prefetch concern from
 * lib/prefetch-registry.ts (which warms react-query's DATA cache for a few
 * detail routes): this module warms the browser's module cache for the
 * Sidebar's own top-level/subroute targets, all of which are static routes
 * with no dynamic id segment (unlike prefetch-registry.ts's detail routes),
 * so a plain exact-match Record is enough -- no regex matchers needed.
 *
 * Each entry duplicates the import() specifier already declared in
 * router-config.tsx's `lazy()` calls (router-config.tsx only exports the
 * wrapped `lazy()` component references, not the raw loader functions, so
 * there is nothing to import from there directly). Vite resolves a dynamic
 * `import()` by MODULE PATH, not call site, so this does not create a
 * second copy of any chunk -- it is the exact same chunk router-config.tsx's
 * `lazy()` will eventually request; calling the loader early just warms the
 * browser's cache for it. A renamed page file breaks BOTH this file's and
 * router-config.tsx's import specifiers, and `tsc -b` (a required gate)
 * catches that drift at compile time.
 *
 * `/ingestion` is mapped to IngestionTimelinePage (not IngestionConnectorsPage)
 * to match IngestionTabRedirect's (router.tsx) actual default render when no
 * `?tab=` param is present -- see router-config.tsx's Ingestion route-hierarchy
 * comment.
 */

type ChunkLoader = () => Promise<unknown>;

export const ROUTE_CHUNK_LOADERS: Record<string, ChunkLoader> = {
  "/": () => import("@/pages/DashboardPage.tsx"),
  "/butlers": () => import("@/pages/ButlersPage.tsx"),
  "/qa": () => import("@/pages/QaOverviewPage.tsx"),
  "/ingestion": () => import("@/pages/IngestionTimelinePage.tsx"),
  "/approvals": () => import("@/pages/ApprovalsPage.tsx"),
  "/decisions": () => import("@/pages/DecisionsPage.tsx"),
  "/memory": () => import("@/pages/MemoryPage.tsx"),
  "/entities": () => import("@/components/relationship/PlexPage.tsx"),
  "/secrets": () => import("@/pages/SecretsPage.tsx"),
  "/settings": () => import("@/pages/SettingsConsolePage.tsx"),
  "/education": () => import("@/pages/EducationPage.tsx"),
  "/health": () => import("@/pages/HealthOverviewPage.tsx"),
  "/health/measurements": () => import("@/pages/MeasurementsPage.tsx"),
  "/health/medications": () => import("@/pages/MedicationsPage.tsx"),
  "/health/conditions": () => import("@/pages/ConditionsPage.tsx"),
  "/health/symptoms": () => import("@/pages/SymptomsPage.tsx"),
  "/health/meals": () => import("@/pages/MealsPage.tsx"),
  "/health/research": () => import("@/pages/ResearchPage.tsx"),
  "/calendar": () => import("@/pages/CalendarWorkspacePage.tsx"),
  "/chronicles": () => import("@/pages/ChroniclesPage.tsx"),
  "/timeline": () => import("@/pages/TimelinePage.tsx"),
  "/notifications": () => import("@/pages/NotificationsPage.tsx"),
  "/issues": () => import("@/pages/IssuesPage.tsx"),
  "/sessions": () => import("@/pages/SessionsPage.tsx"),
  "/spend": () => import("@/pages/SpendPage.tsx"),
  "/audit-log": () => import("@/pages/AuditLogPage.tsx"),
  "/system": () => import("@/pages/SystemPage.tsx"),
};

/** Resolve a Sidebar path to its chunk loader, or null if unmapped. */
export function resolveRouteChunkLoader(pathname: string): ChunkLoader | null {
  return ROUTE_CHUNK_LOADERS[pathname] ?? null;
}
