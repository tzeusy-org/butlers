import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
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
    /node --test scripts\/worktree-tooling-contract\.mjs/,
    'the normal test command must run this worktree-tooling contract in CI',
  )

  assert.match(viteConfig, /cacheDir:\s*["']\.vite["']/)
  assert.match(viteConfig, /fileURLToPath\(import\.meta\.url\)/)
  assert.doesNotMatch(viteConfig, /\b__dirname\b/)

  for (const configName of ['tsconfig.app.json', 'tsconfig.node.json']) {
    const tsconfig = readFrontendFile(configName)
    assert.match(tsconfig, /"tsBuildInfoFile":\s*"\.\/\.vite\//)
    assert.doesNotMatch(tsconfig, /"tsBuildInfoFile":\s*"\.\/node_modules\//)
  }
})
