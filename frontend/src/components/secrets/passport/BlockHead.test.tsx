// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// BlockHead tests — bu-qo3sf, repointed bu-sd0l7.2
//
// bu-sd0l7.2: this used to test the orphan ./BlockHead.tsx (label/caption
// props, never imported by any page) while the actual credential pages
// (pages.tsx, GoogleAppCredentials.tsx) render the atoms.tsx BlockHead
// (eyebrow/right props) — a green suite pinning dead code. Repointed onto
// the shipping atoms.tsx export; prop names changed accordingly and the
// ReactNode-caption case was dropped (atoms.tsx's `right` is string-only).
//
// Coverage:
//   - Renders the eyebrow text
//   - Eyebrow is uppercase (CSS class, not string transform)
//   - Optional right caption is rendered when provided
//   - No right caption element when omitted
//   - className forwarding
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { BlockHead } from "./atoms.tsx"

describe("BlockHead: eyebrow rendering", () => {
  it("renders the eyebrow text", () => {
    const html = renderToStaticMarkup(<BlockHead eyebrow="Audit" />)
    expect(html).toContain("Audit")
  })

  it("applies uppercase tracking to the eyebrow", () => {
    // atoms.tsx BlockHead renders eyebrow via Mono(upper) — uppercase is a
    // CSS textTransform, not a string transform.
    const html = renderToStaticMarkup(<BlockHead eyebrow="Scopes" />)
    expect(html).toContain("uppercase")
  })

  it("applies mono font to the eyebrow", () => {
    const html = renderToStaticMarkup(<BlockHead eyebrow="WhatBreaks" />)
    expect(html).toContain("font-mono")
  })
})

describe("BlockHead: right caption", () => {
  it("renders the right caption when provided", () => {
    const html = renderToStaticMarkup(
      <BlockHead eyebrow="Audit" right="last 10 entries" />,
    )
    expect(html).toContain("last 10 entries")
  })

  it("does not render a right caption element when omitted", () => {
    const html = renderToStaticMarkup(<BlockHead eyebrow="Audit" />)
    expect(html).not.toContain("last 10 entries")
  })
})

describe("BlockHead: className forwarding", () => {
  it("merges additional className", () => {
    const html = renderToStaticMarkup(
      <BlockHead eyebrow="Probe" className="bh-custom" />,
    )
    expect(html).toContain("bh-custom")
  })
})
