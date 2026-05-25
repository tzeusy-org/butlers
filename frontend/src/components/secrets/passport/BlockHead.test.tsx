// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// BlockHead tests — bu-qo3sf
//
// Coverage:
//   - Renders the label
//   - Label is uppercase (CSS class, not string transform)
//   - Optional caption is rendered when provided
//   - No caption element when caption is omitted
//   - className forwarding
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { BlockHead } from "./BlockHead"

describe("BlockHead: label rendering", () => {
  it("renders the label text", () => {
    const html = renderToStaticMarkup(<BlockHead label="Audit" />)
    expect(html).toContain("Audit")
  })

  it("applies uppercase class to the label", () => {
    const html = renderToStaticMarkup(<BlockHead label="Scopes" />)
    // Eyebrow applies uppercase CSS class
    expect(html).toContain("uppercase")
  })

  it("applies mono font to the label", () => {
    const html = renderToStaticMarkup(<BlockHead label="WhatBreaks" />)
    expect(html).toContain("font-mono")
  })
})

describe("BlockHead: caption", () => {
  it("renders the caption when provided", () => {
    const html = renderToStaticMarkup(
      <BlockHead label="Audit" caption="last 10 entries" />,
    )
    expect(html).toContain("last 10 entries")
  })

  it("does not render a caption element when omitted", () => {
    const html = renderToStaticMarkup(<BlockHead label="Audit" />)
    expect(html).not.toContain("last 10 entries")
  })

  it("renders a ReactNode caption", () => {
    const html = renderToStaticMarkup(
      <BlockHead label="Scopes" caption={<span className="cap-node">5 of 7</span>} />,
    )
    expect(html).toContain("cap-node")
    expect(html).toContain("5 of 7")
  })
})

describe("BlockHead: className forwarding", () => {
  it("merges additional className", () => {
    const html = renderToStaticMarkup(
      <BlockHead label="Probe" className="bh-custom" />,
    )
    expect(html).toContain("bh-custom")
  })
})
