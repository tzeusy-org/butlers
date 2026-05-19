// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// Pill tests — bu-ec2wb
//
// Coverage:
//   - Renders children label
//   - Renders as a <button> element
//   - selected=false (default): aria-checked=false, unselected styling class
//   - selected=true: aria-checked=true, selected styling
//   - count prop is rendered when provided
//   - ARIA: role="switch" + aria-checked
//   - className forwarding
//   - disabled state
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { Pill } from "./Pill"

// ---------------------------------------------------------------------------
// Renders children
// ---------------------------------------------------------------------------

describe("Pill: renders label", () => {
  it("renders children text", () => {
    const html = renderToStaticMarkup(<Pill>unidentified</Pill>)
    expect(html).toContain("unidentified")
  })

  it("renders children with count", () => {
    const html = renderToStaticMarkup(<Pill count={3}>duplicate</Pill>)
    expect(html).toContain("duplicate")
    expect(html).toContain("3")
  })

  it("does not render count element when count is not provided", () => {
    const html = renderToStaticMarkup(<Pill>stale</Pill>)
    // No aria-label="N items" when count is not supplied
    expect(html).not.toContain("aria-label=")
  })
})

// ---------------------------------------------------------------------------
// Element type
// ---------------------------------------------------------------------------

describe("Pill: element type", () => {
  it("renders as a <button> element", () => {
    const html = renderToStaticMarkup(<Pill>label</Pill>)
    expect(html).toContain("<button")
    expect(html).toContain("</button>")
  })

  it("has type=button to prevent form submission", () => {
    const html = renderToStaticMarkup(<Pill>label</Pill>)
    expect(html).toContain('type="button"')
  })
})

// ---------------------------------------------------------------------------
// ARIA — toggle switch semantics
// ---------------------------------------------------------------------------

describe("Pill: ARIA role and state", () => {
  it('has role="switch"', () => {
    const html = renderToStaticMarkup(<Pill>label</Pill>)
    expect(html).toContain('role="switch"')
  })

  it("aria-checked=false when not selected (default)", () => {
    const html = renderToStaticMarkup(<Pill>label</Pill>)
    expect(html).toContain('aria-checked="false"')
  })

  it("aria-checked=true when selected=true", () => {
    const html = renderToStaticMarkup(<Pill selected>label</Pill>)
    expect(html).toContain('aria-checked="true"')
  })
})

// ---------------------------------------------------------------------------
// Count badge
// ---------------------------------------------------------------------------

describe("Pill: count prop", () => {
  it("renders count=0", () => {
    const html = renderToStaticMarkup(<Pill count={0}>stale</Pill>)
    expect(html).toContain("0")
  })

  it("renders large count", () => {
    const html = renderToStaticMarkup(<Pill count={42}>duplicate</Pill>)
    expect(html).toContain("42")
  })

  it("count span has aria-label with count value", () => {
    const html = renderToStaticMarkup(<Pill count={7}>label</Pill>)
    expect(html).toContain("7 items")
  })
})

// ---------------------------------------------------------------------------
// className forwarding
// ---------------------------------------------------------------------------

describe("Pill: className forwarding", () => {
  it("forwards className to the root button", () => {
    const html = renderToStaticMarkup(<Pill className="my-pill-class">label</Pill>)
    expect(html).toContain("my-pill-class")
  })
})

// ---------------------------------------------------------------------------
// Disabled state
// ---------------------------------------------------------------------------

describe("Pill: disabled", () => {
  it("applies disabled attribute when disabled prop is set", () => {
    const html = renderToStaticMarkup(<Pill disabled>label</Pill>)
    expect(html).toContain("disabled")
  })
})

// ---------------------------------------------------------------------------
// Mono font and pill shape
// ---------------------------------------------------------------------------

describe("Pill: typography and shape", () => {
  it("includes font-mono class", () => {
    const html = renderToStaticMarkup(<Pill>label</Pill>)
    expect(html).toContain("font-mono")
  })

  it("includes rounded-full class for pill shape", () => {
    const html = renderToStaticMarkup(<Pill>label</Pill>)
    expect(html).toContain("rounded-full")
  })

  it("includes border class", () => {
    const html = renderToStaticMarkup(<Pill>label</Pill>)
    expect(html).toContain("border")
  })
})
