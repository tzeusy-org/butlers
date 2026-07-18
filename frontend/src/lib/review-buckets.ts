// ---------------------------------------------------------------------------
// Education review-timeline bucketing [bu-fhsph]
//
// Both education review surfaces — ReviewTimeline (education page) and the
// ButlerEducationReviewsTab pending-reviews panel — sort due reviews into
// Overdue / Today / This-week / Later buckets. The Today boundary is the last
// instant of *today*, which is a wall-clock notion and therefore timezone
// dependent. Both surfaces previously anchored that boundary to the viewer's
// HOST-local midnight (`new Date(now.getFullYear(), now.getMonth(), …)`), so a
// review due near midnight bucketed differently depending on where the browser
// ran — and the vitest `TZ=UTC` pin (vite.config.ts) hid the skew.
//
// This helper anchors the day boundary to the OWNER's configured timezone (via
// endOfDayInTz), so the two surfaces agree and the bucket a review lands in no
// longer depends on the host zone. It never falls back to host-local time —
// callers pass the owner tz (components feed `useTimezone()`; the app default
// is Asia/Singapore).
//
// The Overdue|Today boundary is `now` — a pure instant comparison that is the
// same in every timezone, so only the Today|This-week (and This-week|Later)
// boundaries are tz-sensitive here.
// ---------------------------------------------------------------------------

import { endOfDayInTz } from "@/lib/tz-format";

/** A review-timeline bucket, in chronological order. */
export type ReviewBucket = "overdue" | "today" | "this-week" | "later";

/**
 * How the "This week" upper boundary is anchored. The two surfaces disagree on
 * this by design, so it is a parameter rather than a hard-coded choice:
 *
 * - `"end-of-today"` — 7 days past the end of *today* (owner tz). Used by
 *   ReviewTimeline, whose weekEnd was `todayEnd + 7d`.
 * - `"now"` — exactly 7×24h from `now`. Used by ButlerEducationReviewsTab,
 *   whose weekEnd was `now + 7d`.
 */
export type WeekAnchor = "end-of-today" | "now";

const WEEK_MS = 7 * 24 * 60 * 60 * 1000;

/**
 * Owner-tz-anchored boundaries used to bucket review due-dates.
 *
 * `todayEnd` is the last instant of *today* in `tz` (NOT the host zone), so a
 * viewer in any host timezone computes the same boundary. `weekEnd` extends
 * `WEEK_MS` past either `todayEnd` or `now`, per `weekAnchor`.
 */
export function reviewBoundaries(
  now: Date,
  tz: string,
  weekAnchor: WeekAnchor,
): { todayEnd: Date; weekEnd: Date } {
  const todayEnd = endOfDayInTz(now, tz);
  const weekEnd =
    weekAnchor === "end-of-today"
      ? new Date(todayEnd.getTime() + WEEK_MS)
      : new Date(now.getTime() + WEEK_MS);
  return { todayEnd, weekEnd };
}

/**
 * Classify a review's `next_review_at` into a timeline bucket, anchored to
 * owner-tz midnight.
 *
 * Pure: `now`, `tz`, and `weekAnchor` are all injected, so the result never
 * depends on the host timezone. A review whose due instant has already passed
 * is `"overdue"` (a tz-independent instant comparison); otherwise it is placed
 * relative to the owner-tz `todayEnd` / `weekEnd` boundaries.
 */
export function classifyReviewBucket(
  nextReviewAt: string | Date,
  now: Date,
  tz: string,
  weekAnchor: WeekAnchor,
): ReviewBucket {
  const reviewDate =
    nextReviewAt instanceof Date ? nextReviewAt : new Date(nextReviewAt);
  const { todayEnd, weekEnd } = reviewBoundaries(now, tz, weekAnchor);
  if (reviewDate < now) return "overdue";
  if (reviewDate <= todayEnd) return "today";
  if (reviewDate <= weekEnd) return "this-week";
  return "later";
}
