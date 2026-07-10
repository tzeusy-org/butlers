// ---------------------------------------------------------------------------
// contrast.test.ts — token-contrast unit test (bu-86c4c.16)
//
// JARVIS audit move 11 (cross:accessibility, critical finding): "color
// contrast is verified nowhere (and measurably fails in the light theme)."
// This test reads the REAL `--token: oklch(...)` literals straight out of
// src/index.css (not a hardcoded mirror that could drift from the source of
// truth) and asserts every text-color token clears WCAG AA (4.5:1 normal
// text — the dashboard's dominant type is 9-11px mono, so the 3:1
// large-text exemption never applies) against both surface backgrounds it
// is actually painted on (--bg and --bg-elev), in both themes.
//
// Verified failures at bead-open time (documented so a future regression is
// obvious even if this file's math changes):
//   light --amber vs --bg = 2.01:1   (fixed here: minted --amber-text,
//                                      the readable variant for text sites;
//                                      base --amber is untouched — it is
//                                      also a fill/border token used far
//                                      more broadly than as text, and does
//                                      not need to carry the AA floor there)
//   light --dim   vs --bg = 3.49:1   (fixed here: light --dim retuned)
//   dark  --dim   vs --bg = 4.08:1   (fixed here: dark --dim retuned)
//
// --red is ALSO below AA as text (light --red vs --bg = 3.84:1) and is used
// as text at ~57 call sites across the app (vs. --amber's ~10) — out of
// scope for this bead's diff size; tracked as an explicit follow-up rather
// than silently left unfixed (see the "known gaps" describe block below).
// ---------------------------------------------------------------------------

import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { describe, expect, it } from "vitest"

import { contrastRatio, relativeLuminance, WCAG_AA_NORMAL_TEXT, type Oklch } from "./contrast"

const CSS_PATH = fileURLToPath(new URL("../index.css", import.meta.url))
const CSS_SOURCE = readFileSync(CSS_PATH, "utf-8")

// ---------------------------------------------------------------------------
// Extract the `:root { ... }` (light) and `.dark { ... }` blocks verbatim,
// then parse every `--token: oklch(L C H);` declaration within each.
// ---------------------------------------------------------------------------

function extractTopLevelBlock(source: string, selector: string): string {
  const startPattern = new RegExp(`^${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")} \\{$`, "m")
  const startMatch = startPattern.exec(source)
  if (!startMatch) {
    throw new Error(`Could not find "${selector} {" block in index.css`)
  }
  const bodyStart = startMatch.index + startMatch[0].length
  const closeIndex = source.indexOf("\n}", bodyStart)
  if (closeIndex === -1) {
    throw new Error(`Could not find closing "}" for "${selector}" block in index.css`)
  }
  return source.slice(bodyStart, closeIndex)
}

function parseOklchTokens(block: string): Map<string, Oklch> {
  const tokens = new Map<string, Oklch>()
  // Matches `--name: oklch(L C H);` — plain 3-component literals only (the
  // alpha-channel tokens like --border-soft aren't text colors and are out
  // of scope for this test).
  const pattern = /--([a-z][a-z0-9-]*):\s*oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\)/gi
  let match: RegExpExecArray | null
  while ((match = pattern.exec(block)) !== null) {
    const [, name, l, c, h] = match
    // First declaration wins (later duplicate names in the same block would
    // be a bug worth catching, not silently overwriting).
    if (!tokens.has(name)) {
      tokens.set(name, { l: Number(l), c: Number(c), h: Number(h) })
    }
  }
  return tokens
}

const LIGHT_TOKENS = parseOklchTokens(extractTopLevelBlock(CSS_SOURCE, ":root"))
const DARK_TOKENS = parseOklchTokens(extractTopLevelBlock(CSS_SOURCE, ".dark"))

function requireToken(tokens: Map<string, Oklch>, name: string): Oklch {
  const value = tokens.get(name)
  if (!value) throw new Error(`Token --${name} not found`)
  return value
}

// ---------------------------------------------------------------------------
// Math sanity — verify the oklch->sRGB->WCAG pipeline against known values
// before trusting it to grade the real tokens.
// ---------------------------------------------------------------------------

describe("contrast: math sanity", () => {
  it("black vs white is the maximum 21:1 ratio", () => {
    const black: Oklch = { l: 0, c: 0, h: 0 }
    const white: Oklch = { l: 1, c: 0, h: 0 }
    expect(contrastRatio(black, white)).toBeCloseTo(21, 0)
  })

  it("a color against itself is always 1:1", () => {
    const amber: Oklch = { l: 0.769, c: 0.189, h: 84 }
    expect(contrastRatio(amber, amber)).toBeCloseTo(1, 5)
  })

  it("relative luminance of white is 1 and black is 0", () => {
    expect(relativeLuminance({ l: 1, c: 0, h: 0 })).toBeCloseTo(1, 2)
    expect(relativeLuminance({ l: 0, c: 0, h: 0 })).toBeCloseTo(0, 2)
  })

  it("contrast ratio is symmetric regardless of argument order", () => {
    const a: Oklch = { l: 0.769, c: 0.189, h: 84 }
    const b: Oklch = { l: 0.985, c: 0.003, h: 85 }
    expect(contrastRatio(a, b)).toBeCloseTo(contrastRatio(b, a), 10)
  })
})

