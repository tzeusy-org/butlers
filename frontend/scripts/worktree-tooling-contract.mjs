import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { tmpdir } from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const FRONTEND_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

function readFrontendFile(fileName) {
  return readFileSync(path.join(FRONTEND_ROOT, fileName), 'utf8')
}

test('frontend commands keep generated caches local when dependencies are symlinked', () => {
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
  const temporary = mkdtempSync(path.join(tmpdir(), 'butlers-frontend-tooling-'))
  try {
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
