/**
 * usePrefetchOnIntent -- hover/focus "intent" -> prefetch (bu-qvnce.14 slice
 * 4, deferred from PR #2927). Zero prefetchQuery call sites existed before
 * this; wired into RowLink and DisclosureRow (src/components/ui/) plus the
 * command palette's highlighted result (EntityFinder.tsx).
 *
 * A pointer-sweep across a dense list (five rows crossed on the way to a
 * sixth) must not fire five speculative fetches -- entering a row starts a
 * short timer (default 120ms); leaving before it fires cancels it. Only a
 * genuine pause-over-a-row reaches the fetch.
 *
 * The `to` -> query mapping lives in lib/prefetch-registry.ts. A `to` that
 * doesn't match any registry entry is a no-op -- this hook is safe to attach
 * unconditionally to every RowLink/DisclosureRow without maintaining a
 * parallel "is this route covered" list.
 *
 * Uses `QueryClientContext` directly (not `useQueryClient()`) so a row
 * rendered outside a `QueryClientProvider` (most component unit tests here
 * predate react-query and don't wrap one) degrades to a no-op instead of
 * throwing "No QueryClient set."
 */

import { useCallback, useContext, useEffect, useRef } from "react";
import { QueryClientContext } from "@tanstack/react-query";

import { resolvePrefetchTarget } from "@/lib/prefetch-registry";

/** Pointer-sweep debounce -- short enough to feel instant on a deliberate
 *  hover, long enough that crossing a row without stopping fires nothing. */
export const PREFETCH_INTENT_DELAY_MS = 120;

export interface PrefetchIntentHandlers {
  /** Start the intent timer (attach to onPointerEnter/onFocus). */
  schedule: () => void;
  /** Cancel any pending timer (attach to onPointerLeave/onBlur). */
  cancel: () => void;
  onPointerEnter: () => void;
  onPointerLeave: () => void;
  onFocus: () => void;
  onBlur: () => void;
  /** Warm immediately for keyboard activation or a click before navigation. */
  warmNow?: () => void;
  onActivate?: () => void;
}

/**
 * @param to Target path to resolve via the route-registry prefetch map.
 *   `null`/`undefined` (or a target unmapped in the registry) makes every
 *   returned handler a no-op.
 * @param delayMs Intent debounce, default `PREFETCH_INTENT_DELAY_MS`.
 */
export function usePrefetchOnIntent(
  to: string | null | undefined,
  delayMs: number = PREFETCH_INTENT_DELAY_MS,
): PrefetchIntentHandlers {
  const queryClient = useContext(QueryClientContext);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Read via ref rather than a schedule/cancel dependency so callers that
  // change `to` on every render (e.g. a list row) don't churn the returned
  // handler identities. Written in an effect (not during render) -- this
  // repo's `react-hooks/refs` lint rule forbids writing a ref's `.current`
  // during render outright (the "latest ref" idiom of mutating synchronously
  // in the render body is not available here). Effect registration order is
  // stable within a component (React flushes passive effects in hook-call
  // order), so a caller invoking `schedule()` from its own effect that reads
  // `to` (e.g. EntityFinder's highlight-prefetch effect) is safe as long as
  // that effect is declared after this hook is called -- which every current
  // call site does.
  const toRef = useRef(to);
  useEffect(() => {
    toRef.current = to;
  }, [to]);

  const cancel = useCallback(() => {
    if (timerRef.current != null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const schedule = useCallback(() => {
    cancel();
    if (!queryClient) return;
    const target = toRef.current ? resolvePrefetchTarget(toRef.current) : null;
    if (!target) return;
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      void queryClient.prefetchQuery(target);
    }, delayMs);
  }, [cancel, delayMs, queryClient]);

  const warmNow = useCallback(() => {
    cancel();
    if (!queryClient) return;
    const target = toRef.current ? resolvePrefetchTarget(toRef.current) : null;
    if (!target) return;
    void queryClient.prefetchQuery(target);
  }, [cancel, queryClient]);

  // A row unmounted mid-hover (e.g. list re-sorts/filters out from under the
  // cursor) must not fire a prefetch for a target no longer in view.
  useEffect(() => cancel, [cancel]);

  return {
    schedule,
    cancel,
    onPointerEnter: schedule,
    onPointerLeave: cancel,
    // Focus is the keyboard-intent signal. Pointer hover remains debounced;
    // starting here also means focus-then-activate cannot cancel the warmup.
    onFocus: warmNow,
    onBlur: cancel,
    warmNow,
    onActivate: warmNow,
  };
}
