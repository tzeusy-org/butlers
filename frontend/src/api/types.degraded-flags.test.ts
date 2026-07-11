/// <reference types="node" />
/**
 * types.degraded-flags.test.ts — FE flag-consumption registry (bu-tpudw.5)
 *
 * CLAUDE.md "API Conventions — Degraded-Mode Response Envelope": every
 * fan-out/aggregation endpoint on the dashboard API surfaces a genuine
 * source failure via a named flag rather than a silent zero/empty/all-clear.
 * That promise is only as good as the frontend actually reading the flag —
 * a flag the backend emits and nobody renders is functionally the same as
 * no flag at all: the failure is invisible to the owner.
 *
 * REGISTRY below is the single source of truth for "which envelope flags
 * this contract covers." It is cross-checked two ways:
 *
 *   1. every entry names a field that is actually declared in
 *      frontend/src/api/types.ts (catches a stale registry entry after a
 *      rename/removal on the backend or FE-types side);
 *   2. every non-`pending` entry has at least one real consumer somewhere
 *      under frontend/src (excluding the type declaration itself and test
 *      files) — an emitted flag with zero readers fails this test.
 *
 * To register a newly-shipped flag, add ONE entry to REGISTRY. If the flag
 * is typed/emitted but the frontend consumer has not landed yet, set
 * `pending` to a short tracking note instead of leaving the flag out
 * entirely — that keeps the registry (and thus this contract) complete
 * without blocking CI on sequencing between backend and frontend PRs.
 */

import fs from "node:fs"
import path from "node:path"
import { describe, expect, it } from "vitest"

// ---------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------

const API_DIR = path.resolve(__dirname) // frontend/src/api
const SRC_DIR = path.resolve(__dirname, "..") // frontend/src
const TYPES_FILE = path.join(API_DIR, "types.ts")

// ---------------------------------------------------------------------------
// Registry
// ---------------------------------------------------------------------------

interface DegradedFlagEntry {
  /** Property name as declared on a response interface in api/types.ts. */
  flag: string
  /** Short human note: which endpoint(s)/surface emits this flag. */
  emittedBy: string
  /**
   * Regex used to detect a real consumer, matched against every non-test
   * source file under frontend/src (types.ts excluded). Defaults to a
   * `\b<flag>\b` word-boundary match, which is safe for this fleet's
   * multi-word snake_case identifiers (they don't collide with unrelated
   * code). Override only when the bare flag name is too generic to search
   * safely on its own — see the `available` entry below.
   */
  consumerPattern?: RegExp
  /**
   * Burn-down marker: the flag is typed/emitted by the backend but this
   * repo's frontend has not yet wired a consumer for it. Set to a short
   * tracking note (issue id, PR, or "sequenced after move-2") — the entry
   * still has to name a real field in types.ts, but the consumer-presence
   * assertion is skipped for it.
   */
  pending?: string
}

const REGISTRY: DegradedFlagEntry[] = [
  { flag: "sources_degraded", emittedBy: "sessions list/aggregate, search, issues, approvals" },
  { flag: "pools_failed", emittedBy: "memory stats" },
  { flag: "unavailable_butlers", emittedBy: "spend summary/daily/by-schedule/top-sessions" },
  { flag: "stripe_source_error", emittedBy: "butlers board (per-row hourly activity)" },
  { flag: "sessions_source_error", emittedBy: "butlers board (aggregate rollup)" },
  { flag: "registry_source_error", emittedBy: "butlers board (registry fan-out)" },
  { flag: "cost_source_error", emittedBy: "butlers board (cost fan-out)" },
  { flag: "sources_partially_degraded", emittedBy: "butlers board (aggregate rollup)" },
  { flag: "source_available", emittedBy: "notifications list/stats" },
  { flag: "entries_source_available", emittedBy: "calendar workspace read (events fan-out)" },
  {
    flag: "people_source_available",
    emittedBy: "calendar workspace linked-people resolution",
  },
  { flag: "sources_available", emittedBy: "calendar workspace audit trail" },
  { flag: "issues_available", emittedBy: "calendar conflict/overcommitment radar" },
  {
    flag: "available",
    emittedBy: "calendar workspace duplicate-cluster detection (CalendarDuplicatesResponse)",
    // "available" alone is too generic a token to search for safely across
    // the whole frontend (unrelated types could legitimately declare a
    // same-named boolean) — scope the search to the property-access shape
    // actually used to read this specific flag.
    consumerPattern: /\.available\b/,
  },
  { flag: "catalogue_available", emittedBy: "secrets breaks-catalogue" },
]

