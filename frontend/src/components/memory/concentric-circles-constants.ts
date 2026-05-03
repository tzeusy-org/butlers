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
  1500: "Recognizable",
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

export const TIER_RING_COLORS: Record<Tier, string> = {
  5: "#7c3aed",    // violet-700
  15: "#2563eb",   // blue-600
  50: "#0891b2",   // cyan-600
  150: "#059669",  // emerald-600
  500: "#ca8a04",  // yellow-600
  1500: "#9ca3af", // gray-400
};

export function getInitials(name: string): string {
  const parts = name.trim().split(/\s+/);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }
  return name.slice(0, 2).toUpperCase();
}

/** Case-insensitive substring match against the entry's canonical_name. */
export function matchesSearch(entry: DunbarEntry, query: string): boolean {
  if (!query) return true;
  return entry.canonical_name.toLowerCase().includes(query.toLowerCase());
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

/** ease-out-expo easing for jump-to-tier animation. */
export function easeOutExpo(t: number): number {
  return t === 1 ? 1 : 1 - Math.pow(2, -10 * t);
}
