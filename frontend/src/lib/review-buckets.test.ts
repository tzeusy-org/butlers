// ---------------------------------------------------------------------------
// review-buckets tests [bu-fhsph]
//
// Regression guard for the host-local review-bucketing bug: both education
// review surfaces anchored the Today / This-week boundary to the viewer's HOST
// timezone, so a review due near midnight bucketed differently depending on
// where the browser ran. vitest pins TZ=UTC (vite.config.ts), which hid the
// skew — so these tests vary process.env.TZ explicitly (matching the
// daily-buckets.test.ts pattern) and assert owner-tz-anchored bucketing.
//
// The owner tz here is Asia/Singapore (UTC+8), the app default (DEFAULT_TZ).
// ---------------------------------------------------------------------------

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { classifyReviewBucket, reviewBoundaries, type ReviewBucket } from "./review-buckets";

const OWNER_TZ = "Asia/Singapore"; // UTC+8, no DST
const HOST_TIMEZONES = ["UTC", "Asia/Singapore", "America/Los_Angeles", "Pacific/Kiritimati"];

/**
 * The OLD host-local classifier, reproduced here so the flip it caused is
 * asserted rather than merely described. Anchors `todayEnd` to the host zone's
 * midnight via local Date getters — the exact logic both surfaces used before
 * bu-fhsph. Under vitest's pinned TZ=UTC this is deterministic.
 */
function classifyHostLocal(nextReviewAt: string, now: Date): ReviewBucket {
  const reviewDate = new Date(nextReviewAt);
  const todayEnd = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 23, 59, 59, 999);
  const weekEnd = new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000);
  if (reviewDate < now) return "overdue";
  if (reviewDate <= todayEnd) return "today";
  if (reviewDate <= weekEnd) return "this-week";
  return "later";
}

describe("reviewBoundaries", () => {
  it("anchors todayEnd to owner-tz midnight, not the host zone", () => {
    // now = 2026-06-10 10:00 SGT. End of *today in Singapore* is 23:59:59.999
    // SGT = 2026-06-10T15:59:59.999Z — NOT 2026-06-10T23:59:59Z (end of the UTC
    // day), which is what a host-local anchor would have produced.
    const now = new Date("2026-06-10T02:00:00.000Z");
    const { todayEnd } = reviewBoundaries(now, OWNER_TZ, "now");
    expect(todayEnd.toISOString()).toBe("2026-06-10T15:59:59.999Z");
  });

  it("preserves each surface's week-anchor semantics", () => {
    const now = new Date("2026-06-10T02:00:00.000Z");
    // "now" anchor: strictly 7×24h from now.
    expect(reviewBoundaries(now, OWNER_TZ, "now").weekEnd.toISOString()).toBe(
      "2026-06-17T02:00:00.000Z",
    );
    // "end-of-today" anchor: 7 days past the owner-tz end of today.
    expect(reviewBoundaries(now, OWNER_TZ, "end-of-today").weekEnd.toISOString()).toBe(
      "2026-06-17T15:59:59.999Z",
    );
  });
});

describe("classifyReviewBucket — owner-tz anchoring", () => {
  const now = new Date("2026-06-10T02:00:00.000Z"); // 2026-06-10 10:00 SGT

  it("buckets a review due late today (owner tz) as Today", () => {
    // 2026-06-10 23:30 SGT = 2026-06-10T15:30:00Z — still today in Singapore.
    expect(classifyReviewBucket("2026-06-10T15:30:00.000Z", now, OWNER_TZ, "now")).toBe("today");
  });

  it("buckets a review just past owner-tz midnight as This-week, not Today", () => {
    // 2026-06-11 00:30 SGT = 2026-06-10T16:30:00Z — tomorrow in Singapore.
    expect(classifyReviewBucket("2026-06-10T16:30:00.000Z", now, OWNER_TZ, "now")).toBe(
      "this-week",
    );
  });

  it("classifies past-due, near-future, and far-future reviews correctly", () => {
    expect(classifyReviewBucket("2026-06-10T01:00:00.000Z", now, OWNER_TZ, "now")).toBe("overdue");
    expect(classifyReviewBucket("2026-06-13T00:00:00.000Z", now, OWNER_TZ, "now")).toBe(
      "this-week",
    );
    expect(classifyReviewBucket("2026-06-24T00:00:00.000Z", now, OWNER_TZ, "now")).toBe("later");
  });

  it("respects the week anchor for a review in the 7-day boundary gap", () => {
    // 2026-06-17 10:00Z sits between weekEnd("now")=2026-06-17T02:00Z and
    // weekEnd("end-of-today")=2026-06-17T15:59:59Z.
    const review = "2026-06-17T10:00:00.000Z";
    expect(classifyReviewBucket(review, now, OWNER_TZ, "now")).toBe("later");
    expect(classifyReviewBucket(review, now, OWNER_TZ, "end-of-today")).toBe("this-week");
  });
});

describe("classifyReviewBucket — host-timezone invariance", () => {
  const originalTZ = process.env.TZ;
  // 2026-06-10 10:00 SGT, frozen so the helper's callers see a fixed `now`.
  const FROZEN_NOW = new Date("2026-06-10T02:00:00.000Z");

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(FROZEN_NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
    process.env.TZ = originalTZ;
  });

  it("lands a review near owner-midnight in the same bucket in every host zone", () => {
    // A review just past owner-tz midnight must read "this-week" everywhere —
    // the whole point is that the bucket cannot depend on the host zone.
    for (const review of [
      "2026-06-10T15:30:00.000Z", // late today SGT → today
      "2026-06-10T16:30:00.000Z", // just past SGT midnight → this-week
    ]) {
      const results = new Set<ReviewBucket>();
      for (const tz of HOST_TIMEZONES) {
        process.env.TZ = tz;
        results.add(classifyReviewBucket(review, new Date(), OWNER_TZ, "now"));
      }
      expect(results.size).toBe(1);
    }
  });

  it("would flip Today↔This-week under the old host-local anchor", () => {
    // Under the pinned host TZ=UTC, the old host-local classifier calls this
    // review "today" (before the UTC day-end), while owner-tz anchoring
    // correctly calls it "this-week" (already tomorrow in Singapore). This is
    // the exact regression the owner-tz anchor fixes.
    const review = "2026-06-10T16:30:00.000Z";
    process.env.TZ = "UTC";
    expect(classifyHostLocal(review, new Date())).toBe("today");
    expect(classifyReviewBucket(review, new Date(), OWNER_TZ, "now")).toBe("this-week");
  });
});
