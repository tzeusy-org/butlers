// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// IdentityChip tests — bu-qo3sf, repointed bu-sd0l7.2
//
// bu-sd0l7.2: repointed from the orphan ./IdentityChip.tsx (never imported
// outside its own test) onto the shipping atoms.tsx export, which
// DirectionPassport.tsx, Spine.tsx, and pages.tsx all render (confirmed by
// the e2e suite's `[data-identity-id="wei"]` locator, which only the
// atoms.tsx copy emits).
//
// Real behaviour divergence: the dead copy derived the dot colour from a
// closed `role` enum ("owner"/"member"/"unknown") and recoloured the whole
// chip's TEXT (muted vs full-fg) based on a `selected` flag. The shipping
// copy takes an opaque `hue` from the caller (no role→colour table lives in
// the atom itself — Spine.tsx/DirectionPassport.tsx own that mapping) and an
// `active` flag that instead toggles BORDER/BACKGROUND emphasis — the label
// text stays full-fg regardless of active state. It also supports an
// optional `onClick`, rendering as a <button> with a "▾" affordance.
//
// Coverage:
//   - Renders the label (name)
//   - Renders the role text
//   - hue is applied verbatim to the dot (caller-supplied, not derived)
//   - Falls back to --fg dot colour when hue is omitted
//   - active=true applies border-strong + bg-elev emphasis
//   - active=false (default) applies the soft border
//   - dot is aria-hidden
//   - onClick renders a clickable button with data-identity-id
//   - no onClick renders a plain non-interactive div
//   - className forwarding
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { IdentityChip } from "./atoms.tsx"

describe("IdentityChip: content rendering", () => {
  it("renders the label", () => {
    const html = renderToStaticMarkup(
      <IdentityChip id="tze" label="Tze" role="owner" />,
    )
    expect(html).toContain("Tze")
  })

  it("renders the role text", () => {
    const html = renderToStaticMarkup(
      <IdentityChip id="tze" label="Tze" role="owner" />,
    )
    expect(html).toContain("owner")
  })
})

describe("IdentityChip: hue (caller-supplied, not derived)", () => {
  it("applies the given hue to the dot", () => {
    const html = renderToStaticMarkup(
      <IdentityChip id="tze" label="Tze" role="owner" hue="var(--role-admin)" />,
    )
    expect(html).toContain("var(--role-admin)")
  })

  it("falls back to --fg when hue is omitted", () => {
    const html = renderToStaticMarkup(
      <IdentityChip id="alex" label="Alex" role="member" />,
    )
    expect(html).toContain("var(--fg)")
  })
})

describe("IdentityChip: active state (border/background, not text colour)", () => {
  it("active=true applies border-strong emphasis", () => {
    const html = renderToStaticMarkup(
      <IdentityChip id="tze" label="Tze" role="owner" active />,
    )
    expect(html).toContain("border-[var(--border-strong)]")
  })

  it("active=false (default) applies the soft border", () => {
    const html = renderToStaticMarkup(
      <IdentityChip id="tze" label="Tze" role="owner" />,
    )
    expect(html).toContain("border-[var(--border-soft)]")
  })
})

describe("IdentityChip: dot accessibility", () => {
  it("dot is aria-hidden", () => {
    const html = renderToStaticMarkup(
      <IdentityChip id="tze" label="Tze" role="owner" />,
    )
    expect(html).toContain('aria-hidden="true"')
  })
})

describe("IdentityChip: interactivity", () => {
  it("onClick renders a button carrying data-identity-id", () => {
    const html = renderToStaticMarkup(
      <IdentityChip id="tze" label="Tze" role="owner" onClick={() => {}} />,
    )
    expect(html).toContain("<button")
    expect(html).toContain('data-identity-id="tze"')
  })

  it("no onClick renders a plain div, not a button", () => {
    const html = renderToStaticMarkup(
      <IdentityChip id="tze" label="Tze" role="owner" />,
    )
    expect(html).not.toContain("<button")
    expect(html).toContain('data-identity-id="tze"')
  })
})

describe("IdentityChip: className forwarding", () => {
  it("merges additional className", () => {
    const html = renderToStaticMarkup(
      <IdentityChip id="tze" label="Tze" role="owner" className="ic-custom" />,
    )
    expect(html).toContain("ic-custom")
  })
})
