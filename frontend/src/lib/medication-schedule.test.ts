import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Medication } from "@/api/types";
import {
  computeNextDoses,
  minutesOfDayInTimeZone,
  parseScheduleTime,
} from "@/lib/medication-schedule";

describe("parseScheduleTime", () => {
  it("parses a well-formed HH:MM into minutes-since-midnight", () => {
    expect(parseScheduleTime("00:00")).toBe(0);
    expect(parseScheduleTime("08:00")).toBe(480);
    expect(parseScheduleTime("08:30")).toBe(510);
    expect(parseScheduleTime("23:59")).toBe(1439);
  });

  it("accepts a single-digit hour", () => {
    expect(parseScheduleTime("8:00")).toBe(480);
    expect(parseScheduleTime("9:05")).toBe(545);
  });

  it("trims surrounding whitespace", () => {
    expect(parseScheduleTime("  08:00  ")).toBe(480);
    expect(parseScheduleTime("\t12:15\n")).toBe(735);
  });

  it("rejects out-of-range hours and minutes", () => {
    expect(parseScheduleTime("24:00")).toBeNull();
    expect(parseScheduleTime("25:00")).toBeNull();
    expect(parseScheduleTime("12:60")).toBeNull();
    expect(parseScheduleTime("12:99")).toBeNull();
  });

  it("rejects malformed strings", () => {
    expect(parseScheduleTime("")).toBeNull();
    expect(parseScheduleTime("0800")).toBeNull();
    expect(parseScheduleTime("8")).toBeNull();
    expect(parseScheduleTime("08:0")).toBeNull();
    expect(parseScheduleTime("08:000")).toBeNull();
    expect(parseScheduleTime("8:00am")).toBeNull();
    expect(parseScheduleTime("noon")).toBeNull();
    expect(parseScheduleTime("08-00")).toBeNull();
    expect(parseScheduleTime("08:00:00")).toBeNull();
  });

  it("rejects non-string inputs", () => {
    expect(parseScheduleTime(null)).toBeNull();
    expect(parseScheduleTime(undefined)).toBeNull();
    expect(parseScheduleTime(480)).toBeNull();
    expect(parseScheduleTime({ time: "08:00" })).toBeNull();
    expect(parseScheduleTime(["08:00"])).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Timezone-aware next-dose computation (bu-6ztft — HEALTH-CRITICAL)
//
// vitest pins the runner to TZ=UTC (vite.config.ts). These specs vary
// process.env.TZ explicitly so the regression — reading "now" from the
// viewer's HOST clock instead of the OWNER's timezone — is actually
// exercised. Host-local logic would make the computed next dose depend on
// where the dashboard is viewed from; the owner-tz logic must not.
// ---------------------------------------------------------------------------

const ZONES = ["UTC", "Asia/Singapore", "America/Los_Angeles", "Pacific/Kiritimati"];

function med(overrides: Partial<Medication> = {}): Medication {
  return {
    id: "med-1",
    name: "Vitamin D",
    dosage: "1000IU",
    frequency: "daily",
    schedule: ["09:00", "11:00"],
    active: true,
    notes: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

describe("minutesOfDayInTimeZone", () => {
  const originalTZ = process.env.TZ;
  // 2026-07-07T02:00:00Z → 10:00 in Singapore (UTC+8), 19:00 the previous day
  // in Los Angeles (UTC-7 PDT), 02:00 in UTC.
  const at = new Date("2026-07-07T02:00:00.000Z");

  afterEach(() => {
    process.env.TZ = originalTZ;
  });

  it("reads the wall clock in the given zone, not the host zone", () => {
    for (const hostTz of ZONES) {
      process.env.TZ = hostTz;
      expect(minutesOfDayInTimeZone("Asia/Singapore", at)).toBe(10 * 60); // 10:00
      expect(minutesOfDayInTimeZone("America/Los_Angeles", at)).toBe(19 * 60); // 19:00
      expect(minutesOfDayInTimeZone("UTC", at)).toBe(2 * 60); // 02:00
    }
  });

  it("reads midnight as 00:00, never 24:00", () => {
    // 2026-07-06T16:00:00Z is exactly 00:00 the next day in Singapore.
    const midnight = new Date("2026-07-06T16:00:00.000Z");
    expect(minutesOfDayInTimeZone("Asia/Singapore", midnight)).toBe(0);
  });

  it("falls back to UTC for an unusable timezone rather than to host-local", () => {
    process.env.TZ = "America/Los_Angeles";
    expect(minutesOfDayInTimeZone("Not/AZone", at)).toBe(minutesOfDayInTimeZone("UTC", at));
  });
});

describe("computeNextDoses: timezone stability", () => {
  const originalTZ = process.env.TZ;

  beforeEach(() => {
    vi.useFakeTimers();
    // 10:00 in Singapore, 19:00 (prev day) in Los Angeles.
    vi.setSystemTime(new Date("2026-07-07T02:00:00.000Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
    process.env.TZ = originalTZ;
  });

  it("returns the same next dose regardless of the viewer's host timezone", () => {
    const meds = [med({ schedule: ["09:00", "11:00"] })];
    const results = new Set<string>();
    for (const hostTz of ZONES) {
      process.env.TZ = hostTz;
      // Owner timezone is Asia/Singapore; default `now` picks up the fake clock.
      const [nextDose] = computeNextDoses(meds, "Asia/Singapore");
      results.add(`${nextDose.time}|${nextDose.tomorrow}|${nextDose.minutesAway}`);
    }
    // One distinct result across every host timezone: the computation is
    // anchored to the owner zone, not the host clock.
    expect(results.size).toBe(1);
    expect([...results][0]).toBe("11:00|false|60"); // 11:00 today, 60 min away
  });
});

describe("computeNextDoses: owner vs host timezone flips the next dose", () => {
  const originalTZ = process.env.TZ;
  // Fixed instant: 10:00 in Singapore, 19:00 (previous day) in Los Angeles.
  const now = new Date("2026-07-07T02:00:00.000Z");

  afterEach(() => {
    process.env.TZ = originalTZ;
  });

  it("picks a different next dose for a Singapore owner than host-local would", () => {
    // The dashboard is being viewed from Los Angeles.
    process.env.TZ = "America/Los_Angeles";
    const meds = [med({ schedule: ["09:00", "11:00"] })];

    // Correct: owner is in Singapore (now 10:00) → next dose is 11:00 TODAY.
    const [ownerNext] = computeNextDoses(meds, "Asia/Singapore", now);
    expect(ownerNext.time).toBe("11:00");
    expect(ownerNext.tomorrow).toBe(false);

    // The old host-local behavior (viewer in LA, now 19:00) would instead have
    // said the next dose is 09:00 TOMORROW — a wrong, health-critical answer.
    // Passing the LA zone reproduces that mislabeling, proving the two differ.
    const [hostNext] = computeNextDoses(meds, "America/Los_Angeles", now);
    expect(hostNext.time).toBe("09:00");
    expect(hostNext.tomorrow).toBe(true);

    expect(ownerNext.time).not.toBe(hostNext.time);
    expect(ownerNext.tomorrow).not.toBe(hostNext.tomorrow);
  });

  it("skips inactive meds and unparseable schedule entries", () => {
    process.env.TZ = "America/Los_Angeles";
    const meds = [
      med({ id: "a", active: false, schedule: ["11:00"] }),
      med({ id: "b", schedule: ["not-a-time", "11:00"] }),
    ];
    const doses = computeNextDoses(meds, "Asia/Singapore", now);
    expect(doses).toHaveLength(1);
    expect(doses[0].medicationId).toBe("b");
    expect(doses[0].time).toBe("11:00");
  });
});
