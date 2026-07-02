/**
 * Dunbar tier constants shared by the Plex (PlexPage / plex-layout).
 * Historically extracted for the Concentric Circles social map; that view is
 * retired, but the tier scale and its warm-hue ring ramp live on here.
 */

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
