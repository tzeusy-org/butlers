// REQ-frontend-lane-efficiency-002, REQ-frontend-lane-efficiency-003, REQ-frontend-lane-efficiency-005.
import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { tmpdir } from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { lintCacheKey } from './lint-content-cache.mjs'

const FRONTEND_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

function readFrontendFile(fileName) {
  return readFileSync(path.join(FRONTEND_ROOT, fileName), 'utf8')
}

test('frontend commands keep generated caches local when dependencies are symlinked', async () => {
  const packageJson = JSON.parse(readFrontendFile('package.json'))
  const viteConfig = readFrontendFile('vite.config.ts')

  for (const command of ['dev', 'build', 'test']) {
    assert.match(
      packageJson.scripts[command],
      /--configLoader runner/,
      `${command} must use Vite's runner loader before it can write through a shared node_modules symlink`,
    )
  }

  assert.match(
    packageJson.scripts.test,
    /node --test --test-timeout=300000 scripts\/worktree-tooling-contract\.mjs/,
    'the normal test command must run this worktree-tooling contract in CI',
  )

  assert.match(packageJson.scripts['test:worktree-tooling'], /--test-timeout=300000/)
  // Public locked subpaths resolve to the SAME implementations, including
  // DST/locale/invalid-date behavior; no replacement date implementation.
  for (const [packageName, names] of Object.entries({
    'date-fns': ['addDays', 'addMonths', 'addWeeks', 'format', 'startOfDay', 'endOfDay', 'startOfMonth', 'startOfWeek', 'isValid', 'parseISO', 'subDays', 'subHours', 'differenceInCalendarDays', 'formatDistanceToNow'],
    'date-fns-tz': ['formatInTimeZone', 'fromZonedTime', 'toZonedTime'],
  })) {
    const barrel = await import(packageName)
    for (const name of names) {
      const direct = await import(`${packageName}/${name}`)
      assert.equal(direct[name], barrel[name], `${packageName}/${name} implementation drift`)
    }
  }
  const { formatInTimeZone } = await import('date-fns-tz/formatInTimeZone')
  assert.equal(formatInTimeZone('2026-03-08T06:59:00Z', 'America/New_York', 'HH:mm'), '01:59')
  assert.equal(formatInTimeZone('2026-03-08T07:01:00Z', 'America/New_York', 'HH:mm'), '03:01')
  assert.throws(() => formatInTimeZone(new Date('invalid'), 'UTC', 'HH:mm'), RangeError)
  const { format } = await import('date-fns/format')
  const { enGB } = await import('date-fns/locale/en-GB')
  assert.equal(format(new Date('2026-10-09T12:00:00Z'), 'PPP', { locale: enGB }), '9 October 2026')

  const temporary = mkdtempSync(path.join(tmpdir(), 'butlers-frontend-tooling-'))
  try {
    // Actual installed ESLint, miniature source/config only. No mock linter or
    // shared writable cache. Warm success must not admit changed source/rules.
    const lintRoot = path.join(temporary, 'lint-control')
    mkdirSync(path.join(lintRoot, 'scripts'), { recursive: true })
    symlinkSync(path.join(FRONTEND_ROOT, 'node_modules'), path.join(lintRoot, 'node_modules'), 'dir')
    writeFileSync(path.join(lintRoot, 'package.json'), JSON.stringify({ type: 'module' }))
    writeFileSync(path.join(lintRoot, 'package-lock.json'), readFrontendFile('package-lock.json'))
    const cacheRunner = readFrontendFile('scripts/lint-content-cache.mjs')
    writeFileSync(path.join(lintRoot, 'scripts/lint-content-cache.mjs'), cacheRunner)
    const config = "export default [{ ignores: ['scripts/**', '.eslintcache/**'] }, { rules: {'no-undef':'error'} }]\n"
    writeFileSync(path.join(lintRoot, 'eslint.config.js'), config)
    writeFileSync(path.join(lintRoot, 'subject.js'), '0;\n')
    const runLint = () => spawnSync(process.execPath, [path.join(lintRoot, 'scripts/lint-content-cache.mjs')], {
      cwd: lintRoot, encoding: 'utf8', timeout: 20_000,
    })
    assert.equal(runLint().status, 0)
    const oldKey = lintCacheKey(lintRoot)
    const warm = runLint()
    assert.equal(warm.status, 0)
    assert.match(warm.stdout, /compatible-cache/)
    writeFileSync(path.join(lintRoot, 'subject.js'), 'unregisteredName;\n')
    assert.equal(runLint().status, 1, 'warm source-content change must fail actual no-undef')
    writeFileSync(path.join(lintRoot, 'subject.js'), '0;\n')
    writeFileSync(path.join(lintRoot, 'eslint.config.js'), "export default [{ ignores: ['scripts/**', '.eslintcache/**'] }, { rules: {'no-restricted-syntax':['error','Literal[value=0]']} }]\n")
    assert.notEqual(lintCacheKey(lintRoot), oldKey)
    assert.equal(runLint().status, 1, 'changed actual rule cannot reuse old successful cache')
    writeFileSync(path.join(lintRoot, 'eslint.config.js'), config)
    assert.equal(runLint().status, 0)
    writeFileSync(path.join(lintRoot, '.eslintcache', oldKey), 'invalid-json')
    const corrupt = runLint()
    assert.equal(corrupt.status, 0)
    assert.match(corrupt.stdout, /cache-unavailable-full-lint/)
    // Deliberately malformed advisory cache still reaches actual enforcement.
    writeFileSync(path.join(lintRoot, 'subject.js'), 'unregisteredName;\n')
    assert.equal(runLint().status, 1)
    writeFileSync(path.join(lintRoot, 'subject.js'), '0;\n')
    assert.equal(runLint().status, 0)

    // Actual installed retry classifier with real first-fail/retry-pass.
    // Browser-free mechanism control, not production project/browser proof.
    const retryRoot = path.join(temporary, 'retry-control')
    mkdirSync(retryRoot)
    symlinkSync(path.join(FRONTEND_ROOT, 'node_modules'), path.join(retryRoot, 'node_modules'), 'dir')
    const retryConfig = path.join(retryRoot, 'playwright.config.ts')
    writeFileSync(retryConfig, `import production from ${JSON.stringify(path.join(FRONTEND_ROOT, 'playwright.config.ts'))};
export default { ...production, testDir: '.', testMatch: '**/retry.spec.ts', projects: [{}], workers: 1, webServer: undefined, reporter: 'json', outputDir: './results', use: {} };`)
    const retryTest = path.join(retryRoot, 'retry.spec.ts')
    writeFileSync(retryTest, `import { test, expect } from '@playwright/test';
test('ordinary positive', () => expect(2 + 2).toBe(4));
test('retry eventually passes', async ({}, info) => { expect(info.retry).toBeGreaterThan(0); });`)
    const retry = spawnSync(process.execPath, [path.join(FRONTEND_ROOT, 'node_modules/playwright/cli.js'), 'test', '--config', retryConfig], {
      cwd: retryRoot, encoding: 'utf8', timeout: 20_000, env: { ...process.env, CI: '1' },
    })
    assert.equal(retry.status, 1, 'actual retry-passed classification must refuse green')
    const retryProof = JSON.parse(retry.stdout)
    assert.equal(retryProof.stats.expected, 1)
    assert.equal(retryProof.stats.flaky, 1)
    assert.equal(retryProof.stats.unexpected, 0)
    writeFileSync(retryTest, `import { test, expect } from '@playwright/test';
test('ordinary positive', () => expect(2 + 2).toBe(4));
test('restored positive', () => expect(true).toBe(true));`)
    const restoredRetry = spawnSync(process.execPath, [path.join(FRONTEND_ROOT, 'node_modules/playwright/cli.js'), 'test', '--config', retryConfig], {
      cwd: retryRoot, encoding: 'utf8', timeout: 20_000, env: { ...process.env, CI: '1' },
    })
    assert.equal(restoredRetry.status, 0)
    assert.equal(JSON.parse(restoredRetry.stdout).stats.expected, 2)

    const sleepFile = path.join(temporary, 'timeout.test.mjs')
    writeFileSync(sleepFile, `import test from 'node:test'
import assert from 'node:assert/strict'
test('ordinary positive', () => assert.equal(2 + 2, 4))
test('named sleeping test', async () => {
  await new Promise(resolve => setTimeout(resolve, 200))
})
`)
    // Short independent mechanism control; the installed production bound is300s.
    const childEnv = { ...process.env }
    delete childEnv.NODE_TEST_CONTEXT
    const sleep = spawnSync(process.execPath, ['--test', '--test-reporter=tap', '--test-timeout=50', sleepFile], {
      encoding: 'utf8', timeout: 5000, env: childEnv,
    })
    assert.equal(sleep.status, 1, sleep.stderr)
    assert.match(sleep.stdout, /named sleeping test/)
    assert.match(sleep.stdout, /test timed out after 50ms/)
    assert.match(sleep.stdout, /# pass 1/)

    const reader = readFrontendFile('scripts/playwright-cache-version.mjs')
    for (const mode of ['valid', 'wrong-lock', 'mixed-install', 'missing-lock',
      'valid-browser', 'wrong-browser', 'missing-browser', 'corrupt-browser']) {
      const root = path.join(temporary, mode)
      mkdirSync(path.join(root, 'scripts'), { recursive: true })
      writeFileSync(path.join(root, 'scripts/playwright-cache-version.mjs'), reader)
      const packages = {}
      for (const name of ['@playwright/test', 'playwright', 'playwright-core']) {
        const directory = path.join(root, 'node_modules', name)
        mkdirSync(directory, { recursive: true })
        const version = mode === 'mixed-install' && name === 'playwright-core' ? '1.59.0' : '1.60.0'
        writeFileSync(path.join(directory, 'package.json'), JSON.stringify({ name, version }))
        packages[`node_modules/${name}`] = {
          version: mode === 'wrong-lock' && name === 'playwright' ? '1.59.0' : '1.60.0',
        }
      }
      if (mode !== 'missing-lock') {
        writeFileSync(path.join(root, 'package-lock.json'), JSON.stringify({ packages }))
      }
      writeFileSync(path.join(root, 'node_modules/playwright-core/browsers.json'), JSON.stringify({
        browsers: [{ name: 'chromium', browserVersion: '139.0.0.0' }],
      }))
      // Synthetic launch boundary, not evidence of actual OS dependencies or Chromium.
      writeFileSync(path.join(root, 'node_modules/@playwright/test/index.js'), `
const { writeFileSync } = require('node:fs')
exports.chromium = { launch: async options => {
  if (!options.headless) throw new Error('headless option missing')
  if (${JSON.stringify(['missing-browser', 'corrupt-browser'].includes(mode))}) {
    throw new Error('synthetic cached binary unavailable')
  }
  return { version: () => ${JSON.stringify(mode === 'wrong-browser' ? '138.0.0.0' : '139.0.0.0')},
    close: async () => writeFileSync(${JSON.stringify(path.join(root, 'closed'))}, 'closed') }
} }
`)
      const verify = mode.endsWith('browser')
      const result = spawnSync(process.execPath, [path.join(root, 'scripts/playwright-cache-version.mjs'),
        ...(verify ? ['--verify-browser'] : [])], { encoding: 'utf8', timeout: 5000 })
      assert.equal(result.status, mode === 'valid' || mode === 'valid-browser' ? 0 : 1, mode)
      if (mode === 'valid') assert.equal(result.stdout.trim(), '1.60.0')
      if (mode === 'valid-browser' || mode === 'wrong-browser') {
        assert.equal(readFileSync(path.join(root, 'closed'), 'utf8'), 'closed')
      }
    }
  } finally {
    rmSync(temporary, { recursive: true, force: true })
  }

  assert.match(viteConfig, /cacheDir:\s*["']\.vite["']/)
  assert.match(viteConfig, /fileURLToPath\(import\.meta\.url\)/)
  assert.doesNotMatch(viteConfig, /\b__dirname\b/)

  for (const configName of ['tsconfig.app.json', 'tsconfig.node.json']) {
    const tsconfig = readFrontendFile(configName)
    assert.match(tsconfig, /"tsBuildInfoFile":\s*"\.\/\.vite\//)
    assert.doesNotMatch(tsconfig, /"tsBuildInfoFile":\s*"\.\/node_modules\//)
  }
})
