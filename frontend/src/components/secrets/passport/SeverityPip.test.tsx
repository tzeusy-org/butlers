// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// SeverityPip tests — bu-qo3sf
//
// Coverage:
//   - Each severity renders the correct glyph
//   - Each severity renders the correct colour token
//   - role="img" + aria-label for accessibility
//   - className forwarding
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { SeverityPip } from "./SeverityPip"

const PIP_CASES = [
  { severity: "high" as const,   char: "↑", color: "var(--red)",    label: "high severity"   },
  { severity: "medium" as const, char: "·", color: "var(--amber)",  label: "medium severity" },
  { severity: "low" as const,    char: "↓", color: "var(--dim",     label: "low severity"    },
] as const

describe("SeverityPip: glyph characters", () => {
  for (const { severity, char } of PIP_CASES) {
    it(`severity="${severity}" renders "${char}"`, () => {
      const html = renderToStaticMarkup(<SeverityPip severity={severity} />)
      expect(html).toContain(char)
    })
  }
})

describe("SeverityPip: colour tokens", () => {
  for (const { severity, color } of PIP_CASES) {
    it(`severity="${severity}" uses colour starting with "${color}"`, () => {
      const html = renderToStaticMarkup(<SeverityPip severity={severity} />)
      expect(html).toContain(color)
    })
  }
})

describe("SeverityPip: accessibility", () => {
  it('has role="img"', () => {
    const html = renderToStaticMarkup(<SeverityPip severity="high" />)
    expect(html).toContain('role="img"')
  })

  for (const { severity, label } of PIP_CASES) {
    it(`severity="${severity}" has aria-label="${label}"`, () => {
      const html = renderToStaticMarkup(<SeverityPip severity={severity} />)
      expect(html).toContain(`aria-label="${label}"`)
    })
  }
})

describe("SeverityPip: className forwarding", () => {
  it("merges additional className", () => {
    const html = renderToStaticMarkup(
      <SeverityPip severity="high" className="pip-custom" />,
    )
    expect(html).toContain("pip-custom")
  })
})
