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
 *   2. every non-`pending` entry has at least one real consumer — scoped to
 *      the specific file(s) named in `consumerFiles` when given, otherwise
 *      anywhere under frontend/src (excluding the type declaration itself and
 *      test files) — an emitted flag with zero readers fails this test.
 *
 * PER-ENDPOINT GRANULARITY (bu-hmdqz.12): one flag NAME (e.g. `sources_degraded`)
 * is emitted by several unrelated endpoints. A codebase-wide "is this name read
 * anywhere?" check lets a brand-new backend-first emitter hide behind an
 * existing consumer of the same name on a DIFFERENT surface — the sessions
 * LIST-level flag went unread for exactly this reason while the aggregate-level
 * flag was consumed. So an entry that shares a flag name with others carries a
 * `surface` discriminator AND `consumerFiles` scoping the consumer search to
 * the file(s) that own that surface. Add a new emitting surface -> add a new
 * entry, and its own consumer must land in its own file.
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
  /**
   * Discriminator when several endpoints emit the SAME flag name — the
   * (flag, surface) pair must be unique. Omit for a flag emitted by a single
   * surface; the uniqueness check then falls back to the flag name itself.
   */
  surface?: string
  /** Short human note: which endpoint(s)/surface emits this flag. */
  emittedBy: string
  /**
   * Scope the consumer search to these file(s) — each is matched as a
   * path SUFFIX (posix-normalised) against every scannable source file. When
   * set, the consumer pattern must match inside at least one of them, NOT
   * "anywhere under frontend/src". REQUIRED for any flag name shared across
   * surfaces so one surface's consumer can't satisfy another's (bu-hmdqz.12).
   */
  consumerFiles?: string[]
  /**
   * Regex used to detect a real consumer, matched against the scanned file(s)
   * (scoped by `consumerFiles` if given, else every non-test source file under
   * frontend/src with types.ts excluded). Defaults to a `\b<flag>\b`
   * word-boundary match, which is safe for this fleet's multi-word snake_case
   * identifiers (they don't collide with unrelated code). Override only when
   * the bare flag name is too generic to search safely on its own — see the
   * `available` entry below.
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
  // `sources_degraded` is emitted by five unrelated surfaces. Each gets its own
  // entry + consumerFiles scoping so a backend-first emitter on one surface
  // can't hide behind another surface's existing consumer (bu-hmdqz.12).
  {
    flag: "sources_degraded",
    surface: "sessions-list",
    emittedBy: "GET /api/sessions (keyset list) + pinned strips + stripe chart",
    consumerFiles: [
      "pages/SessionsPage.tsx",
      "components/sessions/SessionTable.tsx",
      "components/sessions/SessionsPinnedStrip.tsx",
      "components/dashboard/SessionStripeChart.tsx",
    ],
  },
  {
    flag: "sources_degraded",
    surface: "sessions-aggregate",
    emittedBy: "GET /api/sessions/aggregate (KPI strip + verdict opener)",
    consumerFiles: [
      "components/sessions/SessionsKpiStrip.tsx",
      "pages/SessionsPage.tsx",
    ],
  },
  {
    flag: "sources_degraded",
    surface: "search",
    emittedBy: "GET /api/search (command finder / entity finder)",
    consumerFiles: ["components/layout/EntityFinder.tsx"],
  },
  {
    flag: "sources_degraded",
    surface: "issues",
    emittedBy: "GET /api/issues (issues feed)",
    consumerFiles: ["pages/IssuesPage.tsx"],
  },
  {
    flag: "truncated",
    surface: "issues",
    emittedBy: "GET /api/issues (500-group audit-result cap)",
    consumerFiles: ["pages/IssuesPage.tsx"],
  },
  {
    flag: "sources_degraded",
    surface: "approvals",
    emittedBy: "GET /api/approvals + /api/approvals/history",
    consumerFiles: ["pages/ApprovalsPage.tsx"],
  },
  { flag: "pools_failed", emittedBy: "memory stats" },
  { flag: "catalog_pools_failed", emittedBy: "memory stats (catalog-drift gauge)" },
  {
    flag: "retention_pools_failed",
    emittedBy: "memory stats (expired-retention complete-or-unknown observation)",
    consumerFiles: ["components/memory/MemoryOverture.tsx"],
  },
  { flag: "unavailable_butlers", emittedBy: "spend summary/daily/by-schedule/top-sessions" },
  { flag: "stripe_source_error", emittedBy: "butlers board (per-row hourly activity)" },
  {
    flag: "sessions_source_error",
    emittedBy: "butlers board (aggregate rollup, Overview Sessions KPI)",
    consumerFiles: ["pages/DashboardPage.tsx"],
  },
  { flag: "registry_source_error", emittedBy: "butlers board (registry fan-out)" },
  { flag: "cost_source_error", emittedBy: "butlers board (cost fan-out)" },
  { flag: "sources_partially_degraded", emittedBy: "butlers board (aggregate rollup)" },
  { flag: "source_available", emittedBy: "notifications list/stats" },
  { flag: "entries_source_available", emittedBy: "calendar workspace read (events fan-out)" },
  {
    flag: "people_source_available",
    emittedBy: "calendar workspace linked-people resolution",
  },
  {
    flag: "sources_available",
    surface: "calendar-audit",
    emittedBy: "calendar workspace audit trail",
    consumerFiles: ["pages/CalendarWorkspacePage.tsx"],
  },
  {
    flag: "sources_available",
    surface: "calendar-meta",
    emittedBy:
      "calendar workspace meta + read source-freshness (calendar_sources fan-out, bu-sn71y)",
    consumerFiles: ["pages/CalendarWorkspacePage.tsx"],
  },
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
  {
    flag: "receipts_available",
    emittedBy: "education curriculum request status read (bu-6jv4m.10)",
    consumerFiles: ["components/education/CurriculumRequestReceiptPanel.tsx"],
  },
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

/** Normalise a filesystem path to posix separators so `consumerFiles`
 * suffix matching (e.g. "pages/SessionsPage.tsx") is platform-independent. */
