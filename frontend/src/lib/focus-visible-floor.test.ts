import path from "node:path"
import { fileURLToPath } from "node:url"

import tailwindcss from "@tailwindcss/vite"
import { JSDOM, VirtualConsole } from "jsdom"
import { build } from "vite"
import { beforeAll, describe, expect, it } from "vitest"

const frontendRoot = fileURLToPath(new URL("../..", import.meta.url))
const cssPath = path.join(frontendRoot, "src/index.css")

const primitiveHosts = {
  button: "button",
  input: "input",
  select: "button",
  textarea: "textarea",
  tabs: "button",
  checkbox: "button",
  switch: "button",
} as const

async function buildGeneratedCss(): Promise<string> {
  const result = await build({
    root: frontendRoot,
    configFile: false,
    logLevel: "silent",
    plugins: [tailwindcss()],
    build: {
      write: false,
      emptyOutDir: false,
      rollupOptions: { input: cssPath },
    },
  })
  if (!Array.isArray(result) && "on" in result) {
    throw new Error("Vite unexpectedly returned a watch-mode build")
  }
  const bundles = Array.isArray(result) ? result : [result]
  const outputs = bundles.flatMap((bundle) => bundle.output)
  const cssAsset = outputs.find(
    (output) => output.type === "asset" && output.fileName.endsWith(".css"),
  )
  if (!cssAsset || cssAsset.type !== "asset") {
    throw new Error("Vite did not emit the generated Tailwind stylesheet")
  }
  return typeof cssAsset.source === "string"
    ? cssAsset.source
    : Buffer.from(cssAsset.source).toString("utf-8")
}

function outlineRulesFrom(css: string): string {
  const virtualConsole = new VirtualConsole()
  const dom = new JSDOM(`<style>${css}</style>`, { virtualConsole })
  const outlineRules: string[] = []

  function visit(rules: CSSRuleList): void {
    for (const rule of rules) {
      if (rule.type === dom.window.CSSRule.STYLE_RULE) {
        const styleRule = rule as CSSStyleRule
        const properties = Array.from(styleRule.style)
        if (properties.some((property) => property.startsWith("outline"))) {
          outlineRules.push(styleRule.cssText)
        }
        continue
      }
      if ("cssRules" in rule) {
        visit((rule as CSSGroupingRule).cssRules)
      }
    }
  }

  for (const sheet of dom.window.document.styleSheets) {
    visit(sheet.cssRules)
  }
  return outlineRules.join("\n")
}

function computedFocusOutline(css: string, host: string, classes: string[]) {
  const virtualConsole = new VirtualConsole()
  const targetMarkup =
    host === "summary"
      ? "<details><summary>Focus target</summary></details>"
      : `<${host}>Focus target</${host}>`
  const dom = new JSDOM(`<style>${css}</style>${targetMarkup}`, {
    pretendToBeVisual: true,
    virtualConsole,
  })
  const target = dom.window.document.querySelector<HTMLElement>(host)
  if (!target) throw new Error(`Could not create focus target <${host}>`)
  target.className = classes.join(" ")
  target.focus()
  return dom.window.getComputedStyle(target)
}

function generatedRuleContaining(css: string, fragment: string): { selector: string; body: string } {
  const fragmentIndex = css.indexOf(fragment)
  if (fragmentIndex === -1) throw new Error(`Generated CSS did not contain ${fragment}`)
  const ruleStart = css.lastIndexOf("}", fragmentIndex) + 1
  const bodyStart = css.indexOf("{", fragmentIndex)
  const ruleEnd = css.indexOf("}", bodyStart)
  if (bodyStart === -1 || ruleEnd === -1) throw new Error(`Could not parse generated rule for ${fragment}`)
  return {
    selector: css.slice(ruleStart, bodyStart),
    body: css.slice(bodyStart + 1, ruleEnd),
  }
}

describe("global focus-visible floor", () => {
  let generatedCss: string
  let generatedOutlineCss: string
  const outlineReset = "outline-" + "none"
  const focusVisibleOutlineReset = "focus-visible:outline-" + "none"

  beforeAll(async () => {
    generatedCss = await buildGeneratedCss()
    generatedOutlineCss = outlineRulesFrom(generatedCss)
  })

  it("keeps the emitted two-pixel focus boundary above every primitive outline utility", () => {
    for (const [name, host] of Object.entries(primitiveHosts)) {
      const style = computedFocusOutline(
        generatedOutlineCss,
        host,
        [outlineReset, focusVisibleOutlineReset, "focus-visible:outline-1"],
      )
      expect(style.outline, `${name} focus outline`).toBe("2px solid var(--focus)")
      expect(style.outlineOffset, `${name} focus outline offset`).toBe("2px")
    }

    const summaryStyle = computedFocusOutline(generatedOutlineCss, "summary", [
      outlineReset,
      focusVisibleOutlineReset,
      "focus-visible:outline-1",
    ])
    expect(summaryStyle.outline, "summary focus outline").toBe("2px solid var(--focus)")
    expect(summaryStyle.outlineOffset, "summary focus outline offset").toBe("2px")
  })

  it("emits an important two-pixel offset above the existing negative utility", () => {
    const floor = generatedRuleContaining(generatedCss, ":focus-visible:focus-visible")
    const negativeUtility = generatedRuleContaining(
      generatedCss,
      ".focus-visible\\:outline-offset-\\[-2px\\]:focus-visible",
    )

    expect(floor.selector).toContain("summary")
    expect(floor.body).toContain("outline-offset:2px!important")
    expect(negativeUtility.body).toBe("outline-offset:-2px")
  })
})
