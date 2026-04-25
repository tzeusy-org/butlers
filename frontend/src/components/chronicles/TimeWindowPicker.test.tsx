// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// Tests for TimeWindowPicker + useTimeWindow — bu-ig72b.20
//
// Covers:
//   - Default window is today (start-of-day → end-of-day)
//   - URL ?from=&to= params set the window
//   - pollingDisabled flag flips correctly at the 24 h boundary
//   - Component renders preset buttons and date inputs
// ---------------------------------------------------------------------------

import { describe, expect, it, afterEach } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"
import { MemoryRouter, Route, Routes } from "react-router"
import {
  endOfDay,
  format,
  startOfDay,
  subDays,
  subHours,
} from "date-fns"

import { isPollingDisabled, useTimeWindow } from "@/hooks/use-time-window"
import { TimeWindowPicker } from "./TimeWindowPicker"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const DATE_FMT = "yyyy-MM-dd"
const fmt = (d: Date) => format(d, DATE_FMT)

// ---------------------------------------------------------------------------
// isPollingDisabled unit tests (pure function — no DOM needed)
// ---------------------------------------------------------------------------

describe("isPollingDisabled", () => {
  it("returns false when `to` is now (within 24 h)", () => {
    expect(isPollingDisabled(new Date())).toBe(false)
  })

  it("returns false when `to` is 23 h ago", () => {
    expect(isPollingDisabled(subHours(new Date(), 23))).toBe(false)
  })

  it("returns true when `to` is exactly 24 h ago", () => {
    expect(isPollingDisabled(subHours(new Date(), 24))).toBe(true)
  })

  it("returns true when `to` is 7 days ago", () => {
    expect(isPollingDisabled(subDays(new Date(), 7))).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// Render helper
// ---------------------------------------------------------------------------

function renderAtUrl(url: string, ui: React.ReactNode): string {
  return renderToStaticMarkup(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="*" element={ui} />
      </Routes>
    </MemoryRouter>,
  )
}

// ---------------------------------------------------------------------------
// TimeWindowPicker render tests
// ---------------------------------------------------------------------------

describe("TimeWindowPicker render", () => {
  it("renders Today preset button", () => {
    function Wrapper() {
      const tw = useTimeWindow()
      return <TimeWindowPicker window={tw} />
    }
    const html = renderAtUrl("/chronicles", <Wrapper />)
    expect(html).toContain("Today")
  })

  it("renders Last 7 days preset button", () => {
    function Wrapper() {
      const tw = useTimeWindow()
      return <TimeWindowPicker window={tw} />
    }
    const html = renderAtUrl("/chronicles", <Wrapper />)
    expect(html).toContain("Last 7 days")
  })

  it("renders date inputs for custom range", () => {
    function Wrapper() {
      const tw = useTimeWindow()
      return <TimeWindowPicker window={tw} />
    }
    const html = renderAtUrl("/chronicles", <Wrapper />)
    expect(html).toContain('type="date"')
  })
})

// ---------------------------------------------------------------------------
// useTimeWindow hook tests (DOM + MemoryRouter)
// ---------------------------------------------------------------------------

// Harness renders hook state as data attributes so tests can read DOM values.
// Using static markup avoids all hook-assignment lint issues.
function TimeWindowDisplay() {
  const tw = useTimeWindow()
  return (
    <div
      data-testid="tw"
      data-preset={tw.preset}
      data-from={fmt(tw.from)}
      data-to={fmt(tw.to)}
      data-polling-disabled={String(tw.pollingDisabled)}
    />
  )
}

function renderHookViaDOM(url: string): Element {
  const html = renderToStaticMarkup(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="*" element={<TimeWindowDisplay />} />
      </Routes>
    </MemoryRouter>,
  )
  const div = document.createElement("div")
  div.innerHTML = html
  return div.querySelector("[data-testid='tw']")!
}

afterEach(() => {
  document.body.innerHTML = ""
})

describe("useTimeWindow — default (no URL params)", () => {
  it("defaults to today preset", () => {
    const el = renderHookViaDOM("/chronicles")
    expect(el.getAttribute("data-preset")).toBe("today")
  })

  it("default `from` is start-of-today", () => {
    const el = renderHookViaDOM("/chronicles")
    expect(el.getAttribute("data-from")).toBe(fmt(startOfDay(new Date())))
  })

  it("default `to` is end-of-today (same calendar day)", () => {
    const el = renderHookViaDOM("/chronicles")
    expect(el.getAttribute("data-to")).toBe(fmt(endOfDay(new Date())))
  })

  it("pollingDisabled is false for today window", () => {
    const el = renderHookViaDOM("/chronicles")
    expect(el.getAttribute("data-polling-disabled")).toBe("false")
  })
})

describe("useTimeWindow — URL params drive the window", () => {
  it("parses ?from=&to= pointing to last week as custom preset", () => {
    const from = subDays(new Date(), 10)
    const to = subDays(new Date(), 5)
    const el = renderHookViaDOM(`/chronicles?from=${fmt(from)}&to=${fmt(to)}`)
    expect(el.getAttribute("data-preset")).toBe("custom")
  })

  it("pollingDisabled is true when to is 7 days ago", () => {
    const from = subDays(new Date(), 10)
    const to = subDays(new Date(), 7)
    const el = renderHookViaDOM(`/chronicles?from=${fmt(from)}&to=${fmt(to)}`)
    expect(el.getAttribute("data-polling-disabled")).toBe("true")
  })

  it("pollingDisabled is false when to is today", () => {
    const from = subDays(new Date(), 6)
    const to = new Date()
    const el = renderHookViaDOM(`/chronicles?from=${fmt(from)}&to=${fmt(to)}`)
    expect(el.getAttribute("data-polling-disabled")).toBe("false")
  })

  it("falls back to today on invalid URL params", () => {
    const el = renderHookViaDOM("/chronicles?from=not-a-date&to=also-bad")
    expect(el.getAttribute("data-preset")).toBe("today")
  })

  it("recognises the week preset from URL params", () => {
    const from = startOfDay(subDays(new Date(), 6))
    const to = endOfDay(new Date())
    const el = renderHookViaDOM(`/chronicles?from=${fmt(from)}&to=${fmt(to)}`)
    expect(el.getAttribute("data-preset")).toBe("week")
  })

  it("recognises the today preset from URL params", () => {
    const from = startOfDay(new Date())
    const to = endOfDay(new Date())
    const el = renderHookViaDOM(`/chronicles?from=${fmt(from)}&to=${fmt(to)}`)
    expect(el.getAttribute("data-preset")).toBe("today")
  })
})
