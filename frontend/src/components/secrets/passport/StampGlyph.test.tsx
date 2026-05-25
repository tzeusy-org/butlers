// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// StampGlyph tests — bu-qo3sf
//
// Coverage:
//   - Each action renders the correct glyph character
//   - Each action renders the correct colour token
//   - role="img" + aria-label for accessibility
//   - className forwarding
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { StampGlyph } from "./StampGlyph"

const GLYPH_CASES = [
  { action: "verified" as const,  char: "✓", color: "var(--green)",  label: "verified"  },
  { action: "rotated" as const,   char: "↻", color: "var(--dim",     label: "rotated"   },
  { action: "failed" as const,    char: "✕", color: "var(--red)",    label: "failed"    },
  { action: "revoked" as const,   char: "⊘", color: "var(--red)",    label: "revoked"   },
  { action: "connected" as const, char: "⊕", color: "var(--green)",  label: "connected" },
  { action: "warned" as const,    char: "!", color: "var(--amber)",   label: "warned"    },
  { action: "overrode" as const,  char: "⤳", color: "var(--amber)",   label: "overrode"  },
  { action: "attempted" as const, char: "▷", color: "var(--dim",     label: "attempted" },
  { action: "set" as const,       char: "⊙", color: "var(--dim",     label: "set"       },
] as const

describe("StampGlyph: glyph characters", () => {
  for (const { action, char } of GLYPH_CASES) {
    it(`action="${action}" renders "${char}"`, () => {
      const html = renderToStaticMarkup(<StampGlyph action={action} />)
      expect(html).toContain(char)
    })
  }
})

describe("StampGlyph: colour tokens", () => {
  for (const { action, color } of GLYPH_CASES) {
    it(`action="${action}" uses colour starting with "${color}"`, () => {
      const html = renderToStaticMarkup(<StampGlyph action={action} />)
      expect(html).toContain(color)
    })
  }
})

describe("StampGlyph: accessibility", () => {
  it('has role="img"', () => {
    const html = renderToStaticMarkup(<StampGlyph action="verified" />)
    expect(html).toContain('role="img"')
  })

  it("has aria-label matching the action", () => {
    for (const { action, label } of GLYPH_CASES) {
      const html = renderToStaticMarkup(<StampGlyph action={action} />)
      expect(html).toContain(`aria-label="${label}"`)
    }
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
