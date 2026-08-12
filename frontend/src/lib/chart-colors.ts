// ---------------------------------------------------------------------------
// Chart color registry (bu-86c4c.5, extended bu-qvnce.7)
//
// Single source of truth for every data-visualization color channel in the
// app. Four semantic channels, each with a distinct meaning — do not
// cross-wire them (e.g. never use a butler hue for a generic series, or a
// series color for density):
//
//   series               Generic recharts stroke/fill for trend/line/area/bar
//                        charts with no inherent categorical meaning. Routes
//                        through the theme's tuned --chart-1..5 palette.
//                        chartColor() / chartColorAlpha() below.
//   local-category       Non-butler categorical coloring (tags, contact
//                        labels, arbitrary group-by dimensions) — hash-slotted
//                        across the dedicated --categorical-1..12 ramp.
//   neutral-density-ramp A single achromatic intensity ramp (not a state/
//                        severity color) for density/heatmap visualizations
//                        where "more" is the only signal — e.g. the
//                        Chronicles location heatmap. neutralDensityColor()
//                        below. Kept semantically separate from --severity-*/
//                        --red/--amber/--green so density is never visually
//                        confused with health/state signaling.
//
// IMPORTANT: never wrap a CSS custom property in hsl(var(--x)). Every color
// token in this theme (--primary, --chart-1..5, etc.) is a full oklch(...)
// color literal, not a raw "H S% L%" component tuple, so hsl(var(--x)) is
// invalid CSS — browsers drop the declaration and the series silently
// renders black/invisible in the dark theme. Reference the token directly
// with var(--x) (chartColor below), or use chartColorAlpha for a translucent
// variant (gradient stops, graduated-opacity fills).
//
// A repo-wide grep guard (see eslint.config.js) bans the hsl(var()) pattern
// so this regression cannot come back silently.
// ---------------------------------------------------------------------------

import { oklchToSrgb255, type Oklch } from "@/lib/contrast";
import {
  visualRoleToken,
  type ChartSeriesColor,
} from "@/lib/visual-token-roles";

/**
 * Returns the CSS var() reference for chart series `index` (0-based),
 * cycling through the theme's --chart-1..5 palette.
 *
 * Single-series charts should just call `chartColor()` (defaults to slot 0 /
 * --chart-1) so every small trend/sparkline chart in the app draws from the
 * same first palette color.
 */
export function chartSeriesColor(index = 0): ChartSeriesColor {
  return visualRoleToken("chart-series", index) as ChartSeriesColor;
}

/**
 * Backwards-compatible chart helper for existing single-series consumers.
 * New code should prefer the role-explicit `chartSeriesColor` name.
 */
export function chartColor(index = 0): ChartSeriesColor {
  return chartSeriesColor(index);
}

/**
 * Returns a translucent variant of a chart series color for gradient stops
 * or graduated-opacity fills (e.g. area chart fill gradients, histogram
 * bucket shading). `alphaPercent` is 0-100.
 *
 * Uses color-mix() rather than the old hsl(var(--x) / alpha) trick, which
 * only works when the referenced token holds raw HSL components — ours hold
 * full oklch(...) literals.
 */
export function chartColorAlpha(index: number, alphaPercent: number): string {
  return `color-mix(in oklch, ${chartSeriesColor(index)} ${alphaPercent}%, transparent)`;
}

// ---------------------------------------------------------------------------
// Neutral density ramp (bu-qvnce.7)
//
// WebGL/canvas consumers (MapLibre GL's data-driven fill paint, <canvas>
// heatmaps) parse color strings themselves and cannot resolve CSS custom
// properties, var(), or color-mix() the way the DOM style engine can — so
// this ramp is computed as literal oklch->sRGB math rather than referencing
// --dim/--fg via var(). The two endpoints below are intentionally kept equal
// to index.css's light-mode --dim and --fg literals (see the cross-check in
// chart-colors.test.ts, which reads index.css directly and fails loudly if
// the two drift apart). --dim (not --bg-elev/white) anchors the low end so
// even the faintest cell stays a visible mid-gray against any basemap tile
// color, rather than washing out to near-invisible white.
//
// Deliberately achromatic (chroma 0) — density/frequency is not a state or
// severity signal, so it must not borrow --red/--amber/--green/--severity-*,
// which are reserved for health/attention signaling elsewhere in the app.
// ---------------------------------------------------------------------------

/** Light-mode --dim literal (frontend/src/index.css :root). Low-density endpoint. */
export const NEUTRAL_DENSITY_LOW: Oklch = { l: 0.55, c: 0, h: 0 };
/** Light-mode --fg literal (frontend/src/index.css :root). High-density endpoint. */
export const NEUTRAL_DENSITY_HIGH: Oklch = { l: 0.18, c: 0, h: 0 };

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

/**
 * Returns a literal `rgb()` string for a neutral (achromatic) density ramp,
 * for heatmap/density visualizations where "more" is the only signal (e.g.
 * the Chronicles location heatmap). `normalized` is clamped to [0,1]; 0 is
 * the lightest stop, 1 the darkest.
 *
 * @example
 *   properties: { color: neutralDensityColor(intensity) }
 */
export function neutralDensityColor(normalized: number): string {
  const t = Math.min(1, Math.max(0, normalized));
  const mixed: Oklch = {
    l: lerp(NEUTRAL_DENSITY_LOW.l, NEUTRAL_DENSITY_HIGH.l, t),
    c: lerp(NEUTRAL_DENSITY_LOW.c, NEUTRAL_DENSITY_HIGH.c, t),
    h: lerp(NEUTRAL_DENSITY_LOW.h, NEUTRAL_DENSITY_HIGH.h, t),
  };
  const [r, g, b] = oklchToSrgb255(mixed);
  return `rgb(${r}, ${g}, ${b})`;
}
