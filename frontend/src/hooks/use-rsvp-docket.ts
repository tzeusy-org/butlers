/** Queries for the invitation docket panel (bu-5ii4gj.9 S7). */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getDocket, submitDocket, type DocketSubmitItem } from "@/api/rsvp-docket.ts";

export const DOCKET_QUERY_KEY = ["calendar-rsvp-docket"] as const;

/** Read only while the panel is open: the server judges every card against a fresh calendar read. */
export function useRsvpDocket(options: { enabled: boolean }) {
  return useQuery({
    queryKey: DOCKET_QUERY_KEY,
    queryFn: getDocket,
    enabled: options.enabled,
    staleTime: 0,
  });
}

export function useSubmitRsvpDocket() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (items: DocketSubmitItem[]) => submitDocket(items),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: DOCKET_QUERY_KEY });
      void queryClient.invalidateQueries({ queryKey: ["calendar-invitations"] });
      void queryClient.invalidateQueries({ queryKey: ["calendar-workspace"] });
      void queryClient.invalidateQueries({ queryKey: ["calendar-workspace-audit"] });
    },
  });
}
