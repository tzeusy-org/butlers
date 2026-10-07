import { spawn } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const cli = path.join(frontendRoot, 'node_modules/.bin/playwright')
const verifier = path.join(frontendRoot, 'scripts/playwright-cache-version.mjs')
const TERM_MS = 110_000
const KILL_MS = 120_000
const BACKOFF_MS = 10_000
const TOTAL_MS = 380_000
let activeGroup
let expired = false

// Linux CI: detached children lead a process group, including ordinary descendants.
// Clear the group even when its shell exits before a TERM-resistant installer.
function signalGroup(signal) {
  if (activeGroup === undefined) return
  try {
    process.kill(-activeGroup, signal)
  } catch (error) {
    if (error.code !== 'ESRCH') throw error
  }
}

function attempt(force) {
  return new Promise(resolve => {
    let settled = false
    let termed = false
    const child = spawn('bash', ['-c',
      'set -euo pipefail; "$1" install --with-deps chromium ${3:+"$3"}; node "$2" --verify-browser',
      'bash', cli, verifier, force ? '--force' : '',
    ], { detached: true, stdio: 'inherit' })
    activeGroup = child.pid
    const finish = success => {
      if (settled) return
      settled = true
      clearTimeout(term)
      clearTimeout(kill)
      signalGroup('SIGKILL')
      activeGroup = undefined
      resolve(success && !expired)
    }
    const term = setTimeout(() => {
      termed = true
      signalGroup('SIGTERM')
    }, TERM_MS)
    const kill = setTimeout(() => finish(false), KILL_MS)
    child.once('error', () => finish(false))
    child.once('exit', code => {
      // After TERM, retain the group through the grace period even if bash exits.
      if (!termed) finish(code === 0)
    })
  })
}

const total = setTimeout(() => {
  expired = true
  signalGroup('SIGKILL')
  process.exit(1)
}, TOTAL_MS)
for (const signal of ['SIGINT', 'SIGTERM']) {
  process.once(signal, () => {
    signalGroup('SIGKILL')
    process.exit(1)
  })
}
process.once('exit', () => signalGroup('SIGKILL'))
try {
  let installed = false
  for (let index = 0; index < 3 && !expired; index += 1) {
    if (await attempt(index > 0)) {
      installed = true
      break
    }
    if (index < 2 && !expired) {
      await new Promise(resolve => setTimeout(resolve, BACKOFF_MS))
    }
  }
  if (!installed) {
    console.error('Locked Playwright installation failed after three bounded attempts')
    process.exitCode = 1
  }
} finally {
  clearTimeout(total)
  signalGroup('SIGKILL')
}
