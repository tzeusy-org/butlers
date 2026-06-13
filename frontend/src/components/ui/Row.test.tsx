// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// Row tests — bu-ovq7t
//
// Coverage:
//   - renders children content
//   - mark / meta slots are optional and collapse the grid template
//   - density presets map to vertical padding
//   - interactive flag applies hover tint + cursor
//   - divider toggles the bottom hairline
//   - className + data attributes forward to the root
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { Row } from "./Row"

describe("Row: content", () => {
  it("renders the 1fr children content", () => {
    const html = renderToStaticMarkup(<Row>Alice Johnson</Row>)
    expect(html).toContain("Alice Johnson")
  })
})

describe("Row: slot columns", () => {
  it("two-column (1fr / meta) when only meta is present", () => {
    const html = renderToStaticMarkup(<Row meta={<span>meta</span>}>body</Row>)
    expect(html).toContain("grid-template-columns:1fr auto")
    expect(html).toContain("meta")
  })

  it("two-column (mark / 1fr) when only mark is present", () => {
    const html = renderToStaticMarkup(<Row mark={<span>mk</span>}>body</Row>)
    expect(html).toContain("grid-template-columns:auto 1fr")
  })

  it("three-column when both mark and meta are present", () => {
    const html = renderToStaticMarkup(
      <Row mark={<span>mk</span>} meta={<span>mt</span>}>
        body
      </Row>,
    )
    expect(html).toContain("grid-template-columns:auto 1fr auto")
  })

  it("single-column (1fr) when neither slot is present", () => {
    const html = renderToStaticMarkup(<Row>body</Row>)
    expect(html).toContain("grid-template-columns:1fr")
  })
})

describe("Row: density", () => {
  it("scan density (default) uses 10px vertical padding", () => {
    const html = renderToStaticMarkup(<Row>body</Row>)
    expect(html).toContain("py-2.5")
  })

  it("read density uses 18px vertical padding", () => {
    const html = renderToStaticMarkup(<Row density="read">body</Row>)
    expect(html).toContain("py-[18px]")
  })
})

describe("Row: interactive", () => {
  it("applies hover tint + cursor when interactive", () => {
    const html = renderToStaticMarkup(<Row interactive>body</Row>)
    expect(html).toContain("cursor-pointer")
    expect(html).toContain("hover:bg-foreground/[0.06]")
  })

  it("omits hover affordances when not interactive", () => {
    const html = renderToStaticMarkup(<Row>body</Row>)
    expect(html).not.toContain("cursor-pointer")
  })
})

describe("Row: divider", () => {
  it("draws the bottom hairline by default", () => {
    const html = renderToStaticMarkup(<Row>body</Row>)
    expect(html).toContain("border-b")
  })

  it("suppresses the hairline when divider=false", () => {
    const html = renderToStaticMarkup(<Row divider={false}>body</Row>)
    expect(html).not.toContain("border-b")
  })
})

describe("Row: forwarding", () => {
  it("forwards className and data attributes", () => {
    const html = renderToStaticMarkup(
      <Row className="my-row" data-testid="x">
        body
      </Row>,
    )
    expect(html).toContain("my-row")
    expect(html).toContain('data-testid="x"')
  })
})
