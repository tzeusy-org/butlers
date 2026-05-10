// ---------------------------------------------------------------------------
// ButlerMark — canonical butler letter-mark primitive (bu-myje9)
//
// Renders a 16px square (4px radius) bearing the butler initial.
// Two tones:
//   "fill"    — solid category-hue background, white initial. Active state.
//   "neutral" — transparent background, category-hue initial, hairline border.
//
// This module is also the single source of truth for the butler-name to
// CSS category token mapping. Chart code that only needs the CSS variable
// string should use the exported `butlerHueVar(name)` helper instead of
// importing the component itself.
//
// Doctrine: each butler's hue from --category-1..8 appears only on the butler
// letter-mark (colored squircle with initial). Never on backgrounds, borders,
// buttons, headers, or other chrome. This rule applies to butler hues.
// The categoryHueVar() helper uses the same token pool for non-butler entity
// coloring (contact labels, tags) — that is a documented exception, not a
// violation of the butler-hue rule.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Hue mapping — single source of truth
// ---------------------------------------------------------------------------

/**
 * All eight category CSS tokens, in slot order.
 * Slot assignment is deterministic: known butlers use their roster index;
 * unknown names fall back to a djb2-style hash (mod 8) so distinct unknown
 * butlers still receive distinct colors.
 */
const CATEGORY_VARS = [
  "var(--category-1)",
  "var(--category-2)",
  "var(--category-3)",
  "var(--category-4)",
  "var(--category-5)",
  "var(--category-6)",
  "var(--category-7)",
  "var(--category-8)",
] as const

/**
 * Canonical roster order. The slot a butler occupies in this list is its
 * permanent color slot. Add new butlers at the end; never reorder.
 *
 * Unknown names (not in this list) receive a hash-derived slot so they
 * remain visually distinct from each other and from known butlers. The
 * hash is deterministic across renders and sessions (multiplier-31 rolling hash).
 */
export const KNOWN_BUTLERS: readonly string[] = [
  "chronicler",
  "education",
  "finance",
  "general",
  "health",
  "home",
  "lifestyle",
  "messenger",
  "qa",
  "relationship",
  "travel",
]

/**
 * Multiplier-31 rolling hash (not djb2): maps any string to a non-negative integer.
 * Deterministic across renders and sessions.
 */
function hashName(name: string): number {
  let h = 0
  for (let i = 0; i < name.length; i++) {
    h = (h * 31 + name.charCodeAt(i)) | 0
  }
  return Math.abs(h)
}

/**
 * Map any arbitrary string to a deterministic `--category-N` CSS variable.
 * Uses the same hash algorithm as `butlerHueVar` but without the roster
 * index lookup. Useful for coloring non-butler entities (contact labels,
 * tags, etc.) where positional slot stability is not required.
 *
 * @example
 *   style={{ backgroundColor: categoryHueVar(label.name) }}
 */
export function categoryHueVar(name: string): string {
  return CATEGORY_VARS[hashName(name) % CATEGORY_VARS.length]
}

/**
 * Return the CSS variable string for the butler's category hue.
 *
 * Known butlers use their roster-index slot (stable across all renders).
 * Unknown butlers use a hash-derived slot (stable for a given name, but not
 * guaranteed to be unique across all unknown names).
 *
 * Use this helper in chart code (recharts, stripe charts) that needs the
 * raw CSS variable string without rendering a React component.
 *
 * @example
 *   fill={butlerHueVar("health")}
 */
export function butlerHueVar(name: string): string {
  const idx = KNOWN_BUTLERS.indexOf(name)
  if (idx !== -1) return CATEGORY_VARS[idx % CATEGORY_VARS.length]
  return CATEGORY_VARS[hashName(name) % CATEGORY_VARS.length]
}

// ---------------------------------------------------------------------------
// ButlerMark component
// ---------------------------------------------------------------------------

export interface ButlerMarkProps {
  /** Butler name. Drives both the initial glyph and the hue slot. */
  name: string
  /**
   * Visual tone:
   *   "fill"    — solid hue background, white initial. Use for active/selected state.
   *   "neutral" — transparent background, hue-colored initial, hairline border. Default.
   */
  tone?: "fill" | "neutral"
  /**
   * Size in pixels for the squircle. Defaults to 16.
   * Font size is scaled proportionally (60% of size).
   */
  size?: number
  /** Optional className forwarded to the root element. */
  className?: string
}

/**
 * 16x16 px squircle bearing the butler initial, colored via the canonical
 * `--category-N` hue slot for this butler name.
 *
 * @example
 *   <ButlerMark name="health" tone="fill" />
 *   <ButlerMark name="qa" tone="neutral" />
 */
export function ButlerMark({ name, tone = "neutral", size = 16, className }: ButlerMarkProps) {
  const hue = butlerHueVar(name)
  const initial = (name[0] ?? "?").toUpperCase()

  const baseStyle: React.CSSProperties = {
    width: size,
    height: size,
    borderRadius: Math.round(size * 0.25),
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    fontFamily: "var(--font-sans)",
    fontWeight: 600,
    fontSize: `${(size * 0.6).toFixed(1)}px`, // 60% of size
    lineHeight: 1,
    flexShrink: 0,
  }

  const toneStyle: React.CSSProperties =
    tone === "fill"
      ? {
          backgroundColor: hue,
          color: "white",
          border: "none",
        }
      : {
          backgroundColor: "transparent",
          color: hue,
          border: `1px solid ${hue}`,
        }

  return (
    <span
      style={{ ...baseStyle, ...toneStyle }}
      className={className}
      title={name}
      aria-label={name}
    >
      {initial}
    </span>
  )
}
