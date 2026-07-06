/**
 * useTickingNow — a `now` timestamp that ticks on a wall clock rather than
 * only advancing when its caller re-renders for some other reason.
 *
 * Used by live-elapsed displays (e.g. the Sessions pinned strip's running-
 * session timer, bu-ptaub) so they advance on their own instead of freezing
 * at whatever `now` happened to be at the last data-driven render. Purely
 * local component state — no network refetch is triggered by the tick.
 */

import { useEffect, useState } from "react";

/** Default tick cadence: frequent enough to catch "just started" -> "1m
 * elapsed" promptly without re-rendering needlessly often. */
const DEFAULT_TICK_MS = 15_000;

export function useTickingNow(intervalMs: number = DEFAULT_TICK_MS): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}
