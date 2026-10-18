// ---------------------------------------------------------------------------
// Generic-host aria-label guard (bu-h40h2b.10.1)
//
// ARIA 1.2 prohibits aria-label / aria-labelledby on elements with the generic
// role (a plain div or span) and on a few other text-level hosts: assistive
// technology ignores the label, so the code looks labelled while nothing is
// announced. jsx-a11y has no rule for an element with no implicit role, so this
// scan refuses the pattern in every dashboard component. Fix a site by giving
// the element the role it truly plays (group, region, img, status), by
// appending screen-reader-only text, or by dropping a label that repeats
// visible text. Never add a role only to silence this test.
// ---------------------------------------------------------------------------

import { readdirSync, readFileSync } from "node:fs"
import { join, relative } from "node:path"
import { fileURLToPath } from "node:url"
import ts from "typescript"
import { describe, expect, it } from "vitest"

const GENERIC_HOSTS = new Set([
  "div",
  "span",
  "p",
  "em",
  "strong",
  "i",
  "b",
  "small",
  "u",
  "s",
  "mark",
  "sub",
  "sup",
  "code",
  "blockquote",
])
const NAMING_ATTRS = new Set(["aria-label", "aria-labelledby"])
// Roles that also prohibit naming (ARIA 1.2): a literal one does not rescue the host.
const NAME_PROHIBITED_ROLES = new Set([
  "generic",
  "presentation",
  "none",
  "paragraph",
  "code",
  "emphasis",
  "strong",
  "deletion",
  "insertion",
  "subscript",
  "superscript",
  "caption",
])

// True when the host's role is absent, or a string literal that prohibits naming.
// A computed role (role={expr}) is accepted: its value is not knowable here.
function roleLeavesHostUnnamed(attrs: ts.JsxAttribute[], sf: ts.SourceFile): boolean {
  const role = attrs.find((attr) => attr.name.getText(sf) === "role")
  if (!role) return true
  let init: ts.Node | undefined = role.initializer
  if (init && ts.isJsxExpression(init)) init = init.expression
  if (init && (ts.isStringLiteral(init) || ts.isNoSubstitutionTemplateLiteral(init))) {
    return NAME_PROHIBITED_ROLES.has(init.text.trim().toLowerCase())
  }
  return false
}

function findUnnamedHostLabels(source: string, fileName = "x.tsx"): number[] {
  const sf = ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const lines: number[] = []
  const visit = (node: ts.Node) => {
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      if (GENERIC_HOSTS.has(node.tagName.getText(sf))) {
        const attrs = node.attributes.properties.filter(ts.isJsxAttribute)
        const names = attrs.map((attr) => attr.name.getText(sf))
        if (names.some((name) => NAMING_ATTRS.has(name)) && roleLeavesHostUnnamed(attrs, sf)) {
          lines.push(sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1)
        }
      }
    }
    ts.forEachChild(node, visit)
  }
  visit(sf)
  return lines
}

// Frozen and shrink-only: each entry is a file that another in-flight branch is
// editing, so its sites are left for that branch's author or a follow-up. Lower
// a count (or delete the entry) as sites are fixed; never raise one.
const IN_FLIGHT_ALLOWLIST: Record<string, { count: number; reason: string }> = {
  "components/butler-detail/ButlerEducationReviewsTab.tsx": { count: 1, reason: "file in flight on bu-rj3en4" },
  "components/layout/EntityFinder.tsx": { count: 1, reason: "file in flight on bu-2jtfw.16, bu-ip5c5, pr-4671" },
  "components/layout/Sidebar.tsx": { count: 3, reason: "file in flight on bu-2jtfw.16" },
  "components/ingestion/filters/RuleRow.tsx": { count: 1, reason: "file in flight on bu-q7vx1q.88.3" },
  "components/relationship/FactReporterLine.tsx": { count: 2, reason: "file in flight on bu-a8ms3m" },
  "components/relationship/IdentityCandidateReview.tsx": { count: 1, reason: "file in flight on bu-a8ms3m" },
  "components/ui/Provenance.tsx": { count: 3, reason: "file in flight on bu-a8ms3m" },
  "pages/ChroniclesPage.tsx": { count: 2, reason: "file in flight on bu-jgu0hn, bu-s11n0s.7" },
  "components/qa/CaseDossierHeader.tsx": { count: 1, reason: "file in flight on bu-ly3lv5.9" },
  "components/workspace/TimeWindowPicker.tsx": { count: 1, reason: "file in flight on bu-ly3lv5.9" },
  "pages/SpendPage.tsx": { count: 1, reason: "file in flight on bu-ly3lv5.9, bu-s11n0s.6" },
}

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) sourceFiles(path, out)
    else if (path.endsWith(".tsx") && !/\.test\.tsx$/.test(path)) out.push(path)
  }
  return out
}

describe("generic hosts never carry aria-label or aria-labelledby", () => {
  it("refuses a bare div or span", () => {
    expect(findUnnamedHostLabels('const a = <div aria-label="x" />')).toEqual([1])
    expect(findUnnamedHostLabels('const a = <span aria-labelledby="h">t</span>')).toEqual([1])
    expect(findUnnamedHostLabels('const a = (\n  <p\n    aria-label={x}\n  />\n)')).toEqual([2])
  })

  it("refuses a literal role that also prohibits naming", () => {
    for (const role of ["presentation", "none", "generic", "paragraph", "code", "caption"]) {
      expect(findUnnamedHostLabels(`const a = <div role="${role}" aria-label="x" />`)).toEqual([1])
    }
    expect(findUnnamedHostLabels('const a = <div role={"none"} aria-label="x" />')).toEqual([1])
  })

  it("accepts a host with an explicit role, an interactive host, or no label", () => {
    expect(findUnnamedHostLabels('const a = <div role="group" aria-label="x" />')).toEqual([])
    expect(findUnnamedHostLabels('const a = <div role={role} aria-label="x" />')).toEqual([])
    expect(findUnnamedHostLabels('const a = <section aria-label="x" />')).toEqual([])
    expect(findUnnamedHostLabels('const a = <button aria-label="x" />')).toEqual([])
    expect(findUnnamedHostLabels('const a = <div className="x">t</div>')).toEqual([])
  })

  it("has no unlisted site in src, and every allowlist entry is exact", () => {
    const srcRoot = fileURLToPath(new URL("../../", import.meta.url))
    const actual: Record<string, number> = {}
    for (const file of sourceFiles(srcRoot)) {
      const hits = findUnnamedHostLabels(readFileSync(file, "utf-8"), file)
      if (hits.length) actual[relative(srcRoot, file)] = hits.length
    }
    const allowed: Record<string, number> = {}
    for (const [file, entry] of Object.entries(IN_FLIGHT_ALLOWLIST)) allowed[file] = entry.count
    // A new site fails here; a fixed site fails too until its entry is lowered or removed.
    expect(actual).toEqual(allowed)
    // Shrink-only, mechanically: adding a site and bumping its count in one diff fails here.
    // Lower this number as entries shrink; never raise it.
    expect(Object.values(IN_FLIGHT_ALLOWLIST).reduce((n, entry) => n + entry.count, 0)).toBeLessThanOrEqual(17)
  })
})
