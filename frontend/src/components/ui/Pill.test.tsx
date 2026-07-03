// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// Pill tests — bu-ec2wb, updated bu-86c4c.16
//
// Coverage:
//   - Renders children label
//   - Renders as a <button> element
//   - selected=false (default): aria-pressed=false, unselected styling class
//   - selected=true: aria-pressed=true, selected styling
//   - count prop is rendered when provided
//   - ARIA: toggle-button semantics (aria-pressed, not role="switch") with
//     the count folded into the button's own accessible name
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

  it("does not render an aria-label when count is not provided", () => {
    const html = renderToStaticMarkup(<Pill>stale</Pill>)
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
// ARIA — toggle-button semantics (bu-86c4c.16)
// ---------------------------------------------------------------------------

describe("Pill: ARIA role and state", () => {
  it('does NOT use role="switch" (a filter chip is not an independent on/off setting)', () => {
    const html = renderToStaticMarkup(<Pill>label</Pill>)
    expect(html).not.toContain('role="switch"')
    expect(html).not.toContain("aria-checked")
  })

  it("aria-pressed=false when not selected (default)", () => {
    const html = renderToStaticMarkup(<Pill>label</Pill>)
    expect(html).toContain('aria-pressed="false"')
  })

  it("aria-pressed=true when selected=true", () => {
    const html = renderToStaticMarkup(<Pill selected>label</Pill>)
    expect(html).toContain('aria-pressed="true"')
  })
})

// ---------------------------------------------------------------------------
// Count badge — folded into the button's accessible name (bu-86c4c.16)
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

  it("folds the count into the button's own aria-label (plural)", () => {
    const html = renderToStaticMarkup(<Pill count={7}>label</Pill>)
    expect(html).toContain('aria-label="label, 7 items"')
  })

  it("count=1 uses singular wording in the folded aria-label", () => {
    const html = renderToStaticMarkup(<Pill count={1}>label</Pill>)
    expect(html).toContain('aria-label="label, 1 item"')
    expect(html).not.toContain("1 items")
  })

  it("count=0 uses plural wording in the folded aria-label", () => {
    const html = renderToStaticMarkup(<Pill count={0}>stale</Pill>)
    expect(html).toContain('aria-label="stale, 0 items"')
  })

  it("the inner count span is aria-hidden once folded into the button label", () => {
    const html = renderToStaticMarkup(<Pill count={3}>duplicate</Pill>)
    expect(html).toMatch(/<span aria-hidden="true"[^>]*>3<\/span>/)
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
