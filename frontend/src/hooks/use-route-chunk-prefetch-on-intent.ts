/**
 * useRouteChunkPrefetchOnIntent -- hover/focus "intent" -> route JS-chunk
 * prefetch (bu-ep4ks.15). Sidebar-specific sibling of usePrefetchOnIntent
 * (use-prefetch-on-intent.ts, bu-qvnce.14 slice 4): that hook warms
 * react-query's DATA cache for a handful of detail routes; this one warms
 * the BROWSER's module cache for a Sidebar item's own route chunk (see
 * lib/route-chunk-registry.ts), so clicking a not-yet-visited page does not
 * pay a network round-trip for its chunk on top of first render.
 *
 * Same pointer-sweep debounce shape as usePrefetchOnIntent, deliberately not
 * shared code with it -- the two hooks prefetch fundamentally different
 * resources (a query cache entry vs. a JS module) with different failure
 * modes (a query prefetch can be safely retried/ignored; a chunk load
 * failure should not surface anywhere near navigation), and forcing them
 * onto one shared generic would only add indirection for ~40 lines saved.
 */

import { useCallback, useEffect, useRef } from "react";

import { resolveRouteChunkLoader } from "@/lib/route-chunk-registry";

/** Pointer-sweep debounce -- matches PREFETCH_INTENT_DELAY_MS's rationale
 *  (use-prefetch-on-intent.ts): short enough to feel instant on a
 *  deliberate hover, long enough that sweeping past a rail item without
 *  stopping does not fire a chunk load for every item crossed. */
export const ROUTE_CHUNK_PREFETCH_INTENT_DELAY_MS = 120;

export interface RouteChunkPrefetchIntentHandlers {
  schedule: () => void;
  cancel: () => void;
  onPointerEnter: () => void;
  onPointerLeave: () => void;
  onFocus: () => void;
  onBlur: () => void;
}

/**
 * @param path Sidebar path to resolve via lib/route-chunk-registry.ts.
 *   `null`/`undefined` (or a path unmapped in the registry) makes every
 *   returned handler a no-op.
 * @param delayMs Intent debounce, default `ROUTE_CHUNK_PREFETCH_INTENT_DELAY_MS`.
 */
export function useRouteChunkPrefetchOnIntent(
  path: string | null | undefined,
  delayMs: number = ROUTE_CHUNK_PREFETCH_INTENT_DELAY_MS,
): RouteChunkPrefetchIntentHandlers {
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Read via ref (not a schedule/cancel dependency) so a caller that changes
  // `path` on every render doesn't churn the returned handler identities --
  // same rationale as usePrefetchOnIntent's toRef.
  const pathRef = useRef(path);
  useEffect(() => {
    pathRef.current = path;
  }, [path]);

  const cancel = useCallback(() => {
    if (timerRef.current != null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const schedule = useCallback(() => {
    cancel();
    const loader = pathRef.current ? resolveRouteChunkLoader(pathRef.current) : null;
    if (!loader) return;
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      // A chunk-load failure here (e.g. offline) must never surface as an
      // uncaught rejection -- the real navigation's own Suspense/error
      // boundary is the one place that failure is allowed to be visible.
      loader().catch(() => {});
    }, delayMs);
  }, [cancel, delayMs]);

  useEffect(() => cancel, [cancel]);

  return {
    schedule,
    cancel,
    onPointerEnter: schedule,
    onPointerLeave: cancel,
    onFocus: schedule,
    onBlur: cancel,
  };
}
