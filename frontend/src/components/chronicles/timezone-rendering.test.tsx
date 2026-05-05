// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// Timezone-rendering tests — bu-k18cm
//
// Acceptance criteria:
//   AC1: GanttSwimlaneInner renders times in the owner's timezone, not the
//        browser's timezone (even when the browser is in a non-SGT zone).
//   AC5: 2026-04-30T00:00:00+00 renders as "08:00" (SGT) in a simulated
//        America/Los_Angeles browser where the local time would be "17:00" (PDT).
//
// Strategy:
//   - Wrap components with a ChroniclesTimezoneProvider that injects a
//     specific tz (simulating owner config) so tests are deterministic.
//   - The browser "timezone" is irrelevant because our formatters use
//     date-fns-tz which is IANA-aware and bypasses Intl defaults.
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { GanttSwimlaneInner } from "./GanttSwimlaneInner"
import { Scrubber } from "@/components/workspace/Scrubber"
import { ChroniclesTimezoneProvider } from "./timezone-context"
import type { ChroniclerEpisode } from "@/api/types"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeEpisode(overrides: Partial<ChroniclerEpisode> & { id: string }): ChroniclerEpisode {
  return {
    source_name: "core.sessions",
    source_ref: overrides.id,
    episode_type: "work",
    start_at: "2026-04-30T00:00:00Z",
    end_at: "2026-04-30T01:00:00Z",
    precision: "minute",
    title: null,
    payload: {},
    privacy: "normal",
    retention_days: null,
    tombstone_at: null,
    canonical_start_at: "2026-04-30T00:00:00Z",
    canonical_end_at: "2026-04-30T01:00:00Z",
    canonical_title: null,
    canonical_privacy: "normal",
    corrected_at: null,
    correction_note: null,
    created_at: "2026-04-30T00:00:00Z",
    updated_at: "2026-04-30T00:00:00Z",
    category: "tasks",
    ...overrides,
  }
}

// Window: 2026-04-30 UTC day
const WINDOW_START = new Date("2026-04-30T00:00:00Z")
const WINDOW_END = new Date("2026-04-30T23:59:59Z")

// ---------------------------------------------------------------------------
// AC5: 2026-04-30T00:00:00Z → "08:00" in Asia/Singapore (UTC+8)
//      and NOT "17:00" (America/Los_Angeles PDT = UTC-7) or "00:00" (UTC)
// ---------------------------------------------------------------------------

describe("GanttSwimlane timezone rendering (bu-k18cm)", () => {
  it("AC5: renders 2026-04-30T00:00:00Z as 08:00 in Asia/Singapore owner tz", () => {
    const ep = makeEpisode({
      id: "ep-tz-sgt",
      canonical_start_at: "2026-04-30T00:00:00Z",
      canonical_end_at: "2026-04-30T01:00:00Z",
    })

    const html = renderToStaticMarkup(
      <ChroniclesTimezoneProvider timezone="Asia/Singapore">
        <GanttSwimlaneInner
          episodes={[ep]}
          windowStart={WINDOW_START}
          windowEnd={WINDOW_END}
        />
      </ChroniclesTimezoneProvider>,
    )

    // 2026-04-30T00:00:00Z is 08:00 SGT (UTC+8)
    expect(html).toContain("08:00")

    // Must NOT render the America/Los_Angeles local time (17:00 PDT on 2026-04-29)
    // or the plain UTC time (00:00).
    // Note: 00:00 may appear as part of the window boundary tick label — check that
    // "17:00" does not appear (LA local time would be wrong).
    expect(html).not.toContain("17:00")
  })

  it("AC5: renders 2026-04-30T00:00:00Z as 16:00 in America/Los_Angeles when owner tz is LA", () => {
    // 2026-04-30T00:00:00Z = 2026-04-29T17:00:00-07:00 (PDT)
    const ep = makeEpisode({
      id: "ep-tz-la",
      canonical_start_at: "2026-04-30T00:00:00Z",
      canonical_end_at: "2026-04-30T01:00:00Z",
    })

    const html = renderToStaticMarkup(
      <ChroniclesTimezoneProvider timezone="America/Los_Angeles">
        <GanttSwimlaneInner
          episodes={[ep]}
          windowStart={WINDOW_START}
          windowEnd={WINDOW_END}
        />
      </ChroniclesTimezoneProvider>,
    )

    // 2026-04-30T00:00:00Z is 17:00 PDT (UTC-7) on 2026-04-29
    expect(html).toContain("17:00")
    // Must NOT show SGT time
    expect(html).not.toContain("08:00")
  })

  it("AC1: axis tick labels render in owner tz, not browser tz", () => {
    // The axis ticks are computed from windowStart/End in UTC.
    // With Asia/Singapore tz, 2026-04-30T00:00:00Z → 08:00 SGT.
    // If browser tz were used (e.g. UTC), tick at midnight UTC would show 00:00.
    const ep = makeEpisode({ id: "ep-axis-tz" })

    const html = renderToStaticMarkup(
      <ChroniclesTimezoneProvider timezone="Asia/Singapore">
        <GanttSwimlaneInner
          episodes={[ep]}
          windowStart={WINDOW_START}
          windowEnd={WINDOW_END}
        />
      </ChroniclesTimezoneProvider>,
    )

    // The first tick is at windowStart = 2026-04-30T00:00:00Z = 08:00 SGT.
    // The axis labels div must contain SGT hour ticks, not browser-tz ticks.
    expect(html).toContain('data-testid="gantt-axis-labels"')
    // 08:00 must appear in the axis labels (first tick rendered in owner tz)
    expect(html).toContain("08:00")
  })
})

