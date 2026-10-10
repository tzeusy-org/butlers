import type { QueryClient } from "@tanstack/react-query";

const observed = new WeakMap<QueryClient, bigint>();
const active = new Map<QueryClient, bigint>();
const listeners = new Set<() => void>();
const serverSnapshot = { pending: false, generation: 0 };
let snapshot = serverSnapshot;

export function getLocationPrivacySnapshot() { return snapshot; }
export function getLocationPrivacyServerSnapshot() { return serverSnapshot; }
export function subscribeLocationPrivacy(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
function notify(begin: boolean) {
  snapshot = { pending: active.size > 0, generation: snapshot.generation + (begin ? 1 : 0) };
  for (const listener of listeners) listener();
}

/** Managed query/map invalidation from a committed server privacy generation.
 * No deadline, client ACK or browser event proves source-side disposal.
 */
export async function reconcileLocationPrivacy(cache: QueryClient, revision: unknown) {
  if (typeof revision !== "string" || !/^(0|[1-9][0-9]*)$/.test(revision)) return;
  const next = BigInt(revision);
  const prior = observed.get(cache);
  if (prior !== undefined && next <= prior) return;
  observed.set(cache, next);
  const changed = {
    predicate: (query: { queryKey: readonly unknown[] }) =>
      query.queryKey[0] === "chronicles" && query.queryKey[1] !== "location-retention",
  };
  // Destroy managed retained GPU geometry before awaiting network work.
  active.set(cache, next);
  notify(true);
  try {
    await cache.cancelQueries(changed);
    // Reset every current/archive variant, including inactive retained data.
    // Active observers refetch; cancelled old promises cannot refill them.
    await cache.resetQueries(changed);
  } finally {
    // A superseding revision owns the current transition; an older settled
    // refetch cannot release that newer fence.
    if (active.get(cache) === next) {
      active.delete(cache);
      notify(false);
    }
  }
}
