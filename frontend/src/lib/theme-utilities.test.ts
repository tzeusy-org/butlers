// @vitest-environment jsdom

import { existsSync, readFileSync, readdirSync } from "node:fs"
import { extname, relative, resolve } from "node:path"
import { afterEach, describe, expect, it } from "vitest"

const FRONTEND_DIR = process.cwd()
const SRC_DIR = resolve(FRONTEND_DIR, "src")
const CSS_SOURCE = readFileSync(resolve(SRC_DIR, "index.css"), "utf-8")
const HTML_SOURCE = readFileSync(resolve(FRONTEND_DIR, "index.html"), "utf-8")
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

describe("first-frame identity", () => {
  afterEach(() => {
    window.localStorage.clear()
    document.documentElement.className = ""
  })

  it("uses local identity assets and a real document title", () => {
    expect(HTML_SOURCE).not.toMatch(/https?:\/\//)
    expect(HTML_SOURCE).not.toContain("fonts.googleapis.com")
    expect(HTML_SOURCE).not.toContain("fonts.gstatic.com")

    const title = /<title>([^<]+)<\/title>/.exec(HTML_SOURCE)?.[1]
    expect(title).toBe("Butlers Dispatch")

    const faviconPath = /<link\s+rel="icon"[^>]+href="([^"]+)"/.exec(HTML_SOURCE)?.[1]
    expect(faviconPath).toBeTruthy()
    expect(existsSync(resolve(FRONTEND_DIR, "public", faviconPath!.replace(/^\//, "")))).toBe(true)
  })

  it.each(["dark", "light"] as const)(
    "stamps the stored %s theme before the app module runs",
    (theme) => {
      const themeScript = /<script>([\s\S]*?)<\/script>/.exec(HTML_SOURCE)?.[1]
      if (!themeScript) throw new Error("Could not find the inline theme script in index.html")
      expect(HTML_SOURCE.indexOf(themeScript)).toBeLessThan(HTML_SOURCE.indexOf('type="module"'))

      window.localStorage.setItem("theme", theme)
      Function(themeScript)()

      expect(document.documentElement.classList.contains(theme)).toBe(true)
      expect(
        document.documentElement.classList.contains(theme === "dark" ? "light" : "dark"),
      ).toBe(false)
    },
  )

  it("loads every declared face from a vendored WOFF2 file", () => {
    const fontUrls = [...CSS_SOURCE.matchAll(/src:\s*url\(["']?(\/fonts\/[^"')]+\.woff2)["']?\)/g)]
      .map((match) => match[1])

    expect(fontUrls).toHaveLength(8)
    expect(new Set(fontUrls).size).toBe(fontUrls.length)
    for (const fontUrl of fontUrls) {
      expect(existsSync(resolve(FRONTEND_DIR, "public", fontUrl.replace(/^\//, "")))).toBe(true)
    }
  })
})
