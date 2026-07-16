// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// ScopeBalance / VisaRow tests — bu-qo3sf, repointed + renamed bu-sd0l7.2
//
// bu-sd0l7.2: this file used to test the orphan ./ScopeRow.tsx, which
// exported three components: ScopeRow, ScopeBalance, VisaRow. Of those,
// ONLY ScopeBalance and VisaRow duplicate a shipping atoms.tsx export;
// `ScopeRow` itself has no atoms.tsx counterpart and, per a repo-wide grep,
// had zero importers anywhere — it was plain dead code, not a duplicate, so
// its coverage has no surviving target to repoint onto and was dropped
// rather than repointed. Renamed to ScopeBalance.test.tsx to reflect what
// actually survives.
//
// Real behaviour divergences vs. the deleted copies (the atoms.tsx versions
// win as the shipping copies actually rendered by pages.tsx):
//   - ScopeBalance took precomputed `granted`/`required` COUNTS (numbers);
//     the shipping copy takes the raw `granted`/`required` scope-name
//     ARRAYS and computes the ratio itself, rendering `null` entirely when
//     `required` is empty. It shows a compact "N/M" ratio + segmented bar,
//     not the old "N of M required scopes granted" prose sentence.
//   - VisaRow took `granted`/`required` booleans (+ optional `requiredBy`
//     for a dynamic "Required by: x, y" tooltip) and derived the status
//     internally. The shipping copy takes the tri-state `state` directly
//     (caller derives it) and shows a fixed per-state legend tooltip
//     instead of the dynamic requiredBy tooltip — the "who requires this"
//     detail is not preserved in the shipping copy. Documented here as a
//     known behaviour loss inherited from the standalone→shipping
//     reunification, not something this bead's scope covers restoring.
//
// Coverage:
//   ScopeBalance:
//     - Renders nothing when there are no required scopes
//     - Renders the have/required ratio text
//     - granted < required → --amber-text foreground and --amber fill
//     - granted >= required → --green colour
//   VisaRow:
//     - missing/extra/granted states render the correct glyph symbol
//     - Each state carries its fixed legend as a title attribute
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { ScopeBalance, VisaRow } from "./atoms.tsx"

const SCOPE = "https://www.googleapis.com/auth/calendar"

// ---------------------------------------------------------------------------
// ScopeBalance
// ---------------------------------------------------------------------------

describe("ScopeBalance: empty required list", () => {
  it("renders nothing when required is empty", () => {
    const html = renderToStaticMarkup(<ScopeBalance granted={[]} required={[]} />)
    expect(html).toBe("")
  })
})

describe("ScopeBalance: ratio text", () => {
  it("renders the have/required ratio", () => {
    const html = renderToStaticMarkup(
      <ScopeBalance granted={["a", "b"]} required={["a", "b", "c"]} />,
    )
    expect(html).toContain("2/3")
  })
})

describe("ScopeBalance: colour", () => {
  it("uses AA-safe --amber-text for the ratio while preserving --amber for the segmented fill", () => {
    const html = renderToStaticMarkup(
      <ScopeBalance granted={["a"]} required={["a", "b", "c"]} />,
    )
    expect(html).toContain("var(--amber-text)")
    expect(html).toContain("var(--amber)")
  })

  it("uses --green when fully granted", () => {
    const html = renderToStaticMarkup(
      <ScopeBalance granted={["a", "b"]} required={["a", "b"]} />,
    )
    expect(html).toContain("var(--green)")
  })
})

// ---------------------------------------------------------------------------
// VisaRow
// ---------------------------------------------------------------------------

describe("VisaRow: state glyphs", () => {
  it('state="missing" renders ∅ and AA-safe --amber-text', () => {
    const html = renderToStaticMarkup(<VisaRow scope={SCOPE} state="missing" />)
    expect(html).toContain("∅")
    expect(html).toContain("var(--amber-text)")
  })

  it('state="extra" renders ✓ and --dim', () => {
    const html = renderToStaticMarkup(<VisaRow scope={SCOPE} state="extra" />)
    expect(html).toContain("✓")
    expect(html).toContain("var(--dim")
  })

  it('state="granted" renders ✓ and --fg', () => {
    const html = renderToStaticMarkup(<VisaRow scope={SCOPE} state="granted" />)
    expect(html).toContain("✓")
    expect(html).toContain("var(--fg")
  })
})

describe("VisaRow: fixed per-state legend tooltip", () => {
  it("state=missing carries a title explaining it is required but not granted", () => {
    const html = renderToStaticMarkup(<VisaRow scope={SCOPE} state="missing" />)
    expect(html).toContain("Required but not yet granted.")
  })

  it("state=extra carries a title explaining the over-grant is not a warning", () => {
    const html = renderToStaticMarkup(<VisaRow scope={SCOPE} state="extra" />)
    // Apostrophe is HTML-entity-encoded (&#x27;) by renderToStaticMarkup.
    expect(html).toContain("Granted beyond what")
    expect(html).toContain("required")
    expect(html).toContain("not a warning")
  })
})
