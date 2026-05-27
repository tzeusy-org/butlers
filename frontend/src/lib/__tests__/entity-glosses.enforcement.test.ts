/// <reference types="node" />
/**
 * entity-glosses.enforcement.test.ts — Frontend canned-gloss LLM-prohibition guardrail
 *
 * Resolves v1 Open Question 23: "Can we statically guarantee that the entity-gloss
 * frontend path never makes an LLM call?"  Answer: yes — this test enforces it.
 *
 * Mirrors the backend guardrail at:
 *   roster/relationship/tests/test_finder_no_llm_transitive.py (PR #1789, bu-wqmck)
 *
 * Banned patterns (v1 Brief Amendment 15)
 * ----------------------------------------
 * 1. Banned LLM SDK imports:
 *    - from 'anthropic' / from "anthropic"
 *    - from 'openai' / from "openai"
 *    - from 'cohere' / from "cohere"
 *    - from '@anthropic-ai/sdk' / from "@anthropic-ai/sdk"
 *
 * 2. Suspicious external fetch/axios calls:
 *    Any fetch() or axios() call (including .get/.post variants) whose URL
 *    argument is NOT a relative path starting with /api/.
 *    Allowed: fetch('/api/...'), fetch(`/api/...`)
 *    Rejected: fetch('https://...'), fetch('http://...'), axios(externalUrl),
 *              any axios call (this path must not use LLM APIs at all)
 *
 * 3. Template-literal arguments to LLM-completion-style functions:
 *    Any call to a function named complete(), chat(), or generate() where
 *    the argument begins with a template literal (backtick).
 *    Practical scope: flags `someFn.complete(` or bare `complete(` followed
 *    by a backtick argument.
 *
 * Rationale
 * ---------
 * The $0/user/day cost constraint depends on NEVER calling Anthropic/OpenAI/Cohere
 * from the entity-gloss frontend path. Glosses are canned strings — deterministic,
 * not AI-generated. Section 0 ("composure is the brand") makes LLM calls here a
 * contract violation, not just a performance concern.
 *
 * Scan scope
 * ----------
 * Files scanned (static string reads, not executed):
 *   - frontend/src/pages/EntityDetailPage.tsx
 *   - frontend/src/lib/entity-glosses.ts
 *   - (frontend/src/components/relationship/EntityDetailView.tsx was removed in a
 *     refactor; EntityDetailPage.tsx is the sole consumer of entity-glosses. The
 *     effective surface is two files.)
 *
 * If a scanned file is renamed or deleted, the test fails with a "file missing"
 * error — intentionally, so that coverage does not silently collapse.
 *
 * [bu-0855u]
 */

import { describe, expect, it } from "vitest"
import fs from "node:fs"
import path from "node:path"

// ---------------------------------------------------------------------------
// Repo root detection — resolve from this file upward to find the frontend/src
// directory boundary, then express scan targets as absolute paths.
// ---------------------------------------------------------------------------

// __dirname is: frontend/src/lib/__tests__
const FRONTEND_SRC = path.resolve(__dirname, "..", "..")  // frontend/src/
const FRONTEND_ROOT = path.resolve(FRONTEND_SRC, "..")    // frontend/

// ---------------------------------------------------------------------------
// Scan targets
//
// Note: EntityDetailView does not exist as a separate file in this layout.
// EntityDetailPage.tsx is the sole top-level consumer of entity-glosses.ts.
// That's the complete frontend surface for the gloss path.
// ---------------------------------------------------------------------------

interface ScanTarget {
  /** Short identifier used in error messages. */
  label: string
  /** Absolute filesystem path. */
  absolutePath: string
  /** Whether the test should hard-fail if the file is not found. */
  required: boolean
}

const SCAN_TARGETS: ScanTarget[] = [
  {
    label: "entity-glosses.ts",
    absolutePath: path.join(FRONTEND_SRC, "lib", "entity-glosses.ts"),
    required: true,
  },
  {
    label: "EntityDetailPage.tsx",
    absolutePath: path.join(FRONTEND_SRC, "pages", "EntityDetailPage.tsx"),
    required: true,
  },
  // EntityDetailView.tsx was removed in a layout refactor; EntityDetailPage is
  // the sole consumer. If this file reappears under a new name that renders
  // glosses, add it here.
]

// ---------------------------------------------------------------------------
// Banned pattern definitions
// ---------------------------------------------------------------------------

/** Banned LLM SDK import substrings (check both quote styles). */
const BANNED_IMPORT_STRINGS: string[] = [
  "from 'anthropic'",
  'from "anthropic"',
  "from 'openai'",
  'from "openai"',
  "from 'cohere'",
  'from "cohere"',
  "from '@anthropic-ai/sdk'",
  'from "@anthropic-ai/sdk"',
]

