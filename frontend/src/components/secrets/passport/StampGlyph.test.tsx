// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// StampGlyph tests — bu-qo3sf, repointed bu-sd0l7.2
//
// bu-sd0l7.2: repointed from the orphan ./StampGlyph.tsx onto the shipping
// atoms.tsx export, which StampRow (also reunified this bead) actually
// renders in pages.tsx's Audit section. Two real, already-shipping
// divergences this repoint surfaces (this is a test-target correction, not
// a behaviour change — atoms.tsx's StampGlyph is what the page has been
// rendering all along):
//   - The dead copy's colour table was hand-maintained per action; the
//     shipping copy derives colour from the shared STAMP_GLYPHS constant's
//     `tone` field via `toneColor()`. Several actions carry `tone: "fg"`,
//     which `toneColor()` maps to plain --fg rather than a semantic colour
//     — so "rotated", "connected", "overrode", and "set" render in plain
//     foreground on the shipping copy, not the dim/green/amber/dim the dead
//     copy's test suite asserted. Also: STAMP_GLYPHS has a "disconnected"
//     action (⊖, dim) the dead copy's action union didn't even include.
//   - The shipping copy renders a bordered box (not a bare glyph) and has
//     no role="img"/aria-label at all — an accessibility gap relative to
//     the dead copy, inherited as-is since fixing it is outside this
//     bead's reunification scope.
//
// Coverage:
//   - Each action renders its glyph character (per constants.ts STAMP_GLYPHS)
//   - Each action renders the colour toneColor() derives from its tone
//   - className forwarding
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { StampGlyph } from "./atoms.tsx"

// Mirrors constants.ts STAMP_GLYPHS + atoms.tsx toneColor()'s tone→token map.
const GLYPH_CASES = [
  { action: "verified",     char: "✓", color: "var(--green" },
  { action: "rotated",      char: "↻", color: "var(--fg"    },
  { action: "failed",       char: "✕", color: "var(--red"   },
  { action: "revoked",      char: "⊘", color: "var(--red"   },
  { action: "connected",    char: "⊕", color: "var(--fg"    },
  { action: "disconnected", char: "⊖", color: "var(--mfg"   },
  { action: "warned",       char: "!", color: "var(--amber" },
  { action: "overrode",     char: "⤳", color: "var(--fg"    },
  { action: "attempted",    char: "▷", color: "var(--mfg"   },
  { action: "set",          char: "⊙", color: "var(--fg"    },
] as const

describe("StampGlyph: glyph characters", () => {
  for (const { action, char } of GLYPH_CASES) {
    it(`action="${action}" renders "${char}"`, () => {
      const html = renderToStaticMarkup(<StampGlyph action={action} />)
      expect(html).toContain(char)
    })
  }
})

describe("StampGlyph: colour tokens (derived from constants.ts tone)", () => {
  for (const { action, color } of GLYPH_CASES) {
    it(`action="${action}" uses colour starting with "${color}"`, () => {
      const html = renderToStaticMarkup(<StampGlyph action={action} />)
      expect(html).toContain(color)
    })
  }
})

describe("StampGlyph: unknown action fallback", () => {
  it("falls back to a dim '·' glyph for an unrecognised action", () => {
    const html = renderToStaticMarkup(<StampGlyph action="__unknown__" />)
    expect(html).toContain("·")
    expect(html).toContain("var(--mfg")
  })
})

describe("StampGlyph: className forwarding", () => {
  it("merges additional className", () => {
    const html = renderToStaticMarkup(
      <StampGlyph action="verified" className="glyph-custom" />,
    )
    expect(html).toContain("glyph-custom")
  })
})
