// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// Voice tests — bu-rixan
//
// Coverage:
//   - Renders children text
//   - Default element is <p>
//   - "as" prop switches element type
//   - Serif font class applied
//   - variant="roman" (default) has no italic class
//   - variant="italic" applies italic style class
//   - className forwarding
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { Voice } from "./Voice"

// ---------------------------------------------------------------------------
// Renders children
// ---------------------------------------------------------------------------

describe("Voice: renders children", () => {
  it("renders prose text", () => {
    const html = renderToStaticMarkup(
      <Voice>Inventory of every credential the system holds.</Voice>,
    )
    expect(html).toContain("Inventory of every credential the system holds.")
  })

  it("renders empty-state line", () => {
    const html = renderToStaticMarkup(
      <Voice variant="italic">Nothing waiting.</Voice>,
    )
    expect(html).toContain("Nothing waiting.")
  })
})

// ---------------------------------------------------------------------------
// Element type
// ---------------------------------------------------------------------------

describe("Voice: element type", () => {
  it("defaults to <p>", () => {
    const html = renderToStaticMarkup(<Voice>prose</Voice>)
    expect(html).toContain("<p")
    expect(html).toContain("</p>")
  })

  it('as="span" renders a <span>', () => {
    const html = renderToStaticMarkup(<Voice as="span">prose</Voice>)
    expect(html).toContain("<span")
    expect(html).toContain("</span>")
  })

  it('as="div" renders a <div>', () => {
    const html = renderToStaticMarkup(<Voice as="div">prose</Voice>)
    expect(html).toContain("<div")
    expect(html).toContain("</div>")
  })
})

// ---------------------------------------------------------------------------
// Typography
// ---------------------------------------------------------------------------

describe("Voice: typography", () => {
  it("includes font-serif class", () => {
    const html = renderToStaticMarkup(<Voice>prose</Voice>)
    expect(html).toContain("font-serif")
  })

  it("applies primary foreground color token", () => {
    const html = renderToStaticMarkup(<Voice>prose</Voice>)
    expect(html).toContain("--fg")
  })
})

// ---------------------------------------------------------------------------
// Variants
// ---------------------------------------------------------------------------

describe("Voice: variants", () => {
  it('variant="roman" (default) does not apply italic class', () => {
    const html = renderToStaticMarkup(<Voice>briefing</Voice>)
    // The roman variant should not include the italic Tailwind class.
    expect(html).not.toContain('"italic"')
    expect(html).not.toContain(" italic ")
  })

  it('variant="italic" applies italic class', () => {
    const html = renderToStaticMarkup(<Voice variant="italic">Nothing waiting.</Voice>)
    expect(html).toContain("italic")
  })

  it("explicit variant=roman matches the default render", () => {
    const htmlDefault = renderToStaticMarkup(<Voice>text</Voice>)
    const htmlRoman = renderToStaticMarkup(<Voice variant="roman">text</Voice>)
    expect(htmlDefault).toBe(htmlRoman)
  })
})

// ---------------------------------------------------------------------------
// className forwarding
// ---------------------------------------------------------------------------

describe("Voice: className forwarding", () => {
  it("forwards className to the root element", () => {
    const html = renderToStaticMarkup(
      <Voice className="my-voice-class">prose</Voice>,
    )
    expect(html).toContain("my-voice-class")
  })
})