// ---------------------------------------------------------------------------
// Scrubber timezone rendering
// ---------------------------------------------------------------------------

describe("Scrubber timezone rendering (bu-k18cm)", () => {
  it("renders window-start label in owner tz with tz abbreviation", () => {
    const html = renderToStaticMarkup(
      <Scrubber
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
        snapMs={[]}
        tz="Asia/Singapore"
        onScrub={() => {}}
      />,
    )

    // 2026-04-30T00:00:00Z → 08:00 SGT
    // The scrubber label includes the tz abbreviation.
    // "SGT" on full-ICU builds, "GMT+8" on minimal-ICU builds (e.g. some CI).
    expect(html).toContain("08:00")
    expect(html).toMatch(/SGT|GMT\+8/)
  })

  it("renders in America/Los_Angeles tz, not in SGT", () => {
    const html = renderToStaticMarkup(
      <Scrubber
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
        snapMs={[]}
        tz="America/Los_Angeles"
        onScrub={() => {}}
      />,
    )

    // 2026-04-30T00:00:00Z → 17:00 PDT on 2026-04-29 (LA)
    expect(html).toContain("17:00")
    // SGT time (08:00) must NOT appear
    expect(html).not.toContain("08:00")
    // Must not show SGT abbreviation (neither full-ICU "SGT" nor minimal-ICU "GMT+8")
    expect(html).not.toMatch(/SGT|GMT\+8/)
  })
})

// ---------------------------------------------------------------------------
// tz-format utility tests
// ---------------------------------------------------------------------------

import {
  formatTimeInTz,
  formatDateTimeInTz,
  formatScrubberLabel,
  formatGanttTickLabel,
  startOfDayInTz,
  endOfDayInTz,
} from "./tz-format"

describe("tz-format utility functions (bu-k18cm)", () => {
  const SGT = "Asia/Singapore"
  const LA = "America/Los_Angeles"

  // 2026-04-30T00:00:00Z = 08:00 SGT, 17:00 PDT (2026-04-29)
  const utcMidnight = "2026-04-30T00:00:00Z"
  const utcMidnightMs = new Date(utcMidnight).getTime()

  describe("formatTimeInTz", () => {
    it("formats UTC midnight as 08:00 in SGT", () => {
      expect(formatTimeInTz(utcMidnight, SGT)).toBe("08:00")
    })

    it("formats UTC midnight as 17:00 in LA (PDT = UTC-7)", () => {
      expect(formatTimeInTz(utcMidnight, LA)).toBe("17:00")
    })

    it("returns '?' for null input", () => {
      expect(formatTimeInTz(null, SGT)).toBe("?")
    })
  })

  describe("formatDateTimeInTz", () => {
    it("formats with SGT abbreviation", () => {
      const result = formatDateTimeInTz(utcMidnight, SGT)
      // "SGT" on full-ICU builds, "GMT+8" on minimal-ICU builds (e.g. some CI).
      expect(result).toMatch(/SGT|GMT\+8/)
      expect(result).toContain("08:00")
    })

    it("returns '—' for null input", () => {
      expect(formatDateTimeInTz(null, SGT)).toBe("—")
    })
  })

  describe("formatScrubberLabel", () => {
    it("includes timezone abbreviation for ≤2 day window", () => {
      const result = formatScrubberLabel(utcMidnightMs, 86_400_000, SGT)
      expect(result).toContain("08:00")
      // "SGT" on full-ICU builds, "GMT+8" on minimal-ICU builds (e.g. some CI).
      expect(result).toMatch(/SGT|GMT\+8/)
    })

    it("differs between SGT and LA for same UTC timestamp", () => {
      const sgt = formatScrubberLabel(utcMidnightMs, 86_400_000, SGT)
      const la = formatScrubberLabel(utcMidnightMs, 86_400_000, LA)
      expect(sgt).not.toBe(la)
      // "SGT" on full-ICU builds, "GMT+8" on minimal-ICU builds (e.g. some CI).
      expect(sgt).toMatch(/SGT|GMT\+8/)
      // LA renders 17:00 (UTC-7 = PDT). The abbreviation may be "PDT" or "GMT-7"
      // depending on the Intl timezone database available in the test environment.
      expect(la).toContain("17:00")
    })
  })

  describe("formatGanttTickLabel", () => {
    it("renders SGT hour for UTC midnight", () => {
      const result = formatGanttTickLabel(utcMidnightMs, 86_400_000, SGT)
      expect(result).toBe("08:00")
    })
  })

  describe("startOfDayInTz / endOfDayInTz", () => {
    it("startOfDayInTz for 2026-04-30 in SGT = 2026-04-29T16:00:00Z", () => {
      // SGT midnight on 2026-04-30 = UTC 2026-04-29T16:00:00Z
      const start = startOfDayInTz(new Date("2026-04-30T00:00:00Z"), SGT)
      expect(start.toISOString()).toBe("2026-04-29T16:00:00.000Z")
    })

    it("endOfDayInTz for 2026-04-30 in SGT = 2026-04-30T15:59:59.999Z", () => {
      // End of 2026-04-30 in SGT = UTC 2026-04-30T15:59:59.999Z
      const end = endOfDayInTz(new Date("2026-04-30T00:00:00Z"), SGT)
      expect(end.toISOString()).toBe("2026-04-30T15:59:59.999Z")
    })

    it("startOfDayInTz for 2026-04-30 in UTC = 2026-04-30T00:00:00Z", () => {
      const start = startOfDayInTz(new Date("2026-04-30T12:00:00Z"), "UTC")
      expect(start.toISOString()).toBe("2026-04-30T00:00:00.000Z")
    })
  })
})
