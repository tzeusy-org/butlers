// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// Title tests — bu-rixan
//
// Coverage:
//   - Renders children text
//   - Default element is <h2>
//   - "as" prop switches element type
//   - Sans font + font-medium (500) applied
//   - Tracking is negative (tight per spec)
//   - className forwarding
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { Title } from "./Title"

// ---------------------------------------------------------------------------
// Renders children
// ---------------------------------------------------------------------------

describe("Title: renders children", () => {
  it("renders page title text", () => {
    const html = renderToStaticMarkup(<Title>Secrets</Title>)
    expect(html).toContain("Secrets")
  })

  it("renders credential title", () => {
    const html = renderToStaticMarkup(<Title>Google OAuth</Title>)
    expect(html).toContain("Google OAuth")
  })
})

// ---------------------------------------------------------------------------
// Element type
// ---------------------------------------------------------------------------

describe("Title: element type", () => {
  it("defaults to <h2>", () => {
    const html = renderToStaticMarkup(<Title>title</Title>)
    expect(html).toContain("<h2")
    expect(html).toContain("</h2>")
  })

  it('as="h1" renders a <h1>', () => {
    const html = renderToStaticMarkup(<Title as="h1">Page Title</Title>)
    expect(html).toContain("<h1")
    expect(html).toContain("</h1>")
  })

  it('as="h3" renders a <h3>', () => {
    const html = renderToStaticMarkup(<Title as="h3">Sub-section</Title>)
    expect(html).toContain("<h3")
    expect(html).toContain("</h3>")
  })

  it('as="span" renders a <span>', () => {
    const html = renderToStaticMarkup(<Title as="span">inline</Title>)
    expect(html).toContain("<span")
    expect(html).toContain("</span>")
  })
})

// ---------------------------------------------------------------------------
// Typography
// ---------------------------------------------------------------------------

describe("Title: typography", () => {
  it("includes font-sans class", () => {
    const html = renderToStaticMarkup(<Title>title</Title>)
    expect(html).toContain("font-sans")
  })

  it("includes font-medium (weight 500) — never bold", () => {
    const html = renderToStaticMarkup(<Title>title</Title>)
    expect(html).toContain("font-medium")
    expect(html).not.toContain("font-bold")
  })

  it("applies negative tracking (tight per spec)", () => {
    const html = renderToStaticMarkup(<Title>title</Title>)
    expect(html).toContain("tracking-[-0.015em]")
  })

  it("applies primary foreground color token", () => {
    const html = renderToStaticMarkup(<Title>title</Title>)
    expect(html).toContain("--fg")
  })
})

// ---------------------------------------------------------------------------
// className forwarding
// ---------------------------------------------------------------------------

describe("Title: className forwarding", () => {
  it("forwards className to the root element", () => {
    const html = renderToStaticMarkup(
      <Title className="my-title-class">title</Title>,
    )
    expect(html).toContain("my-title-class")
  })
})
