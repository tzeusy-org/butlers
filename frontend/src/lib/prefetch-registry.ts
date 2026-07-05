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
 * staleTime on every entry uses POLL_BUS_RECONCILE_MS (see lib/poll-policy.ts)
 * -- these are exactly the bus-covered query keys (event-cache-registry.ts's
 * sessionPatch/approvalPatch invalidate them on their respective fleet
 * events), so a prefetch within that window is intentionally skipped rather
 * than fighting the bus with a redundant fetch.
 */

import { getApprovalDetail, getSession, getTimeline } from "@/api/index.ts";
import { POLL_BUS_RECONCILE_MS } from "@/lib/poll-policy";

/** Head-page size used by TimelinePage's own query (use-timeline-ledger.ts). */
const TIMELINE_HEAD_PAGE_SIZE = 50;

export interface PrefetchTarget {
  queryKey: readonly unknown[];
  queryFn: () => Promise<unknown>;
  staleTime: number;
}

type Matcher = (pathname: string) => PrefetchTarget | null;

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

const MATCHERS: Matcher[] = [matchSessionDetail, matchApprovalDetail, matchTimeline];

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
