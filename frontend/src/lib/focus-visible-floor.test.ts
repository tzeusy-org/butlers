import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"

import { describe, expect, it } from "vitest"

const cssSource = readFileSync(fileURLToPath(new URL("../index.css", import.meta.url)), "utf-8")
const focusSelector = ":is(a, button, input, select, textarea, [role='button'], [tabindex]):focus-visible"

const primitiveHosts = {
  button: "button",
  input: "input",
  select: "button",
  textarea: "textarea",
  tabs: "button",
  checkbox: "button",
  switch: "button",
} as const

function primitiveSource(name: keyof typeof primitiveHosts): string {
  return readFileSync(fileURLToPath(new URL(`../components/ui/${name}.tsx`, import.meta.url)), "utf-8")
}

describe("global focus-visible floor", () => {
  const focusRule = cssSource.match(
    /:is\(a, button, input, select, textarea, \[role='button'], \[tabindex]\):focus-visible \{([^}]*)\}/,
  )?.[1]

  it("uses the measured focus token for a two-pixel keyboard boundary", () => {
    expect(focusRule).toBeDefined()
    expect(focusRule).toContain("outline: 2px solid var(--focus) !important;")
    expect(cssSource).toContain("outline-offset: 2px;")
    expect(focusRule).not.toContain("var(--ring)")
  })

  it("has greater specificity than the outline reset and cannot be reset by a normal utility declaration", () => {
    // :is() adopts the most-specific argument ([role] / [tabindex]), then
    // :focus-visible adds a second pseudo-class: (0,2,0) vs (0,1,0).
    const floorSpecificity = 200
    const outlineNoneSpecificity = 100
    expect(focusSelector.startsWith(":is(")).toBe(true)
    expect(floorSpecificity).toBeGreaterThan(outlineNoneSpecificity)
    expect(focusRule).toContain("!important")
  })

  it("covers all seven UI primitives and keeps their local affordances on --focus", () => {
    for (const [name, host] of Object.entries(primitiveHosts)) {
      const source = primitiveSource(name as keyof typeof primitiveHosts)
      expect(focusSelector).toContain(host)
      expect(source).toContain("ring-focus")
      expect(source).not.toContain("ring-" + "ring")
    }
  })
})
