// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// EntityMark tests — bu-ec2wb
//
// Coverage:
//   - Person entities show up to 2 initials (first letter of each word)
//   - Non-person entities show the type glyph (O, L, X, @, E, G, ?)
//   - tone="fill" applies hue background + white text
//   - tone="neutral" (default) applies transparent bg + hue border
//   - isOwner applies --role-owner border in neutral tone
//   - isUnidentified applies --amber border + amber text in neutral tone
//   - ARIA: role="img" + aria-label present
//   - entityTypeColor returns a --category-N token for known types
//   - className forwarding
//   - size prop scales the element
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { EntityMark, entityTypeColor } from "./EntityMark"

// ---------------------------------------------------------------------------
// entityTypeColor helper
// ---------------------------------------------------------------------------

describe("entityTypeColor: known entity types", () => {
  const KNOWN_TYPES = [
    "person",
    "organization",
    "place",
    "product",
    "account",
    "event",
    "group",
    "other",
  ] as const

  for (const type of KNOWN_TYPES) {
    it(`${type} maps to a valid --category-N token`, () => {
      const token = entityTypeColor(type)
      expect(token).toMatch(/^var\(--category-[1-8]\)$/)
    })
  }

  it("mapping is stable across repeated calls (deterministic)", () => {
    expect(entityTypeColor("person")).toBe(entityTypeColor("person"))
    expect(entityTypeColor("organization")).toBe(entityTypeColor("organization"))
  })

  it("unknown type falls back to var(--fg)", () => {
    expect(entityTypeColor("unknown-type-xyz")).toBe("var(--fg)")
  })

  it("known types all map to distinct slots", () => {
    const types = ["person", "organization", "place", "product", "account", "event", "group", "other"]
    const tokens = types.map((t) => entityTypeColor(t))
    const unique = new Set(tokens)
    expect(unique.size).toBe(types.length)
  })
})

// ---------------------------------------------------------------------------
// Glyph rendering
// ---------------------------------------------------------------------------

describe("EntityMark: person initials", () => {
  it("renders two initials for a two-word name", () => {
    const html = renderToStaticMarkup(
      <EntityMark name="Alice Johnson" entityType="person" />,
    )
    expect(html).toContain("AJ")
  })

  it("renders one initial for a single-word name", () => {
    const html = renderToStaticMarkup(
      <EntityMark name="Alice" entityType="person" />,
    )
    expect(html).toContain("A")
  })

  it("renders at most 2 initials even for long names", () => {
    const html = renderToStaticMarkup(
      <EntityMark name="Alice Bob Carol" entityType="person" />,
    )
    // Should contain AB (first two initials), not ABC
    expect(html).toContain("AB")
    expect(html).not.toContain("ABC")
  })

  it("renders ? for an empty person name", () => {
    const html = renderToStaticMarkup(
      <EntityMark name="" entityType="person" />,
    )
    expect(html).toContain("?")
  })

  it("initials are uppercased", () => {
    const html = renderToStaticMarkup(
      <EntityMark name="alice johnson" entityType="person" />,
    )
    expect(html).toContain("AJ")
  })
})

describe("EntityMark: non-person type glyphs", () => {
  const GLYPH_CASES = [
    { type: "organization", glyph: "O" },
    { type: "place", glyph: "L" },
    { type: "product", glyph: "X" },
    { type: "account", glyph: "@" },
    { type: "event", glyph: "E" },
    { type: "group", glyph: "G" },
    { type: "other", glyph: "?" },
  ] as const

  for (const { type, glyph } of GLYPH_CASES) {
    it(`${type} renders glyph "${glyph}"`, () => {
      const html = renderToStaticMarkup(
        <EntityMark name="Some Entity" entityType={type} />,
      )
      expect(html).toContain(glyph)
    })
  }

  it("unknown entity type renders ? glyph", () => {
    const html = renderToStaticMarkup(
      <EntityMark name="Widget" entityType="widget" />,
    )
    expect(html).toContain("?")
  })
})

// ---------------------------------------------------------------------------
// Tone variants
// ---------------------------------------------------------------------------

