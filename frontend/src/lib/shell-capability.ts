/**
 * The shell capability manifest.
 *
 * A capability is the contract for one navigable dashboard destination.  The
 * shell derives its router, rail, command finder, shortcut help, chunk
 * prefetch, and query prefetch projections from this list.  Keeping the
 * loader next to the route metadata is intentional: a destination cannot be
 * discoverable without also having a lazy page boundary.
 */

import type { ComponentType } from "react";

import * as api from "@/api/index.ts";
import { ENTITY_DETAIL_INITIAL_PARAMS } from "@/lib/entity-detail-query";
import { POLL_BUS_RECONCILE_MS } from "@/lib/poll-policy";
import { fetchSpendForecast } from "@/lib/spend-forecast";
import type { NavIconName } from "@/components/layout/NavIcon";

export type ShellDiscoverability = "global" | "contextual" | "context-only";
export type ShellFamily =
  | "overview"
  | "operations"
  | "telemetry"
  | "memory"
  | "relationship"
  | "settings"
  | "butler"
  | "ingestion"
  | "detail";

export interface ShellQueryWarmup {
  queryKey: readonly unknown[];
  queryFn: () => Promise<unknown>;
  staleTime: number;
}

export type ShellPageLoader = () => Promise<{ default: ComponentType }>;

export interface ShellCapability {
  path: string;
  label: string;
  keywords: readonly string[];
  family: ShellFamily;
  placement: ShellPlacement | null;
  subnav?: { order: number; end?: boolean; label?: string };
  chord?: string;
  discoverability: ShellDiscoverability;
  /** Dynamic route policy. Static capabilities deliberately omit this field. */
  dynamic?: "search-backed" | "context-only";
  loader: ShellPageLoader;
  /** Resolves the exact query cache entry the destination consumes. */
  queryWarmup?: (to: string) => ShellQueryWarmup | null;
}

export interface ShellPlacement {
  section: "Main" | "Dedicated Butlers" | "Telemetry";
  order: number;
  group?: string;
  defaultExpanded?: boolean;
  butler?: string;
  icon?: NavIconName;
  badgeKey?: string;
  badgeVariant?: "red" | "amber";
  tooltip?: string;
  end?: boolean;
}

const DEFAULT_QUERY_STALE_TIME_MS = 30_000;
const TIMELINE_HEAD_PAGE_SIZE = 50;
const SESSION_LIST_INITIAL_PARAMS = { limit: 20 };

function staticWarmup(
  path: string,
  queryKey: readonly unknown[],
  queryFn: () => Promise<unknown>,
  staleTime = DEFAULT_QUERY_STALE_TIME_MS,
) {
  return (to: string): ShellQueryWarmup | null =>
    to === path ? { queryKey, queryFn, staleTime } : null;
}

function segmentPattern(path: string, to: string): string | null {
  const pathname = to.split("?", 1)[0].split("#", 1)[0];
  const expected = path.split("/");
  const actual = pathname.split("/");
  if (expected.length !== actual.length) return null;
  for (let i = 0; i < expected.length; i += 1) {
    if (expected[i].startsWith(":")) continue;
    if (expected[i] !== actual[i]) return null;
  }
  return actual[expected.findIndex((part) => part.startsWith(":"))] ?? null;
}

const dynamic =
  (path: string, factory: (id: string) => ShellQueryWarmup): ShellCapability["queryWarmup"] =>
  (to) => {
    const id = segmentPattern(path, to);
    if (!id) return null;
    try {
      return factory(decodeURIComponent(id));
    } catch {
      return null;
    }
  };

const ingestionEventWarmup: ShellCapability["queryWarmup"] = (to) => {
  const [pathname, queryAndHash = ""] = to.split("?", 2);
  if (pathname !== "/ingestion") return null;
  const eventId = new URLSearchParams(queryAndHash.split("#", 1)[0]).get("event");
  if (!eventId) return null;
  return {
    queryKey: ["ingestion", "events", eventId, "detail"],
    queryFn: () => api.getIngestionEvent(eventId),
    staleTime: POLL_BUS_RECONCILE_MS,
  };
};

