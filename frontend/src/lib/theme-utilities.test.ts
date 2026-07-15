import { readFileSync, readdirSync } from "node:fs"
import { extname, relative } from "node:path"
import { fileURLToPath } from "node:url"
import { describe, expect, it } from "vitest"

const SRC_DIR = fileURLToPath(new URL("..", import.meta.url))
const CSS_SOURCE = readFileSync(fileURLToPath(new URL("../index.css", import.meta.url)), "utf-8")
const ARBITRARY_SURFACE_TOKEN_CLASS =
  /\b[a-z-]+-\[var\(--(?:fg|bg)\)\](?:\/[\w.[\]-]+)?/g

function extractTopLevelBlock(source: string, selector: string): string {
  const startPattern = new RegExp(`^${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")} \\{$`, "m")
  const startMatch = startPattern.exec(source)
  if (!startMatch) throw new Error(`Could not find "${selector} {" block in index.css`)
  const bodyStart = startMatch.index + startMatch[0].length
  const closeIndex = source.indexOf("\n}", bodyStart)
  if (closeIndex === -1) throw new Error(`Could not find closing "}" for "${selector}" block`)
  return source.slice(bodyStart, closeIndex)
}

function sourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = `${directory}/${entry.name}`
    if (entry.isDirectory()) return sourceFiles(path)
    if (relative(SRC_DIR, path) === "lib/theme-utilities.test.ts") return []
    return [".ts", ".tsx"].includes(extname(entry.name)) ? [path] : []
  })
}

describe("Dispatch Tailwind theme utilities", () => {
  it("maps the canonical foreground and page background tokens", () => {
    const theme = extractTopLevelBlock(CSS_SOURCE, "@theme inline")

    expect(theme).toMatch(/--color-fg:\s*var\(--fg\);/)
    expect(theme).toMatch(/--color-bg:\s*var\(--bg\);/)
  })

  it("uses canonical utilities instead of exact arbitrary fg/bg token classes", () => {
    const violations = sourceFiles(SRC_DIR).flatMap((path) => {
      const matches = readFileSync(path, "utf-8").match(ARBITRARY_SURFACE_TOKEN_CLASS) ?? []
      return matches.map((match) => `${relative(SRC_DIR, path)}: ${match}`)
    })

    expect(violations, violations.join("\n")).toHaveLength(0)
  })

  it.each([
    "fill-[var(--fg)]",
    "stroke-[var(--bg)]/40",
    "hover:border-l-[var(--fg)]",
    "focus:ring-offset-[var(--bg)]/[0.15]",
    "from-[var(--fg)]",
  ])("detects every Tailwind color utility family: %s", (className) => {
    expect(className.match(ARBITRARY_SURFACE_TOKEN_CLASS)).toEqual([className.replace(/^[a-z]+:/, "")])
  })

  it.each([
    "color: var(--fg)",
    'style={{ background: "var(--bg)" }}',
    "text-[var(--fg,oklch(0.985_0_0))]",
    "text-[var(--mfg)]",
  ])("allows non-retired token syntax: %s", (source) => {
    expect(source.match(ARBITRARY_SURFACE_TOKEN_CLASS)).toBeNull()
  })
})
