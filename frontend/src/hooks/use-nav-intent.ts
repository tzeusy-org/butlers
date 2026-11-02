/**
 * useNavIntent -- one navigation-intent primitive for route-aware controls
 * (openspec/changes/unify-dashboard-navigation-intent, "Unified Navigation
 * Intent Warmup"). One intent cycle warms every resource the shell capability
 * authority maps for the destination: the lazy route chunk
 * (lib/route-chunk-registry.ts) and, when declared, the destination's query
 * (lib/prefetch-registry.ts), coordinated by one pending timer per instance.
 *
 * Pointer intent needs an uninterrupted dwell (a sweep across a dense list
 * must not fire a speculative fetch per row). Keyboard focus and activation
 * (click, Enter, supported Space) warm immediately and join the current
 * cycle, so each resource starts at most once per cycle. A cycle ends after
 * activation, when neither pointer nor focus remains, when the target changes,
 * or on unmount; a later cycle may warm again (TanStack Query and the browser
 * module cache dedupe natively).
 *
 * Warmup is speculative and failure-contained: no rejection escapes. An
 * unmapped or null target is a no-op. `QueryClientContext` is read directly
 * (not `useQueryClient()`) so a control rendered outside a provider warms its
 * chunk and treats the query as a no-op instead of throwing.
 */

import { useCallback, useContext, useEffect, useRef } from "react";
import { QueryClientContext } from "@tanstack/react-query";

import { resolvePrefetchTarget } from "@/lib/prefetch-registry";
import { resolveRouteChunkLoader } from "@/lib/route-chunk-registry";

/** Uninterrupted pointer dwell before intent is established. */
export const NAV_INTENT_DELAY_MS = 120;

export interface NavIntentHandlers {
  /** Attach to onPointerEnter. */
  onPointerEnter: () => void;
  /** Attach to onPointerLeave / pointer cancellation. */
  onPointerLeave: () => void;
  /** Keyboard intent: warms immediately. */
  onFocus: () => void;
  onBlur: () => void;
  /** Warm immediately before navigation or an imperative activation callback. */
  onActivate: () => void;
}

/**
 * @param to Destination path resolved through the shell capability maps.
 *   `null`/`undefined` (or an unmapped target) makes every handler a no-op.
 * @param delayMs Pointer dwell, default `NAV_INTENT_DELAY_MS`.
 */
export function useNavIntent(
  to: string | null | undefined,
  delayMs: number = NAV_INTENT_DELAY_MS,
): NavIntentHandlers {
  const queryClient = useContext(QueryClientContext);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pointerRef = useRef(false);
  const focusRef = useRef(false);
  const startedRef = useRef({ chunk: false, query: false });
  // Written in an effect (the repo's `react-hooks/refs` rule forbids writes
  // during render) so handler identities stay stable when `to` changes.
  const toRef = useRef(to);

  const clearTimer = useCallback(() => {
    if (timerRef.current != null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const endCycle = useCallback(() => {
    clearTimer();
    startedRef.current = { chunk: false, query: false };
  }, [clearTimer]);

  useEffect(() => {
    toRef.current = to;
    // A replacement target needs a fresh intent signal.
    pointerRef.current = false;
    focusRef.current = false;
    endCycle();
  }, [to, endCycle]);

  // A control unmounted mid-hover must not warm a target no longer in view.
  useEffect(() => endCycle, [endCycle]);

  const warm = useCallback(() => {
    const target = toRef.current;
    if (!target) return;
    const started = startedRef.current;
    if (!started.chunk) {
      const loader = resolveRouteChunkLoader(target);
      if (loader) {
        started.chunk = true;
        try {
          void Promise.resolve(loader()).catch(() => {});
        } catch {
          // A synchronous loader failure is as contained as a rejection.
        }
      }
    }
    if (!started.query && queryClient) {
      const query = resolvePrefetchTarget(target);
      if (query) {
        started.query = true;
        try {
          void Promise.resolve(queryClient.prefetchQuery(query)).catch(() => {});
        } catch {
          // Speculative: the destination's own query owns error presentation.
        }
      }
    }
  }, [queryClient]);

  const onPointerEnter = useCallback(() => {
    pointerRef.current = true;
    clearTimer();
    if (!toRef.current) return;
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      warm();
    }, delayMs);
  }, [clearTimer, delayMs, warm]);

  const onPointerLeave = useCallback(() => {
    pointerRef.current = false;
    clearTimer();
    if (!focusRef.current) endCycle();
  }, [clearTimer, endCycle]);

  const onFocus = useCallback(() => {
    focusRef.current = true;
    clearTimer();
    warm();
  }, [clearTimer, warm]);

  const onBlur = useCallback(() => {
    focusRef.current = false;
    if (!pointerRef.current) endCycle();
  }, [endCycle]);

  const onActivate = useCallback(() => {
    clearTimer();
    warm();
    // Activation dispatch completes with this handler's caller; end the cycle
    // afterwards so focus + activation share one cycle but a later one is free.
    queueMicrotask(endCycle);
  }, [clearTimer, warm, endCycle]);

  return { onPointerEnter, onPointerLeave, onFocus, onBlur, onActivate };
}
