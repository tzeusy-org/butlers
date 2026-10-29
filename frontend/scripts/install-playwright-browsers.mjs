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
const CLEANUP_MS = 2_000
// Reserved supervisor outcomes are emitted only after termination and reaping.
const DEPS_CLEANED_SUCCESS = 80
const DEPS_CLEANED_FAILURE = 81
const DEPS_CLEANED_TIMEOUT = 82
const DEPS_CLEANED_CANCELLED = 83
const depsSupervisor = path.join(frontendRoot, 'scripts/install-playwright-deps.py')
const BROWSER = {
  script: 'set -euo pipefail; "$1" install chromium ${3:+"$3"}; node "$2" --verify-browser',
  termMs: TERM_MS,
  killMs: KILL_MS,
}
let activeGroup
let activeDeps
let cleanupFailed = false
let expired = false

// Linux CI: detached children lead a process group, including ordinary descendants.
// Clear the group even when its shell exits before a TERM-resistant installer.
function signalGroup(signal) {
  return signalPid(activeGroup, signal)
}

function dependencyAttempt(profile) {
  return new Promise(resolve => {
    // The privileged supervisor creates, signals and reaps its own private
    // install group. Closing stdin also cancels it if this parent exits.
    const child = spawn('sudo', ['-n', '--', 'python3', depsSupervisor,
      String(DEPS_TERM_MS), String(DEPS_KILL_MS), process.execPath, cli, profile,
    ], { stdio: ['pipe', 'inherit', 'inherit'] })
    activeDeps = child
    child.stdin.on('error', () => {}) // An early supervisor exit may close the pipe.
    child.once('error', () => { cleanupFailed = true })
    child.once('close', code => {
      activeDeps = undefined
      // Generic zero/nonzero/signal exits cannot prove that cleanup ran.
      if (![DEPS_CLEANED_SUCCESS, DEPS_CLEANED_FAILURE,
        DEPS_CLEANED_TIMEOUT, DEPS_CLEANED_CANCELLED].includes(code)) {
        cleanupFailed = true
      }
      // Cancellation is cleaned but never retryable, including a supervisor
      // signal/EOF while this parent remains live.
      if (code === DEPS_CLEANED_CANCELLED) expired = true
      resolve({ success: code === DEPS_CLEANED_SUCCESS && !expired,
        deadline: code === DEPS_CLEANED_TIMEOUT && !expired && !cleanupFailed })
    })
  })
}

function cancel() {
  expired = true
  activeDeps?.stdin.end()
  signalGroup('SIGKILL')
  process.exitCode = 1
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
      if (!signalGroup('SIGKILL')) cleanupFailed = true
      if (!cleanupFailed) activeGroup = undefined
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

// Reserve the final two seconds for the privileged supervisor's cleanup.
const total = setTimeout(cancel, TOTAL_MS - CLEANUP_MS)
const hardLimit = setTimeout(() => {
  cancel()
  process.exit(1)
}, TOTAL_MS)
for (const signal of ['SIGINT', 'SIGTERM']) {
  process.once(signal, () => {
    cancel()
  })
}
process.once('exit', () => {
  activeDeps?.stdin.end()
  signalGroup('SIGKILL')
})
try {
  const backoff = () => new Promise(resolve => setTimeout(resolve, BACKOFF_MS))
  let depsInstalled = false
  let dependencyProfile = 'runner-default'
  for (let index = 0; index < 2 && !expired && !cleanupFailed; index += 1) {
    const outcome = await dependencyAttempt(dependencyProfile)
    if (outcome.success) {
      depsInstalled = true
      break
    }
    dependencyProfile = outcome.deadline ? 'official-ubuntu-recovery' : 'runner-default'
    if (index < 1 && !expired && !cleanupFailed) await backoff()
  }
  let installed = false
  for (let index = 0; depsInstalled && index < 3 && !expired && !cleanupFailed; index += 1) {
    if (await attempt(BROWSER, index > 0)) {
      installed = true
      break
    }
    if (index < 2 && !expired && !cleanupFailed) await backoff()
  }
  if (cleanupFailed) {
    console.error('Playwright cleanup could not be confirmed; refusing retry')
    process.exitCode = 1
  } else if (expired) {
    console.error('Playwright installation cancelled; refusing retry')
    process.exitCode = 1
  } else if (!depsInstalled) {
    console.error('Playwright OS dependency installation failed after two bounded attempts')
    process.exitCode = 1
  } else if (!installed) {
    console.error('Locked Playwright installation failed after three bounded attempts')
    process.exitCode = 1
  }
} finally {
  clearTimeout(total)
  clearTimeout(hardLimit)
  signalGroup('SIGKILL')
}
