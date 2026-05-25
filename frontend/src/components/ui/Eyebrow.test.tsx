// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// Eyebrow tests — bu-rixan
//
// Coverage:
//   - Renders children text
//   - Default element is <span>
//   - "as" prop switches element type
//   - Mono font class applied
//   - Uppercase + tracking-[0.14em] applied
//   - Muted color token applied
//   - className forwarding
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { Eyebrow } from "./Eyebrow"

// ---------------------------------------------------------------------------
// Renders children
// ---------------------------------------------------------------------------

describe("Eyebrow: renders children", () => {
  it("renders the label text", () => {
    const html = renderToStaticMarkup(<Eyebrow>Overview</Eyebrow>)
    expect(html).toContain("Overview")
  })

  it("renders a composite label with separator", () => {
    const html = renderToStaticMarkup(
      <Eyebrow>Overview · Wed, 7 May 2026 · 14:21</Eyebrow>,
    )
    expect(html).toContain("Overview")
    expect(html).toContain("14:21")
  })
})

// ---------------------------------------------------------------------------
// Element type
// ---------------------------------------------------------------------------

describe("Eyebrow: element type", () => {
  it("defaults to <span>", () => {
    const html = renderToStaticMarkup(<Eyebrow>label</Eyebrow>)
    expect(html).toContain("<span")
    expect(html).toContain("</span>")
  })

  it('as="div" renders a <div>', () => {
    const html = renderToStaticMarkup(<Eyebrow as="div">label</Eyebrow>)
    expect(html).toContain("<div")
    expect(html).toContain("</div>")
  })

  it('as="p" renders a <p>', () => {
    const html = renderToStaticMarkup(<Eyebrow as="p">label</Eyebrow>)
    expect(html).toContain("<p")
    expect(html).toContain("</p>")
  })
})

// ---------------------------------------------------------------------------
// Typography
// ---------------------------------------------------------------------------

describe("Eyebrow: typography", () => {
  it("includes font-mono class", () => {
    const html = renderToStaticMarkup(<Eyebrow>label</Eyebrow>)
    expect(html).toContain("font-mono")
  })

  it("includes uppercase class", () => {
    const html = renderToStaticMarkup(<Eyebrow>label</Eyebrow>)
    expect(html).toContain("uppercase")
  })

  it("applies design-language tracking (0.14em)", () => {
    const html = renderToStaticMarkup(<Eyebrow>label</Eyebrow>)
    expect(html).toContain("tracking-[0.14em]")
  })

  it("applies muted foreground color token", () => {
    const html = renderToStaticMarkup(<Eyebrow>label</Eyebrow>)
    expect(html).toContain("--mfg")
  })

  it("applies leading-none for compact line height", () => {
    const html = renderToStaticMarkup(<Eyebrow>label</Eyebrow>)
    expect(html).toContain("leading-none")
  })
})

// ---------------------------------------------------------------------------
// className forwarding
// ---------------------------------------------------------------------------

describe("Eyebrow: className forwarding", () => {
  it("forwards className to the root element", () => {
    const html = renderToStaticMarkup(
      <Eyebrow className="my-eyebrow-class">label</Eyebrow>,
    )
    expect(html).toContain("my-eyebrow-class")
  })
})
