/**
 * Generic confirm-or-undo-window scheduler for consequential dashboard
 * actions (bu-ep4ks.11 — the shared safety envelope for destructive/bulk
 * actions).
 *
 * Generalizes two hand-rolled duplicates of the same shape: ButlersPage's
 * quarantine-restore undo (bu-86c4c.15, consolidated in bu-3dp0c) and
 * use-approval-decisions.ts's scheduleDecision (bu-qvnce.4, also
 * consolidated in bu-3dp0c — it delegates its storage/timer plumbing to this
 * hook while keeping its own richer public contract, since ApprovalsPage and
 * DashboardPage need the pending VERB (approve/deny/defer), not just a
 * boolean). A click does not fire the real mutation immediately — it
 * schedules it `windowMs` out behind a cancellable timer, so the caller can
 * render an "Undo" toast action in the meantime. The store is MODULE SCOPE
 * (not component/hook state) so a scheduled action survives the triggering
 * component unmounting mid-window — navigating away within the grace period
 * is a normal part of fast operator triage, not an edge case.
 *
 * The optional `meta` payload on `schedule`/`get` is what lets
 * use-approval-decisions.ts carry its `DecisionVerb` through this otherwise
 * payload-free scheduler without hand-rolling a second store — callers that
 * don't need metadata (e.g. ButlersPage) simply never pass or read it.
 */
import { useCallback, useMemo, useSyncExternalStore } from "react";

/** Default grace window between scheduling an action and it actually firing. */
export const UNDO_WINDOW_MS = 5_000;

interface ScheduledEntry<TMeta> {
  timeoutId: number;
  meta?: TMeta;
}

// The module-scope map is shared across every namespace (ids are prefixed
// `${namespace}:${id}`), so its value type must be able to hold whichever
// caller's metadata; each hook instance only ever reads back the type it
// wrote via its own generic parameter.
let snapshot: ReadonlyMap<string, ScheduledEntry<unknown>> = new Map();
const listeners = new Set<() => void>();

function setSnapshot(next: Map<string, ScheduledEntry<unknown>>) {
  snapshot = next;
  for (const listener of listeners) listener();
}

function subscribe(onStoreChange: () => void) {
  listeners.add(onStoreChange);
  return () => {
    listeners.delete(onStoreChange);
  };
}

function getSnapshot() {
  return snapshot;
}

function cancelKey(key: string) {
  const entry = snapshot.get(key);
  if (!entry) return;
  window.clearTimeout(entry.timeoutId);
  const next = new Map(snapshot);
  next.delete(key);
  setSnapshot(next);
}

/**
 * Scope undo-window scheduling to one `namespace` (e.g. "butler-pause",
 * "connector-archive") so two unrelated surfaces never collide on the same
 * raw id. Pass `TMeta` when a caller needs to carry a small payload (e.g. a
 * verb/label) alongside the pending state — read it back via `get`.
 */
export function useUndoWindow<TMeta = never>(namespace: string) {
  const scheduled = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  const key = useCallback((id: string) => `${namespace}:${id}`, [namespace]);

  const isScheduled = useCallback((id: string) => scheduled.has(key(id)), [scheduled, key]);

  /** Read back the `meta` passed to `schedule` for `id`, if it is pending. */
  const get = useCallback(
    (id: string) => scheduled.get(key(id))?.meta as TMeta | undefined,
    [scheduled, key],
  );

  /**
   * Schedule `run` to fire after `windowMs`. Returns `false` (a no-op) when
   * `id` already has a scheduled action pending — callers use this to ignore
   * a repeat click on the same row instead of double-scheduling it.
   */
  const schedule = useCallback(
    (id: string, run: () => void, windowMs: number = UNDO_WINDOW_MS, meta?: TMeta): boolean => {
      const k = key(id);
      if (snapshot.has(k)) return false;

      const timeoutId = window.setTimeout(() => {
        cancelKey(k);
        run();
      }, windowMs);

      setSnapshot(new Map(snapshot).set(k, { timeoutId, meta }));
      return true;
    },
    [key],
  );

  /** Cancel `id`'s scheduled action, if one exists — the toast's "Undo". */
  const cancel = useCallback((id: string) => cancelKey(key(id)), [key]);

  // Plain ids (namespace prefix stripped) with a pending action -- lets a
  // caller compute "which of MY ids are pending" without reaching into the
  // cross-namespace snapshot itself (e.g. ApprovalsPage skipping already-
  // scheduled ids from its own ranked list).
  const namespacePrefix = `${namespace}:`;
  const scheduledIds = useMemo(() => {
    const ids = new Set<string>();
    for (const k of scheduled.keys()) {
      if (k.startsWith(namespacePrefix)) ids.add(k.slice(namespacePrefix.length));
    }
    return ids;
  }, [scheduled, namespacePrefix]);

  // Memoize the returned object itself (not just its individual members) so a
  // caller that depends on the whole `undo` result in its own useMemo/
  // useCallback deps (e.g. use-approval-decisions.ts) gets a referentially
  // stable value across renders where nothing here actually changed --
  // required for the React Compiler to preserve manual memoization that
  // depends on this hook's return value.
  return useMemo(
    () => ({ isScheduled, schedule, cancel, get, scheduledIds }),
    [isScheduled, schedule, cancel, get, scheduledIds],
  );
}
