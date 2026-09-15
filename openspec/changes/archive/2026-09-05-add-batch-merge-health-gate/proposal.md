## Retirement

Archived on 2026-09-05 with `--skip-specs`. The change implemented a manual
exact-base and between-merges health route that is now superseded by the live
GitHub merge queue. Ruleset 22281319 runs required `check`, `guards`, and
`frontend` contexts on the `merge_group` tree and uses SQUASH/ALLGREEN, so
syncing this delta would make the baseline require an obsolete mechanism. The
incident and implementation details below remain as historical rationale. The
unapplied delta is preserved, explicitly non-normative, in
`retired-testing-delta.md` rather than under `specs/`.

## Why

The `Migration Chain Integrity (main)` workflow is already specified: a push to
`main` that touches a migration root family runs the merged tree's chain check
and fails loudly. It worked. On 2026-08-24 two PRs each numbered their migration
`core_204`, each was green because a pull request's CI can only ever see its own
branch, and the workflow went red on the merged tree exactly as designed.

Nobody read it. The batch merge driver checks each PR's own CI before merging it
and never reads the target branch's state between merges, so several more PRs
landed on a main that was already known-red.

Detection was never the gap. A post-merge check that nothing consumes is a check
credited with an answer it was never positioned to give, and specifying the
detector without specifying its reader is what left that gap open.

## What Changes

- Require the merge route to read the target branch's post-merge verdicts for the
  exact base SHA it is about to merge onto, and to halt the batch rather than
  merge the next PR onto a red branch.
- Require the four indistinguishable absences to be classified apart:
  path-filter excluded, run not created yet, run in flight (reported as the empty
  string, not null), and cancelled. None of them may be read as a pass.
- Require the local guard sweep that produces a green verdict to enumerate guards
  from the tree under test, because a new repo-wide guard is absent from every
  branch cut before it and an absent check is invisible to a fail-scan.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `testing`: gains one additive requirement covering the consumer of the
  post-merge integrity gates. No existing requirement changes.

## Impact

- New: `scripts/main_health_gate.py`, `tests/scripts/test_main_health_gate.py`.
- `scripts/merge_pr_exact_base.py` consumes the gate before issuing the merge
  request and gains the `premerge-target-branch-red` (exit 6) and
  `premerge-target-branch-health-unknown` (exit 7) outcomes.
- Documentation: `scripts/README.md`, `AGENTS.md`.

## Deferred

- Enforcing the gate from hosted CI rather than from the merge route. The route
  is the sole final merge path today, which is what makes it the reliable place
  to halt; a hosted merge queue would move that responsibility.
