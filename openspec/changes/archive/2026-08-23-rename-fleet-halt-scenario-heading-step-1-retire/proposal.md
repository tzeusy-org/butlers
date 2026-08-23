## Why

`openspec/specs/dashboard-spend-dashboard/spec.md` carries a scenario headed
"An attention-ledger push notifies the owner once per breach window". The
heading names a *mechanism*, and `harden-runtime-auth-and-breaker-attention`
replaces that mechanism: the fleet-halt push becomes a durable attention
episode appended through Switchboard's outbox, which does not write an
attention-ledger row and does not page the owner directly. When that change
archives, the baseline will carry a heading that contradicts its own body.

A `## MODIFIED Requirements` block cannot fix this. OpenSpec 1.9.0's
`findMissingCurrentScenarios` matches scenario NAMES only, so a MODIFIED block
must reproduce every baseline scenario name verbatim and archive writes the
stale name straight back. Renaming a baseline scenario takes two changes
archived in order: this one retires the requirement, and
`rename-fleet-halt-scenario-heading-step-2-restore` re-adds it under a heading
that names the guarantee instead.

## What Changes

- Remove `Fleet-Halt Visibility` from `dashboard-spend-dashboard` so that
  step 2 can re-add it under a corrected scenario heading. Nothing about the
  required behaviour changes; the requirement's body is re-added byte for byte
  in step 2 apart from the one heading.

## Impact

- Affected specs: `dashboard-spend-dashboard`
- Affected code: none. This is a spec-heading correction only.
