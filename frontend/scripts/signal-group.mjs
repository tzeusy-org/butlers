// Signal a detached child's whole process group.
//
// ESRCH: the group is already gone. EPERM: the group still holds a process we may not
// signal (the root-owned apt that `playwright install-deps` runs under sudo). Neither is
// an installer failure, so neither may crash the caller before it can retry or report.
export function signalGroup(group, signal, kill = process.kill) {
  if (group === undefined) return
  try {
    kill(-group, signal)
  } catch (error) {
    if (error.code !== 'ESRCH' && error.code !== 'EPERM') throw error
  }
}
