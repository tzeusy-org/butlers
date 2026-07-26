/**
 * Route -> prefetch-target map (bu-qvnce.14 slice 4, deferred from PR #2927).
 *
 * `usePrefetchOnIntent` (src/hooks/use-prefetch-on-intent.ts) needs to turn a
 * navigating row's `to` path into "which queryKey+queryFn should I warm the
 * cache with." This module is the one place that answers that question --
 * every entry here reuses the SAME queryKey/queryFn the destination page's
 * own data hook already uses (use-sessions.ts's useGlobalSessionDetail,
 * ApprovalsPage.tsx's Dossier query, use-timeline-ledger.ts's head query), so
 * a prefetch lands in exactly the cache slot the real page reads from
 * instead of creating a parallel, differently-keyed fetch.
 *
 * Deliberately NOT exhaustive: only routes with a cheap, well-known single
 * detail/list fetch are mapped. A `to` that matches nothing here is a no-op
 * (see resolvePrefetchTarget's null return) -- RowLink/DisclosureRow/the
 * command palette can all call this unconditionally without maintaining a
 * parallel "is this route covered" list.
 *
 * Bus-covered entries retain their POLL_BUS_RECONCILE_MS staleTime (see
 * lib/poll-policy.ts); ordinary routes use the QueryClient's 30-second
 * freshness window. This keeps a prefetch behaviorally identical to the page
 * that will consume its cache entry.
 */

import {
  getApprovalDetail,
  getButler,
  getEntity,
  getEpisode,
  getFact,
  getIngestionEvent,
  getMeasurementTypes,
  getRule,
  getSession,
  getSessions,
  getTimeline,
} from "@/api/index.ts";
import { ENTITY_DETAIL_INITIAL_PARAMS } from "@/lib/entity-detail-query";
import { POLL_BUS_RECONCILE_MS } from "@/lib/poll-policy";
import { fetchSpendForecast } from "@/lib/spend-forecast";

/** Head-page size used by TimelinePage's own query (use-timeline-ledger.ts). */
const TIMELINE_HEAD_PAGE_SIZE = 50;
const SESSION_LIST_INITIAL_PARAMS = { limit: 20 };
const DEFAULT_QUERY_STALE_TIME_MS = 30_000;

export interface PrefetchTarget {
  queryKey: readonly unknown[];
  queryFn: () => Promise<unknown>;
  staleTime: number;
}

type Matcher = (pathname: string) => PrefetchTarget | null;

/**
 * The Sidebar only supplies these exact, unfiltered list paths. Match the raw
 * `to` value before pathname normalization so a filtered or anchored link
 * cannot accidentally warm the default list cache entry instead of the
 * destination's own query shape.
 */
function matchSidebarListRoute(to: string): PrefetchTarget | null {
  switch (to) {
    case "/sessions":
      return {
        queryKey: ["sessions", SESSION_LIST_INITIAL_PARAMS],
        queryFn: () => getSessions(SESSION_LIST_INITIAL_PARAMS),
        staleTime: DEFAULT_QUERY_STALE_TIME_MS,
      };
    case "/health":
      // Health's briefing is an LLM-backed, manual-refresh cost-guarded
      // query. Prefetch only the first deterministic page query instead.
      return {
        queryKey: ["health-measurement-types"],
        queryFn: getMeasurementTypes,
        staleTime: DEFAULT_QUERY_STALE_TIME_MS,
      };
    case "/spend":
      return {
        queryKey: ["spend-forecast"],
        queryFn: fetchSpendForecast,
        staleTime: DEFAULT_QUERY_STALE_TIME_MS,
      };
    default:
      return null;
  }
}

/** `/butlers/:name` -- ButlerDetailPage's useButler(name). */
const BUTLER_DETAIL_RE = /^\/butlers\/([^/]+)$/;
function matchButlerDetail(pathname: string): PrefetchTarget | null {
  const m = BUTLER_DETAIL_RE.exec(pathname);
  if (!m) return null;
  const name = decodeURIComponent(m[1]);
  return {
    queryKey: ["butlers", name],
    queryFn: () => getButler(name),
    staleTime: DEFAULT_QUERY_STALE_TIME_MS,
  };
}

/** `/entities/:entityId` -- EntityDetailPage's initial useEntity projection. */
const ENTITY_DETAIL_RE = /^\/entities\/([^/]+)$/;
const ENTITY_SUBROUTES = new Set([
  "index",
  "concentration",
  "circles",
  "hop",
  "columns",
  "social-map",
]);
function matchEntityDetail(pathname: string): PrefetchTarget | null {
  const m = ENTITY_DETAIL_RE.exec(pathname);
  if (!m) return null;
  const entityId = decodeURIComponent(m[1]);
  if (ENTITY_SUBROUTES.has(entityId)) return null;
  return {
    queryKey: ["memory-entity", entityId, ENTITY_DETAIL_INITIAL_PARAMS],
    queryFn: () => getEntity(entityId, ENTITY_DETAIL_INITIAL_PARAMS),
    staleTime: DEFAULT_QUERY_STALE_TIME_MS,
  };
}

/** `/memory/facts/:factId` -- FactDetailPage's useFact(factId). */
const FACT_DETAIL_RE = /^\/memory\/facts\/([^/]+)$/;
function matchFactDetail(pathname: string): PrefetchTarget | null {
  const m = FACT_DETAIL_RE.exec(pathname);
  if (!m) return null;
  const factId = decodeURIComponent(m[1]);
  return {
    queryKey: ["memory-fact", factId],
    queryFn: () => getFact(factId),
    staleTime: DEFAULT_QUERY_STALE_TIME_MS,
  };
}