describe("EntityMark: tone=fill", () => {
  it("applies hue as background", () => {
    const html = renderToStaticMarkup(
      <EntityMark name="Acme" entityType="organization" tone="fill" />,
    )
    // organization → --category-4 (teal)
    expect(html).toContain("var(--category-4)")
    expect(html).toContain("background")
  })

  it("applies white text color", () => {
    const html = renderToStaticMarkup(
      <EntityMark name="Acme" entityType="organization" tone="fill" />,
    )
    expect(html).toContain("#fff")
  })

  it("applies transparent border in fill tone", () => {
    const html = renderToStaticMarkup(
      <EntityMark name="Acme" entityType="organization" tone="fill" />,
    )
    expect(html).toContain("transparent")
  })
})

describe("EntityMark: tone=neutral (default)", () => {
  it("applies transparent background", () => {
    const html = renderToStaticMarkup(
      <EntityMark name="Alice" entityType="person" />,
    )
    expect(html).toContain("transparent")
  })

  it("applies --border-strong border by default in neutral tone", () => {
    const html = renderToStaticMarkup(
      <EntityMark name="Alice" entityType="person" />,
    )
    expect(html).toContain("border-strong")
  })
})

// ---------------------------------------------------------------------------
// Ownership and state borders
// ---------------------------------------------------------------------------

describe("EntityMark: isOwner border", () => {
  it("applies --role-owner border in neutral tone when isOwner=true", () => {
    const html = renderToStaticMarkup(
      <EntityMark name="Alice" entityType="person" isOwner />,
    )
    expect(html).toContain("var(--role-owner)")
  })

  it("does NOT apply --role-owner border in fill tone (fill overrides border)", () => {
    const html = renderToStaticMarkup(
      <EntityMark name="Alice" entityType="person" tone="fill" isOwner />,
    )
    // fill tone always uses transparent border
    expect(html).toContain("transparent")
    expect(html).not.toContain("role-owner")
  })
})

describe("EntityMark: isUnidentified border", () => {
  it("applies --amber border in neutral tone when isUnidentified=true", () => {
    const html = renderToStaticMarkup(
      <EntityMark name="Alice" entityType="person" isUnidentified />,
    )
    expect(html).toContain("var(--amber)")
  })

  it("applies --amber text color when isUnidentified=true in neutral tone", () => {
    const html = renderToStaticMarkup(
      <EntityMark name="Alice" entityType="person" isUnidentified />,
    )
    // amber appears as both text color and border
    const matches = (html.match(/var\(--amber\)/g) ?? []).length
    expect(matches).toBeGreaterThanOrEqual(2)
  })

  it("isOwner takes precedence over isUnidentified for border color", () => {
    const html = renderToStaticMarkup(
      <EntityMark name="Alice" entityType="person" isOwner isUnidentified />,
    )
    // isOwner wins: border should be --role-owner, not --amber
    expect(html).toContain("var(--role-owner)")
  })
})

// ---------------------------------------------------------------------------
// ARIA attributes
// ---------------------------------------------------------------------------

describe("EntityMark: accessibility", () => {
  it("has role=img", () => {
    const html = renderToStaticMarkup(
      <EntityMark name="Alice Johnson" entityType="person" />,
    )
    expect(html).toContain('role="img"')
  })

  it("has aria-label with person name", () => {
    const html = renderToStaticMarkup(
      <EntityMark name="Alice Johnson" entityType="person" />,
    )
    expect(html).toContain('aria-label="Alice Johnson"')
  })

  it("has aria-label including entity type for non-person entities", () => {
    const html = renderToStaticMarkup(
      <EntityMark name="Acme Corp" entityType="organization" />,
    )
    expect(html).toContain("organization")
  })
})

// ---------------------------------------------------------------------------
// Size and className
// ---------------------------------------------------------------------------

describe("EntityMark: size prop", () => {
  it("defaults to 18px", () => {
    const html = renderToStaticMarkup(
      <EntityMark name="Alice" entityType="person" />,
    )
    expect(html).toContain("width:18px")
    expect(html).toContain("height:18px")
  })

  it("renders at the specified size", () => {
    const html = renderToStaticMarkup(
      <EntityMark name="Alice" entityType="person" size={32} />,
    )
    expect(html).toContain("width:32px")
    expect(html).toContain("height:32px")
  })
})

describe("EntityMark: className forwarding", () => {
  it("forwards className to the root span", () => {
    const html = renderToStaticMarkup(
      <EntityMark name="Alice" entityType="person" className="my-test-class" />,
    )
    expect(html).toContain("my-test-class")
  })
})
