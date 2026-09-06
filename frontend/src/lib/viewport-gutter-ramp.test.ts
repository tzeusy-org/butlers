import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"

import { describe, expect, it } from "vitest"

const CSS_SOURCE = readFileSync(fileURLToPath(new URL("../index.css", import.meta.url)), "utf-8")
const HTML_SOURCE = readFileSync(
  fileURLToPath(new URL("../../index.html", import.meta.url)),
  "utf-8",
)

// bu-8cdl1.13 (Viewport and Modality Contract, slice 3): the page-gutter ramp
// and safe-area insets are only real if the tokens resolve to the right
// values, the gutter actually grows (not just "exists") at the same device
// bands the spec defines (phone default, tablet 768px, desktop 1024px), and
// the viewport meta tag actually unlocks non-zero safe-area values on
// notched phones. Assertions below extract declaration *values* through
// whitespace-tolerant patterns rather than matching literal aligned source
// text, so a pure reformat (e.g. Prettier/stylelint) can't break this suite
// without an actual behavior change.

function extractTopLevelBlock(source: string, selector: string): string {
  const startPattern = new RegExp(`^${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")} \\{$`, "m")
  const startMatch = startPattern.exec(source)
  if (!startMatch) {
    throw new Error(`Could not find "${selector} {" block in index.css`)
  }
  const bodyStart = startMatch.index + startMatch[0].length
  const closeIndex = source.indexOf("\n}", bodyStart)
  if (closeIndex === -1) {
    throw new Error(`Could not find closing "}" for ${selector}`)
  }
  return source.slice(bodyStart, closeIndex)
}

function extractMediaRootBlock(source: string, minWidthPx: number): string {
  const startPattern = new RegExp(
    `@media\\s*\\(min-width:\\s*${minWidthPx}px\\)\\s*\\{\\s*:root\\s*\\{`,
  )
  const startMatch = startPattern.exec(source)
  if (!startMatch) {
    throw new Error(`Could not find an @media (min-width: ${minWidthPx}px) { :root { block`)
  }
  const bodyStart = startMatch.index + startMatch[0].length
  const closeIndex = source.indexOf("}", bodyStart)
  if (closeIndex === -1) {
    throw new Error(`Could not find the closing "}" for the ${minWidthPx}px :root block`)
  }
  return source.slice(bodyStart, closeIndex)
}

function declaredValue(block: string, property: string): string {
  const pattern = new RegExp(`${property}:\\s*([^;]+);`)
  const match = pattern.exec(block)
  if (!match) {
    throw new Error(`Could not find a declaration for "${property}"`)
  }
  return match[1].trim()
}

function remValueOfSpaceToken(token: string): number {
  const varMatch = /^var\((--space-\d+)\)$/.exec(token)
  if (!varMatch) {
    throw new Error(`Expected a var(--space-N) reference, got "${token}"`)
  }
  const spacePattern = new RegExp(`${varMatch[1]}:\\s*([\\d.]+)rem;`)
  const match = spacePattern.exec(CSS_SOURCE)
  if (!match) {
    throw new Error(`Could not resolve ${varMatch[1]} to a rem value`)
  }
  return Number(match[1])
}

describe("page-gutter ramp tokens", () => {
  const rootBlock = extractTopLevelBlock(CSS_SOURCE, ":root")

  it("resolves every safe-area inset through env() with a zero fallback", () => {
    for (const side of ["top", "right", "bottom", "left"]) {
      expect(declaredValue(rootBlock, `--safe-area-${side}`)).toBe(
        `env(safe-area-inset-${side}, 0px)`,
      )
    }
  })

  it("grows the page gutter at the tablet (768px) and desktop (1024px) bands, never shrinks it", () => {
    const phoneX = remValueOfSpaceToken(declaredValue(rootBlock, "--page-gutter-x"))
    const phoneY = remValueOfSpaceToken(declaredValue(rootBlock, "--page-gutter-y"))

    const tabletBlock = extractMediaRootBlock(CSS_SOURCE, 768)
    const tabletX = remValueOfSpaceToken(declaredValue(tabletBlock, "--page-gutter-x"))
    const tabletY = remValueOfSpaceToken(declaredValue(tabletBlock, "--page-gutter-y"))

    const desktopBlock = extractMediaRootBlock(CSS_SOURCE, 1024)
    const desktopX = remValueOfSpaceToken(declaredValue(desktopBlock, "--page-gutter-x"))
    const desktopY = remValueOfSpaceToken(declaredValue(desktopBlock, "--page-gutter-y"))

    expect(tabletX).toBeGreaterThan(phoneX)
    expect(tabletY).toBeGreaterThan(phoneY)
    expect(desktopX).toBeGreaterThanOrEqual(tabletX)
    expect(desktopY).toBeGreaterThanOrEqual(tabletY)
  })

  it("uses dvh instead of a fixed 100vh for the body's minimum height", () => {
    const bodyBlock = extractTopLevelBlock(CSS_SOURCE, "body")
    expect(declaredValue(bodyBlock, "min-height")).toBe("100dvh")
  })
})

describe("safe-area viewport meta", () => {
  it("opts into viewport-fit=cover so env(safe-area-inset-*) is non-zero on notched phones", () => {
    const viewportMeta = /<meta\s+name="viewport"\s+content="([^"]*)"\s*\/?>/.exec(HTML_SOURCE)
    if (!viewportMeta) {
      throw new Error("Could not find the viewport meta tag in index.html")
    }
    const directives = viewportMeta[1].split(",").map((part) => part.trim())
    expect(directives).toContain("viewport-fit=cover")
  })
})
