/** When the day verdict next changes on its own: a meeting starts or ends, 18:00, or midnight. */

import { addDays } from "date-fns";
import { formatInTimeZone, fromZonedTime } from "date-fns-tz";

import type { DayVerdictResponse } from "@/api/types.ts";

/** The server flips an unspecified verdict to tomorrow from this owner-local hour. */
export const VERDICT_ROLLOVER_HOUR = 18;

/** The next owner-local `hour:00` strictly after `now`, as an instant. */
function nextLocalHour(now: Date, zone: string, hour: number): Date {
  const day = formatInTimeZone(now, zone, "yyyy-MM-dd");
  const today = fromZonedTime(`${day}T${String(hour).padStart(2, "0")}:00:00`, zone);
  if (today.getTime() > now.getTime()) return today;
  const tomorrow = formatInTimeZone(addDays(new Date(`${day}T12:00:00Z`), 1), "UTC", "yyyy-MM-dd");
  return fromZonedTime(`${tomorrow}T${String(hour).padStart(2, "0")}:00:00`, zone);
}

/** Milliseconds until the verdict is next due a re-read; never under a second. */
export function msUntilVerdictBoundary(verdict: DayVerdictResponse | undefined, now: Date): number {
  if (!verdict) return 60_000;
  const candidates = [
    nextLocalHour(now, verdict.zone, VERDICT_ROLLOVER_HOUR).getTime(),
    nextLocalHour(now, verdict.zone, 0).getTime(),
  ];
  for (const entry of [verdict.current, verdict.next]) {
    if (!entry) continue;
    for (const iso of [entry.start, entry.end]) {
      const at = new Date(iso).getTime();
      if (at > now.getTime()) candidates.push(at);
    }
  }
  return Math.min(Math.max(Math.min(...candidates) - now.getTime(), 1000), 2 ** 31 - 1);
}
