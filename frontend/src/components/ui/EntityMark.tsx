// ---------------------------------------------------------------------------
// EntityMark — canonical entity type-mark primitive (bu-ec2wb)
//
// Renders a compact squircle mark for an entity. Person entities show up to
// two initials; all other types show a type glyph (O, L, X, @, E, G).
// Two tones: fill (active/selected) and neutral (default, hairline border).
//
// Brief §2: "Build new EntityMark with tone (fill/neutral), size, person
//            initials vs. org/place/product glyph, ownership/state borders."
// Amendment 9: Reuses --category-1..8, --role-owner, --amber, --fg, --border-strong
//              tokens only. No new tokens.
//
// Distinct from ButlerMark (butler letter-marks, colored by butler hue) —
// EntityMark is for entities in the entity graph.
// ---------------------------------------------------------------------------

import { ENTITY_BADGE_TEXT } from "@/lib/entity-model"

// ---------------------------------------------------------------------------
// Type-glyph catalog
// Per prototype/data.jsx: person → initials; others → fixed glyph.
// ---------------------------------------------------------------------------

/**
 * Entity types recognized by EntityMark.
 * "other" is the catch-all fallback bucket.
 */
export type EntityType =
  | "person"
  | "organization"
  | "place"
  | "product"
  | "account"
  | "event"
  | "group"
  | "other"

/** Single-character type glyphs for non-person entities. */
const TYPE_GLYPHS: Record<string, string> = {
  person: "", // initials rendered separately
  organization: "O",
  place: "L",
  product: "X",
  account: "@",
  event: "E",
  group: "G",
  other: "?",
} as const

/**
 * Return the type glyph for a non-person entity.
 * Falls back to "?" for unknown types.
 */
function typeGlyph(entityType: string): string {
  return TYPE_GLYPHS[entityType] ?? "?"
}

// ---------------------------------------------------------------------------
// Color mapping
// Maps entity type to a --category-N slot. Fixed assignments so entity
// types always render consistently regardless of order. Reuses the
// existing --category-1..8 token pool (Amendment 9: no new tokens).
//
// Slots are distinct from butler-hue slots; entity type and butler name
// share the same token pool but are in separate domains.
// ---------------------------------------------------------------------------

const TYPE_COLOR_SLOTS: Record<string, string> = {
  person: "var(--category-1)", // blue
  organization: "var(--category-4)", // teal
  place: "var(--category-7)", // cyan
  product: "var(--category-3)", // amber
  account: "var(--category-6)", // mauve
  event: "var(--category-2)", // violet
  group: "var(--category-8)", // orange
  other: "var(--category-5)", // rose
} as const

/**
 * Map an entity type to its CSS category color token.
 * Uses fixed slot assignments — stable across renders and sessions.
 *
 * @example
 *   style={{ color: entityTypeColor("person") }}  // "var(--category-1)"
 */
export function entityTypeColor(entityType: string): string {
  return TYPE_COLOR_SLOTS[entityType] ?? "var(--fg)"
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface EntityMarkProps {
  /**
   * The entity's canonical name. Used to derive initials for "person" entities.
   * For other types the glyph from the type catalog is used instead.
   */
  name: string
  /**
   * Entity type. Drives both the glyph and the border/color behavior.
   * Unknown types fall back to "other".
   */
  entityType?: string
  /**
   * Visual tone:
   *   "fill"    — solid hue background, white glyph. Use for active/selected state.
   *   "neutral" — transparent background, hue border, fg glyph. Default.
   */
  tone?: "fill" | "neutral"
  /**
   * Size in pixels for the squircle. Defaults to 18 (per prototype atoms.jsx).
   * Font size is scaled proportionally.
   */
  size?: number
  /**
   * When true, applies the --role-owner violet border (neutral tone only).
   * Owner entities receive a visually distinct border per Brief §2.
   */
  isOwner?: boolean
  /**
   * When true, applies the --amber border to signal unidentified state
   * (neutral tone only). Overridden by isOwner.
   */
  isUnidentified?: boolean
  /** Optional className forwarded to the root element. */
  className?: string
}

/**
 * Entity type-mark: squircle with initials (person) or a type glyph (other).
 *
 * @example
 *   <EntityMark name="Alice Johnson" entityType="person" />
 *   <EntityMark name="Acme Corp" entityType="organization" tone="fill" />
 *   <EntityMark name="Alice" entityType="person" isOwner />
 *   <EntityMark name="Unknown" entityType="person" isUnidentified />
 */
export function EntityMark({
  name,
  entityType = "other",
  tone = "neutral",
  size = 18,
  isOwner = false,
  isUnidentified = false,
  className,
}: EntityMarkProps) {
  const isPerson = entityType === "person"
  const hue = entityTypeColor(entityType)

  // Derive glyph: up to 2 initials for persons, fixed glyph for others.
  const glyph = isPerson
    ? name
        .split(/\s+/)
        .slice(0, 2)
        .map((w) => w[0] ?? "")
        .join("")
        .toUpperCase()
        .slice(0, 2) || "?"
    : typeGlyph(entityType)

  // Background and text color per tone.
  const bg = tone === "fill" ? hue : "transparent"
  const fg =
    tone === "fill"
      ? ENTITY_BADGE_TEXT
      : isUnidentified
        ? "var(--amber)"
        : "var(--fg)"

  // Border color: fill uses no border; neutral uses ownership/state hierarchy.
  const borderColor =
    tone === "fill"
      ? "transparent"
      : isOwner
        ? "var(--role-owner)"
        : isUnidentified
          ? "var(--amber)"
          : "var(--border-strong,oklch(1 0 0/0.18))"

  // Font: sans (weighted) for persons (initials need more width),
  // mono for glyph types (single char, even spacing).
  const fontFamily = isPerson ? "var(--font-sans)" : "var(--font-mono)"
  const fontWeight = isPerson ? 600 : 500
  const fontSize = Math.max(8, Math.round(size * (isPerson ? 0.42 : 0.5)))
  const letterSpacing = isPerson ? "-0.02em" : "0.02em"

  const label = isPerson
    ? name || "entity"
    : `${entityType} entity`

  return (
    <span
      role="img"
      aria-label={label}
      className={className}
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: size,
        height: size,
        borderRadius: 3,
        background: bg,
        color: fg,
        border: `1px solid ${borderColor}`,
        fontFamily,
        fontWeight,
        fontSize,
        letterSpacing,
        lineHeight: 1,
        flexShrink: 0,
      }}
    >
      {glyph}
    </span>
  )
}