// ---------------------------------------------------------------------------
// Real tokens — both themes, both surface backgrounds the tokens are
// actually painted against.
// ---------------------------------------------------------------------------

// NOTE: "amber" is deliberately excluded — it is a fill/border token used
// far more broadly than as text (badges, dots, outlines) and is not held to
// the text AA floor. Text sites use "amber-text" instead (checked below).
// "red" is also excluded — like amber it is a fill/border token; text sites
// use "red-text" instead (bu-f310e, checked below).
const TEXT_TOKENS = ["dim", "amber-text", "red-text", "fg", "mfg", "green"]
const BG_TOKENS = ["bg", "bg-elev"]

describe.each(["light", "dark"] as const)("contrast: %s theme text tokens vs surface backgrounds", (theme) => {
  const tokens = theme === "light" ? LIGHT_TOKENS : DARK_TOKENS

  it.each(TEXT_TOKENS)("--%s clears WCAG AA (4.5:1) against every surface bg", (textName) => {
    const text = requireToken(tokens, textName)
    for (const bgName of BG_TOKENS) {
      const bg = requireToken(tokens, bgName)
      const ratio = contrastRatio(text, bg)
      expect(
        ratio,
        `--${textName} (${theme}) vs --${bgName} = ${ratio.toFixed(2)}:1, below the ${WCAG_AA_NORMAL_TEXT}:1 AA floor`,
      ).toBeGreaterThanOrEqual(WCAG_AA_NORMAL_TEXT)
    }
  })
})

// ---------------------------------------------------------------------------
// Regression pins — the exact failures the audit found must never come back.
// ---------------------------------------------------------------------------

describe("contrast: regression pins for the audit's verified failures", () => {
  it("light --amber-text vs --bg is no longer ~2.01:1 (base --amber's old ratio)", () => {
    const ratio = contrastRatio(requireToken(LIGHT_TOKENS, "amber-text"), requireToken(LIGHT_TOKENS, "bg"))
    expect(ratio).toBeGreaterThanOrEqual(WCAG_AA_NORMAL_TEXT)
  })

  it("light --dim vs --bg is no longer ~3.49:1", () => {
    const ratio = contrastRatio(requireToken(LIGHT_TOKENS, "dim"), requireToken(LIGHT_TOKENS, "bg"))
    expect(ratio).toBeGreaterThanOrEqual(WCAG_AA_NORMAL_TEXT)
  })

  it("dark --dim vs --bg is no longer ~4.08:1", () => {
    const ratio = contrastRatio(requireToken(DARK_TOKENS, "dim"), requireToken(DARK_TOKENS, "bg"))
    expect(ratio).toBeGreaterThanOrEqual(WCAG_AA_NORMAL_TEXT)
  })

  it("light --red-text vs --bg is no longer ~3.84:1 (base --red's old text ratio)", () => {
    // bu-f310e: minted --red-text (the readable variant for text sites) and
    // repointed every text-[var(--red)] site to it; base --red stays bright
    // for fills/borders. This pins the fix so the 3.84:1 gap can't silently
    // return.
    const ratio = contrastRatio(requireToken(LIGHT_TOKENS, "red-text"), requireToken(LIGHT_TOKENS, "bg"))
    expect(ratio).toBeGreaterThanOrEqual(WCAG_AA_NORMAL_TEXT)
  })
})

// ---------------------------------------------------------------------------
// Known gaps — documented, not silently passing. Do not delete these without
// also fixing the underlying token; they exist so "the contrast test is
// green" never gets read as "every token is AA-safe."
// ---------------------------------------------------------------------------

describe("contrast: fill/border tokens intentionally below the text AA floor", () => {
  // These tokens are NOT text colors — they paint fills, borders, badges, and
  // rail accents where the 4.5:1 normal-text floor does not apply. Their
  // text-safe siblings (--amber-text, --red-text) carry the AA guarantee and
  // are graded above. This block documents the deliberate split so "base
  // --red is below 4.5:1" is never misread as an unfixed regression.
  it("light --red (fill token) sits below the text AA floor by design (~3.84:1); text sites use --red-text", () => {
    const ratio = contrastRatio(requireToken(LIGHT_TOKENS, "red"), requireToken(LIGHT_TOKENS, "bg"))
    expect(ratio).toBeLessThan(WCAG_AA_NORMAL_TEXT)
    expect(ratio).toBeGreaterThan(3.5)
  })
})
