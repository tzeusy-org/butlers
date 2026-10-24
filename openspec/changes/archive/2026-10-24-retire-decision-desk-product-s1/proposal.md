# Retire Decision Desk frontend and Helm bridge (S1)

## Why

Owner decision bu-g2zuv2 = A (2026-10-21) removes engineering tracker tooling
from the personal-assistant product. bu-99t5s3.6 steps 1-2 are this slice.

## What Changes

- Remove Decision Desk and bead-detail frontend pages, route/command entries,
  hooks, badge queries and associated product-only tests.
- Remove the Helm bridge workload/export mounts/credential declaration and
  bridge image build; retain effective tracker confinement for every pod.
- Preserve approvals, QA, owner authentication and the engineering `bd` workflow.

## Impact

Backend routes, jobs/TOML, callbacks and intent tables remain for subsequent
removal after finite API4949 custody and an actually safe deployed transition.
No table drop, live deploy/credential operation, digest or history-loss execution
occurs here. The parent six acceptance criteria remain open across slices.
