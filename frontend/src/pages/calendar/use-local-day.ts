/**
 * The browser-local calendar day, re-evaluated at local midnight and when a
 * hidden tab becomes visible again. A memo with `[]` deps would freeze
 * "tomorrow" at mount, so a long-lived tab would keep labelling the wrong day.
 */

import { useEffect, useState } from "react";
import { addDays, startOfDay } from "date-fns";

/** Milliseconds from `now` until the next local midnight (always > 0). */
export function msUntilNextLocalMidnight(now: Date): number {
  return addDays(startOfDay(now), 1).getTime() - now.getTime();
}

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
      }, msUntilNextLocalMidnight(new Date()) + 50);
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") sync();
    };

    arm();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      if (timer !== undefined) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  return dayStart;
}
