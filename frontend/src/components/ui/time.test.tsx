// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// <Time> component tests — bu-v1tt2.2
//
// Coverage:
//   - absolute mode: default + custom timezone, each precision variant
//   - relative mode: with mocked Date.now
//   - smart mode: crossover at < 24 h vs >= 24 h
//   - title attribute present / absent
//   - datetime attribute is ISO 8601
//   - className forwarded
//
// Strategy:
//   - renderToStaticMarkup for lightweight HTML introspection (no React
//     runtime DOM + act needed; same pattern as timezone-rendering.test.tsx).
//   - vi.useFakeTimers / vi.setSystemTime for deterministic "now" in relative
//     and smart tests.
//   - Wrap in ChroniclesTimezoneProvider to supply the context timezone; use
//     the `timezone` prop to override in override-specific tests.
// ---------------------------------------------------------------------------

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { ChroniclesTimezoneProvider } from "@/components/chronicles/timezone-context"
import { Time } from "./time"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Render <Time> inside a provider so context is always satisfied. */
function render(
  props: React.ComponentProps<typeof Time>,
  providerTz = "Asia/Singapore",
): string {
  return renderToStaticMarkup(
    <ChroniclesTimezoneProvider timezone={providerTz}>
      <Time {...props} />
    </ChroniclesTimezoneProvider>,
  )
}

/** Parse the rendered HTML and return the <time> element attributes + text. */
function parseTime(html: string) {
  const div = document.createElement("div")
  div.innerHTML = html
  const el = div.querySelector("time")
  if (!el) throw new Error("No <time> element found in: " + html)
  return {
    datetime: el.getAttribute("datetime"),
    title: el.getAttribute("title"),
    text: el.textContent ?? "",
    className: el.getAttribute("class"),
  }
}

// ---------------------------------------------------------------------------
// Fixed reference points
// ---------------------------------------------------------------------------

// 2026-05-03T00:00:00Z  — UTC midnight
const FIXED_ISO = "2026-05-03T00:00:00Z"
// Same instant expressed as a Date
const FIXED_DATE = new Date(FIXED_ISO)

// "now" for fake-timer tests: 4 minutes after FIXED_ISO
const NOW_4MIN_LATER = new Date("2026-05-03T00:04:00Z")
// "now" for smart-mode crossover tests: 25 h after FIXED_ISO (absolute branch)
const NOW_25H_LATER = new Date("2026-05-04T01:00:00Z")
// "now" for smart-mode crossover tests: 23 h after FIXED_ISO (relative branch)
const NOW_23H_LATER = new Date("2026-05-03T23:00:00Z")

// ---------------------------------------------------------------------------
// 1. absolute mode
// ---------------------------------------------------------------------------

describe("absolute mode", () => {
  it("renders full date + time + timezone in Asia/Singapore", () => {
    // 2026-05-03T00:00:00Z = 08:00 SGT (UTC+8)
    const { text } = parseTime(render({ value: FIXED_ISO, mode: "absolute" }))
    expect(text).toContain("May 3, 2026")
    expect(text).toContain("8:00 AM")
    // tz abbreviation: "SGT" on full-ICU, "GMT+8" on minimal-ICU builds
    expect(text).toMatch(/SGT|GMT\+8/)
  })

  it("renders in America/New_York when timezone prop overrides context", () => {
    // 2026-05-03T00:00:00Z = 2026-05-02T20:00:00-04:00 (EDT)
    const { text } = parseTime(
      render({ value: FIXED_ISO, mode: "absolute", timezone: "America/New_York" }),
    )
    expect(text).toContain("May 2, 2026")
    expect(text).toContain("8:00 PM")
    // tz abbreviation: "EDT" on full-ICU, "GMT-4" on minimal-ICU builds
    expect(text).toMatch(/EDT|GMT-4/)
  })

  it("uses the context timezone when no timezone prop is given", () => {
    const { text } = parseTime(
      render({ value: FIXED_ISO, mode: "absolute" }, "America/Los_Angeles"),
    )
    // 2026-05-03T00:00:00Z = 2026-05-02T17:00:00-07:00 (PDT)
    expect(text).toContain("May 2, 2026")
    expect(text).toContain("5:00 PM")
    expect(text).toMatch(/PDT|GMT-7/)
  })
})

// ---------------------------------------------------------------------------
// 2. precision variants (absolute mode)
// ---------------------------------------------------------------------------

describe("precision variants (mode=absolute, timezone=Asia/Singapore)", () => {
  const SGT = "Asia/Singapore"

  it("precision=minute (default) includes HH:MM", () => {
    const { text } = parseTime(
      render({ value: FIXED_ISO, mode: "absolute", precision: "minute" }, SGT),
    )
    // "8:00 AM" or "08:00"
    expect(text).toMatch(/8:00/)
    // Must NOT include seconds
    expect(text).not.toMatch(/8:00:\d{2}/)
  })

  it("precision=second includes HH:MM:SS", () => {
    const { text } = parseTime(
      render({ value: FIXED_ISO, mode: "absolute", precision: "second" }, SGT),
    )
    expect(text).toMatch(/8:00:00/)
  })

  it("precision=hour includes hour only (no minutes)", () => {
    const { text } = parseTime(
      render({ value: FIXED_ISO, mode: "absolute", precision: "hour" }, SGT),
    )
    // "8 AM" format — must not have ":00"
    expect(text).toMatch(/8 AM/)
    expect(text).not.toMatch(/8:00/)
  })

  it("precision=day includes date but no time", () => {
    const { text } = parseTime(
      render({ value: FIXED_ISO, mode: "absolute", precision: "day" }, SGT),
    )
    // SGT is UTC+8 so 2026-05-03T00:00:00Z = May 3, 2026 in SGT
    expect(text).toContain("May 3, 2026")
    // Must not contain a colon (no time component)
    expect(text).not.toContain(":")
  })
})

