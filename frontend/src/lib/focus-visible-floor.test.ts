import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"

import { describe, expect, it } from "vitest"

const cssSource = readFileSync(fileURLToPath(new URL("../index.css", import.meta.url)), "utf-8")

describe("global focus-visible floor", () => {
  it("gives every native or role-based control a visible outline", () => {
    expect(cssSource).toContain(":where(a, button, [role='button'], [tabindex]):focus-visible {")
    expect(cssSource).toContain("outline: 2px solid var(--fg);")
    expect(cssSource).toContain("outline-offset: 2px;")
  })

  it("uses the design-language foreground token rather than the low-contrast ring token", () => {
    const focusRule = cssSource.match(
      /:where\(a, button, \[role='button'], \[tabindex]\):focus-visible \{([^}]*)\}/,
    )?.[1]

    expect(focusRule).toBeDefined()
    expect(focusRule).toContain("var(--fg)")
    expect(focusRule).not.toContain("var(--ring)")
  })

  it("does not rely on a color-only global outline declaration", () => {
    expect(cssSource).not.toMatch(/\*\s*\{\s*@apply[^}]*outline-ring\/50/)
  })
})
