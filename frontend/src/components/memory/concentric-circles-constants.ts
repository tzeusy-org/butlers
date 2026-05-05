/**
 * Shared constants and helpers for the Concentric Circles social map.
 * Extracted to a separate module to satisfy the react-refresh/only-export-components
 * rule (canvas component file must only export React components).
 */

import type { DunbarEntry } from "@/api/types";

export const TIERS = [5, 15, 50, 150, 500, 1500] as const;
export type Tier = (typeof TIERS)[number];

export const TIER_NAMES: Record<Tier, string> = {
  5: "Support Clique",
  15: "Sympathy Group",
  50: "Good Friends",
  150: "Dunbar's Number",
  500: "Acquaintances",
  1500: "Familiar Faces",
};

// Ring radii as fractions of the total radius (0 = center, 1 = edge).
// Inner tiers get proportionally more space to show detail.
export const TIER_RADIUS_FRACTIONS: Record<Tier, number> = {
  5: 0.16,
  15: 0.30,
  50: 0.46,
  150: 0.62,
  500: 0.78,
  1500: 0.94,
};

/**
 * Single warm hue (terracotta, h≈35) with chroma + lightness falloff: intimacy
 * reads as saturation. Inner tiers (5/15/50) are pushed to near-maximum chroma
 * for presence; outer tiers (150/500/1500) fade gently.
 * Committed warm-hue strategy -- do NOT introduce a second accent hue.
 */
export const TIER_RING_COLORS: Record<Tier, string> = {
  5:    "oklch(0.50 0.22 35)", // inner: near-max chroma at this lightness
  15:   "oklch(0.55 0.20 35)", // inner: bold step down
  50:   "oklch(0.62 0.16 35)", // inner: still saturated
  150:  "oklch(0.71 0.10 35)", // outer: starts fading
  500:  "oklch(0.76 0.06 35)", // outer: muted
  1500: "oklch(0.80 0.02 35)", // outer: near-neutral
};

/**
 * Owner node color: deep graphite-warm in the same h≈35 family.
 * Visually distinct from tier-5 via lightness (darker), not competing hue.
 * This avoids the violet-vs-terracotta complementary clash from the original design.
 * Uses a CSS custom property so the light/dark theme can each set an appropriate lightness.
 */
export const OWNER_COLOR = "var(--social-map-owner)";

export function getInitials(name: string): string {
  const parts = name.trim().split(/\s+/);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }
  // For single-word names, use grapheme-aware extraction to safely handle
  // emoji and multi-codepoint characters. Intl.Segmenter splits on grapheme
  // cluster boundaries, preventing surrogate-pair corruption.
  const graphemes = [..._segmenter.segment(parts[0])].map((g) => g.segment);
  return graphemes.slice(0, 2).join("").toUpperCase();
}

/** Case-insensitive substring match against the entry's canonical_name or any alias. */
export function matchesSearch(entry: DunbarEntry, query: string): boolean {
  if (!query) return true;
  const q = query.toLowerCase();
  if (entry.canonical_name.toLowerCase().includes(q)) return true;
  return (entry.aliases ?? []).some((alias) => alias.toLowerCase().includes(q));
}

/** Place N nodes evenly around a circle of radius r, centered at cx,cy. */
export function circlePositions(
  n: number,
  r: number,
  cx: number,
  cy: number,
  angleOffset = 0,
): Array<{ x: number; y: number }> {
  return Array.from({ length: n }, (_, i) => {
    const angle = angleOffset + (i * 2 * Math.PI) / n - Math.PI / 2;
    return {
      x: cx + r * Math.cos(angle),
      y: cy + r * Math.sin(angle),
    };
  });
}

/**
 * Per-tier badge angles in radians (0 = east, π/2 = south, π = west, 3π/2 = north).
 * Chosen to spread badges in a clockwise fan in the lower hemisphere,
 * away from the top arc where tier labels live.
 *
 * Tier 50   → 0       (3 o'clock, east)
 * Tier 150  → π/4     (4–5 o'clock, down-right)
 * Tier 500  → π/2     (6 o'clock, south)
 * Tier 1500 → 3π/4    (7–8 o'clock, down-left)
 */
export const TIER_BADGE_ANGLES: Partial<Record<Tier, number>> = {
  50: 0,
  150: Math.PI / 4,
  500: Math.PI / 2,
  1500: (3 * Math.PI) / 4,
};

/** ease-out-expo easing for jump-to-tier animation. */
export function easeOutExpo(t: number): number {
  return t === 1 ? 1 : 1 - Math.pow(2, -10 * t);
}

// Module-level Segmenter instance -- reused across all truncateGraphemes calls
// to avoid the constructor overhead on every render tick.
const _segmenter = new Intl.Segmenter(undefined, { granularity: "grapheme" });

/**
 * Truncate a string to at most `maxGraphemes` grapheme clusters, appending "…"
 * if truncated. Uses Intl.Segmenter to avoid splitting surrogate pairs (emoji,
 * CJK) mid-codepoint — plain `slice()` operates on UTF-16 code units and can
 * produce replacement characters (U+FFFD) for names like "Ana 🌸".
 */
export function truncateGraphemes(s: string, maxGraphemes: number): string {
  if (maxGraphemes <= 0) return "";
  const graphemes = [..._segmenter.segment(s)];
  if (graphemes.length <= maxGraphemes) return s;
  return graphemes.slice(0, maxGraphemes - 1).map((g) => g.segment).join("") + "…";
}
