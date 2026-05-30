// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// Mono tests — bu-rixan
//
// Coverage:
//   - Renders children text
//   - Default element is <span>
//   - "as" prop switches element type
//   - Mono font + 11px size applied
//   - tabular-nums class applied (non-negotiable per Dispatch §2c)
//   - muted=false uses --fg (default)
//   - muted=true uses --mfg
//   - className forwarding
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { Mono } from "./Mono"

// ---------------------------------------------------------------------------
// Renders children
// ---------------------------------------------------------------------------

describe("Mono: renders children", () => {
  it("renders text content", () => {
    const html = renderToStaticMarkup(<Mono>sha256:7a3f…</Mono>)
    expect(html).toContain("sha256:7a3f")
  })

  it("renders numeric content", () => {
    const html = renderToStaticMarkup(<Mono>14:21</Mono>)
    expect(html).toContain("14:21")
  })
})

// ---------------------------------------------------------------------------
// Element type
// ---------------------------------------------------------------------------

describe("Mono: element type", () => {
  it("defaults to <span>", () => {
    const html = renderToStaticMarkup(<Mono>label</Mono>)
    expect(html).toContain("<span")
    expect(html).toContain("</span>")
  })

  it('as="code" renders a <code>', () => {
    const html = renderToStaticMarkup(<Mono as="code">BUTLER_KEY</Mono>)
    expect(html).toContain("<code")
    expect(html).toContain("</code>")
  })

  it('as="div" renders a <div>', () => {
    const html = renderToStaticMarkup(<Mono as="div">label</Mono>)
    expect(html).toContain("<div")
    expect(html).toContain("</div>")
  })
})

// ---------------------------------------------------------------------------
// Typography
// ---------------------------------------------------------------------------

describe("Mono: typography", () => {
  it("includes font-mono class", () => {
    const html = renderToStaticMarkup(<Mono>label</Mono>)
    expect(html).toContain("font-mono")
  })

  it("includes tabular-nums class (Dispatch §2c non-negotiable)", () => {
    const html = renderToStaticMarkup(<Mono>42</Mono>)
    expect(html).toContain("tabular-nums")
  })
})

// ---------------------------------------------------------------------------
// Color variants
// ---------------------------------------------------------------------------

describe("Mono: color variants", () => {
  it("default (muted=false) uses --fg foreground token", () => {
    const html = renderToStaticMarkup(<Mono>label</Mono>)
    expect(html).toContain("--fg")
  })

  it("muted=true uses --mfg muted foreground token", () => {
    const html = renderToStaticMarkup(<Mono muted>label</Mono>)
    expect(html).toContain("--mfg")
  })
})

// ---------------------------------------------------------------------------
// className forwarding
// ---------------------------------------------------------------------------

describe("Mono: className forwarding", () => {
  it("forwards className to the root element", () => {
    const html = renderToStaticMarkup(<Mono className="my-mono-class">label</Mono>)
    expect(html).toContain("my-mono-class")
  })
})
