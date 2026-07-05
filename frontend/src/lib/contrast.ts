/**
 * contrast.ts — pure-math oklch → sRGB → WCAG contrast ratio (bu-86c4c.16)
 *
 * JARVIS audit move 11 (cross:accessibility, critical finding): color
 * contrast was "verified nowhere" — the app leans on 9-11px mono type, so
 * the WCAG 1.4.3 "large text" 3:1 exemption never applies; every text/
 * background pair below must clear 4.5:1 (AA, normal text).
 *
 * No jsdom / getComputedStyle involved — index.css's `oklch(...)` token
 * literals are converted to linear sRGB via the standard Björn Ottosson
 * OKLab matrices, then to relative luminance per the WCAG formula. This
 * lets contrast.test.ts assert real ratios against the literal values in
 * index.css without a browser.
 */

export interface Oklch {
  l: number
  c: number
  /** Hue in degrees. */
  h: number
}

/** Converts an OKLCH color to linear sRGB (each channel may be outside [0,1] — out of gamut). */
function oklchToLinearSrgb(color: Oklch): [number, number, number] {
  const hRad = (color.h * Math.PI) / 180
  const a = color.c * Math.cos(hRad)
  const b = color.c * Math.sin(hRad)

  const lPrime = color.l + 0.3963377774 * a + 0.2158037573 * b
  const mPrime = color.l - 0.1055613458 * a - 0.0638541728 * b
  const sPrime = color.l - 0.0894841775 * a - 1.291485548 * b

  const l = lPrime ** 3
  const m = mPrime ** 3
  const s = sPrime ** 3

  const r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
  const g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
  const bl = -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s

  return [r, g, bl]
}

/** Linear-light sRGB channel -> gamma-encoded sRGB channel (clamped to [0,1]). */
function linearToGammaChannel(c: number): number {
  const clamped = Math.min(1, Math.max(0, c))
  return clamped <= 0.0031308 ? 12.92 * clamped : 1.055 * clamped ** (1 / 2.4) - 0.055
}

/** Gamma-encoded sRGB channel [0,1] -> linear-light channel, per the WCAG relative-luminance formula. */
function gammaToLinearChannel(c: number): number {
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
}

/** WCAG relative luminance (0 = black, 1 = white) of an OKLCH color, gamut-clamped like a real browser paints it. */
export function relativeLuminance(color: Oklch): number {
  const [rLin, gLin, bLin] = oklchToLinearSrgb(color)
  const r = gammaToLinearChannel(linearToGammaChannel(rLin))
  const g = gammaToLinearChannel(linearToGammaChannel(gLin))
  const b = gammaToLinearChannel(linearToGammaChannel(bLin))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

/**
 * Converts an OKLCH color to gamma-encoded 8-bit sRGB channels (0-255,
 * gamut-clamped), for callers that need a literal `rgb()` string rather than
 * a relative-luminance number — e.g. WebGL/canvas contexts (MapLibre GL,
 * `<canvas>`) that parse color strings themselves and cannot resolve CSS
 * custom properties or `color-mix()` the way a real DOM style engine can.
 * See `chart-colors.ts`'s `neutralDensityColor` for the motivating caller.
 */
export function oklchToSrgb255(color: Oklch): [number, number, number] {
  const [rLin, gLin, bLin] = oklchToLinearSrgb(color)
  const toByte = (c: number) => Math.round(linearToGammaChannel(c) * 255)
  return [toByte(rLin), toByte(gLin), toByte(bLin)]
}

/** WCAG contrast ratio (1:1 to 21:1) between two OKLCH colors, order-independent. */
export function contrastRatio(a: Oklch, b: Oklch): number {
  const lumA = relativeLuminance(a)
  const lumB = relativeLuminance(b)
  const lighter = Math.max(lumA, lumB)
  const darker = Math.min(lumA, lumB)
  return (lighter + 0.05) / (darker + 0.05)
}

/** WCAG AA minimum contrast ratio for normal-weight text under 18pt/14pt-bold. */
export const WCAG_AA_NORMAL_TEXT = 4.5
