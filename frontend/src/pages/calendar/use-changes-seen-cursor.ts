import { useCallback, useState } from "react";

const KEY = "calendar-changes-seen-at";
const DAY_MS = 24 * 60 * 60 * 1000;
const MAX_AGE_MS = 14 * DAY_MS - 60 * 1000; // the route refuses a window past 14 days

function readStored(): string | null {
  try {
    return window.localStorage.getItem(KEY);
  } catch {
    return null; // storage can be blocked or cleared; the page works without it
  }
}

/** The instant before which the owner has seen changes; a per-viewer convenience only. */
export function clampSince(stored: string | null, now: number): string {
  const parsed = stored === null ? Number.NaN : Date.parse(stored);
  const floor = now - MAX_AGE_MS;
  const since = Number.isNaN(parsed) ? now - DAY_MS : Math.min(Math.max(parsed, floor), now);
  return new Date(since).toISOString();
}

export function useChangesSeenCursor() {
  const [since, setSince] = useState(() => clampSince(readStored(), Date.now()));
  const markSeen = useCallback(() => {
    const now = new Date().toISOString();
    try {
      window.localStorage.setItem(KEY, now);
    } catch {
      // keep the in-memory cursor
    }
    setSince(now);
  }, []);
  return { since, markSeen };
}
