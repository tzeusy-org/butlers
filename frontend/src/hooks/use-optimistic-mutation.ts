/**
 * Shared optimistic-mutation primitives (JARVIS audit move 10, bu-86c4c.13).
 *
 * Extracted from use-issues.ts's onMutate/rollback pair (the first mutation
 * hook in the codebase to do cancel -> snapshot -> optimistic-apply ->
 * rollback-on-error -> invalidate-on-settle) plus ApprovalsPage's inline
 * multi-key variant. Every `useMutation` call site in the dashboard should be
 * classified into exactly one of:
 *
 * - OPTIMISTIC: the action is reversible and low-consequence from the
 *   owner's point of view (toggles, acks, dismissals, label/pin changes).
 *   Apply the change to the cache immediately via {@link useOptimisticMutation}
 *   or {@link useOptimisticListMutation}, and roll back on error. The owner
 *   should never see a spinner for these.
 * - HONEST-PENDING: the action triggers real, possibly slow, possibly
 *   irreversible backend work (triggering a butler run, replaying an event,
 *   rotating/revoking a secret, connecting an external account, deleting
 *   data). Keep the plain `useMutation` + pending-state UI — faking success
 *   before the backend confirms it would be a lie the audit explicitly
 *   flags ("no error impersonating health"; the same doctrine applies in
 *   reverse to fabricated success).
 *
 * Do not reach for these helpers for HONEST-PENDING mutations just because
 * they're available — the point is not to make everything optimistic, it's
 * to make the *right* mutations optimistic through one shared, tested path
 * instead of N hand-rolled onMutate/onError/onSettled triples.
 */

import { useMutation, type UseMutationResult, type QueryClient } from "@tanstack/react-query";
import { useQueryClient } from "@tanstack/react-query";

/** A query key as accepted by TanStack Query (`queryKey` option / `QueryKey` type). */
type QueryKeyLike = readonly unknown[];

export interface UseOptimisticMutationOptions<TData, TVariables, TSnapshot> {
  /** The actual network call. Same as a plain `useMutation`'s `mutationFn`. */
  mutationFn: (variables: TVariables) => Promise<TData>;
  /**
   * Query key(s) to cancel in-flight fetches for before applying the
   * optimistic update, so a stale in-flight response can't clobber it.
   * Accepts a function of the mutation variables for per-call keys.
   */
  cancelQueryKeys?: QueryKeyLike[] | ((variables: TVariables) => QueryKeyLike[]);
  /**
   * Apply the optimistic change to the cache and return a snapshot that
   * {@link rollback} can use to undo it. Runs synchronously inside
   * `onMutate`, after the cancel above has been awaited.
   */
  applyOptimisticUpdate: (variables: TVariables, queryClient: QueryClient) => TSnapshot;
  /** Undo exactly what {@link applyOptimisticUpdate} did, using its snapshot. */
  rollback: (snapshot: TSnapshot, queryClient: QueryClient) => void;
  /**
   * Query key(s) to invalidate once the mutation settles (success or
   * error), reconciling the optimistic guess with server truth. Accepts a
   * function of the variables and (if successful) the response data.
   */
  invalidateQueryKeys?:
    | QueryKeyLike[]
    | ((variables: TVariables, data: TData | undefined) => QueryKeyLike[]);
  /** Extra success handling (toasts, navigation) beyond the cache reconciliation above. */
  onSuccess?: (data: TData, variables: TVariables) => void;
  /** Extra error handling (toasts) beyond the automatic rollback above. */
  onError?: (error: Error, variables: TVariables) => void;
}

interface OptimisticContext<TSnapshot> {
  snapshot: TSnapshot;
}

/**
 * Generic optimistic-mutation hook: cancel -> snapshot+apply -> (rollback on
 * error) -> invalidate on settle. See the module doc for when to use this vs.
 * a plain HONEST-PENDING `useMutation`.
 */
export function useOptimisticMutation<TData, TVariables, TSnapshot>(
  options: UseOptimisticMutationOptions<TData, TVariables, TSnapshot>,
): UseMutationResult<TData, Error, TVariables, OptimisticContext<TSnapshot>> {
  const queryClient = useQueryClient();
  const {
    mutationFn,
    cancelQueryKeys,
    applyOptimisticUpdate,
    rollback,
    invalidateQueryKeys,
    onSuccess,
    onError,
  } = options;

  return useMutation({
    mutationFn,
    onMutate: async (variables: TVariables) => {
      const keys =
        typeof cancelQueryKeys === "function" ? cancelQueryKeys(variables) : (cancelQueryKeys ?? []);
      await Promise.all(keys.map((queryKey) => queryClient.cancelQueries({ queryKey })));
      const snapshot = applyOptimisticUpdate(variables, queryClient);
      return { snapshot };
    },
    onError: (error, variables, context) => {
      if (context) rollback(context.snapshot, queryClient);
      onError?.(error as Error, variables);
    },
    onSuccess: (data, variables) => {
      onSuccess?.(data, variables);
    },
    onSettled: (data, _error, variables) => {
      const keys =
        typeof invalidateQueryKeys === "function"
          ? invalidateQueryKeys(variables, data)
          : (invalidateQueryKeys ?? []);
      keys.forEach((queryKey) => void queryClient.invalidateQueries({ queryKey }));
    },
  });
}

// ---------------------------------------------------------------------------
// List-cache convenience layer
// ---------------------------------------------------------------------------

/** The common `{ data: T[], meta }` envelope shared by ApiResponse<T[]>, PaginatedResponse<T>, KeysetResponse<T>, and CursorPaginatedResponse<T>. */
interface ListEnvelope<TItem> {
  data: TItem[];
  [key: string]: unknown;
}

