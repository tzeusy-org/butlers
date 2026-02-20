/**
 * TanStack Query hook for the issues API.
 */

import { useQuery } from "@tanstack/react-query";

import { getIssues } from "@/api/index.ts";

/** Fetch grouped issues across all butlers. */
export function useIssues() {
  return useQuery({
    queryKey: ["issues"],
    queryFn: () => getIssues(),
    refetchInterval: 30_000,
  });
}
