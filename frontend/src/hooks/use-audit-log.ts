import { useQuery } from "@tanstack/react-query";
import type { AuditLogParams } from "@/api/types";
import { getAuditLog } from "@/api/client";

export function useAuditLog(params?: AuditLogParams) {
  return useQuery({
    queryKey: ["audit-log", params],
    queryFn: () => getAuditLog(params),
    refetchInterval: 30_000,
    // Never-blank list (JARVIS audit move 10): keep the previous page/filter's
    // rows visible while the new combination fetches.
    placeholderData: (prev) => prev,
  });
}
