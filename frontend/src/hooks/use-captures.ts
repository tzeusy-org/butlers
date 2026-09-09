import { useQuery } from "@tanstack/react-query";

import { getCaptures } from "@/api/index.ts";

/**
 * Primary update path for the held-captures panel -- captures has no fleet
 * event bus type yet, so this is a plain poll rather than a bus-covered
 * reconciliation sweep (see src/lib/poll-policy.ts).
 */
const HELD_CAPTURES_POLL_MS = 30_000;

/**
 * Held captures -- orphaned rows from capture() whose routing session never
 * finished (bu-2jtfw.9). Polls so a stale "held" clears once routing catches
 * up, or once the owner acts on it via Dispatch.
 */
export function useHeldCaptures() {
  return useQuery({
    queryKey: ["captures", "held"],
    queryFn: () => getCaptures({ state: "held", limit: 50 }),
    refetchInterval: HELD_CAPTURES_POLL_MS,
  });
}