// ---------------------------------------------------------------------------
// Source-file scanning
// ---------------------------------------------------------------------------

/**
 * Files that only ever *describe* a flag, never *read* it, so a mention
 * there must not count as consumption:
 *   - types.ts: the type declaration file itself — the registry's own
 *     "declared in types.ts" check already covers this file; excluding it
 *     here stops a bare interface-field declaration from also counting as
 *     "consumption".
 * client.ts is intentionally NOT special-cased here (see stripComments
 * below) — its flag mentions live inside JSDoc `/** *\/` blocks, which
 * comment-stripping already neutralizes for every scanned file, not just
 * this one.
 */
const EXCLUDED_BASENAMES = new Set(["types.ts"])

/**
 * Best-effort removal of `/* ... *\/` block comments and `// ...` line
 * comments before consumer-pattern matching.
 *
 * Without this, a *prose mention* of a flag name anywhere under
 * frontend/src — a JSDoc blurb, a stale inline comment left behind after
 * the real call site was deleted — satisfies the word-boundary regex just
 * as well as an actual property read, letting the "has a real consumer"
 * assertion pass vacuously even when nothing in the file actually branches
 * on the flag. Comment-stripping is what makes the check test *code*
 * instead of *prose about code*. This is line/block-comment stripping only
 * (not a full TS parse), so it does not special-case comment markers that
 * appear inside string/template literals — acceptable for this repo's
 * flag names, which are snake_case identifiers that don't occur in URL-like
 * string literals.
 */
function stripComments(content: string): string {
  return content.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "")
}

function isScannableSourceFile(filePath: string): boolean {
  if (!(filePath.endsWith(".ts") || filePath.endsWith(".tsx"))) return false
  if (filePath.endsWith(".test.ts") || filePath.endsWith(".test.tsx")) return false
  if (EXCLUDED_BASENAMES.has(path.basename(filePath))) return false
  return true
}

function listSourceFiles(dir: string): string[] {
  const out: string[] = []
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      if (entry.name === "node_modules") continue
      out.push(...listSourceFiles(full))
    } else if (isScannableSourceFile(full)) {
      out.push(full)
    }
  }
  return out
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("degraded-envelope FE flag-consumption registry (bu-tpudw.5)", () => {
  it("api/types.ts exists and is readable", () => {
    expect(fs.existsSync(TYPES_FILE)).toBe(true)
  })

  it("REGISTRY has no duplicate flag entries", () => {
    const flags = REGISTRY.map((entry) => entry.flag)
    expect(new Set(flags).size).toBe(flags.length)
  })

  it("every registry entry names a field actually declared in api/types.ts", () => {
    const typesContent = fs.readFileSync(TYPES_FILE, "utf-8")
    const missing = REGISTRY.filter(
      (entry) => !new RegExp(`\\b${entry.flag}\\??:\\s`).test(typesContent),
    )

    expect(
      missing.map((entry) => entry.flag),
      "Registry entries not found as declared fields in frontend/src/api/types.ts.\n" +
        "A registry entry must name a field that is actually typed there — remove or\n" +
        "fix the stale entry:\n\n" +
        missing.map((entry) => `  - ${entry.flag} (${entry.emittedBy})`).join("\n"),
    ).toEqual([])
  })

  it("every non-pending registry flag has at least one real frontend consumer", () => {
    const sourceFiles = listSourceFiles(SRC_DIR)
    const contentByFile = new Map<string, string>(
      sourceFiles.map((file) => [file, stripComments(fs.readFileSync(file, "utf-8"))]),
    )

    const unconsumed = REGISTRY.filter((entry) => !entry.pending).filter((entry) => {
      const pattern = entry.consumerPattern ?? new RegExp(`\\b${entry.flag}\\b`)
      return !sourceFiles.some((file) => pattern.test(contentByFile.get(file) ?? ""))
    })

    expect(
      unconsumed.map((entry) => entry.flag),
      "Degraded-envelope flag(s) emitted by the backend have ZERO frontend\n" +
        "consumers under frontend/src (excluding api/types.ts and *.test.ts files).\n" +
        "An emitted flag nobody reads means a real source failure can render as a\n" +
        "silent all-clear in the UI — wire a consumer, or if the flag is mid-rollout,\n" +
        "mark it `pending` in REGISTRY (types.degraded-flags.test.ts) with a tracking note.\n\n" +
        unconsumed.map((entry) => `  - ${entry.flag} (${entry.emittedBy})`).join("\n"),
    ).toEqual([])
  })
})
