/**
 * Generic confirm-or-undo-window scheduler for consequential dashboard
 * actions (bu-ep4ks.11 — the shared safety envelope for destructive/bulk
 * actions).
 *
 * Generalizes two hand-rolled duplicates of the same shape: ButlersPage's
 * quarantine-restore undo (bu-86c4c.15) and use-approval-decisions.ts's
 * scheduleDecision (bu-qvnce.4). A click does not fire the real mutation
 * immediately — it schedules it `windowMs` out behind a cancellable timer, so
 * the caller can render an "Undo" toast action in the meantime. The store is
 * MODULE SCOPE (not component/hook state) so a scheduled action survives the
 * triggering component unmounting mid-window — navigating away within the
 * grace period is a normal part of fast operator triage, not an edge case.
 *
 * This intentionally does not replace ButlersPage's or
 * use-approval-decisions.ts's own stores — consolidating those pre-existing,
 * independently-shipped duplicates onto this hook is a larger follow-up
 * (flagged in this bead's report), not bundled into this change.
 */
import { useCallback, useSyncExternalStore } from "react";

/** Default grace window between scheduling an action and it actually firing. */
export const UNDO_WINDOW_MS = 5_000;

interface ScheduledEntry {
  timeoutId: number;
}

let snapshot: ReadonlyMap<string, ScheduledEntry> = new Map();
const listeners = new Set<() => void>();

function setSnapshot(next: Map<string, ScheduledEntry>) {
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
 * raw id.
 */
export function useUndoWindow(namespace: string) {
  const scheduled = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  const key = useCallback((id: string) => `${namespace}:${id}`, [namespace]);

  const isScheduled = useCallback((id: string) => scheduled.has(key(id)), [scheduled, key]);

  /**
   * Schedule `run` to fire after `windowMs`. Returns `false` (a no-op) when
   * `id` already has a scheduled action pending — callers use this to ignore
   * a repeat click on the same row instead of double-scheduling it.
   */
  const schedule = useCallback(
    (id: string, run: () => void, windowMs: number = UNDO_WINDOW_MS): boolean => {
      const k = key(id);
      if (snapshot.has(k)) return false;

      const timeoutId = window.setTimeout(() => {
        cancelKey(k);
        run();
      }, windowMs);

      setSnapshot(new Map(snapshot).set(k, { timeoutId }));
      return true;
    },
    [key],
  );

  /** Cancel `id`'s scheduled action, if one exists — the toast's "Undo". */
  const cancel = useCallback((id: string) => cancelKey(key(id)), [key]);

  return { isScheduled, schedule, cancel };
}
