// ---------------------------------------------------------------------------
// chronicles-date-nav — pure helpers for the Chronicles retrospective archive.
//
// The archive navigates whole calendar days held as "YYYY-MM-DD" strings (the
// owner-tz calendar date echoed by the briefing). All math anchors the date at
// UTC midnight so it never drifts with the browser timezone, mirroring how the
// backend resolves the day window. Day strings compare correctly with `<`/`>`
// because the fixed-width format is lexicographically ordered.
// ---------------------------------------------------------------------------

const DAY_MS = 86_400_000;

const WEEKDAYS = [
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
] as const;

const MONTHS_SHORT = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
] as const;

const ISO_DAY_RE = /^\d{4}-\d{2}-\d{2}$/;

/**
 * True when `s` is a real "YYYY-MM-DD" calendar date. Guards against malformed
 * `?date=` deep links that would otherwise produce Invalid Dates downstream
 * (a crashing stepper or an "undefined" greeting).
 */
export function isValidIsoDay(s: string | null | undefined): s is string {
  if (!s || !ISO_DAY_RE.test(s)) return false;
  const d = new Date(`${s}T00:00:00Z`);
  // Round-trip rejects normalized impossibilities (e.g. 2026-02-30, 2026-13-01).
  return !isNaN(d.getTime()) && d.toISOString().slice(0, 10) === s;
}

function isoToUtc(iso: string): Date {
  return new Date(`${iso}T00:00:00Z`);
}

function utcToIso(d: Date): string {
  return d.toISOString().slice(0, 10);
}

/** Shift an ISO day string by `delta` whole days. */
export function addIsoDays(iso: string, delta: number): string {
  const d = isoToUtc(iso);
  d.setUTCDate(d.getUTCDate() + delta);
  return utcToIso(d);
}

export function prevIsoDay(iso: string): string {
  return addIsoDays(iso, -1);
}

export function nextIsoDay(iso: string): string {
  return addIsoDays(iso, 1);
}

/** Clamp an ISO day into `[earliest, latest]`; a null earliest means unbounded. */
export function clampIsoDay(
  iso: string,
  earliest: string | null | undefined,
  latest: string,
): string {
  let out = iso;
  if (out > latest) out = latest;
  if (earliest && out < earliest) out = earliest;
  return out;
}

/** True when stepping backward is no longer possible (at/before the earliest). */
export function isAtEarliest(iso: string, earliest: string | null | undefined): boolean {
  return earliest != null && iso <= earliest;
}

/** True when stepping forward is no longer possible (at/after the latest). */
export function isAtLatest(iso: string, latest: string): boolean {
  return iso >= latest;
}

function diffDays(aIso: string, bIso: string): number {
  return Math.round((isoToUtc(aIso).getTime() - isoToUtc(bIso).getTime()) / DAY_MS);
}

/**
 * The temporal subject for the greeting line, relative to the most recent
 * settled day (`latest`, normally yesterday): "Yesterday" for the latest day,
 * the weekday name for the prior few days, else a short "5 May" date. Keeps the
 * greet honest when the owner browses an older day, where "Yesterday" is wrong.
 */
export function greetSubject(iso: string, latest: string): string {
  const back = diffDays(latest, iso);
  if (back <= 0) return "Yesterday";
  if (back <= 5) return WEEKDAYS[isoToUtc(iso).getUTCDay()];
  const d = isoToUtc(iso);
  return `${d.getUTCDate()} ${MONTHS_SHORT[d.getUTCMonth()]}`;
}
