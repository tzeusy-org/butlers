/**
 * The browser-local calendar day, re-evaluated at local midnight and when a
 * hidden tab becomes visible again. A memo with `[]` deps would freeze
 * "tomorrow" at mount, so a long-lived tab would keep labelling the wrong day. A timer runs on a
 * monotonic clock that can stop across OS sleep, so it re-arms at most every minute and the day is
 * also re-read on focus, pageshow and visibility.
 */

import { useEffect, useState } from "react";
import { addDays, startOfDay } from "date-fns";

/** Milliseconds from `now` until the next local midnight (always > 0). */
export function msUntilNextLocalMidnight(now: Date): number {
  return addDays(startOfDay(now), 1).getTime() - now.getTime();
}

/** Longest wait between re-reads: bounds how stale a timer paused by sleep can leave the day. */
const MAX_REARM_MS = 60_000;

/** Start of the current browser-local day; changes identity only when the day changes. */
export function useLocalDayStart(): Date {
  const [dayStart, setDayStart] = useState(() => startOfDay(new Date()));

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;

    const sync = () => {
      const current = startOfDay(new Date());
      setDayStart((prev) => (prev.getTime() === current.getTime() ? prev : current));
    };
    const arm = () => {
      timer = setTimeout(() => {
        sync();
        arm();
      }, Math.min(msUntilNextLocalMidnight(new Date()) + 50, MAX_REARM_MS));
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") sync();
    };

    arm();
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", sync);
    window.addEventListener("pageshow", sync);
    return () => {
      if (timer !== undefined) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", sync);
      window.removeEventListener("pageshow", sync);
    };
  }, []);

  return dayStart;
}
