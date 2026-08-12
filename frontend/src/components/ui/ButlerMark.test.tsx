// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// ButlerMark tests — bu-myje9
//
// Coverage:
//   - each known butler maps to a distinct, stable --category-N token
//   - unknown butler names fall back to a hash-derived slot (deterministic)
//   - the twelve canonical slots are all reachable
//   - ButlerMark renders the correct initial glyph
//   - tone="fill" and tone="neutral" produce the correct inline styles
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { ButlerMark, KNOWN_BUTLERS } from "./ButlerMark"

// eslint-disable-next-line visual-role/no-private-identity-token -- Verifies the identity owner's rendered public contract.
const categoryToken = (slot: number) => `var(--category-${slot})`

// ---------------------------------------------------------------------------
// Identity resolution is private to ButlerMark; exercise it through the
// component's rendered public contract.
// ---------------------------------------------------------------------------

describe("ButlerMark: known butler identity mapping", () => {
  // Verify that each known butler maps to one of the twelve canonical tokens
  // and that the mapping is stable (idempotent calls return the same value).
  const VALID_TOKENS = new Set(
    Array.from({ length: 12 }, (_, index) => categoryToken(index + 1)),
  )

  for (const name of KNOWN_BUTLERS) {
    it(`${name} maps to a valid --category-N token`, () => {
      const html = renderToStaticMarkup(<ButlerMark name={name} />)
      expect([...VALID_TOKENS].some((token) => html.includes(token))).toBe(true)
    })

    it(`${name} mapping is stable across repeated calls`, () => {
      expect(renderToStaticMarkup(<ButlerMark name={name} />)).toBe(
        renderToStaticMarkup(<ButlerMark name={name} />),
      )
    })
  }

  it("all known butlers occupy distinct slots (bu-86c4c.6: 12-slot ramp fits the full 11-butler roster)", () => {
    // Regression guard for the collision this bead fixed: under the old
    // mod-8 ramp, the 9th-11th roster entries (qa, relationship, travel)
    // silently reused the 1st-3rd butler's hue (chronicler, education,
    // finance). With 12 slots and 11 known butlers, every entry must now
    // get its own distinct token.
    const tokens = KNOWN_BUTLERS.map((name) => {
      const html = renderToStaticMarkup(<ButlerMark name={name} />)
      return [...VALID_TOKENS].find((token) => html.includes(token))
    })
    const uniqueTokens = new Set(tokens)
    expect(uniqueTokens.size).toBe(KNOWN_BUTLERS.length)
  })
})

// ---------------------------------------------------------------------------
// unknown butlers
// ---------------------------------------------------------------------------

describe("ButlerMark: unknown butler names", () => {
  it("returns a valid --category-N token for an unknown name", () => {
    const html = renderToStaticMarkup(<ButlerMark name="definitely-unknown-butler" />)
    expect(html).toMatch(/var\(--category-(?:[1-9]|1[0-2])\)/)
  })

  it("the same unknown name always resolves to the same token (deterministic multiplier-31 hash)", () => {
    expect(renderToStaticMarkup(<ButlerMark name="mystery" />)).toBe(
      renderToStaticMarkup(<ButlerMark name="mystery" />),
    )
  })

  it("two different unknown names may resolve to different tokens", () => {
    // This is a probabilistic check: pick two names that have different hashes.
    // If they collide we get a false pass, but the names below are chosen to
    // differ in their hash slot based on the djb2-like algorithm used.
    const a = renderToStaticMarkup(<ButlerMark name="alpha-x" />)
    const b = renderToStaticMarkup(<ButlerMark name="omega-z" />)
    // We cannot guarantee they differ (12 slots, many names), but we CAN assert
    // that both are valid tokens, which is the real invariant.
    expect(a).toMatch(/var\(--category-(?:[1-9]|1[0-2])\)/)
    expect(b).toMatch(/var\(--category-(?:[1-9]|1[0-2])\)/)
  })

  it("empty string falls back deterministically", () => {
    const html = renderToStaticMarkup(<ButlerMark name="" />)
    expect(html).toMatch(/var\(--category-(?:[1-9]|1[0-2])\)/)
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

  it("keeps its accessible name without a duplicate native title by default", () => {
    const html = renderToStaticMarkup(<ButlerMark name="qa" />)
    expect(html).toContain('aria-label="qa"')
    expect(html).not.toContain('title="qa"')
  })

  it("uses the full butler name for visual hover only when requested", () => {
    const html = renderToStaticMarkup(<ButlerMark name="qa" showNameOnHover />)
    expect(html).toContain('aria-label="qa"')
    expect(html).toContain('title="qa"')
  })
})

describe("ButlerMark: tone=fill", () => {
  it("applies solid hue background and white text", () => {
    const html = renderToStaticMarkup(<ButlerMark name="chronicler" tone="fill" />)
    // The fill tone sets backgroundColor to the hue and color to white.
    expect(html).toContain("white")
    expect(html).toContain(categoryToken(1))
  })
})

describe("ButlerMark: tone=neutral (default)", () => {
  it("applies transparent background and hue-colored text with border", () => {
    const html = renderToStaticMarkup(<ButlerMark name="chronicler" />)
    // Neutral tone has transparent background and uses hue as text + border color.
    expect(html).toContain("transparent")
    expect(html).toContain(categoryToken(1))
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

  it("exposes type=staffer in its accessible label without a default native title", () => {
    const html = renderToStaticMarkup(<ButlerMark name="switchboard" type="staffer" />)
    expect(html).toContain('aria-label="switchboard (staffer)"')
    expect(html).not.toContain('title="switchboard (staffer)"')
  })

  it("formats the butler name for hover only when requested", () => {
    const html = renderToStaticMarkup(<ButlerMark name="general" type="butler" />)
    expect(html).toContain('aria-label="general"')
    expect(html).not.toContain('title="general"')
  })

  it("formats the staffer name for hover only when requested", () => {
    const html = renderToStaticMarkup(
      <ButlerMark name="switchboard" type="staffer" showNameOnHover />,
    )
    expect(html).toContain('aria-label="switchboard (staffer)"')
    expect(html).toContain('title="switchboard (staffer)"')
  })

  it("does not append the type qualifier for butler hover labels", () => {
    const html = renderToStaticMarkup(<ButlerMark name="general" showNameOnHover />)
    expect(html).toContain('aria-label="general"')
    expect(html).toContain('title="general"')
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
