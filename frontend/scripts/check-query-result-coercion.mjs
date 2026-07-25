#!/usr/bin/env node
// check-query-result-coercion.mjs (bu-ep4ks.5)
//
// Population guard for the "fabricated-calm" defect class: a component reads
// a query hook's `.data` (or its `.meta.total`/`.meta.count` envelope field)
// and coerces a missing value to `?? []` / `?? 0` instead of also checking
// `isError`. When the underlying fetch fails, `data` is `undefined` too --
// the bare coercion makes a genuine outage render identically to a
// confirmed-empty/confirmed-zero result (see components/ui/query-boundary.tsx
// header + butlers/CLAUDE.md "Degraded-Mode Response Envelope"). Every widget
// fixed in bu-ep4ks.5 (MindMapGraph, MasteryTrendChart, StrugglingNodesCard,
// PlexPage halo, SpendPage top-sessions/by-schedule, AttentionRail
// fading-facts/stale-embeddings) had exactly this shape.
//
// This does NOT prove a fetch failure is mishandled -- it is a syntactic
// smell detector, not a semantic one (it cannot see whether `isError` is
// checked elsewhere in the component). Treat a flagged site as "audit this,"
// not "always wrong." Follows the check-em-dash-copy.mjs pattern: ESLint's
// own TypeScript/JSX parser via a standalone override config (kept out of
// the elaborate eslint.config.js, whose no-restricted-syntax blocks do not
// merge across matching config objects -- see that file's header), plus a
// baseline ratchet so the ~130 pre-existing sites this audit found do not
// block CI immediately. The gate fails only on a NEW net-new site; existing
// sites are frozen in query-coercion-baseline.json pending their own
// follow-up cleanup. Regenerate the baseline with --update-baseline.
//
// Known scope limits (why this is "components only," not repo-wide):
//   - `.data` is TanStack Query's own envelope field name, ubiquitous in this
//     codebase for query results -- but a `.data` access on a value that is
//     NOT a query result (rare in components/pages) will still be flagged.
//     If genuinely unrelated to a query, add a line-level disable comment for
//     no-restricted-syntax with a one-line reason (see the escape-hatch
//     pattern used throughout eslint.config.js's own selector comments).
//   - `.meta.total` / `.meta.count` mirrors the backend's `meta.<flag>`
//     degraded-envelope convention (butlers/CLAUDE.md API Conventions), so
//     it is scoped to exactly that shape, not any `.total`/`.count` field.
//   - hooks/ (src/hooks/**) are intentionally NOT covered: a hook is often
//     the correct place to define a query's own normalized shape, and this
//     guard is about call sites silently re-deriving one without isError.
//   - Intentional coercions where isError genuinely doesn't apply (a
//     non-query optional field, a value already checked upstream) are a
//     legitimate reason to keep the coercion -- use the same line-level
//     disable-comment escape hatch, not a rule-wide suppression.

import { ESLint } from 'eslint'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import tseslint from 'typescript-eslint'

const FRONTEND_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const BASELINE_PATH = path.join(FRONTEND_ROOT, 'query-coercion-baseline.json')

const SELECTORS = [
  {
    selector:
      'LogicalExpression[operator="??"][right.type="ArrayExpression"][right.elements.length=0] MemberExpression[property.name="data"]',
    message: 'query-result-coercion',
  },
  {
    selector:
      'LogicalExpression[operator="??"][right.type="Literal"][right.value=0] MemberExpression[property.name="data"]',
    message: 'query-result-coercion',
  },
  {
    selector:
      'LogicalExpression[operator="??"][right.type="Literal"][right.value=0] MemberExpression[property.name=/^(?:total|count)$/][object.property.name="meta"]',
    message: 'query-result-coercion',
  },
]

const TEST_GLOBS = [
  '**/*.test.{ts,tsx}',
  '**/*.spec.{ts,tsx}',
  '**/*.stories.{ts,tsx}',
  '**/*.a11y.{ts,tsx}',
  'src/test/**',
]

const eslint = new ESLint({
  cwd: FRONTEND_ROOT,
  overrideConfigFile: true,
  overrideConfig: tseslint.config(
    { ignores: ['dist/**', 'src/components/ui/**', ...TEST_GLOBS] },
    {
      files: ['**/*.{ts,tsx}'],
      extends: [tseslint.configs.base],
      rules: { 'no-restricted-syntax': ['error', ...SELECTORS] },
    },
  ),
})

const results = await eslint.lintFiles(['src/components', 'src/pages'])

const counts = {}
const lines = {}
for (const r of results) {
  const rel = path.relative(FRONTEND_ROOT, r.filePath).split(path.sep).join('/')
  const msgs = r.messages.filter((m) => m.ruleId === 'no-restricted-syntax')
  if (msgs.length) {
    counts[rel] = msgs.length
    lines[rel] = msgs.map((m) => m.line)
  }
}

const updateBaseline = process.argv.includes('--update-baseline')
if (updateBaseline) {
  const sorted = Object.fromEntries(Object.entries(counts).sort(([a], [b]) => a.localeCompare(b)))
  fs.writeFileSync(BASELINE_PATH, JSON.stringify(sorted, null, 2) + '\n')
  const total = Object.values(counts).reduce((a, b) => a + b, 0)
  console.log(
    `Wrote baseline: ${total} query-result coercion site(s) across ${Object.keys(counts).length} file(s) -> ${path.basename(BASELINE_PATH)}`,
  )
  process.exit(0)
}

const baseline = fs.existsSync(BASELINE_PATH)
  ? JSON.parse(fs.readFileSync(BASELINE_PATH, 'utf8'))
  : {}

const regressions = []
const improved = []
for (const [rel, current] of Object.entries(counts).sort()) {
  const allowed = baseline[rel] ?? 0
  if (current > allowed) {
    regressions.push(rel)
    console.log(`\n${rel}: ${current} coercion site(s), baseline allows ${allowed} (lines ${lines[rel].join(', ')})`)
  }
}
for (const [rel, allowed] of Object.entries(baseline)) {
  const current = counts[rel] ?? 0
  if (current < allowed) improved.push(`${rel} (${allowed} -> ${current})`)
}

if (improved.length) {
  console.log(`\nBaseline can be lowered (fewer coercion sites than recorded): ${improved.join(', ')}`)
  console.log('  Run: npm run lint:query-coercion -- --update-baseline')
}

if (regressions.length) {
  const netNew = regressions.reduce((a, r) => a + counts[r] - (baseline[r] ?? 0), 0)
  console.log(
    `\n${netNew} net-new query-result coercion site(s) (\`?? []\` / \`?? 0\` on a query's .data / .meta.total / .meta.count) across ${regressions.length} file(s).`,
  )
  console.log(
    'A query result defaulted with ?? [] / ?? 0 without also checking isError makes a fetch failure ' +
      'render identically to a confirmed-empty/confirmed-zero result (bu-ep4ks.5). Thread isError into ' +
      'SourceDegradedNote (components/ui/query-boundary.tsx) instead, or if this is genuinely not a query ' +
      'result, add a line-level eslint-disable-next-line no-restricted-syntax with a one-line reason.',
  )
  process.exit(1)
}

console.log('No net-new query-result coercion sites.')
process.exit(0)
