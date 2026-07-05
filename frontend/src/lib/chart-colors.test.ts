import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { describe, expect, it } from "vitest"

import {
  butlerHueVar,
  categoryHueVar,
  chartColor,
  chartColorAlpha,
  NEUTRAL_DENSITY_HIGH,
  NEUTRAL_DENSITY_LOW,
  neutralDensityColor,
} from "./chart-colors"

describe("chartColor / chartColorAlpha", () => {
  it("cycles through the 5-slot --chart palette and wraps", () => {
    expect(chartColor(0)).toBe("var(--chart-1)")
    expect(chartColor(4)).toBe("var(--chart-5)")
    expect(chartColor(5)).toBe("var(--chart-1)")
  })

  it("chartColorAlpha wraps chartColor in a color-mix() with the given alpha", () => {
    expect(chartColorAlpha(0, 40)).toBe("color-mix(in oklch, var(--chart-1) 40%, transparent)")
  })
})

describe("butler-identity / category channel re-exports", () => {
  it("re-exports the same functions ui/ButlerMark implements (single source of truth)", () => {
    expect(typeof butlerHueVar).toBe("function")
    expect(typeof categoryHueVar).toBe("function")
    expect(butlerHueVar("health")).toMatch(/^var\(--category-\d+\)$/)
    expect(categoryHueVar("some-tag")).toMatch(/^var\(--category-\d+\)$/)
  })
})

describe("neutralDensityColor", () => {
  it("returns a literal rgb() string (required — WebGL/canvas callers cannot resolve var())", () => {
    expect(neutralDensityColor(0)).toMatch(/^rgb\(\d+, \d+, \d+\)$/)
    expect(neutralDensityColor(1)).toMatch(/^rgb\(\d+, \d+, \d+\)$/)
  })

  it("is achromatic — equal R, G, B channels at every stop", () => {
    for (const t of [0, 0.25, 0.5, 0.75, 1]) {
      const match = neutralDensityColor(t).match(/^rgb\((\d+), (\d+), (\d+)\)$/)
      expect(match).not.toBeNull()
      const [, r, g, b] = match!
      expect(r).toBe(g)
      expect(g).toBe(b)
    }
  })

  it("darkens monotonically as normalized intensity increases", () => {
    const channel = (rgb: string) => Number(rgb.match(/^rgb\((\d+),/)![1])
    const c0 = channel(neutralDensityColor(0))
    const c50 = channel(neutralDensityColor(0.5))
    const c100 = channel(neutralDensityColor(1))
    expect(c0).toBeGreaterThan(c50)
    expect(c50).toBeGreaterThan(c100)
  })

  it("clamps out-of-range input to the [0,1] endpoints", () => {
    expect(neutralDensityColor(-5)).toBe(neutralDensityColor(0))
    expect(neutralDensityColor(5)).toBe(neutralDensityColor(1))
  })
})

// ---------------------------------------------------------------------------
// Drift guard — NEUTRAL_DENSITY_LOW/HIGH must stay in lockstep with
// index.css's light-mode --dim/--fg literals (mirrors contrast.test.ts's
// approach of reading the CSS source directly rather than hand-copying
// values that could silently drift).
// ---------------------------------------------------------------------------

const CSS_PATH = fileURLToPath(new URL("../index.css", import.meta.url))
const CSS_SOURCE = readFileSync(CSS_PATH, "utf-8")

function extractRootToken(name: string): { l: number; c: number; h: number } {
  const startPattern = /^:root \{$/m
  const startMatch = startPattern.exec(CSS_SOURCE)
  if (!startMatch) throw new Error('Could not find ":root {" block in index.css')
  const bodyStart = startMatch.index + startMatch[0].length
  const closeIndex = CSS_SOURCE.indexOf("\n}", bodyStart)
  const block = CSS_SOURCE.slice(bodyStart, closeIndex)
  const pattern = new RegExp(`--${name}:\\s*oklch\\(\\s*([\\d.]+)\\s+([\\d.]+)\\s+([\\d.]+)\\s*\\)`)
  const match = pattern.exec(block)
  if (!match) throw new Error(`Token --${name} not found in :root`)
  const [, l, c, h] = match
  return { l: Number(l), c: Number(c), h: Number(h) }
}

describe("neutral-density-ramp endpoints stay in sync with index.css", () => {
  it("NEUTRAL_DENSITY_LOW matches light-mode --dim", () => {
    expect(NEUTRAL_DENSITY_LOW).toEqual(extractRootToken("dim"))
  })

  it("NEUTRAL_DENSITY_HIGH matches light-mode --fg", () => {
    expect(NEUTRAL_DENSITY_HIGH).toEqual(extractRootToken("fg"))
  })
})
