/**
 * TanStack Query hooks for the issues API.
 */

import { useQuery } from "@tanstack/react-query";

import { dismissIssue, getIssues, undismissIssue } from "@/api/index.ts";
import type { ApiResponse, Issue } from "@/api/types";
import { useOptimisticListMutation } from "@/hooks/use-optimistic-mutation.ts";

/** Query key for the active issues feed. */
const ACTIVE_ISSUES_KEY = ["issues", { dismissed: false }] as const;
/** Query key for the dismissed (restorable) issues view. */
const DISMISSED_ISSUES_KEY = ["issues", { dismissed: true }] as const;

/** Fetch grouped issues across all butlers.
 *
 * When `includeDismissed` is true, the query returns *only* the dismissed
 * issues so the UI can offer a restore affordance. The two views are cached
 * under distinct query keys so toggling between them does not clobber the
 * active feed.
 */
export function useIssues(includeDismissed = false) {
  return useQuery({
    queryKey: includeDismissed ? DISMISSED_ISSUES_KEY : ACTIVE_ISSUES_KEY,
    queryFn: () => getIssues(includeDismissed),
    refetchInterval: 30_000,
  });
}

/**
 * Dismiss (ack) an issue group server-side.
 *
 * Unlike the old localStorage-only behaviour, this persists the dismissal in
 * the backend so it holds across browsers and sessions. The dismissed issue is
 * optimistically removed from the cached active feed and both issue views are
 * invalidated on settle so the server's filtered views are the source of truth.
 */
export function useDismissIssue() {
  return useOptimisticListMutation<ApiResponse<unknown>, string, Issue>({
    mutationFn: (issueKey: string) => dismissIssue(issueKey),
    listKeyPrefix: ACTIVE_ISSUES_KEY,
    updateItems: (issues, issueKey) => issues.filter((issue) => issue.issue_key !== issueKey),
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
