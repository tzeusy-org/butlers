// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// KV tests — bu-qo3sf
//
// Coverage:
//   - Renders label text
//   - Renders value text
//   - Label uses --mfg muted colour
//   - Value uses full foreground by default
//   - valueMuted=true uses muted foreground for value
//   - className forwarding
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { KV } from "./KV"

describe("KV: content rendering", () => {
  it("renders the label", () => {
    const html = renderToStaticMarkup(<KV label="issued" value="14 Jan 2026" />)
    expect(html).toContain("issued")
  })

  it("renders the value", () => {
    const html = renderToStaticMarkup(<KV label="issued" value="14 Jan 2026" />)
    expect(html).toContain("14 Jan 2026")
  })

  it("renders a ReactNode value", () => {
    const html = renderToStaticMarkup(
      <KV label="source" value={<span>butler:health</span>} />,
    )
    expect(html).toContain("butler:health")
  })
})

describe("KV: typography", () => {
  it("label uses --mfg colour token", () => {
    const html = renderToStaticMarkup(<KV label="expires" value="—" />)
    expect(html).toContain("var(--mfg")
  })

  it("value is full-foreground by default", () => {
    const html = renderToStaticMarkup(<KV label="expires" value="never" />)
    // Mono component with muted=false uses --fg
    expect(html).toContain("var(--fg")
  })

  it("valueMuted=true renders value in muted colour", () => {
    const html = renderToStaticMarkup(<KV label="expires" value="—" valueMuted />)
    // Mono with muted=true uses --mfg
    const mfgCount = (html.match(/var\(--mfg/g) ?? []).length
    // Both label AND value use --mfg when valueMuted
    expect(mfgCount).toBeGreaterThanOrEqual(2)
  })
})

describe("KV: className forwarding", () => {
  it("merges additional className", () => {
    const html = renderToStaticMarkup(
      <KV label="cat" value="oauth" className="kv-custom" />,
    )
    expect(html).toContain("kv-custom")
  })
})
