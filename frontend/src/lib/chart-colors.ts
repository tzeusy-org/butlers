// ---------------------------------------------------------------------------
// Chart series color helper (bu-86c4c.5)
//
// Single source of truth for recharts stroke/fill colors. Routes every chart
// series through the theme's tuned --chart-1..5 palette (see index.css)
// instead of ad hoc per-component tokens.
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

const CHART_COLOR_VARS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
] as const;

/**
 * Returns the CSS var() reference for chart series `index` (0-based),
 * cycling through the theme's --chart-1..5 palette.
 *
 * Single-series charts should just call `chartColor()` (defaults to slot 0 /
 * --chart-1) so every small trend/sparkline chart in the app draws from the
 * same first palette color.
 */
export function chartColor(index = 0): string {
  const len = CHART_COLOR_VARS.length;
  return CHART_COLOR_VARS[((index % len) + len) % len];
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
  return `color-mix(in oklch, ${chartColor(index)} ${alphaPercent}%, transparent)`;
}
