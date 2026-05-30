// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// Sliver tests — bu-qo3sf
//
// Coverage:
//   - ok state renders transparent background (no colour)
//   - error states (expired, revoked, failed) render --red
//   - warning states (scope_mismatch, expiring_soon) render --amber
//   - never_set renders --dim
//   - Always renders 2px wide
//   - aria-hidden (decorative)
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { Sliver } from "./Sliver"

describe("Sliver: state-to-colour mapping", () => {
  it('state="ok" renders transparent (no colour token)', () => {
    const html = renderToStaticMarkup(<Sliver state="ok" />)
    // transparent background — must NOT contain a colour token
    expect(html).toContain("transparent")
    expect(html).not.toContain("var(--red)")
    expect(html).not.toContain("var(--amber)")
    expect(html).not.toContain("var(--green)")
  })

  it('state="expired" renders --red', () => {
    const html = renderToStaticMarkup(<Sliver state="expired" />)
    expect(html).toContain("var(--red)")
  })

  it('state="revoked" renders --red', () => {
    const html = renderToStaticMarkup(<Sliver state="revoked" />)
    expect(html).toContain("var(--red)")
  })

  it('state="failed" renders --red', () => {
    const html = renderToStaticMarkup(<Sliver state="failed" />)
    expect(html).toContain("var(--red)")
  })

  it('state="scope_mismatch" renders --amber', () => {
    const html = renderToStaticMarkup(<Sliver state="scope_mismatch" />)
    expect(html).toContain("var(--amber)")
  })

  it('state="expiring_soon" renders --amber', () => {
    const html = renderToStaticMarkup(<Sliver state="expiring_soon" />)
    expect(html).toContain("var(--amber)")
  })

  it('state="never_set" renders --dim', () => {
    const html = renderToStaticMarkup(<Sliver state="never_set" />)
    expect(html).toContain("var(--dim")
  })
})

describe("Sliver: dimensions", () => {
  it("renders 2px wide", () => {
    const html = renderToStaticMarkup(<Sliver state="ok" />)
    expect(html).toContain("width:2px")
  })
})

describe("Sliver: accessibility", () => {
  it("is aria-hidden (decorative)", () => {
    const html = renderToStaticMarkup(<Sliver state="expired" />)
    expect(html).toContain('aria-hidden="true"')
  })
})

describe("Sliver: className forwarding", () => {
  it("merges additional className", () => {
    const html = renderToStaticMarkup(<Sliver state="ok" className="custom-cls" />)
    expect(html).toContain("custom-cls")
  })
})