function toPosix(filePath: string): string {
  return filePath.split(path.sep).join("/")
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

  it("REGISTRY has no duplicate (flag, surface) entries", () => {
    // A flag may appear more than once (one entry per emitting surface); the
    // (flag, surface) PAIR is what must be unique. Entries without a surface
    // fall back to the flag name as their key (a single-surface flag).
    const keys = REGISTRY.map((entry) => `${entry.flag}::${entry.surface ?? ""}`)
    expect(new Set(keys).size).toBe(keys.length)
  })

  it("every flag shared across surfaces scopes its consumer with consumerFiles", () => {
    // Per-endpoint granularity guard (bu-hmdqz.12): if a flag NAME is emitted by
    // more than one surface, every entry for that name MUST scope its consumer
    // search (consumerFiles) — otherwise a codebase-wide match lets one
    // surface's consumer vacuously satisfy another's.
    const countByFlag = new Map<string, number>()
    for (const entry of REGISTRY) {
      countByFlag.set(entry.flag, (countByFlag.get(entry.flag) ?? 0) + 1)
    }
    const unscoped = REGISTRY.filter(
      (entry) =>
        (countByFlag.get(entry.flag) ?? 0) > 1 &&
        !entry.pending &&
        (entry.consumerFiles?.length ?? 0) === 0,
    )
    expect(
      unscoped.map((entry) => `${entry.flag} (${entry.surface ?? "no surface"})`),
      "A flag emitted by multiple surfaces must scope each entry's consumer with\n" +
        "consumerFiles, so one surface's reader can't stand in for another's:\n\n" +
        unscoped.map((entry) => `  - ${entry.flag} / ${entry.surface ?? "?"}`).join("\n"),
    ).toEqual([])
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

  it("every consumerFiles path resolves to a scannable source file", () => {
    // A stale/misspelled consumerFiles suffix would silently scope the search
    // to zero files and fail the consumer assertion for the wrong reason —
    // catch it explicitly with a clearer message.
    const sourceFiles = listSourceFiles(SRC_DIR).map(toPosix)
    const dangling: string[] = []
    for (const entry of REGISTRY) {
      for (const suffix of entry.consumerFiles ?? []) {
        if (!sourceFiles.some((file) => file.endsWith(suffix))) {
          dangling.push(`${entry.flag}/${entry.surface ?? "?"} -> ${suffix}`)
        }
      }
    }
    expect(
      dangling,
      "consumerFiles suffix(es) match no source file under frontend/src — a\n" +
        "rename/move left the registry pointing at a nonexistent path:\n\n" +
        dangling.map((d) => `  - ${d}`).join("\n"),
    ).toEqual([])
  })

  it("every non-pending registry flag has at least one real, correctly-scoped consumer", () => {
    const sourceFiles = listSourceFiles(SRC_DIR)
    const contentByFile = new Map<string, string>(
      sourceFiles.map((file) => [file, stripComments(fs.readFileSync(file, "utf-8"))]),
    )

    const unconsumed = REGISTRY.filter((entry) => !entry.pending).filter((entry) => {
      const pattern = entry.consumerPattern ?? new RegExp(`\\b${entry.flag}\\b`)
      // Scope to consumerFiles (matched as path suffixes) when given, else the
      // whole tree. Scoping is what makes this per-endpoint: the sessions-list
      // flag must be read in a sessions-list file, not merely somewhere.
      const scoped = entry.consumerFiles
        ? sourceFiles.filter((file) =>
            entry.consumerFiles!.some((suffix) => toPosix(file).endsWith(suffix)),
          )
        : sourceFiles
      return !scoped.some((file) => pattern.test(contentByFile.get(file) ?? ""))
    })

    expect(
      unconsumed.map((entry) => `${entry.flag}${entry.surface ? `/${entry.surface}` : ""}`),
      "Degraded-envelope flag(s) have ZERO real consumer in their scoped file(s)\n" +
        "(consumerFiles), or anywhere under frontend/src for unscoped flags\n" +
        "(excluding api/types.ts and *.test.ts).\n" +
        "An emitted flag nobody reads means a real source failure can render as a\n" +
        "silent all-clear in the UI — wire a consumer on that surface, or if the flag\n" +
        "is mid-rollout, mark it `pending` in REGISTRY with a tracking note.\n\n" +
        unconsumed
          .map((entry) => `  - ${entry.flag}/${entry.surface ?? "?"} (${entry.emittedBy})`)
          .join("\n"),
    ).toEqual([])
  })
})