const page = (loader: () => Promise<{ default: ComponentType }>) => loader;

/** One typed source for all shell projections. */
export const SHELL_CAPABILITIES: readonly ShellCapability[] = [
  { path: "/", label: "Overview", keywords: ["home", "dashboard"], family: "overview", placement: { section: "Main", order: 0, icon: "overview", end: true }, chord: "o", discoverability: "global", loader: page(() => import("@/pages/DashboardPage.tsx")) },
  { path: "/butlers", label: "Butlers", keywords: ["staff", "agents"], family: "butler", placement: { section: "Main", order: 1, icon: "butlers" }, chord: "b", discoverability: "global", loader: page(() => import("@/pages/ButlersPage.tsx")) },
  { path: "/qa", label: "QA", keywords: ["quality", "patrol"], family: "operations", placement: { section: "Main", order: 2, butler: "qa", badgeKey: "qa-escalations", badgeVariant: "red", icon: "qa" }, discoverability: "global", loader: page(() => import("@/pages/QaOverviewPage.tsx")) },
  { path: "/ingestion", label: "Ingestion", keywords: ["dispatch", "timeline", "events"], family: "ingestion", placement: { section: "Main", order: 3, icon: "ingestion" }, subnav: { order: 0, end: true, label: "Timeline" }, chord: "e", discoverability: "global", loader: page(() => import("@/pages/IngestionTimelinePage.tsx")), queryWarmup: ingestionEventWarmup },
  { path: "/approvals", label: "Approvals", keywords: ["pending", "review"], family: "operations", placement: { section: "Main", order: 4, icon: "approvals", badgeKey: "approvals-pending", badgeVariant: "amber" }, chord: "p", discoverability: "global", loader: page(() => import("@/pages/ApprovalsPage.tsx")) },
  { path: "/decisions", label: "Decisions", keywords: ["approval", "autonomy"], family: "operations", placement: { section: "Main", order: 5, icon: "decisions", badgeKey: "decisions-open", badgeVariant: "amber" }, chord: "d", discoverability: "global", loader: page(() => import("@/pages/DecisionsPage.tsx")) },
  { path: "/memory", label: "Memory", keywords: ["facts", "episodes", "rules"], family: "memory", placement: { section: "Main", order: 6, icon: "memory" }, chord: "m", discoverability: "global", loader: page(() => import("@/pages/MemoryPage.tsx")) },
  { path: "/entities", label: "Entities", keywords: ["people", "relationships", "plex"], family: "relationship", placement: { section: "Main", order: 7, icon: "entities" }, discoverability: "global", loader: page(() => import("@/components/relationship/PlexPage.tsx")) },
  { path: "/secrets", label: "Secrets", keywords: ["credentials", "keys"], family: "settings", placement: { section: "Main", order: 8, icon: "secrets" }, discoverability: "global", loader: page(() => import("@/pages/SecretsPage.tsx")) },
  { path: "/settings", label: "Settings", keywords: ["configuration", "preferences"], family: "settings", placement: { section: "Main", order: 9, icon: "settings" }, discoverability: "global", loader: page(() => import("@/pages/SettingsConsolePage.tsx")) },

  { path: "/education", label: "Education", keywords: ["learning", "study"], family: "butler", placement: { section: "Dedicated Butlers", order: 0, butler: "education" }, discoverability: "global", loader: page(() => import("@/pages/EducationPage.tsx")) },
  { path: "/health", label: "Overview", keywords: ["health", "wellness"], family: "butler", placement: { section: "Dedicated Butlers", order: 1, group: "Health", butler: "health", end: true }, chord: "h", discoverability: "global", loader: page(() => import("@/pages/HealthOverviewPage.tsx")), queryWarmup: staticWarmup("/health", ["health-measurement-types"], () => api.getMeasurementTypes()) },
  { path: "/calendar", label: "Calendar", keywords: ["schedule", "events"], family: "butler", placement: { section: "Dedicated Butlers", order: 2 }, discoverability: "global", loader: page(() => import("@/pages/CalendarWorkspacePage.tsx")) },
  { path: "/chronicles", label: "Chronicles", keywords: ["retrospective", "lived time"], family: "butler", placement: { section: "Dedicated Butlers", order: 3, butler: "chronicler", tooltip: "Retrospective lived-time reconstruction" }, discoverability: "global", loader: page(() => import("@/pages/ChroniclesPage.tsx")) },

  { path: "/timeline", label: "Timeline", keywords: ["events", "ledger", "activity"], family: "telemetry", placement: { section: "Telemetry", order: 0, icon: "timeline" }, chord: "t", discoverability: "global", loader: page(() => import("@/pages/TimelinePage.tsx")), queryWarmup: staticWarmup("/timeline", ["timeline", { limit: TIMELINE_HEAD_PAGE_SIZE }], () => api.getTimeline({ limit: TIMELINE_HEAD_PAGE_SIZE }), POLL_BUS_RECONCILE_MS) },
  { path: "/notifications", label: "Notifications", keywords: ["alerts", "messages"], family: "telemetry", placement: { section: "Telemetry", order: 1, icon: "notifications" }, chord: "n", discoverability: "global", loader: page(() => import("@/pages/NotificationsPage.tsx")) },
  { path: "/issues", label: "Issues", keywords: ["errors", "bugs"], family: "telemetry", placement: { section: "Telemetry", order: 2, icon: "issues" }, chord: "i", discoverability: "global", loader: page(() => import("@/pages/IssuesPage.tsx")) },
  { path: "/sessions", label: "Sessions", keywords: ["runs", "history"], family: "telemetry", placement: { section: "Telemetry", order: 3, icon: "sessions" }, chord: "s", discoverability: "global", loader: page(() => import("@/pages/SessionsPage.tsx")), queryWarmup: staticWarmup("/sessions", ["sessions", SESSION_LIST_INITIAL_PARAMS], () => api.getSessions(SESSION_LIST_INITIAL_PARAMS)) },
  { path: "/spend", label: "Spend", keywords: ["costs", "forecast", "usage"], family: "telemetry", placement: { section: "Telemetry", order: 4, icon: "spend" }, discoverability: "global", loader: page(() => import("@/pages/SpendPage.tsx")), queryWarmup: staticWarmup("/spend", ["spend-forecast"], fetchSpendForecast) },
  { path: "/audit-log", label: "Audit Log", keywords: ["history", "actions"], family: "telemetry", placement: { section: "Telemetry", order: 5, icon: "audit" }, chord: "a", discoverability: "global", loader: page(() => import("@/pages/AuditLogPage.tsx")) },
  { path: "/system", label: "System", keywords: ["instance", "runtime"], family: "telemetry", placement: { section: "Telemetry", order: 6, icon: "system", tooltip: "Instance ownership and runtime facts" }, discoverability: "global", loader: page(() => import("@/pages/SystemPage.tsx")) },

  { path: "/health/measurements", label: "Measurements", keywords: ["health", "vitals"], family: "butler", placement: { section: "Dedicated Butlers", order: 10, group: "Health", butler: "health" }, discoverability: "global", loader: page(() => import("@/pages/MeasurementsPage.tsx")) },
  { path: "/health/medications", label: "Medications", keywords: ["health", "prescriptions"], family: "butler", placement: { section: "Dedicated Butlers", order: 11, group: "Health", butler: "health" }, discoverability: "global", loader: page(() => import("@/pages/MedicationsPage.tsx")) },
  { path: "/health/conditions", label: "Conditions", keywords: ["health", "diagnoses"], family: "butler", placement: { section: "Dedicated Butlers", order: 12, group: "Health", butler: "health" }, discoverability: "global", loader: page(() => import("@/pages/ConditionsPage.tsx")) },
  { path: "/health/symptoms", label: "Symptoms", keywords: ["health", "signs"], family: "butler", placement: { section: "Dedicated Butlers", order: 13, group: "Health", butler: "health" }, discoverability: "global", loader: page(() => import("@/pages/SymptomsPage.tsx")) },
  { path: "/health/meals", label: "Meals", keywords: ["health", "nutrition"], family: "butler", placement: { section: "Dedicated Butlers", order: 14, group: "Health", butler: "health" }, discoverability: "global", loader: page(() => import("@/pages/MealsPage.tsx")) },
  { path: "/health/research", label: "Research", keywords: ["health", "studies"], family: "butler", placement: { section: "Dedicated Butlers", order: 15, group: "Health", butler: "health" }, discoverability: "global", loader: page(() => import("@/pages/ResearchPage.tsx")) },

  { path: "/settings/permissions", label: "Permissions", keywords: ["settings", "access"], family: "settings", placement: null, discoverability: "global", loader: page(() => import("@/pages/SettingsPermissionsPage.tsx")) },
  { path: "/settings/models", label: "Models", keywords: ["settings", "runtime"], family: "settings", placement: null, discoverability: "global", loader: page(() => import("@/pages/SettingsModelsPage.tsx")) },
  { path: "/entities/index", label: "Entities Index", keywords: ["entities", "contacts", "people"], family: "relationship", placement: null, discoverability: "global", loader: page(() => import("@/components/relationship/EntitiesIndexPage.tsx").then(({ EntitiesIndexPage }) => ({ default: EntitiesIndexPage }))) },
  { path: "/entities/index?has=contact", label: "Contacts", keywords: ["entities", "contacts"], family: "relationship", placement: null, chord: "c", discoverability: "global", loader: page(() => import("@/components/relationship/EntitiesIndexPage.tsx").then(({ EntitiesIndexPage }) => ({ default: EntitiesIndexPage }))) },
  { path: "/entities/concentration", label: "Concentration", keywords: ["entities", "network"], family: "relationship", placement: null, discoverability: "global", loader: page(() => import("@/components/relationship/ConcentrationPage.tsx")) },
  { path: "/entities/circles", label: "Circles", keywords: ["entities", "groups", "relationships"], family: "relationship", placement: null, discoverability: "global", loader: page(() => import("@/components/relationship/CirclesPage.tsx")) },
  { path: "/ingestion/connectors", label: "Connectors", keywords: ["ingestion", "providers", "channels"], family: "ingestion", placement: null, subnav: { order: 1 }, chord: "r", discoverability: "global", loader: page(() => import("@/pages/IngestionConnectorsPage.tsx")) },
  { path: "/ingestion/filters", label: "Filters", keywords: ["ingestion", "rules", "routing"], family: "ingestion", placement: null, subnav: { order: 2 }, chord: "f", discoverability: "global", loader: page(() => import("@/pages/IngestionFiltersPage.tsx")) },

  { path: "/butlers/:name", label: "Butler detail", keywords: ["butler", "agent"], family: "detail", placement: null, dynamic: "search-backed", discoverability: "contextual", loader: page(() => import("@/pages/ButlerDetailPage.tsx")), queryWarmup: dynamic("/butlers/:name", (name) => ({ queryKey: ["butlers", name], queryFn: () => api.getButler(name), staleTime: DEFAULT_QUERY_STALE_TIME_MS })) },
  { path: "/sessions/:id", label: "Session detail", keywords: ["session", "run"], family: "detail", placement: null, dynamic: "search-backed", discoverability: "contextual", loader: page(() => import("@/pages/SessionDetailPage.tsx")), queryWarmup: dynamic("/sessions/:id", (id) => ({ queryKey: ["session-detail-global", id], queryFn: () => api.getSession(id), staleTime: POLL_BUS_RECONCILE_MS })) },
  { path: "/entities/:entityId", label: "Entity detail", keywords: ["entity", "person"], family: "detail", placement: null, dynamic: "search-backed", discoverability: "contextual", loader: page(() => import("@/pages/EntityDetailPage.tsx")), queryWarmup: (to) => {
    const id = segmentPattern("/entities/:entityId", to)
    if (!id || ["index", "concentration", "circles", "hop", "columns", "social-map"].includes(id)) return null
    try {
      const decoded = decodeURIComponent(id)
      return { queryKey: ["memory-entity", decoded, ENTITY_DETAIL_INITIAL_PARAMS], queryFn: () => api.getEntity(decoded, ENTITY_DETAIL_INITIAL_PARAMS), staleTime: DEFAULT_QUERY_STALE_TIME_MS }
    } catch {
      return null
    }
  } },
  { path: "/memory/facts/:factId", label: "Fact detail", keywords: ["memory", "fact"], family: "detail", placement: null, dynamic: "search-backed", discoverability: "contextual", loader: page(() => import("@/pages/FactDetailPage.tsx")), queryWarmup: dynamic("/memory/facts/:factId", (id) => ({ queryKey: ["memory-fact", id], queryFn: () => api.getFact(id), staleTime: DEFAULT_QUERY_STALE_TIME_MS })) },
  { path: "/memory/rules/:ruleId", label: "Rule detail", keywords: ["memory", "rule"], family: "detail", placement: null, dynamic: "search-backed", discoverability: "contextual", loader: page(() => import("@/pages/RuleDetailPage.tsx")), queryWarmup: dynamic("/memory/rules/:ruleId", (id) => ({ queryKey: ["memory-rule", id], queryFn: () => api.getRule(id), staleTime: DEFAULT_QUERY_STALE_TIME_MS })) },
  { path: "/memory/episodes/:episodeId", label: "Episode detail", keywords: ["memory", "episode"], family: "detail", placement: null, dynamic: "search-backed", discoverability: "contextual", loader: page(() => import("@/pages/EpisodeDetailPage.tsx")), queryWarmup: dynamic("/memory/episodes/:episodeId", (id) => ({ queryKey: ["memory-episode", id], queryFn: () => api.getEpisode(id), staleTime: DEFAULT_QUERY_STALE_TIME_MS })) },
  { path: "/approvals/:id", label: "Approval detail", keywords: ["approval", "review"], family: "detail", placement: null, dynamic: "search-backed", discoverability: "contextual", loader: page(() => import("@/pages/ApprovalsPage.tsx")), queryWarmup: dynamic("/approvals/:id", (id) => ({ queryKey: ["approvals", "detail", id], queryFn: () => api.getApprovalDetail(id), staleTime: POLL_BUS_RECONCILE_MS })) },
  { path: "/ingestion/connectors/:connectorType/:endpointIdentity", label: "Connector detail", keywords: ["ingestion", "connector", "provider"], family: "detail", placement: null, dynamic: "context-only", discoverability: "context-only", loader: page(() => import("@/pages/ConnectorDetailPage.tsx")) },
  { path: "/qa/patrols/:patrolId", label: "QA patrol detail", keywords: ["qa", "patrol"], family: "detail", placement: null, dynamic: "context-only", discoverability: "context-only", loader: page(() => import("@/pages/QaPatrolDetailPage.tsx")) },
  { path: "/qa/investigations/:attemptId", label: "QA investigation detail", keywords: ["qa", "investigation"], family: "detail", placement: null, dynamic: "context-only", discoverability: "context-only", loader: page(() => import("@/pages/QaInvestigationDetailPage.tsx")) },
];

export function getShellCapability(path: string): ShellCapability | undefined {
  return SHELL_CAPABILITIES.find((capability) => capability.path === path);
}

export function resolveShellCapability(pathname: string): ShellCapability | undefined {
  const path = pathname.split("?", 1)[0].split("#", 1)[0];
  return SHELL_CAPABILITIES.find((capability) => {
    if (!capability.path.includes(":")) return capability.path === path;
    return segmentPattern(capability.path, path) !== null;
  });
}

export function resolveShellWarmup(to: string): ShellQueryWarmup | null {
  for (const capability of SHELL_CAPABILITIES) {
    const target = capability.queryWarmup?.(to);
    if (target) return target;
  }
  return null;
}
