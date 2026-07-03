/**
 * Shared approve/deny/defer mutations for pending approvals (JARVIS audit
 * move 6, bu-86c4c.14 — Act loop / hot queue).
 *
 * Extracted from ApprovalsPage's inline mutation block so the exact same
 * optimistic cache-eviction + rollback-on-error + toast behavior can be
 * reused by any surface that renders actionable pending approvals — today
 * that's ApprovalsPage (the full dossier) and DashboardPage's Needs-attention
 * list (inline triage without leaving the pane).
 *
 * Cache eviction matches on the `["approvals", "flat", "waiting"]` KEY
 * PREFIX, which react-query's setQueriesData/getQueriesData treat as a
 * partial match — every cached "waiting" list, regardless of its `limit`
 * (ApprovalsPage's 100/200/... vs. the dashboard's top-3), updates together
 * from a single decision made on either surface.
 *
 * Built on {@link useOptimisticMutation} (bu-86c4c.13) — no hand-rolled
 * onMutate/onError/onSettled here.
 */
import { useQueryClient } from "@tanstack/react-query";

import { approveApproval, deferApproval, denyApproval } from "@/api/index.ts";
import type { ApiResponse, ApprovalAction, ApprovalSummary } from "@/api/index.ts";
import { toast } from "sonner";

import { useOptimisticMutation } from "@/hooks/use-optimistic-mutation.ts";

const WAITING_KEY_PREFIX = ["approvals", "flat", "waiting"] as const;
const HISTORY_KEY = ["approvals", "history"] as const;

type PendingSnapshot = [readonly unknown[], unknown][];

export interface UseApprovalDecisionMutationsOptions {
  /**
   * Called synchronously, inside `onMutate`, the moment an id is optimistically
   * removed from every cached "waiting" list — before the network call
   * resolves. Lets a page-level caller react to the decision immediately
   * (e.g. ApprovalsPage advances its URL selection to the next pending item).
   */
  onDecided?: (id: string) => void;
}

/**
 * Returns three ready-to-`.mutate()` mutations (approve/deny/defer) sharing
 * one optimistic-drop + rollback-on-error + reconcile-on-settle cache
 * contract. Each already carries its own success/error toast — callers don't
 * need to add their own for the common path.
 */
export function useApprovalDecisionMutations(
  options: UseApprovalDecisionMutationsOptions = {},
) {
  const qc = useQueryClient();
  const { onDecided } = options;

  function dropFromPending(id: string): PendingSnapshot {
    const prev = qc.getQueriesData({ queryKey: WAITING_KEY_PREFIX });
    qc.setQueriesData<ApiResponse<ApprovalSummary[]>>(
      { queryKey: WAITING_KEY_PREFIX },
      (old) => (old?.data ? { ...old, data: old.data.filter((a) => a.id !== id) } : old),
    );
    onDecided?.(id);
    return prev;
  }

  function rollback(prev: PendingSnapshot | undefined) {
    prev?.forEach(([key, snap]) => qc.setQueryData(key, snap));
  }

  const reconcileKeys = [WAITING_KEY_PREFIX, HISTORY_KEY];

  const approveMut = useOptimisticMutation<ApiResponse<ApprovalAction>, string, PendingSnapshot>({
    mutationFn: (id: string) => approveApproval(id),
    applyOptimisticUpdate: (id) => dropFromPending(id),
    rollback,
    invalidateQueryKeys: () => reconcileKeys,
    onSuccess: (res) => {
      // Honest outcome: the action only ran if the backend dispatched it
      // (status "executed" / dispatched=true). Otherwise it is approved but
      // un-run and stays retry-able — never claim success.
      const action = res?.data;
      const ran = action?.dispatched === true || action?.status === "executed";
      if (ran) {
        toast.success("Approved & dispatched");
      } else {
        toast.warning("Approved. Queued, not yet run. Retry from History.");
      }
    },
    onError: (e) => toast.error(`Approve failed: ${e.message}`),
  });

  const denyMut = useOptimisticMutation<
    ApiResponse<ApprovalAction>,
    { id: string; reason?: string },
    PendingSnapshot
  >({
    mutationFn: ({ id, reason }) => denyApproval(id, reason ? { reason } : undefined),
    applyOptimisticUpdate: ({ id }) => dropFromPending(id),
    rollback,
    invalidateQueryKeys: () => reconcileKeys,
    onSuccess: () => toast.success("Denied"),
    onError: (e) => toast.error(`Deny failed: ${e.message}`),
  });

  const deferMut = useOptimisticMutation<
    ApiResponse<ApprovalAction>,
    { id: string; hours: number },
    PendingSnapshot
  >({
    mutationFn: ({ id, hours }) => deferApproval(id, { hours }),
    applyOptimisticUpdate: ({ id }) => dropFromPending(id),
    rollback,
    invalidateQueryKeys: () => reconcileKeys,
    onSuccess: () => toast.success("Deferred"),
    onError: (e) => toast.error(`Defer failed: ${e.message}`),
  });

  return { approveMut, denyMut, deferMut };
}
