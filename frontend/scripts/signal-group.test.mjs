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

// ESRCH confirms absence; EPERM must retain cleanup ownership and forbid retry.
it('distinguishes a gone group from an unsignalable member', () => {
  expect(signalGroup(4242, 'SIGKILL', failing('ESRCH'))).toBe(true)
  expect(signalGroup(4242, 'SIGKILL', failing('EPERM'))).toBe(false)
})

it('still surfaces other failures and ignores an unset group', () => {
  expect(() => signalGroup(4242, 'SIGKILL', failing('EINVAL'))).toThrow(/EINVAL/)
  expect(() => signalGroup(undefined, 'SIGKILL', failing('EINVAL'))).not.toThrow()
})
