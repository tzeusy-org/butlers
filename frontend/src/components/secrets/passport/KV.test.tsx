// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// KV tests — bu-qo3sf, repointed bu-sd0l7.2
//
// bu-sd0l7.2: repointed from the orphan ./KV.tsx onto the shipping
// atoms.tsx KV — pages.tsx (the actual credential page) builds its KV band
// exclusively from `<KV label=... value=... size=... mono=... />`, the
// atoms.tsx signature. The deleted file's own docstring claimed it was "the
// variant tuned for credential page density" — that claim was false; it was
// never wired into pages.tsx and only its own test exercised it. The
// shipping copy has a materially different, narrower contract:
//   - value is a plain string (no ReactNode support)
//   - no className / no arbitrary HTML attribute passthrough at all
//   - label colour is --dim (not --mfg)
//   - muting is controlled by `mono` (render as plain span) and `valueColor`
//     (explicit colour string), not a `valueMuted` boolean
//
// Coverage:
//   - Renders label text
//   - Renders value text
//   - Label uses --dim colour
//   - Value uses full foreground by default
//   - valueColor overrides the value colour
//   - mono=false renders the value as a plain (non-Mono) span
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { KV } from "./atoms.tsx"

describe("KV: content rendering", () => {
  it("renders the label", () => {
    const html = renderToStaticMarkup(<KV label="issued" value="14 Jan 2026" />)
    expect(html).toContain("issued")
  })

  it("renders the value", () => {
    const html = renderToStaticMarkup(<KV label="issued" value="14 Jan 2026" />)
    expect(html).toContain("14 Jan 2026")
  })
})

describe("KV: typography", () => {
  it("label uses --dim colour token", () => {
    const html = renderToStaticMarkup(<KV label="expires" value="—" />)
    expect(html).toContain("var(--dim")
  })

  it("value is full-foreground by default", () => {
    const html = renderToStaticMarkup(<KV label="expires" value="never" />)
    expect(html).toContain("var(--fg")
  })

  it("valueColor overrides the value colour", () => {
    const html = renderToStaticMarkup(
      <KV label="expires" value="never" valueColor="var(--amber)" />,
    )
    expect(html).toContain("var(--amber)")
  })
})

describe("KV: mono toggle", () => {
  it("mono=false renders the value in a plain sans span, not Mono", () => {
    const html = renderToStaticMarkup(<KV label="source" value="Google" mono={false} />)
    expect(html).toContain("font-sans")
  })

  it("mono=true (default) renders the value in mono", () => {
    const html = renderToStaticMarkup(<KV label="source" value="Google" />)
    expect(html).toContain("font-mono")
  })
})
