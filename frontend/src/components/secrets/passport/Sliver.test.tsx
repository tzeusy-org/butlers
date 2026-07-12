// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// Sliver tests — bu-qo3sf, repointed bu-sd0l7.2
//
// bu-sd0l7.2: repointed from the orphan ./Sliver.tsx onto the shipping
// atoms.tsx export, which Spine.tsx actually renders. Real divergences:
//   - The dead copy declared its OWN local `CredentialState` union with a
//     `expiring_soon` member; the real app type (types.ts, shared with the
//     rest of the page) uses `expiring`, plus two states the dead copy
//     didn't know about at all: `warn` and `rotating`. STATE_CATALOG
//     (constants.ts) is the single source of truth for which states carry a
//     sliver — driven by a `sliver: boolean` flag per state, not a
//     colour-token lookup.
//   - The dead copy always rendered a transparent 2px element for
//     non-colour states (occupying layout space). The shipping copy renders
//     `null` — nothing at all — when the state's `sliver` flag is false.
//   - The shipping copy is `position: absolute` (an overlay pinned to the
//     row's left edge), not a static flex/self-stretch item — a real layout
//     strategy difference from the dead copy.
//
// Coverage:
//   - States with sliver:true render their colour token (expired, revoked,
//     scope_mismatch, expiring, failed)
//   - States with sliver:false render nothing at all (ok, warn, rotating,
//     never_set)
//   - aria-hidden (decorative) when rendered
//   - className forwarding when rendered
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { Sliver } from "./atoms.tsx"

describe("Sliver: states with a sliver", () => {
  it('state="expired" renders --red', () => {
    const html = renderToStaticMarkup(<Sliver state="expired" />)
    expect(html).toContain("var(--red")
  })

  it('state="revoked" renders --red', () => {
    const html = renderToStaticMarkup(<Sliver state="revoked" />)
    expect(html).toContain("var(--red")
  })

  it('state="failed" renders --red', () => {
    const html = renderToStaticMarkup(<Sliver state="failed" />)
    expect(html).toContain("var(--red")
  })

  it('state="scope_mismatch" renders --amber', () => {
    const html = renderToStaticMarkup(<Sliver state="scope_mismatch" />)
    expect(html).toContain("var(--amber")
  })

  it('state="expiring" renders --amber', () => {
    const html = renderToStaticMarkup(<Sliver state="expiring" />)
    expect(html).toContain("var(--amber")
  })
})

describe("Sliver: states with no sliver render nothing", () => {
  it('state="ok" renders nothing (no colour, no placeholder element)', () => {
    const html = renderToStaticMarkup(<Sliver state="ok" />)
    expect(html).toBe("")
  })

  it('state="warn" renders nothing', () => {
    const html = renderToStaticMarkup(<Sliver state="warn" />)
    expect(html).toBe("")
  })

  it('state="rotating" renders nothing', () => {
    const html = renderToStaticMarkup(<Sliver state="rotating" />)
    expect(html).toBe("")
  })

  it('state="never_set" renders nothing', () => {
    const html = renderToStaticMarkup(<Sliver state="never_set" />)
    expect(html).toBe("")
  })
})

describe("Sliver: accessibility", () => {
  it("is aria-hidden (decorative) when rendered", () => {
    const html = renderToStaticMarkup(<Sliver state="expired" />)
    expect(html).toContain('aria-hidden="true"')
  })
})

describe("Sliver: className forwarding", () => {
  it("merges additional className when rendered", () => {
    const html = renderToStaticMarkup(<Sliver state="expired" className="custom-cls" />)
    expect(html).toContain("custom-cls")
  })
})
