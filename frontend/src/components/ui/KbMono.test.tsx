// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// KbMono tests — bu-ec2wb
//
// Coverage:
//   - Renders children as the key label
//   - Renders as a <kbd> element
//   - Has expected Tailwind classes (font-mono, rounded, border)
//   - className forwarding
//   - Other HTML attributes forwarded (e.g., title)
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { KbMono } from "./KbMono"

// ---------------------------------------------------------------------------
// Renders children
// ---------------------------------------------------------------------------

describe("KbMono: renders children", () => {
  it("renders a plain key label", () => {
    const html = renderToStaticMarkup(<KbMono>K</KbMono>)
    expect(html).toContain("K")
  })

  it("renders a symbol key label", () => {
    const html = renderToStaticMarkup(<KbMono>⌘</KbMono>)
    expect(html).toContain("⌘")
  })

  it("renders multi-character labels", () => {
    const html = renderToStaticMarkup(<KbMono>Ctrl</KbMono>)
    expect(html).toContain("Ctrl")
  })
})

// ---------------------------------------------------------------------------
// Element type
// ---------------------------------------------------------------------------

describe("KbMono: element type", () => {
  it("renders as a <kbd> element", () => {
    const html = renderToStaticMarkup(<KbMono>Enter</KbMono>)
    expect(html).toContain("<kbd")
    expect(html).toContain("</kbd>")
  })
})

// ---------------------------------------------------------------------------
// Styling classes (per Brief §2: mono font, small padding, hairline border)
// ---------------------------------------------------------------------------

describe("KbMono: styling", () => {
  it("includes font-mono class", () => {
    const html = renderToStaticMarkup(<KbMono>K</KbMono>)
    expect(html).toContain("font-mono")
  })

  it("includes rounded class", () => {
    const html = renderToStaticMarkup(<KbMono>K</KbMono>)
    expect(html).toContain("rounded")
  })

  it("includes border class for hairline border", () => {
    const html = renderToStaticMarkup(<KbMono>K</KbMono>)
    expect(html).toContain("border")
  })

  it("uses --border-strong token for border color", () => {
    const html = renderToStaticMarkup(<KbMono>K</KbMono>)
    expect(html).toContain("border-strong")
  })
})

// ---------------------------------------------------------------------------
// className forwarding
// ---------------------------------------------------------------------------

describe("KbMono: className forwarding", () => {
  it("forwards className to the root element", () => {
    const html = renderToStaticMarkup(<KbMono className="my-kbd-class">K</KbMono>)
    expect(html).toContain("my-kbd-class")
  })
})

// ---------------------------------------------------------------------------
// Other HTML attributes
// ---------------------------------------------------------------------------

describe("KbMono: HTML attribute forwarding", () => {
  it("forwards title attribute", () => {
    const html = renderToStaticMarkup(<KbMono title="Command key">⌘</KbMono>)
    expect(html).toContain('title="Command key"')
  })
})