// ---------------------------------------------------------------------------
// 3. relative mode (mocked clock)
// ---------------------------------------------------------------------------

describe("relative mode", () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("renders 'X minutes ago' when 4 minutes have passed", () => {
    vi.setSystemTime(NOW_4MIN_LATER)
    const { text } = parseTime(render({ value: FIXED_ISO, mode: "relative" }))
    expect(text).toMatch(/\d+ minutes? ago/)
  })

  it("renders relative text ('in X ...') for a near-future date in relative mode", () => {
    vi.setSystemTime(FIXED_DATE)
    // 1 hour in the future
    const futureIso = new Date(FIXED_DATE.getTime() + 60 * 60 * 1_000).toISOString()
    const { text } = parseTime(render({ value: futureIso, mode: "relative" }))
    // date-fns uses "in about 1 hour" or similar
    expect(text).toMatch(/in/)
  })
})

// ---------------------------------------------------------------------------
// 4. smart mode crossover
// ---------------------------------------------------------------------------

describe("smart mode (< 24 h → relative, >= 24 h → absolute)", () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("renders relative when < 24 h old", () => {
    vi.setSystemTime(NOW_23H_LATER) // 23 h after FIXED_ISO
    const { text } = parseTime(render({ value: FIXED_ISO, mode: "smart" }))
    // relative text contains "ago" or "hours"
    expect(text).toMatch(/ago|hour/)
    // Must not contain a year (absolute would include the year)
    expect(text).not.toContain("2026")
  })

  it("renders absolute when >= 24 h old", () => {
    vi.setSystemTime(NOW_25H_LATER) // 25 h after FIXED_ISO
    const { text } = parseTime(render({ value: FIXED_ISO, mode: "smart" }))
    // absolute text contains the year
    expect(text).toContain("2026")
    // Must not end with "ago"
    expect(text).not.toMatch(/ago$/)
  })

  it("defaults to smart mode when mode prop is omitted", () => {
    // Just after the threshold — should render absolute
    vi.setSystemTime(NOW_25H_LATER)
    const { text } = parseTime(render({ value: FIXED_ISO }))
    expect(text).toContain("2026")
  })
})

// ---------------------------------------------------------------------------
// 4b. smart mode — future date threshold symmetry
// ---------------------------------------------------------------------------

describe("smart mode — future dates obey the same threshold", () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("renders relative for a future date within 24 h", () => {
    vi.setSystemTime(FIXED_DATE)
    // 1 hour in the future — within threshold → should be relative
    const futureIso = new Date(FIXED_DATE.getTime() + 60 * 60 * 1_000).toISOString()
    const { text } = parseTime(render({ value: futureIso, mode: "smart" }))
    expect(text).toMatch(/in/)
    expect(text).not.toContain("2026")
  })

  it("renders absolute for a future date beyond 24 h", () => {
    vi.setSystemTime(FIXED_DATE)
    // 25 hours in the future — beyond threshold → should be absolute
    const futureIso = new Date(FIXED_DATE.getTime() + 25 * 60 * 60 * 1_000).toISOString()
    const { text } = parseTime(render({ value: futureIso, mode: "smart" }))
    expect(text).toContain("2026")
    expect(text).not.toMatch(/^in\s/)
  })
})

// ---------------------------------------------------------------------------
// 5. datetime attribute is ISO 8601
// ---------------------------------------------------------------------------

describe("datetime attribute", () => {
  it("is always a valid ISO 8601 UTC string regardless of mode", () => {
    for (const mode of ["absolute", "relative", "smart"] as const) {
      const { datetime } = parseTime(render({ value: FIXED_ISO, mode }))
      expect(datetime).toBe(FIXED_DATE.toISOString())
    }
  })

  it("accepts a Date object as value and still sets correct datetime", () => {
    const { datetime } = parseTime(render({ value: FIXED_DATE, mode: "absolute" }))
    expect(datetime).toBe(FIXED_DATE.toISOString())
  })
})

// ---------------------------------------------------------------------------
// 6. title attribute present / absent
// ---------------------------------------------------------------------------

describe("title attribute", () => {
  it("is present by default (title=true)", () => {
    const { title } = parseTime(render({ value: FIXED_ISO, mode: "absolute" }))
    expect(title).toBe(FIXED_DATE.toISOString())
  })

  it("is absent when showTitle=false", () => {
    const { title } = parseTime(render({ value: FIXED_ISO, mode: "absolute", showTitle: false }))
    expect(title).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// 7. className forwarding
// ---------------------------------------------------------------------------

describe("className forwarding", () => {
  it("applies className to the <time> element", () => {
    const { className } = parseTime(
      render({ value: FIXED_ISO, mode: "absolute", className: "text-muted-foreground" }),
    )
    expect(className).toBe("text-muted-foreground")
  })

  it("renders without className when not provided", () => {
    const { className } = parseTime(render({ value: FIXED_ISO, mode: "absolute" }))
    expect(className).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// 8. invalid date guard
// ---------------------------------------------------------------------------

describe("invalid date guard", () => {
  it("renders a safe placeholder instead of throwing for an invalid date string", () => {
    // "not-a-date" parses to an Invalid Date; toISOString() would throw RangeError
    // without the guard. Verify the component renders the raw value instead.
    const html = render({ value: "not-a-date" })
    const div = document.createElement("div")
    div.innerHTML = html
    const el = div.querySelector("time")
    expect(el).not.toBeNull()
    // No datetime attribute on invalid dates
    expect(el!.getAttribute("datetime")).toBeNull()
    // Raw value surfaced as text
    expect(el!.textContent).toBe("not-a-date")
  })
})
