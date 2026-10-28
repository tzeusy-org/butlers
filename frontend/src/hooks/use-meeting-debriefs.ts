/**
 * TanStack Query hook for the pending meeting debriefs (bu-q7vx1q.63).
 */

import { useQuery } from "@tanstack/react-query";

import { getPendingMeetingDebriefs } from "@/api/client.ts";
import { POLL_BUS_RECONCILE_MS } from "@/lib/poll-policy.ts";

export const PENDING_DEBRIEFS_KEY = ["relationship", "meeting-debriefs", "pending"] as const;

/** Prompted, unanswered debriefs; a reply from Telegram drops out on the next refetch. */
export function usePendingMeetingDebriefs() {
  return useQuery({
    queryKey: PENDING_DEBRIEFS_KEY,
    queryFn: getPendingMeetingDebriefs,
    refetchInterval: POLL_BUS_RECONCILE_MS,
  });
}
