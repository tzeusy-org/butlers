import { useQuery } from "@tanstack/react-query";
import type { AuditLogParams } from "@/api/types";
import { getAuditLog } from "@/api/client";

/**
 * Primary poll interval for audit log queries (bu-ep4ks.15).
 * No fleet-bus event type covers this domain (see
 * event-cache-registry.ts's EVENT_CACHE_REGISTRY) -- this cadence IS
 * the update path, not a reconciliation sweep.
 */
const AUDIT_LOG_POLL_MS = 30_000;

export function useAuditLog(params?: AuditLogParams) {
  return useQuery({
    queryKey: ["audit-log", params],
    queryFn: () => getAuditLog(params),
    refetchInterval: AUDIT_LOG_POLL_MS,
    // Never-blank list (JARVIS audit move 10): keep the previous page/filter's
    // rows visible while the new combination fetches.
    placeholderData: (prev) => prev,
  });
}
