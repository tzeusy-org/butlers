// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// ButlerMark tests — bu-myje9
//
// Coverage:
//   - each known butler maps to a distinct, stable --category-N token
//   - unknown butler names fall back to a hash-derived slot (deterministic)
//   - the eight canonical slots are all reachable
//   - ButlerMark renders the correct initial glyph
//   - tone="fill" and tone="neutral" produce the correct inline styles
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { ButlerMark, butlerHueVar, KNOWN_BUTLERS } from "./ButlerMark"

// ---------------------------------------------------------------------------
// butlerHueVar: known butlers
// ---------------------------------------------------------------------------

describe("butlerHueVar: known butlers", () => {
  // Verify that each known butler maps to one of the eight canonical tokens
  // and that the mapping is stable (idempotent calls return the same value).
  const VALID_TOKENS = new Set([
    "var(--category-1)",
    "var(--category-2)",
    "var(--category-3)",
    "var(--category-4)",
    "var(--category-5)",
    "var(--category-6)",
    "var(--category-7)",
    "var(--category-8)",
  ])

  for (const name of KNOWN_BUTLERS) {
    it(`${name} maps to a valid --category-N token`, () => {
      const token = butlerHueVar(name)
      expect(VALID_TOKENS.has(token)).toBe(true)
    })

    it(`${name} mapping is stable across repeated calls`, () => {
      expect(butlerHueVar(name)).toBe(butlerHueVar(name))
    })
  }

  it("the first eight known butlers each occupy a distinct slot", () => {
    // The roster has 11 known butlers. The first 8 must have unique slots;
    // the 9th onward wraps. This assertion confirms no two of the first 8
    // share a token (which would indicate a mapping collision in slot order).
    const first8 = KNOWN_BUTLERS.slice(0, 8)
    const tokens = first8.map((n) => butlerHueVar(n))
    const uniqueTokens = new Set(tokens)
    expect(uniqueTokens.size).toBe(8)
  })
})

// ---------------------------------------------------------------------------
// butlerHueVar: unknown butlers
// ---------------------------------------------------------------------------

describe("butlerHueVar: unknown butler names", () => {
  it("returns a valid --category-N token for an unknown name", () => {
    const token = butlerHueVar("definitely-unknown-butler")
    expect(token).toMatch(/^var\(--category-[1-8]\)$/)
  })

  it("the same unknown name always resolves to the same token (deterministic multiplier-31 hash)", () => {
    expect(butlerHueVar("mystery")).toBe(butlerHueVar("mystery"))
  })

  it("two different unknown names may resolve to different tokens", () => {
    // This is a probabilistic check: pick two names that have different hashes.
    // If they collide we get a false pass, but the names below are chosen to
    // differ in their hash slot based on the djb2-like algorithm used.
    const a = butlerHueVar("alpha-x")
    const b = butlerHueVar("omega-z")
    // We cannot guarantee they differ (8 slots, many names), but we CAN assert
    // that both are valid tokens, which is the real invariant.
    expect(a).toMatch(/^var\(--category-[1-8]\)$/)
    expect(b).toMatch(/^var\(--category-[1-8]\)$/)
  })

  it("empty string falls back deterministically", () => {
    const token = butlerHueVar("")
    expect(token).toMatch(/^var\(--category-[1-8]\)$/)
  })
})

// ---------------------------------------------------------------------------
// ButlerMark component rendering
// ---------------------------------------------------------------------------

describe("ButlerMark: initial glyph", () => {
  it("renders the uppercase first letter of the butler name", () => {
    const html = renderToStaticMarkup(<ButlerMark name="health" />)
    expect(html).toContain("H")
  })

  it("renders '?' for an empty string name", () => {
    const html = renderToStaticMarkup(<ButlerMark name="" />)
    expect(html).toContain("?")
  })

  it("renders the title attribute with the butler name for accessibility", () => {
    const html = renderToStaticMarkup(<ButlerMark name="qa" />)
    expect(html).toContain('title="qa"')
    expect(html).toContain('aria-label="qa"')
  })
})

describe("ButlerMark: tone=fill", () => {
  it("applies solid hue background and white text", () => {
    const html = renderToStaticMarkup(<ButlerMark name="chronicler" tone="fill" />)
    // The fill tone sets backgroundColor to the hue and color to white.
    expect(html).toContain("white")
    expect(html).toContain("var(--category-1)")
  })
})

describe("ButlerMark: tone=neutral (default)", () => {
  it("applies transparent background and hue-colored text with border", () => {
    const html = renderToStaticMarkup(<ButlerMark name="chronicler" />)
    // Neutral tone has transparent background and uses hue as text + border color.
    expect(html).toContain("transparent")
    expect(html).toContain("var(--category-1)")
  })
})

describe("ButlerMark: type=staffer vs type=butler", () => {
  it("renders full circle (border-radius:50%) for type=staffer", () => {
    const html = renderToStaticMarkup(<ButlerMark name="switchboard" type="staffer" />)
    expect(html).toContain("border-radius:50%")
  })

  it("renders squircle (border-radius:4px) for type=butler", () => {
    const html = renderToStaticMarkup(<ButlerMark name="general" type="butler" />)
    expect(html).not.toContain("border-radius:50%")
    expect(html).toContain("border-radius:4px")
  })

  it("renders squircle when type is omitted (backwards-compatible)", () => {
    const html = renderToStaticMarkup(<ButlerMark name="general" />)
    expect(html).not.toContain("border-radius:50%")
    expect(html).toContain("border-radius:4px")
  })
})

describe("ButlerMark: className forwarding", () => {
  it("forwards className to the root span", () => {
    const html = renderToStaticMarkup(
      <ButlerMark name="health" className="my-extra-class" />,
    )
    expect(html).toContain("my-extra-class")
  })
})

describe("ButlerMark: size prop", () => {
  it("defaults to 16px width and height", () => {
    const html = renderToStaticMarkup(<ButlerMark name="health" />)
    expect(html).toContain("width:16px")
    expect(html).toContain("height:16px")
  })

  it("renders at the specified size", () => {
    const html = renderToStaticMarkup(<ButlerMark name="health" size={28} />)
    expect(html).toContain("width:28px")
    expect(html).toContain("height:28px")
  })

  it("scales font-size proportionally (60% of size)", () => {
    // size=16 → 9.6px; size=28 → 16.8px
    const html16 = renderToStaticMarkup(<ButlerMark name="health" size={16} />)
    const html28 = renderToStaticMarkup(<ButlerMark name="health" size={28} />)
    expect(html16).toContain("9.6px")
    expect(html28).toContain("16.8px")
  })

  it("existing callers are unaffected by the default (backwards-compatible)", () => {
    // Render without size prop; should produce the same output as size={16}.
    const htmlDefault = renderToStaticMarkup(<ButlerMark name="health" />)
    const htmlExplicit = renderToStaticMarkup(<ButlerMark name="health" size={16} />)
    expect(htmlDefault).toBe(htmlExplicit)
  })
})