/** `/memory/episodes/:episodeId` -- EpisodeDetailPage's useEpisode(episodeId). */
const EPISODE_DETAIL_RE = /^\/memory\/episodes\/([^/]+)$/;
function matchEpisodeDetail(pathname: string): PrefetchTarget | null {
  const m = EPISODE_DETAIL_RE.exec(pathname);
  if (!m) return null;
  const episodeId = decodeURIComponent(m[1]);
  return {
    queryKey: ["memory-episode", episodeId],
    queryFn: () => getEpisode(episodeId),
    staleTime: DEFAULT_QUERY_STALE_TIME_MS,
  };
}

/** `/memory/rules/:ruleId` -- RuleDetailPage's useRule(ruleId). */
const RULE_DETAIL_RE = /^\/memory\/rules\/([^/]+)$/;
function matchRuleDetail(pathname: string): PrefetchTarget | null {
  const m = RULE_DETAIL_RE.exec(pathname);
  if (!m) return null;
  const ruleId = decodeURIComponent(m[1]);
  return {
    queryKey: ["memory-rule", ruleId],
    queryFn: () => getRule(ruleId),
    staleTime: DEFAULT_QUERY_STALE_TIME_MS,
  };
}

/** `/sessions/:id` -- SessionDetailPage's useGlobalSessionDetail(id). */
const SESSION_DETAIL_RE = /^\/sessions\/([^/]+)$/;
function matchSessionDetail(pathname: string): PrefetchTarget | null {
  const m = SESSION_DETAIL_RE.exec(pathname);
  if (!m) return null;
  const id = decodeURIComponent(m[1]);
  return {
    queryKey: ["session-detail-global", id],
    queryFn: () => getSession(id),
    staleTime: POLL_BUS_RECONCILE_MS,
  };
}

/** `/approvals/:id` -- ApprovalsPage's Dossier query (Q.detail(id)). */
const APPROVAL_DETAIL_RE = /^\/approvals\/([^/]+)$/;
function matchApprovalDetail(pathname: string): PrefetchTarget | null {
  const m = APPROVAL_DETAIL_RE.exec(pathname);
  if (!m) return null;
  const id = decodeURIComponent(m[1]);
  return {
    queryKey: ["approvals", "detail", id],
    queryFn: () => getApprovalDetail(id),
    staleTime: POLL_BUS_RECONCILE_MS,
  };
}

/** `/timeline` -- TimelinePage's head query (useTimelineLedger -> useTimeline). */
function matchTimeline(pathname: string): PrefetchTarget | null {
  if (pathname !== "/timeline") return null;
  const params = { limit: TIMELINE_HEAD_PAGE_SIZE };
  return {
    queryKey: ["timeline", params],
    queryFn: () => getTimeline(params),
    staleTime: POLL_BUS_RECONCILE_MS,
  };
}

/**
 * `/ingestion?event=:id` -- the ingestion ledger's URL-backed EventDrawer.
 *
 * The drawer is an inline disclosure rather than a standalone route, but its
 * URL is still the canonical deep-link shape. Keeping the prefetch mapping on
 * that real URL lets DisclosureRow use the same intent helper as navigating
 * rows without inventing a synthetic route just for cache warming.
 */
function matchIngestionEventDetail(to: string): PrefetchTarget | null {
  const [pathname, queryAndHash = ""] = to.split("?", 2);
  if (pathname !== "/ingestion") return null;
  const eventId = new URLSearchParams(queryAndHash.split("#", 1)[0]).get("event");
  if (!eventId) return null;
  return {
    // Matches useIngestionEventDetail -> ingestionEventKeys.detail(requestId).
    queryKey: ["ingestion", "events", eventId, "detail"],
    queryFn: () => getIngestionEvent(eventId),
    staleTime: POLL_BUS_RECONCILE_MS,
  };
}

const MATCHERS: Matcher[] = [
  matchButlerDetail,
  matchEntityDetail,
  matchFactDetail,
  matchEpisodeDetail,
  matchRuleDetail,
  matchSessionDetail,
  matchApprovalDetail,
  matchTimeline,
];

/**
 * Resolve a navigating row's `to` target to a prefetch descriptor, or `null`
 * when the route isn't mapped. Query-string/hash suffixes are stripped
 * before matching -- only the pathname decides the target.
 *
 * Matchers `decodeURIComponent` the id segment, which throws `URIError` on
 * malformed percent-encoding (e.g. a lone `%`). This is called unconditionally
 * from a pointer/focus handler on every mapped row -- a caller must never see
 * an uncaught exception just from hovering, so a decode failure degrades to
 * the same no-op as an unmapped route instead of throwing.
 */
export function resolvePrefetchTarget(to: string): PrefetchTarget | null {
  const ingestionEventTarget = matchIngestionEventDetail(to);
  if (ingestionEventTarget) return ingestionEventTarget;
  const sidebarListTarget = matchSidebarListRoute(to);
  if (sidebarListTarget) return sidebarListTarget;
  const pathname = to.split("?")[0].split("#")[0];
  try {
    for (const matcher of MATCHERS) {
      const target = matcher(pathname);
      if (target) return target;
    }
  } catch {
    return null;
  }
  return null;
}
