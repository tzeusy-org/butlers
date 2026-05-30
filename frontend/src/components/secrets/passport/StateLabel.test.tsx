// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// StateLabel tests — bu-qo3sf
//
// Coverage:
//   - Each credential state renders the correct colour token
//   - Human-readable labels (no underscores)
//   - ok and never_set render --dim (neutral, no state colour)
//   - className forwarding
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { StateLabel } from "./StateLabel"

const STATE_CASES = [
  { state: "ok" as const,            label: "ok",             color: "var(--dim" },
  { state: "expired" as const,       label: "expired",        color: "var(--red)" },
  { state: "revoked" as const,       label: "revoked",        color: "var(--red)" },
  { state: "failed" as const,        label: "failed",         color: "var(--red)" },
  { state: "scope_mismatch" as const, label: "scope mismatch", color: "var(--amber)" },
  { state: "expiring_soon" as const, label: "expiring soon",  color: "var(--amber)" },
  { state: "never_set" as const,     label: "never set",      color: "var(--dim" },
] as const

describe("StateLabel: label text", () => {
  for (const { state, label } of STATE_CASES) {
    it(`state="${state}" renders "${label}"`, () => {
      const html = renderToStaticMarkup(<StateLabel state={state} />)
      expect(html).toContain(label)
    })
  }
})

describe("StateLabel: colour tokens", () => {
  for (const { state, color } of STATE_CASES) {
    it(`state="${state}" uses colour token starting with "${color}"`, () => {
      const html = renderToStaticMarkup(<StateLabel state={state} />)
      expect(html).toContain(color)
    })
  }
})

describe("StateLabel: no underscores in rendered text", () => {
  it("scope_mismatch renders without underscores", () => {
    const html = renderToStaticMarkup(<StateLabel state="scope_mismatch" />)
    expect(html).toContain("scope mismatch")
    expect(html).not.toContain("scope_mismatch")
  })

  it("expiring_soon renders without underscores", () => {
    const html = renderToStaticMarkup(<StateLabel state="expiring_soon" />)
    expect(html).toContain("expiring soon")
    expect(html).not.toContain("expiring_soon")
  })

  it("never_set renders without underscores", () => {
    const html = renderToStaticMarkup(<StateLabel state="never_set" />)
    expect(html).toContain("never set")
    expect(html).not.toContain("never_set")
  })
})

describe("StateLabel: className forwarding", () => {
  it("merges additional className", () => {
    const html = renderToStaticMarkup(<StateLabel state="ok" className="test-cls" />)
    expect(html).toContain("test-cls")
  })
})
