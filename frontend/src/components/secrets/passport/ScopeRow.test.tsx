// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// ScopeRow / ScopeBalance / VisaRow tests — bu-qo3sf
//
// Coverage:
//   ScopeRow:
//     - Renders scope text
//     - granted status: full-fg colour + ✓ glyph
//     - missing status: --amber colour + ! glyph
//     - extra status: --dim colour + · glyph
//     - required=true renders "required" eyebrow tag
//     - required=false (default) omits the tag
//   ScopeBalance:
//     - Renders "N of M required scopes granted"
//     - granted < required → --amber colour
//     - granted >= required → --dim colour
//   VisaRow:
//     - Derives correct status from granted/required flags
//     - requiredBy produces a title attribute
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { ScopeRow, ScopeBalance, VisaRow } from "./ScopeRow"

const SCOPE = "https://www.googleapis.com/auth/calendar"

// ---------------------------------------------------------------------------
// ScopeRow
// ---------------------------------------------------------------------------

describe("ScopeRow: content rendering", () => {
  it("renders the scope text", () => {
    const html = renderToStaticMarkup(<ScopeRow scope={SCOPE} status="granted" />)
    expect(html).toContain(SCOPE)
  })
})

describe("ScopeRow: status glyphs", () => {
  it('status="granted" renders ✓', () => {
    const html = renderToStaticMarkup(<ScopeRow scope={SCOPE} status="granted" />)
    expect(html).toContain("✓")
  })

  it('status="missing" renders !', () => {
    const html = renderToStaticMarkup(<ScopeRow scope={SCOPE} status="missing" />)
    expect(html).toContain("!")
  })

  it('status="extra" renders ·', () => {
    const html = renderToStaticMarkup(<ScopeRow scope={SCOPE} status="extra" />)
    expect(html).toContain("·")
  })
})

describe("ScopeRow: status colours", () => {
  it('status="granted" uses --fg colour', () => {
    const html = renderToStaticMarkup(<ScopeRow scope={SCOPE} status="granted" />)
    expect(html).toContain("var(--fg")
  })

  it('status="missing" uses --amber colour', () => {
    const html = renderToStaticMarkup(<ScopeRow scope={SCOPE} status="missing" />)
    expect(html).toContain("var(--amber)")
  })

  it('status="extra" uses --dim colour', () => {
    const html = renderToStaticMarkup(<ScopeRow scope={SCOPE} status="extra" />)
    expect(html).toContain("var(--dim")
  })
})

describe("ScopeRow: required tag", () => {
  it("renders 'required' tag when required=true", () => {
    const html = renderToStaticMarkup(
      <ScopeRow scope={SCOPE} status="missing" required />,
    )
    expect(html).toContain("required")
  })

  it("omits 'required' tag when required=false (default)", () => {
    const html = renderToStaticMarkup(<ScopeRow scope={SCOPE} status="extra" />)
    // "extra" is not required, but the word "required" could appear in class names
    // We check the eyebrow text is absent — it would appear as standalone text
    // Use a more targeted check: the Eyebrow "required" text should not be in the output
    // Since status=extra and required defaults to false, we assert the eyebrow tag absent
    expect(html).not.toContain(">required<")
  })
})

// ---------------------------------------------------------------------------
// ScopeBalance
// ---------------------------------------------------------------------------

describe("ScopeBalance: content", () => {
  it("renders count text (N of M required scopes granted)", () => {
    const html = renderToStaticMarkup(<ScopeBalance granted={5} required={7} />)
    expect(html).toContain("5")
    expect(html).toContain("7")
    expect(html).toContain("required")
    expect(html).toContain("granted")
  })

  it("renders singular 'scope' when required=1", () => {
    const html = renderToStaticMarkup(<ScopeBalance granted={1} required={1} />)
    expect(html).toContain("scope")
    expect(html).not.toContain("scopes")
  })

  it("renders plural 'scopes' when required>1", () => {
    const html = renderToStaticMarkup(<ScopeBalance granted={2} required={3} />)
    expect(html).toContain("scopes")
  })
})

describe("ScopeBalance: colour", () => {
  it("uses --amber when granted < required", () => {
    const html = renderToStaticMarkup(<ScopeBalance granted={3} required={7} />)
    expect(html).toContain("var(--amber)")
  })

  it("uses --dim when granted >= required", () => {
    const html = renderToStaticMarkup(<ScopeBalance granted={7} required={7} />)
    expect(html).toContain("var(--dim")
  })
})

// ---------------------------------------------------------------------------
// VisaRow
// ---------------------------------------------------------------------------

describe("VisaRow: status derivation", () => {
  it("granted=false, required=true → missing status (! glyph, --amber)", () => {
    const html = renderToStaticMarkup(
      <VisaRow scope={SCOPE} granted={false} required />,
    )
    expect(html).toContain("!")
    expect(html).toContain("var(--amber)")
  })

  it("granted=true, required=false → extra status (· glyph, --dim)", () => {
    const html = renderToStaticMarkup(
      <VisaRow scope={SCOPE} granted required={false} />,
    )
    expect(html).toContain("·")
    expect(html).toContain("var(--dim")
  })

  it("granted=true, required=true → granted status (✓ glyph, --fg)", () => {
    const html = renderToStaticMarkup(
      <VisaRow scope={SCOPE} granted required />,
    )
    expect(html).toContain("✓")
    expect(html).toContain("var(--fg")
  })
})

describe("VisaRow: requiredBy title", () => {
  it("adds title attribute when requiredBy is provided", () => {
    const html = renderToStaticMarkup(
      <VisaRow
        scope={SCOPE}
        granted={false}
        required
        requiredBy={["health", "calendar"]}
      />,
    )
    expect(html).toContain("health")
    expect(html).toContain("calendar")
  })
})
