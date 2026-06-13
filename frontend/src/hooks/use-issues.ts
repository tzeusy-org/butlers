/**
 * TanStack Query hooks for the issues API.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { dismissIssue, getIssues } from "@/api/index.ts";
import type { ApiResponse, Issue } from "@/api/types";

/** Fetch grouped issues across all butlers. */
export function useIssues() {
  return useQuery({
    queryKey: ["issues"],
    queryFn: () => getIssues(),
    refetchInterval: 30_000,
  });
}

/**
 * Dismiss (ack) an issue group server-side.
 *
 * Unlike the old localStorage-only behaviour, this persists the dismissal in
 * the backend so it holds across browsers and sessions. The dismissed issue is
 * optimistically removed from the cached feed and the query is invalidated on
 * settle so the server's filtered view is the source of truth.
 */
export function useDismissIssue() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (issueKey: string) => dismissIssue(issueKey),
    onMutate: async (issueKey: string) => {
      await queryClient.cancelQueries({ queryKey: ["issues"] });
      const previous = queryClient.getQueryData<ApiResponse<Issue[]>>(["issues"]);
      if (previous) {
        queryClient.setQueryData<ApiResponse<Issue[]>>(["issues"], {
          ...previous,
          data: previous.data.filter((issue) => issue.issue_key !== issueKey),
        });
      }
      return { previous };
    },
    onError: (_err, _issueKey, context) => {
      if (context?.previous) {
        queryClient.setQueryData(["issues"], context.previous);
      }
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["issues"] });
    },
  });
}
