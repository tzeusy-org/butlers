// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// <Time> — app-level timezone provider tests (bu-ldj6y)
//
// Verifies that <Time> resolves to the owner timezone when wrapped only by
// AppTimezoneProvider (not ChroniclesTimezoneProvider). This proves the
// provider works for non-chronicles pages — e.g. SettingsPage, ButlersPage.
//
// AC: When AppTimezoneProvider supplies "Europe/London" (owner tz), <Time>
//     must render in London time, NOT in the DEFAULT_TZ (Asia/Singapore) fallback.
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { AppTimezoneProvider } from "@/components/ui/timezone-context"
import { Time } from "@/components/ui/time"

// ---------------------------------------------------------------------------
// Fixed reference point
// ---------------------------------------------------------------------------

// 2026-05-03T00:00:00Z
//   Asia/Singapore (UTC+8):  08:00 SGT  — DEFAULT_TZ fallback
//   Europe/London (UTC+1):   01:00 BST  — owner tz injected by AppTimezoneProvider
//   America/New_York (UTC-4): 20:00 EDT (2026-05-02)
const FIXED_ISO = "2026-05-03T00:00:00Z"

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("<Time> with AppTimezoneProvider (non-chronicles context, bu-ldj6y)", () => {
  it("resolves to owner tz (Europe/London), not DEFAULT_TZ (Asia/Singapore)", () => {
    const html = renderToStaticMarkup(
      <AppTimezoneProvider timezone="Europe/London">
        <Time value={FIXED_ISO} mode="absolute" precision="minute" />
      </AppTimezoneProvider>,
    )

    const div = document.createElement("div")
    div.innerHTML = html
    const el = div.querySelector("time")
    expect(el).not.toBeNull()
    const text = el!.textContent ?? ""

    // Europe/London BST (UTC+1): 01:00 on 2026-05-03
    expect(text).toContain("1:00 AM")
    // Must NOT render SGT (08:00) which is the DEFAULT_TZ fallback
    expect(text).not.toContain("8:00 AM")
  })

  it("resolves to America/New_York when owner tz is America/New_York", () => {
    const html = renderToStaticMarkup(
      <AppTimezoneProvider timezone="America/New_York">
        <Time value={FIXED_ISO} mode="absolute" precision="minute" />
      </AppTimezoneProvider>,
    )

    const div = document.createElement("div")
    div.innerHTML = html
    const el = div.querySelector("time")
    expect(el).not.toBeNull()
    const text = el!.textContent ?? ""

    // America/New_York EDT (UTC-4): 20:00 on 2026-05-02
    expect(text).toContain("8:00 PM")
    // Must NOT render SGT (08:00 AM) — the DEFAULT_TZ fallback
    expect(text).not.toContain("8:00 AM")
  })

  it("falls back to DEFAULT_TZ (Asia/Singapore) when no provider is present", () => {
    // Without a provider, context defaults to DEFAULT_TZ = "Asia/Singapore"
    const html = renderToStaticMarkup(
      <Time value={FIXED_ISO} mode="absolute" precision="minute" />,
    )

    const div = document.createElement("div")
    div.innerHTML = html
    const el = div.querySelector("time")
    expect(el).not.toBeNull()
    const text = el!.textContent ?? ""

    // DEFAULT_TZ fallback: 08:00 SGT
    expect(text).toContain("8:00 AM")
    expect(text).toMatch(/SGT|GMT\+8/)
  })
})
