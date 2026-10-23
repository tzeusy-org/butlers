// Signal only the detached group owned by this installer. A denied signal
// must not crash the caller, but cannot authorize forgetting ownership/retry.
export function signalGroup(group, signal, kill = process.kill) {
  if (group === undefined) return true
  try {
    kill(-group, signal)
    return true
  } catch (error) {
    if (error.code === 'ESRCH') return true
    if (error.code === 'EPERM') return false
    throw error
  }
}
