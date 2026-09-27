# reconcile-backup-recovery-truth

Define one content-blind, artifact-bound recovery proof without weakening backup coverage, credential recovery, bootstrap ownership, protected drill authority, dashboard truth, or operational evidence gates.

## Adoption status

The owner adopted the umbrella artifact at
`1ff636d44b590c0baddf7eb143ee23559d6d170e` on 2026-09-15. That decision
remains recorded for those exact bytes; it did not adopt the separate
`artifact-bound-filtered-event-restore-verification` sibling.

This revision corrects terminal persistence ordering and cleanup-failure
projection semantics, removes an incorrect promise that foreign-key additions
wait on `ACCESS SHARE` locks, and clarifies the existing private aggregate-count
allowance. It is a review candidate awaiting renewed exact-artifact owner
adoption after independent review. Earlier adoption, green CI, or merge
authorization does not adopt this revised candidate or its sibling.
