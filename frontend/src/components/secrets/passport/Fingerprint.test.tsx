// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// Fingerprint tests — bu-qo3sf
//
// Coverage:
//   - Renders the scheme portion
//   - Renders the hash portion
//   - Scheme portion is muted; hash portion is full-foreground
//   - Handles fingerprint strings without a colon gracefully
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { Fingerprint } from "./Fingerprint"

describe("Fingerprint: content rendering", () => {
  it("renders the scheme part", () => {
    const html = renderToStaticMarkup(<Fingerprint fingerprint="sha256:7a3f8e9b" />)
    expect(html).toContain("sha256")
  })

  it("renders the hash part", () => {
    const html = renderToStaticMarkup(<Fingerprint fingerprint="sha256:7a3f8e9b" />)
    expect(html).toContain("7a3f8e9b")
  })

  it("renders the colon separator", () => {
    const html = renderToStaticMarkup(<Fingerprint fingerprint="sha256:7a3f8e9b" />)
    expect(html).toContain(":")
  })
})

describe("Fingerprint: colour split", () => {
  it("scheme part uses muted colour token", () => {
    const html = renderToStaticMarkup(<Fingerprint fingerprint="sha256:7a3f8e9b" />)
    // The muted Mono wrapper uses the --mfg token
    expect(html).toContain("var(--mfg")
  })

  it("hash part uses full foreground colour token", () => {
    const html = renderToStaticMarkup(<Fingerprint fingerprint="sha256:7a3f8e9b" />)
    // The full-fg Mono uses --fg token
    expect(html).toContain("var(--fg")
  })
})

describe("Fingerprint: edge cases", () => {
  it("renders a fingerprint with no colon as a single muted segment", () => {
    const html = renderToStaticMarkup(<Fingerprint fingerprint="plaintoken" />)
    expect(html).toContain("plaintoken")
    // No hash segment rendered when no colon
    expect(html).not.toContain("var(--fg")
  })
})

describe("Fingerprint: className forwarding", () => {
  it("merges additional className", () => {
    const html = renderToStaticMarkup(
      <Fingerprint fingerprint="sha256:7a3f8e9b" className="fp-cls" />,
    )
    expect(html).toContain("fp-cls")
  })
})
