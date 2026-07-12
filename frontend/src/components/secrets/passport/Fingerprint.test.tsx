// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// Fingerprint tests — bu-qo3sf, repointed bu-sd0l7.2
//
// bu-sd0l7.2: repointed from the orphan ./Fingerprint.tsx (never imported
// outside its own test + the also-orphan ./FingerprintRow.tsx) onto the
// shipping atoms.tsx export, which pages.tsx actually renders via
// FingerprintRow. Prop is `value` (nullable) not `fingerprint` (required),
// and there's a real behaviour divergence on the no-colon edge case: the
// dead copy rendered the whole string as a single muted "scheme" segment,
// while the shipping copy treats a colon-less string as an unscoped *hash*
// (full --fg colour, not muted) — see the "no colon" case below.
//
// Coverage:
//   - Renders the scheme portion
//   - Renders the hash portion
//   - Scheme portion is muted; hash portion is full-foreground
//   - null/missing value renders a muted "—" placeholder
//   - Handles fingerprint strings without a colon (shipping behaviour: full
//     value treated as hash, rendered in full foreground, not muted)
//   - dim=true swaps the muted/foreground split to a dimmer variant
//   - className forwarding
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { Fingerprint } from "./atoms.tsx"

describe("Fingerprint: content rendering", () => {
  it("renders the scheme part", () => {
    const html = renderToStaticMarkup(<Fingerprint value="sha256:7a3f8e9b" />)
    expect(html).toContain("sha256")
  })

  it("renders the hash part", () => {
    const html = renderToStaticMarkup(<Fingerprint value="sha256:7a3f8e9b" />)
    expect(html).toContain("7a3f8e9b")
  })
})

describe("Fingerprint: colour split", () => {
  it("scheme part uses muted colour token", () => {
    const html = renderToStaticMarkup(<Fingerprint value="sha256:7a3f8e9b" />)
    expect(html).toContain("var(--mfg")
  })

  it("hash part uses full foreground colour token", () => {
    const html = renderToStaticMarkup(<Fingerprint value="sha256:7a3f8e9b" />)
    expect(html).toContain("var(--fg")
  })
})

describe("Fingerprint: null value", () => {
  it("renders a muted em-dash placeholder when value is null", () => {
    const html = renderToStaticMarkup(<Fingerprint value={null} />)
    expect(html).toContain("—")
    expect(html).toContain("var(--dim")
  })
})

describe("Fingerprint: edge cases", () => {
  it("a value with no colon renders as a full-foreground hash (shipping behaviour)", () => {
    // bu-sd0l7.2: this is the opposite of the deleted dead copy, which
    // rendered a colon-less value as a single MUTED segment. atoms.tsx's
    // scheme/hash split treats everything before the (absent) colon as an
    // empty scheme and the whole string as the hash.
    const html = renderToStaticMarkup(<Fingerprint value="plaintoken" />)
    expect(html).toContain("plaintoken")
    expect(html).toContain("var(--fg")
  })
})

describe("Fingerprint: dim variant", () => {
  it("dim=true renders the hash in --mfg instead of --fg", () => {
    const html = renderToStaticMarkup(<Fingerprint value="sha256:7a3f8e9b" dim />)
    expect(html).not.toContain("var(--fg")
  })
})

describe("Fingerprint: className forwarding", () => {
  it("merges additional className", () => {
    const html = renderToStaticMarkup(
      <Fingerprint value="sha256:7a3f8e9b" className="fp-cls" />,
    )
    expect(html).toContain("fp-cls")
  })
})
