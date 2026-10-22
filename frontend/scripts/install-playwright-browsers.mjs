import { spawn } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { signalGroup as signalPid } from './signal-group.mjs'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const cli = path.join(frontendRoot, 'node_modules/.bin/playwright')
const verifier = path.join(frontendRoot, 'scripts/playwright-cache-version.mjs')
// OS packages (apt, under sudo) and the browser download are separate phases so a slow
// apt on a congested runner cannot consume the browser download's deadline.
const DEPS_TERM_MS = 150_000
const DEPS_KILL_MS = 160_000
const TERM_MS = 110_000
const KILL_MS = 120_000
const BACKOFF_MS = 10_000
const TOTAL_MS = 720_000
const DEPS = { script: 'set -euo pipefail; "$1" install-deps chromium', termMs: DEPS_TERM_MS, killMs: DEPS_KILL_MS }
const BROWSER = {
  script: 'set -euo pipefail; "$1" install chromium ${3:+"$3"}; node "$2" --verify-browser',
  termMs: TERM_MS,
  killMs: KILL_MS,
}
let activeGroup
let expired = false

// Linux CI: detached children lead a process group, including ordinary descendants.
// Clear the group even when its shell exits before a TERM-resistant installer.
function signalGroup(signal) {
  signalPid(activeGroup, signal)
}

function attempt(phase, force) {
  return new Promise(resolve => {
    let settled = false
    let termed = false
    const child = spawn('bash', ['-c', phase.script,
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
    }, phase.termMs)
    const kill = setTimeout(() => finish(false), phase.killMs)
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
  const backoff = () => new Promise(resolve => setTimeout(resolve, BACKOFF_MS))
  let depsInstalled = false
  for (let index = 0; index < 2 && !expired; index += 1) {
    if (await attempt(DEPS, false)) {
      depsInstalled = true
      break
    }
    if (index < 1 && !expired) await backoff()
  }
  let installed = false
  for (let index = 0; depsInstalled && index < 3 && !expired; index += 1) {
    if (await attempt(BROWSER, index > 0)) {
      installed = true
      break
    }
    if (index < 2 && !expired) await backoff()
  }
  if (!depsInstalled) {
    console.error('Playwright OS dependency installation failed after two bounded attempts')
    process.exitCode = 1
  } else if (!installed) {
    console.error('Locked Playwright installation failed after three bounded attempts')
    process.exitCode = 1
  }
} finally {
  clearTimeout(total)
  signalGroup('SIGKILL')
}
