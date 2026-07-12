/**
 * TanStack Query hooks for the issues API.
 */

import { useQuery } from "@tanstack/react-query";

import { dismissIssue, getIssueOccurrences, getIssues, undismissIssue } from "@/api/index.ts";
import type { ApiResponse, Issue } from "@/api/types";
import { useOptimisticListMutation } from "@/hooks/use-optimistic-mutation.ts";
import { useBusAwarePollInterval } from "@/hooks/use-bus-aware-poll-interval";

/** Query key for the active issues feed. */
const ACTIVE_ISSUES_KEY = ["issues", { dismissed: false }] as const;
/** Query key for the dismissed (restorable) issues view. */
const DISMISSED_ISSUES_KEY = ["issues", { dismissed: true }] as const;

export interface UseIssuesOptions {
  /**
   * When true, the query returns *only* the dismissed issues so the UI can
   * offer a restore affordance. The two views are cached under distinct
   * query keys so toggling between them does not clobber the active feed.
   */
  includeDismissed?: boolean;
  /**
   * Time window bounding audit-derived issues (bu-qvnce.13), e.g. "24h" |
   * "7d" | "30d" | "all". Omitted entirely lets the backend's own default
   * (7d) apply.
   */
  window?: string;
}

/** Fetch grouped issues across all butlers. */
export function useIssues(options: UseIssuesOptions = {}) {
  const { includeDismissed = false, window } = options;
  // Live path: the fleet event bus (bu-86c4c.8) invalidates ["issues"] on
  // every new audit-log error. Polling is a bus-aware reconciliation sweep
  // (bu-01r64.3) — a safety net, not the primary update path.
  const refetchInterval = useBusAwarePollInterval();
  return useQuery({
    queryKey: [...(includeDismissed ? DISMISSED_ISSUES_KEY : ACTIVE_ISSUES_KEY), { window }],
    queryFn: () => getIssues({ includeDismissed, window }),
    refetchInterval,
  });
}

/** Variables for {@link useDismissIssue}. */
export interface DismissIssueVariables {
  issueKey: string;
  /**
   * The issue's current `last_seen_at`, recorded as the ack's recurrence
   * watermark (acknowledge-until-recurrence, JARVIS audit move 6,
   * bu-86c4c.15). When the group's `last_seen_at` later advances past this
   * value, the server automatically un-acks it — this is NOT dismiss-forever.
   */
  lastSeenAt?: string | null;
}

/**
 * Acknowledge an issue group server-side (acknowledge-until-recurrence, JARVIS
 * audit move 6, bu-86c4c.15 — NOT dismiss-forever).
 *
 * Persists the ack in the backend so it holds across browsers and sessions,
 * but only until the group recurs: pass the issue's `last_seen_at` so the
 * server can detect a genuinely new occurrence and re-surface it
 * automatically. The acked issue is optimistically removed from the cached
 * active feed and both issue views are invalidated on settle so the server's
 * filtered views (which apply the recurrence check) are the source of truth.
 */
export function useDismissIssue() {
  return useOptimisticListMutation<ApiResponse<unknown>, DismissIssueVariables, Issue>({
    mutationFn: ({ issueKey, lastSeenAt }: DismissIssueVariables) =>
      dismissIssue(issueKey, lastSeenAt),
    listKeyPrefix: ACTIVE_ISSUES_KEY,
    updateItems: (issues, { issueKey }) => issues.filter((issue) => issue.issue_key !== issueKey),
    // Broad prefix so BOTH the active and dismissed views refresh, not just
    // the one this mutation optimistically touched.
    invalidateQueryKeys: [["issues"]],
  });
}

/**
 * Undismiss (restore) a previously-dismissed issue group server-side.
 *
 * Mirrors {@link useDismissIssue}: optimistically removes the issue from the
 * cached dismissed view, then invalidates both issue views on settle so the
 * restored issue reappears in the active feed.
 */
export function useUndismissIssue() {
  return useOptimisticListMutation<ApiResponse<unknown>, string, Issue>({
    mutationFn: (issueKey: string) => undismissIssue(issueKey),
    listKeyPrefix: DISMISSED_ISSUES_KEY,
    updateItems: (issues, issueKey) => issues.filter((issue) => issue.issue_key !== issueKey),
    invalidateQueryKeys: [["issues"]],
  });
}

/**
 * Fetch the raw audit_log rows behind one issue group's "Seen Nx" count
 * (JARVIS audit move 6). Only enabled while `issueKey` is provided and the
 * caller has actually expanded the row — no point fetching occurrences for
 * every collapsed issue on the page.
 *
 * `window` (bu-hmdqz.4) MUST be the same window the feed itself is showing
 * (IssuesPage's `activeWindow`) so the group re-derived here can never
 * disagree with the group the user is looking at. `limit` supports the
 * panel's "Load more" control (default 50, grows toward the backend's
 * 500-row cap); the query key includes it so a limit bump refetches instead
 * of serving a stale, smaller cached page.
 */
export function useIssueOccurrences(
  issueKey: string | null,
  enabled: boolean,
  window?: string,
  limit?: number,
) {
  return useQuery({
    queryKey: ["issues", "occurrences", issueKey, { window, limit }],
    queryFn: () => getIssueOccurrences(issueKey as string, { window, limit }),
    enabled: enabled && !!issueKey,
  });
}
