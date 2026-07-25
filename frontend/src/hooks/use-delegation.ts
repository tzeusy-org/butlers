/**
 * TanStack Query hooks for GET /api/delegation/ledger (bu-gxmfx / bu-ep4ks.3).
 *
 * `useDelegationLedger` is a thin wrapper for a filtered page of the ledger
 * (butler detail's "delegated out" / "delegated in" panels). `useStuckDelegations`
 * is the fleet-wide "wake protocol failed" query the attention surface uses --
 * wake_stuck=true restricts to callback_failed/task_conflict rows, the two
 * failure states that otherwise render as an ordinary answered row.
 */

import { useQuery } from "@tanstack/react-query";

import { listDelegationLedger, type DelegationLedgerParams } from "@/api/index.ts";
import { useBusAwarePollInterval } from "@/hooks/use-bus-aware-poll-interval";

export function useDelegationLedger(params: DelegationLedgerParams = {}) {
  const refetchInterval = useBusAwarePollInterval();
  return useQuery({
    queryKey: ["delegation-ledger", params],
    queryFn: () => listDelegationLedger(params),
    refetchInterval,
  });
}

const DEFAULT_STUCK_LIMIT = 5;

/**
 * Fleet-wide delegations stuck in callback_failed or task_conflict --
 * previously invisible over the API (bu-ep4ks.3). `isError` must gate the
 * caller's render: a failed fetch is NOT "nothing stuck" (fleet degraded-
 * source convention -- never fabricate calm).
 */
export function useStuckDelegations(limit: number = DEFAULT_STUCK_LIMIT) {
  const query = useDelegationLedger({ wake_stuck: true, limit });
  return {
    rows: query.data?.data ?? [],
    total: query.data?.meta.total ?? 0,
    isLoading: query.isLoading,
    isError: query.isError,
  };
}
