#!/usr/bin/env node
// check-em-dash-copy.mjs (bu-370uj)
//
// Frontend counterpart to scripts/check-no-em-dashes.py: enforces
// non-negotiable #6 ("no em-dashes in user-facing dashboard copy") over the
// frontend source, without touching the elaborate project eslint.config.js
// (whose no-restricted-syntax blocks do not merge -- see its header comment).
//
// Uses ESLint's own TypeScript/JSX parser (via a standalone override config)
// so it only inspects real string / JSX-text / template AST nodes -- code
// COMMENTS are never matched. It flags an em-dash (U+2014) only when it sits in
// PROSE (adjacent to other visible text); a lone "—" empty-value placeholder
// glyph (`?? "—"`, `<td>—</td>`) is the established no-data convention across
// this dashboard (90+ sites) and is intentionally exempt.
//
// Baseline ratchet: pre-existing prose em-dashes are frozen per-file in
// em-dash-copy-baseline.json (cleanup tracked separately -- bu-7ekpn owns
// query-boundary.tsx, the rest need a follow-up). The gate fails only when a
// file exceeds its baseline (a new em-dash) or a file with no baseline entry
// gains any (a new dirty file). Regenerate with --update-baseline.
//
// Test / story / e2e files are excluded: they assert on / fixture real copy.

import { ESLint } from 'eslint'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import tseslint from 'typescript-eslint'

const FRONTEND_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const BASELINE_PATH = path.join(FRONTEND_ROOT, 'em-dash-copy-baseline.json')

// Prose-only: require a non-space, non-dash char adjacent to the em-dash, so a
// bare "—" placeholder never matches.
const PROSE_EMDASH = String.raw`\S[^—]*—|—[^—]*\S`
const SELECTORS = [
  { selector: `JSXText[value=/${PROSE_EMDASH}/]`, message: 'em-dash-in-copy' },
  { selector: `Literal[value=/${PROSE_EMDASH}/]`, message: 'em-dash-in-copy' },
  { selector: `TemplateElement[value.raw=/${PROSE_EMDASH}/]`, message: 'em-dash-in-copy' },
]

// Test / story / e2e / a11y files: excluded (they fixture / assert real copy).
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
    { ignores: ['dist/**', ...TEST_GLOBS] },
    {
      files: ['**/*.{ts,tsx}'],
      extends: [tseslint.configs.base],
      rules: { 'no-restricted-syntax': ['error', ...SELECTORS] },
    },
  ),
})

const results = await eslint.lintFiles(['src', 'tests'])

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
  console.log(`Wrote baseline: ${total} prose em-dash(es) across ${Object.keys(counts).length} file(s) -> ${path.basename(BASELINE_PATH)}`)
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
    console.log(`\n${rel}: ${current} prose em-dash(es), baseline allows ${allowed} (lines ${lines[rel].join(', ')})`)
  }
}
for (const [rel, allowed] of Object.entries(baseline)) {
  const current = counts[rel] ?? 0
  if (current < allowed) improved.push(`${rel} (${allowed} -> ${current})`)
}

if (improved.length) {
  console.log(`\nBaseline can be lowered (fewer em-dashes than recorded): ${improved.join(', ')}`)
  console.log('  Run: npm run lint:emdash -- --update-baseline')
}

if (regressions.length) {
  const netNew = regressions.reduce((a, r) => a + counts[r] - (baseline[r] ?? 0), 0)
  console.log(`\n${netNew} net-new em-dash(es) in user-facing copy across ${regressions.length} file(s).`)
  console.log('Replace with a comma, colon, or parentheses (non-negotiable #6). A lone "—" placeholder is exempt.')
  process.exit(1)
}

console.log('No net-new em-dashes in user-facing copy.')
process.exit(0)
