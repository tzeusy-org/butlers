// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// ProviderMark tests — bu-qo3sf, repointed bu-sd0l7.2
//
// bu-sd0l7.2: this bead's core finding. Two ProviderMark implementations
// rendered on the SAME credential page: pages.tsx/Spine.tsx used the
// atoms.tsx copy directly (glyph/label/size props — size varies 14px in the
// spine, 36px in the page header), while WhatBreaks.tsx (also rendered on
// that page, via pages.tsx) imported a second, divergent ./ProviderMark.tsx
// (a `provider` slug prop that auto-derived the initial). Only the
// standalone copy had a green test suite — fabricated confidence, since the
// page itself never rendered it standalone.
//
// Reunified onto the shipping atoms.tsx copy: WhatBreaksRow now computes
// `glyph={entry.butler.charAt(0).toUpperCase()}` at the call site (the same
// derivation the deleted atom used to do internally) instead of the atom
// deriving it. This preserves WhatBreaks' visible behaviour exactly, while
// unifying on an atom that also supports curated (non-first-letter) glyphs
// — e.g. mock-data.ts assigns "steam" the glyph "V" to avoid colliding with
// "spotify"'s "S", which an auto-derived first-letter would not preserve.
//
// Real behaviour/style divergences vs. the deleted copy (documented, not
// fixed — this bead reunifies, it doesn't redesign):
//   - border uses --border-strong (thicker) not --border (hairline)
//   - text uses --fg (full) not --mfg (muted)
//   - fontSize scales with `size` (Math.round(size*0.5)) rather than a fixed
//     60% of a fixed 22px
//   - no extra HTML attribute passthrough (no {...props} spread)
//
// Coverage:
//   - Renders the given glyph verbatim (caller-supplied, not derived)
//   - No background colour (transparent)
//   - No category hue token
//   - size defaults to 22px; is configurable (14px / 36px call sites)
//   - aria-label uses the given label
//   - className forwarding
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { ProviderMark } from "./atoms.tsx"

describe("ProviderMark: glyph rendering", () => {
  it("renders the given glyph verbatim", () => {
    const html = renderToStaticMarkup(<ProviderMark glyph="G" label="google" />)
    expect(html).toContain("G")
  })

  it("renders a curated multi-letter glyph unchanged (e.g. steam's 'V')", () => {
    const html = renderToStaticMarkup(<ProviderMark glyph="V" label="steam" />)
    expect(html).toContain("V")
  })
})

describe("ProviderMark: no colour", () => {
  it("does not use any category hue token", () => {
    const html = renderToStaticMarkup(<ProviderMark glyph="G" label="google" />)
    expect(html).not.toMatch(/var\(--category-/)
  })

  it("background is transparent (no fill)", () => {
    const html = renderToStaticMarkup(<ProviderMark glyph="G" label="google" />)
    expect(html).toContain("transparent")
  })
})

describe("ProviderMark: dimensions", () => {
  it("defaults to 22px wide and 22px tall", () => {
    const html = renderToStaticMarkup(<ProviderMark glyph="G" label="google" />)
    expect(html).toContain("width:22px")
    expect(html).toContain("height:22px")
  })

  it("honours a configurable size (14px, the Spine.tsx call site)", () => {
    const html = renderToStaticMarkup(<ProviderMark glyph="G" label="google" size={14} />)
    expect(html).toContain("width:14px")
    expect(html).toContain("height:14px")
  })

  it("honours a configurable size (36px, the pages.tsx call site)", () => {
    const html = renderToStaticMarkup(<ProviderMark glyph="G" label="google" size={36} />)
    expect(html).toContain("width:36px")
    expect(html).toContain("height:36px")
  })
})

describe("ProviderMark: accessibility", () => {
  it("aria-label uses the given label", () => {
    const html = renderToStaticMarkup(<ProviderMark glyph="S" label="spotify" />)
    expect(html).toContain('aria-label="spotify"')
  })
})

describe("ProviderMark: className forwarding", () => {
  it("merges additional className", () => {
    const html = renderToStaticMarkup(
      <ProviderMark glyph="G" label="google" className="pm-custom" />,
    )
    expect(html).toContain("pm-custom")
  })
})
