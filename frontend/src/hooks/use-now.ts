/**
 * The owner's `/now` card (bu-5ii4gj.2 P3). Not polled: it is re-read when a `calendar` bus event
 * invalidates the `["now"]` key, when the agenda line's freshness ends, and at local midnight.
 */

import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, getNow } from "@/api/client.ts";
import type { NowCard } from "@/api/types.ts";

export const NOW_QUERY_KEY = ["now"] as const;

/** Shortest wait: an already-expired line must not turn into a tight re-read loop. */
const MIN_TIMER_MS = 5000;

/** Longest a single timer may wait; setTimeout overflows past 2^31 - 1 ms. */
const MAX_TIMER_MS = 2 ** 31 - 1;

/** Milliseconds until the next re-read is due: the soonest line expiry or the next local midnight. */
export function msUntilNextBoundary(card: NowCard | undefined, now: Date): number {
  const midnight = new Date(now);
  midnight.setHours(24, 0, 0, 0);
  let wait = midnight.getTime() - now.getTime();
  for (const line of card?.lines ?? []) {
    if (!line.fresh_until) continue;
    const at = new Date(line.fresh_until).getTime();
    if (Number.isFinite(at)) wait = Math.min(wait, at - now.getTime());
  }
  return Math.min(Math.max(wait, MIN_TIMER_MS), MAX_TIMER_MS);
}

export function isNowSurfaceOff(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

export function useNow() {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: NOW_QUERY_KEY,
    queryFn: getNow,
    staleTime: 30_000,
    // A 404 means the surface is off and a 503 that the reader is down: neither heals by retrying.
    retry: (count, error) =>
      !(error instanceof ApiError && (error.status === 404 || error.status === 503)) &&
      count < 1,
  });

  const card = query.data?.data;
  useEffect(() => {
    if (!card) return;
    const timer = setTimeout(
      () => void queryClient.invalidateQueries({ queryKey: NOW_QUERY_KEY }),
      msUntilNextBoundary(card, new Date()),
    );
    return () => clearTimeout(timer);
  }, [card, query.dataUpdatedAt, queryClient]);

  return query;
}