/**
 * Matches fetch() or axios() calls with non-/api/ URL arguments.
 *
 * Allows:
 *   fetch('/api/...')  fetch(`/api/...`)
 *
 * Flags:
 *   fetch('https://...')  fetch('http://...')  fetch(someVar)
 *   axios(...)  axios.get(...)  axios.post(...)  etc.
 *
 * Strategy: find every fetch( or axios( occurrence; if immediately followed
 * by a string/template literal that does NOT start with /api/, flag it.
 * Also flag every bare axios call regardless (LLM proxy calls would go here).
 */
const SUSPICIOUS_FETCH_PATTERN = /\b(fetch|axios(?:\.\w+)?)\s*\(\s*(['"`])((?:(?!\2).)*)/g

/**
 * Template-literal argument to LLM-completion-style functions.
 *
 * Matches:  something.complete(`  or  complete(`
 *           something.chat(`      or  chat(`
 *           something.generate(`  or  generate(`
 *
 * Does NOT match: setLoadingState(complete)  or  isComplete(
 * because we require `(` immediately after the function name followed by `.
 */
const COMPLETION_TEMPLATE_LITERAL_PATTERN = /(?:^|[.\s(,;])(?:complete|chat|generate)\s*\(\s*`/m

// ---------------------------------------------------------------------------
// Violation collector
// ---------------------------------------------------------------------------

interface Violation {
  file: string
  line: number
  column: number
  pattern: string
  snippet: string
}

function collectViolations(label: string, content: string): Violation[] {
  const violations: Violation[] = []
  const lines = content.split("\n")

  // Helper: get line + column from a match index
  function indexToLineCol(index: number): { line: number; column: number } {
    const before = content.slice(0, index)
    const line = before.split("\n").length
    const lastNl = before.lastIndexOf("\n")
    const column = index - lastNl
    return { line, column }
  }

  // --- Check 1: Banned LLM SDK imports ---
  for (const banned of BANNED_IMPORT_STRINGS) {
    let searchFrom = 0
    while (true) {
      const idx = content.indexOf(banned, searchFrom)
      if (idx === -1) break
      const pos = indexToLineCol(idx)
      violations.push({
        file: label,
        line: pos.line,
        column: pos.column,
        pattern: "banned-llm-import",
        snippet: lines[pos.line - 1]?.trim() ?? banned,
      })
      searchFrom = idx + banned.length
    }
  }

  // --- Check 2: Suspicious fetch/axios calls ---
  {
    const re = new RegExp(SUSPICIOUS_FETCH_PATTERN.source, "g")
    let m: RegExpExecArray | null
    while ((m = re.exec(content)) !== null) {
      const funcName = m[1] as string   // fetch or axios...
      const quote = m[2] as string      // ' " or `
      const urlStart = m[3] as string   // start of URL argument

      // Allowed: relative paths starting with /api/
      const isRelativeApi = urlStart.startsWith("/api/")

      // Allowed: fetch with relative /api/ prefix
      if (funcName === "fetch" && isRelativeApi) continue

      // Flag everything else — external URLs, variable refs, all axios calls
      const pos = indexToLineCol(m.index)
      const lineText = lines[pos.line - 1]?.trim() ?? m[0]

      // Determine the specific reason
      let reason: string
      if (funcName.startsWith("axios")) {
        reason = "banned-axios-call (LLM proxy risk)"
      } else if (urlStart.startsWith("https://") || urlStart.startsWith("http://")) {
        reason = "fetch-external-url"
      } else if (quote === "`" && !isRelativeApi) {
        reason = "fetch-non-api-template-literal"
      } else {
        reason = "fetch-non-api-url"
      }

      violations.push({
        file: label,
        line: pos.line,
        column: pos.column,
        pattern: reason,
        snippet: lineText,
      })
    }
  }

  // --- Check 3: Template-literal passed to completion/chat/generate ---
  {
    // Walk line by line for readable line numbers
    for (let i = 0; i < lines.length; i++) {
      const lineText = lines[i] ?? ""
      // Use a per-line match — the flag pattern is multiline but the critical
      // part (function name + backtick) fits on one line in practice.
      if (/(?:^|[.\s(,;])(?:complete|chat|generate)\s*\(\s*`/.test(lineText)) {
        violations.push({
          file: label,
          line: i + 1,
          column: 1,
          pattern: "completion-template-literal",
          snippet: lineText.trim(),
        })
      }
    }
  }

  return violations
}

// ---------------------------------------------------------------------------
// Format violations for readable test failure messages
// ---------------------------------------------------------------------------

function formatViolations(violations: Violation[]): string {
  return violations
    .map(
      (v) =>
        `  [${v.file}:${v.line}:${v.column}] (${v.pattern})\n    > ${v.snippet}`,
    )
    .join("\n")
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("entity-gloss LLM-prohibition enforcement (bu-0855u)", () => {
  // -------------------------------------------------------------------------
  // Sanity gate: every required target file must be readable.
  // If a file is renamed, this test fails loudly rather than passing silently.
  // -------------------------------------------------------------------------

  it("all required scan-target files exist and are readable", () => {
    for (const target of SCAN_TARGETS) {
      if (!target.required) continue
      expect(
        fs.existsSync(target.absolutePath),
        `Required scan target missing: ${target.label}\n` +
          `  Expected at: ${target.absolutePath}\n` +
          `  If the file was renamed, update SCAN_TARGETS in this test.`,
      ).toBe(true)
    }
  })

  // -------------------------------------------------------------------------
  // Main enforcement: no banned patterns in any target file.
  // -------------------------------------------------------------------------

  it("no banned LLM SDK imports appear in the gloss code path", () => {
    const allViolations: Violation[] = []

    for (const target of SCAN_TARGETS) {
      if (!fs.existsSync(target.absolutePath)) {
        if (target.required) {
          throw new Error(
            `Required scan target not found: ${target.label} at ${target.absolutePath}`,
          )
        }
        continue
      }
      const content = fs.readFileSync(target.absolutePath, "utf-8")
      const violations = collectViolations(target.label, content)
      allViolations.push(...violations.filter((v) => v.pattern === "banned-llm-import"))
    }

    expect(
      allViolations,
      `Banned LLM SDK imports found in the entity-gloss code path.\n` +
        `These violate the $0/user/day cost contract (Brief §4, Amendment 15).\n\n` +
        formatViolations(allViolations),
    ).toHaveLength(0)
  })

  it("no suspicious external fetch/axios calls appear in the gloss code path", () => {
    const allViolations: Violation[] = []

    for (const target of SCAN_TARGETS) {
      if (!fs.existsSync(target.absolutePath)) {
        if (target.required) {
          throw new Error(
            `Required scan target not found: ${target.label} at ${target.absolutePath}`,
          )
        }
        continue
      }
      const content = fs.readFileSync(target.absolutePath, "utf-8")
      const violations = collectViolations(target.label, content)
      allViolations.push(
        ...violations.filter(
          (v) =>
            v.pattern === "fetch-external-url" ||
            v.pattern === "banned-axios-call (LLM proxy risk)" ||
            v.pattern === "fetch-non-api-url" ||
            v.pattern === "fetch-non-api-template-literal",
        ),
      )
    }

    expect(
      allViolations,
      `Suspicious fetch/axios calls found in the entity-gloss code path.\n` +
        `Only fetch('/api/...') or fetch(\`/api/...\`) is permitted here.\n\n` +
        formatViolations(allViolations),
    ).toHaveLength(0)
  })

  it("no template-literal arguments to LLM completion/chat/generate functions", () => {
    const allViolations: Violation[] = []

    for (const target of SCAN_TARGETS) {
      if (!fs.existsSync(target.absolutePath)) {
        if (target.required) {
          throw new Error(
            `Required scan target not found: ${target.label} at ${target.absolutePath}`,
          )
        }
        continue
      }
      const content = fs.readFileSync(target.absolutePath, "utf-8")
      const violations = collectViolations(target.label, content)
      allViolations.push(
        ...violations.filter((v) => v.pattern === "completion-template-literal"),
      )
    }

    expect(
      allViolations,
      `Template-literal arguments to completion/chat/generate functions found.\n` +
        `These indicate dynamic prompt construction — banned in the gloss path.\n\n` +
        formatViolations(allViolations),
    ).toHaveLength(0)
  })

  // -------------------------------------------------------------------------
  // Combined: all three checks in one pass — fail with full report.
  // This is the canonical single-assertion used by CI.
  // -------------------------------------------------------------------------

  it("no LLM-prohibition violations (combined check — all patterns)", () => {
    const allViolations: Violation[] = []

    for (const target of SCAN_TARGETS) {
      if (!fs.existsSync(target.absolutePath)) {
        if (target.required) {
          throw new Error(
            `Required scan target not found: ${target.label} at ${target.absolutePath}`,
          )
        }
        continue
      }
      const content = fs.readFileSync(target.absolutePath, "utf-8")
      allViolations.push(...collectViolations(target.label, content))
    }

    expect(
      allViolations,
      `LLM-prohibition violations found in entity-gloss frontend code path.\n` +
        `Resolves v1 Open Question 23. Banned set: Amendment 15.\n\n` +
        `Violations (${allViolations.length}):\n` +
        formatViolations(allViolations),
    ).toHaveLength(0)
  })
})
