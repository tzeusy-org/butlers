// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// ProviderMark tests — bu-qo3sf
//
// Coverage:
//   - Renders the first character of provider slug uppercased
//   - No background colour (transparent)
//   - No butler category hue (neutral — no category-N token)
//   - 22px square dimensions
//   - aria-label contains the provider slug
//   - className forwarding
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { ProviderMark } from "./ProviderMark"

describe("ProviderMark: initial rendering", () => {
  it("renders the uppercased first character of the provider slug", () => {
    const html = renderToStaticMarkup(<ProviderMark provider="google" />)
    expect(html).toContain("G")
  })

  it("renders uppercase initial for multi-char providers", () => {
    const html = renderToStaticMarkup(<ProviderMark provider="telegram" />)
    expect(html).toContain("T")
  })

  it("handles single-char provider slugs", () => {
    const html = renderToStaticMarkup(<ProviderMark provider="x" />)
    expect(html).toContain("X")
  })
})

describe("ProviderMark: no colour", () => {
  it("does not use any category hue token", () => {
    const html = renderToStaticMarkup(<ProviderMark provider="google" />)
    expect(html).not.toContain("var(--category-")
  })

  it("background is transparent (no fill)", () => {
    const html = renderToStaticMarkup(<ProviderMark provider="google" />)
    expect(html).toContain("transparent")
  })
})

describe("ProviderMark: dimensions", () => {
  it("renders 22px wide and 22px tall", () => {
    const html = renderToStaticMarkup(<ProviderMark provider="google" />)
    expect(html).toContain("width:22px")
    expect(html).toContain("height:22px")
  })
})

describe("ProviderMark: accessibility", () => {
  it("aria-label contains the provider slug", () => {
    const html = renderToStaticMarkup(<ProviderMark provider="spotify" />)
    expect(html).toContain('aria-label="spotify"')
  })
})

describe("ProviderMark: className forwarding", () => {
  it("merges additional className", () => {
    const html = renderToStaticMarkup(
      <ProviderMark provider="google" className="pm-custom" />,
    )
    expect(html).toContain("pm-custom")
  })
})
