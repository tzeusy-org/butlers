// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// Display tests — bu-rixan
//
// Coverage:
//   - Renders children text
//   - Default element is <h1>
//   - "as" prop switches element type
//   - Sans font + font-medium (500) applied
//   - Tracking is negative (tight per spec)
//   - className forwarding
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { Display } from "./Display"

// ---------------------------------------------------------------------------
// Renders children
// ---------------------------------------------------------------------------

describe("Display: renders children", () => {
  it("renders headline text", () => {
    const html = renderToStaticMarkup(<Display>Secrets</Display>)
    expect(html).toContain("Secrets")
  })

  it("renders numeric display content", () => {
    const html = renderToStaticMarkup(<Display as="span">42</Display>)
    expect(html).toContain("42")
  })
})

// ---------------------------------------------------------------------------
// Element type
// ---------------------------------------------------------------------------

describe("Display: element type", () => {
  it("defaults to <h1>", () => {
    const html = renderToStaticMarkup(<Display>headline</Display>)
    expect(html).toContain("<h1")
    expect(html).toContain("</h1>")
  })

  it('as="h2" renders a <h2>', () => {
    const html = renderToStaticMarkup(<Display as="h2">headline</Display>)
    expect(html).toContain("<h2")
    expect(html).toContain("</h2>")
  })

  it('as="span" renders a <span>', () => {
    const html = renderToStaticMarkup(<Display as="span">42</Display>)
    expect(html).toContain("<span")
    expect(html).toContain("</span>")
  })

  it('as="div" renders a <div>', () => {
    const html = renderToStaticMarkup(<Display as="div">headline</Display>)
    expect(html).toContain("<div")
    expect(html).toContain("</div>")
  })
})

// ---------------------------------------------------------------------------
// Typography
// ---------------------------------------------------------------------------

describe("Display: typography", () => {
  it("includes font-sans class", () => {
    const html = renderToStaticMarkup(<Display>headline</Display>)
    expect(html).toContain("font-sans")
  })

  it("includes font-medium (weight 500) — never bold", () => {
    const html = renderToStaticMarkup(<Display>headline</Display>)
    expect(html).toContain("font-medium")
    // Must NOT be bold — Dispatch spec: "Display weight is 500, never 700."
    expect(html).not.toContain("font-bold")
  })

  it("applies negative tracking (tight per spec)", () => {
    const html = renderToStaticMarkup(<Display>headline</Display>)
    expect(html).toContain("tracking-[-0.025em]")
  })

  it("applies primary foreground color token", () => {
    const html = renderToStaticMarkup(<Display>headline</Display>)
    expect(html).toContain("--fg")
  })
})

// ---------------------------------------------------------------------------
// className forwarding
// ---------------------------------------------------------------------------

describe("Display: className forwarding", () => {
  it("forwards className to the root element", () => {
    const html = renderToStaticMarkup(
      <Display className="my-display-class">headline</Display>,
    )
    expect(html).toContain("my-display-class")
  })
})
