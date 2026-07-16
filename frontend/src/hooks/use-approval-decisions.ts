/**
 * Shared approve/deny/defer mutations for pending approvals (JARVIS audit
 * move 6, bu-86c4c.14 — Act loop / hot queue).
 *
 * Extracted from ApprovalsPage's inline mutation block so the exact same
 * optimistic cache-eviction + rollback-on-error + toast behavior can be
 * reused by any surface that renders actionable pending approvals — today
 * that's ApprovalsPage (the full dossier), DashboardPage's Needs-attention
 * list, and ButlerOverviewTab's awaiting-action preview.
 *
 * Cache eviction matches on the `["approvals", "flat", "waiting"]` KEY
 * PREFIX, which react-query's setQueriesData/getQueriesData treat as a
 * partial match — every cached "waiting" list, regardless of its `limit`
 * (ApprovalsPage's 100/200/... vs. the dashboard's top-3), updates together
 * from a single decision made on either surface.
 *
 * Built on {@link useOptimisticMutation} (bu-86c4c.13) — no hand-rolled
 * onMutate/onError/onSettled here.
 *
 * Also owns the shared undo-window grace contract (bu-qvnce.4): scheduling a
 * decision (rather than firing it immediately) is what makes an approve/deny/
 * defer click undoable. This was previously hand-rolled inside ApprovalsPage
 * for its keyboard-triage (a/d/x) path only; extracting it here lets
 * DashboardPage's one-click attention-list rows share the IDENTICAL grace
 * window instead of firing irreversibly the moment they're clicked.
 */
import { useSyncExternalStore } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { approveApproval, deferApproval, denyApproval } from "@/api/index.ts";
import type { ApiResponse, ApprovalAction, ApprovalSummary } from "@/api/index.ts";
import { toast } from "sonner";

import { useOptimisticMutation } from "@/hooks/use-optimistic-mutation.ts";

const WAITING_KEY_PREFIX = ["approvals", "flat", "waiting"] as const;
const HISTORY_KEY = ["approvals", "history"] as const;
const ACTIONS_KEY_PREFIX = ["approvals", "actions"] as const;

type PendingSnapshot = [readonly unknown[], unknown][];

export type DecisionVerb = "approve" | "deny" | "defer";

/** Grace window between scheduling a decision and it actually firing. */
export const UNDO_WINDOW_MS = 5_000;

export interface ScheduledDecision {
  verb: DecisionVerb;
  timeoutId: number;
}

// ---------------------------------------------------------------------------
// Scheduled-decision store -- MODULE SCOPE, not component/hook state.
//
// A decision scheduled from ANY surface (ApprovalsPage's a/d/x keyboard
// triage, DashboardPage's attention-list rows) must survive that surface
// unmounting mid undo-window -- navigating away within 5s is a normal part of
// fast triage, not an edge case. Module scope also means the two surfaces
// share one truth: scheduling a decision on the dashboard and switching to
// /approvals within the window shows the SAME pending/undo state there, and
// the id is consistently skipped from either surface's own selection.
// ---------------------------------------------------------------------------
let scheduledDecisionsSnapshot: ReadonlyMap<string, ScheduledDecision> = new Map();
const scheduledDecisionsListeners = new Set<() => void>();

function setScheduledDecisionsSnapshot(next: Map<string, ScheduledDecision>) {
  scheduledDecisionsSnapshot = next;
  for (const listener of scheduledDecisionsListeners) listener();
}

function subscribeScheduledDecisions(onStoreChange: () => void) {
  scheduledDecisionsListeners.add(onStoreChange);
  return () => {
    scheduledDecisionsListeners.delete(onStoreChange);
  };
}

function getScheduledDecisionsSnapshot() {
  return scheduledDecisionsSnapshot;
}

/** Cancel and clear an id's scheduled decision, if one exists. */
function cancelScheduledDecision(id: string) {
  const entry = scheduledDecisionsSnapshot.get(id);
  if (!entry) return;
  window.clearTimeout(entry.timeoutId);
  const next = new Map(scheduledDecisionsSnapshot);
  next.delete(id);
  setScheduledDecisionsSnapshot(next);
}

export interface UseApprovalDecisionMutationsOptions {
  /**
   * Called synchronously, inside `onMutate`, the moment an id is optimistically
   * removed from every cached "waiting" list — before the network call
   * resolves. Lets a page-level caller react to the decision immediately
   * (e.g. ApprovalsPage advances its URL selection to the next pending item).
   */
  onDecided?: (id: string) => void;
  /**
   * Opt into the shared undo-window contract (bu-qvnce.4). When true,
   * `scheduleDecision()` delays the real mutation by UNDO_WINDOW_MS (instead
   * of running it immediately) and the hook exposes `scheduledDecisions` +
   * `cancelDecision` so a caller can render an inline pending/undo state and
   * skip already-scheduled ids from its own selection. Off by default:
   * calling `scheduleDecision` without opting in just runs `run()` right
   * away, matching every pre-existing one-click surface unchanged.
   */
  undoWindow?: boolean;
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
  const { onDecided, undoWindow = false } = options;
  const scheduledDecisions = useSyncExternalStore(
    subscribeScheduledDecisions,
    getScheduledDecisionsSnapshot,
    getScheduledDecisionsSnapshot,
  );

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

  // Overview's awaiting-action rows use the paged `actions` endpoint rather
  // than the flat waiting list. Reconcile that sibling cache after an inline
  // decision so the row and its pending-count metadata refresh together.
  const reconcileKeys = [WAITING_KEY_PREFIX, HISTORY_KEY, ACTIONS_KEY_PREFIX];

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

  /**
   * Schedule a decision to fire after UNDO_WINDOW_MS, unless cancelled first
   * via `cancelDecision(id)`. Returns `true` if this call actually scheduled
   * (or immediately ran) the decision, `false` if `id` was already scheduled
   * (a no-op -- ignore repeat verbs on the same id). When `undoWindow` was
   * not opted into, `run()` fires immediately -- same as calling it directly.
   */
  function scheduleDecision(id: string, verb: DecisionVerb, run: () => void): boolean {
    if (scheduledDecisionsSnapshot.has(id)) return false;

    if (!undoWindow) {
      run();
      return true;
    }

    const timeoutId = window.setTimeout(() => {
      const next = new Map(scheduledDecisionsSnapshot);
      next.delete(id);
      setScheduledDecisionsSnapshot(next);
      run();
    }, UNDO_WINDOW_MS);

    setScheduledDecisionsSnapshot(new Map(scheduledDecisionsSnapshot).set(id, { verb, timeoutId }));
    return true;
  }

  return {
    approveMut,
    denyMut,
    deferMut,
    /** Ids with a decision scheduled but not yet fired (see `scheduleDecision`). */
    scheduledDecisions,
    scheduleDecision,
    cancelDecision: cancelScheduledDecision,
  };
}