function isListEnvelope<TItem>(value: unknown): value is ListEnvelope<TItem> {
  return (
    typeof value === "object" &&
    value !== null &&
    Array.isArray((value as { data?: unknown }).data)
  );
}

export type ListSnapshot = [QueryKeyLike, unknown][];

/**
 * Rewrite every cached query whose key starts with `keyPrefix` (matching
 * across every param variant — e.g. every distinct filter/cursor combination
 * of a list) via `updateItems`. Mirrors ApprovalsPage's `dropFromPending`
 * pair, generalized to any list envelope shape and any per-item transform
 * (filter OR patch). Does not snapshot — callers that need rollback should
 * snapshot via `getQueriesData({ queryKey: keyPrefix })` first (see
 * {@link snapshotAndUpdateLists} for the single-prefix case).
 */
function applyListUpdate<TItem>(
  queryClient: QueryClient,
  keyPrefix: QueryKeyLike,
  updateItems: (items: TItem[]) => TItem[],
): void {
  queryClient.setQueriesData({ queryKey: keyPrefix }, (old: unknown) => {
    // Most list endpoints cache an envelope (`{ data: T[], meta }`), but a
    // few (e.g. getPendingContacts) cache the bare array directly — support
    // both rather than silently no-op'ing on the bare-array shape.
    if (Array.isArray(old)) return updateItems(old as TItem[]);
    if (!isListEnvelope<TItem>(old)) return old;
    return { ...old, data: updateItems(old.data) };
  });
}

/**
 * Snapshot every cached query whose key starts with `keyPrefix`, then rewrite
 * each one's `data` array via `updateItems`. Returns the pre-update snapshot
 * for {@link rollbackLists}.
 */
export function snapshotAndUpdateLists<TItem>(
  queryClient: QueryClient,
  keyPrefix: QueryKeyLike,
  updateItems: (items: TItem[]) => TItem[],
): ListSnapshot {
  const snapshot = queryClient.getQueriesData({ queryKey: keyPrefix });
  applyListUpdate(queryClient, keyPrefix, updateItems);
  return snapshot;
}

/** Undo {@link snapshotAndUpdateLists}, restoring every snapshotted key verbatim. */
export function rollbackLists(queryClient: QueryClient, snapshot: ListSnapshot): void {
  snapshot.forEach(([queryKey, data]) => queryClient.setQueryData(queryKey, data));
}

export interface UseOptimisticListMutationOptions<TData, TVariables, TItem> {
  mutationFn: (variables: TVariables) => Promise<TData>;
  /**
   * Prefix (or prefixes) matching every cached list query to update, e.g.
   * `["notifications"]`, or `[["notifications"], ["butler-notifications"]]`
   * when the same item is mirrored under more than one query-key namespace.
   */
  listKeyPrefix: QueryKeyLike | QueryKeyLike[];
  /** Produce the updated items for one matched list from its current items. */
  updateItems: (items: TItem[], variables: TVariables) => TItem[];
  /**
   * Keys to invalidate on settle. Defaults to the (normalized) list
   * prefix(es); pass an explicit list to invalidate a *broader* prefix (e.g.
   * an unfiltered `["issues"]` so sibling filtered views — active vs.
   * dismissed — refresh together) or to add sibling caches (e.g. a stats
   * endpoint).
   */
  invalidateQueryKeys?: QueryKeyLike[];
  onSuccess?: (data: TData, variables: TVariables) => void;
  onError?: (error: Error, variables: TVariables) => void;
}

function isSingleKey(prefix: QueryKeyLike | QueryKeyLike[]): prefix is QueryKeyLike {
  return !Array.isArray(prefix[0]);
}

function normalizeKeyPrefixes(prefix: QueryKeyLike | QueryKeyLike[]): QueryKeyLike[] {
  return isSingleKey(prefix) ? [prefix] : prefix;
}

/**
 * The common case of {@link useOptimisticMutation}: one item in one or more
 * cached lists is removed or patched immediately, and rolled back on error.
 * Covers dismiss/undismiss, mark-read/ack, and add/remove-from-list mutations
 * without hand-writing cancel/snapshot/rollback each time.
 */
export function useOptimisticListMutation<TData, TVariables, TItem>(
  options: UseOptimisticListMutationOptions<TData, TVariables, TItem>,
): UseMutationResult<TData, Error, TVariables, OptimisticContext<ListSnapshot>> {
  const { listKeyPrefix, updateItems, invalidateQueryKeys, ...rest } = options;
  const prefixes = normalizeKeyPrefixes(listKeyPrefix);
  return useOptimisticMutation<TData, TVariables, ListSnapshot>({
    ...rest,
    cancelQueryKeys: prefixes,
    applyOptimisticUpdate: (variables, queryClient) => {
      // Snapshot EVERY prefix before mutating ANY of them. If two prefixes
      // in `prefixes` overlap (e.g. one is a strict prefix of another, so
      // the same cached query matches both), updating the first before
      // snapshotting the second would capture already-mutated data as the
      // "original" — corrupting rollback silently on error. Two phases
      // (snapshot-all, then apply-all) keeps every snapshot pre-mutation
      // regardless of prefix overlap.
      const snapshots = prefixes.map((prefix) => queryClient.getQueriesData({ queryKey: prefix }));
      prefixes.forEach((prefix) =>
        applyListUpdate<TItem>(queryClient, prefix, (items) => updateItems(items, variables)),
      );
      return snapshots.flat();
    },
    rollback: (snapshot, queryClient) => rollbackLists(queryClient, snapshot),
    invalidateQueryKeys: invalidateQueryKeys ?? prefixes,
  });
}
