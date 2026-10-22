import { expect, it } from 'vitest'
import { signalGroup } from './signal-group.mjs'

const failing = code => () => {
  throw Object.assign(new Error(`kill ${code}`), { code })
}

it('signals the negated process group', () => {
  const calls = []
  signalGroup(4242, 'SIGKILL', (target, signal) => calls.push([target, signal]))
  expect(calls).toEqual([[-4242, 'SIGKILL']])
})

// ESRCH: the group is already gone. EPERM: a root-owned apt (sudo) is in the group; the
// installer must still reach its retry/backoff path instead of crashing.
it('tolerates a gone group and an unsignalable root-owned member', () => {
  expect(() => signalGroup(4242, 'SIGKILL', failing('ESRCH'))).not.toThrow()
  expect(() => signalGroup(4242, 'SIGKILL', failing('EPERM'))).not.toThrow()
})

it('still surfaces other failures and ignores an unset group', () => {
  expect(() => signalGroup(4242, 'SIGKILL', failing('EINVAL'))).toThrow(/EINVAL/)
  expect(() => signalGroup(undefined, 'SIGKILL', failing('EINVAL'))).not.toThrow()
})
