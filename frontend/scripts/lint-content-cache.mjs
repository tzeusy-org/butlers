import { createHash } from 'node:crypto'
import { spawnSync } from 'node:child_process'
import { copyFileSync, existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, renameSync, rmSync } from 'node:fs'
import { arch, platform, release } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

export function lintCacheKey(directory = root) {
  const files = ['eslint.config.js', 'package.json', 'package-lock.json']
  function scripts(dir) {
    for (const item of readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      const full = path.join(dir, item.name)
      if (item.isSymbolicLink()) throw new Error('cache source symlink')
      if (item.isDirectory()) scripts(full)
      else if (item.isFile()) files.push(path.relative(directory, full))
    }
  }
  scripts(path.join(directory, 'scripts'))
  const locked = JSON.parse(readFileSync(path.join(directory, 'package-lock.json'), 'utf8')).packages['node_modules/eslint'].version
  const installed = JSON.parse(readFileSync(path.join(directory, 'node_modules/eslint/package.json'), 'utf8')).version
  if (locked !== installed) throw new Error('locked linter unavailable')
  const content = files.map(name => [name, createHash('sha256').update(readFileSync(path.join(directory, name))).digest('hex')])
  return createHash('sha256').update(JSON.stringify({ node: process.versions.node, eslint: installed, platform: platform(), release: release(), arch: arch(), content })).digest('hex')
}

export function lint(directory = root) {
  const executable = path.join(directory, 'node_modules/eslint/bin/eslint.js')
  let cache, staging, key
  let degradation = 'cold-cache'
  try {
    key = lintCacheKey(directory)
    const location = path.join(directory, '.eslintcache')
    if (existsSync(location) && (!lstatSync(location).isDirectory() || lstatSync(location).isSymbolicLink())) throw new Error('private cache unavailable')
    mkdirSync(location, { recursive: true })
    cache = path.join(location, key)
    staging = mkdtempSync(path.join(location, 'owned-'))
    if (existsSync(cache)) {
      if (!lstatSync(cache).isFile() || lstatSync(cache).isSymbolicLink() || lstatSync(cache).size > 32 * 1024 * 1024) throw new Error('cache malformed')
      JSON.parse(readFileSync(cache, 'utf8'))
      copyFileSync(cache, path.join(staging, 'cache'))
      degradation = 'compatible-cache'
    }
  } catch {
    degradation = 'cache-unavailable-full-lint'
    if (staging) rmSync(staging, { recursive: true, force: true })
    staging = undefined
  }
  console.log(`lint-cache: ${degradation}`)
  const cacheArguments = staging ? ['--cache', '--cache-strategy', 'content', '--cache-location', path.join(staging, 'cache')] : []
  // Configuration is loaded by each actual ESLint worker. No config object is
  // serialized and no custom rule is removed to make concurrency possible.
  const child = spawnSync(process.execPath, [executable, '.', '--concurrency', 'auto', ...cacheArguments], { cwd: directory, stdio: 'inherit' })
  try {
    // A failed lint can never publish a new successful cache. Only this call's
    // private staging directory is removed; no peer/shared cache is touched.
    if (child.status === 0 && staging && existsSync(path.join(staging, 'cache'))) renameSync(path.join(staging, 'cache'), cache)
  } finally {
    if (staging) rmSync(staging, { recursive: true, force: true })
  }
  return child.status ?? 2
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (process.argv[2] === '--key') {
    try { console.log(lintCacheKey()) } catch { console.error('lint-cache: identity-unavailable'); process.exitCode = 2 }
  } else process.exitCode = lint()
}
