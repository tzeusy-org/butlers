// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// TierBadge tests — bu-ec2wb
//
// Coverage:
//   - All canonical Dunbar tier values render the correct tier label
//   - tierColor() maps tier values to --tier-N tokens
//   - Unknown/fallback tier maps to --tier-6
//   - Renders 6px colored dot and tier label
//   - ARIA: role="img" + aria-label present
//   - className forwarding
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { TierBadge, tierColor, tierLabel } from "./TierBadge"

// ---------------------------------------------------------------------------
// tierColor helper
// ---------------------------------------------------------------------------

describe("tierColor: canonical tier sizes", () => {
  const TIER_CASES = [
    { tier: 5, token: "var(--tier-1)" },
    { tier: 15, token: "var(--tier-2)" },
    { tier: 50, token: "var(--tier-3)" },
    { tier: 150, token: "var(--tier-4)" },
    { tier: 500, token: "var(--tier-5)" },
    { tier: 1500, token: "var(--tier-6)" },
  ] as const

  for (const { tier, token } of TIER_CASES) {
    it(`tier ${tier} maps to ${token}`, () => {
      expect(tierColor(tier)).toBe(token)
    })
  }

  it("unknown tier falls back to var(--tier-6)", () => {
    expect(tierColor(999)).toBe("var(--tier-6)")
    expect(tierColor(0)).toBe("var(--tier-6)")
    expect(tierColor(-1)).toBe("var(--tier-6)")
  })

  it("mapping is stable (deterministic)", () => {
    expect(tierColor(5)).toBe(tierColor(5))
  })
})

// ---------------------------------------------------------------------------
// tierLabel helper
// ---------------------------------------------------------------------------

describe("tierLabel: canonical tier sizes", () => {
  it("tier 5 → S (Support Clique)", () => expect(tierLabel(5)).toBe("S"))
  it("tier 15 → A (Sympathy Group)", () => expect(tierLabel(15)).toBe("A"))
  it("tier 50 → B (Good Friends)", () => expect(tierLabel(50)).toBe("B"))
  it("tier 150 → C (Meaningful)", () => expect(tierLabel(150)).toBe("C"))
  it("tier 500 → D (Acquaintances)", () => expect(tierLabel(500)).toBe("D"))
  it("tier 1500 → F (Recognizable / fallback)", () => expect(tierLabel(1500)).toBe("F"))
  it("unknown tier → F (fallback)", () => expect(tierLabel(999)).toBe("F"))
})

// ---------------------------------------------------------------------------
// TierBadge component rendering
// ---------------------------------------------------------------------------

describe("TierBadge: renders tier label", () => {
  it("renders S for tier 5", () => {
    const html = renderToStaticMarkup(<TierBadge tier={5} />)
    expect(html).toContain("S")
  })

  it("renders A for tier 15", () => {
    const html = renderToStaticMarkup(<TierBadge tier={15} />)
    expect(html).toContain("A")
  })

  it("renders F for unknown tier", () => {
    const html = renderToStaticMarkup(<TierBadge tier={999} />)
    expect(html).toContain("F")
  })
})

describe("TierBadge: renders colored dot", () => {
  it("renders the tier-1 color token for tier 5", () => {
    const html = renderToStaticMarkup(<TierBadge tier={5} />)
    expect(html).toContain("var(--tier-1)")
  })

  it("renders the tier-6 color token for unknown tiers", () => {
    const html = renderToStaticMarkup(<TierBadge tier={42} />)
    expect(html).toContain("var(--tier-6)")
  })

  it("includes a 6px circle (width and height 6px)", () => {
    const html = renderToStaticMarkup(<TierBadge tier={5} />)
    // The dot is 6px by 6px per Brief §2
    expect(html).toContain("width:6px")
    expect(html).toContain("height:6px")
  })
})

// ---------------------------------------------------------------------------
// ARIA attributes
// ---------------------------------------------------------------------------

describe("TierBadge: accessibility", () => {
  it("has role=img", () => {
    const html = renderToStaticMarkup(<TierBadge tier={5} />)
    expect(html).toContain('role="img"')
  })

  it("has aria-label with tier label", () => {
    const html = renderToStaticMarkup(<TierBadge tier={5} />)
    expect(html).toContain('aria-label="Tier S"')
  })

  it("aria-label reflects the correct label for each tier", () => {
    expect(renderToStaticMarkup(<TierBadge tier={150} />)).toContain('aria-label="Tier C"')
    expect(renderToStaticMarkup(<TierBadge tier={500} />)).toContain('aria-label="Tier D"')
  })
})

// ---------------------------------------------------------------------------
// className forwarding
// ---------------------------------------------------------------------------

describe("TierBadge: className forwarding", () => {
  it("forwards className to the root span", () => {
    const html = renderToStaticMarkup(<TierBadge tier={5} className="my-tier-class" />)
    expect(html).toContain("my-tier-class")
  })
})
