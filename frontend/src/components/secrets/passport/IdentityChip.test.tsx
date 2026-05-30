// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// IdentityChip tests — bu-qo3sf
//
// Coverage:
//   - Renders name
//   - Renders role label (lowercase)
//   - owner role uses role-admin colour token
//   - member role uses category-1 colour token
//   - unknown role uses muted-foreground colour token
//   - selected=true renders full-foreground text
//   - selected=false (default) renders muted text
//   - dot is aria-hidden
//   - className forwarding
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { IdentityChip } from "./IdentityChip"

describe("IdentityChip: content rendering", () => {
  it("renders the name", () => {
    const html = renderToStaticMarkup(<IdentityChip name="Tze" role="owner" />)
    expect(html).toContain("Tze")
  })

  it("renders the role label", () => {
    const html = renderToStaticMarkup(<IdentityChip name="Tze" role="owner" />)
    expect(html).toContain("owner")
  })

  it("renders member role label", () => {
    const html = renderToStaticMarkup(<IdentityChip name="Alex" role="member" />)
    expect(html).toContain("member")
  })
})

describe("IdentityChip: role dot colours", () => {
  it("owner role uses role-admin colour token", () => {
    const html = renderToStaticMarkup(<IdentityChip name="Tze" role="owner" />)
    expect(html).toContain("var(--role-admin")
  })

  it("member role uses category-1 colour token", () => {
    const html = renderToStaticMarkup(<IdentityChip name="Alex" role="member" />)
    expect(html).toContain("var(--category-1)")
  })

  it("unknown role uses muted-foreground colour token", () => {
    const html = renderToStaticMarkup(<IdentityChip name="?" role="unknown" />)
    expect(html).toContain("var(--muted-foreground)")
  })
})

describe("IdentityChip: selected state", () => {
  it("selected=true applies full-foreground class", () => {
    const html = renderToStaticMarkup(
      <IdentityChip name="Tze" role="owner" selected />,
    )
    expect(html).toContain("var(--fg")
  })

  it("selected=false (default) applies muted class", () => {
    const html = renderToStaticMarkup(<IdentityChip name="Tze" role="owner" />)
    expect(html).toContain("var(--mfg")
  })
})

describe("IdentityChip: dot accessibility", () => {
  it("dot is aria-hidden", () => {
    const html = renderToStaticMarkup(<IdentityChip name="Tze" role="owner" />)
    expect(html).toContain('aria-hidden="true"')
  })
})

describe("IdentityChip: className forwarding", () => {
  it("merges additional className", () => {
    const html = renderToStaticMarkup(
      <IdentityChip name="Tze" role="owner" className="ic-custom" />,
    )
    expect(html).toContain("ic-custom")
  })
})
