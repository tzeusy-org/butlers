## Why

Step 2 of the two-step retirement begun in
`retire-drift-composition-escalation-model-step-1-retire`, which removed `QA
Escalation After Sustained Drift` from `deployment-and-drift`. This one re-adds
it stating one escalation model instead of two.

The baseline stated the drift-composition model;
`define-infrastructure-reliability-lifecycle` replaces it with a
per-`(schema, chain)` `deployment_drift` episode on
`infrastructure-reliability`'s L0/L1/L2/L3 schedule, and two of its scenarios
assert the opposite of two baseline ones. A
`## MODIFIED` block cannot express that replacement -- it must reproduce every
baseline scenario name verbatim, so it can only carry the contradiction or
silently prune baseline content. Removing the requirement and re-adding it can.

## What Changes

- Re-add `QA Escalation After Sustained Drift` to `deployment-and-drift`
  carrying the six episode-model scenarios only. They are the scenarios
  `define-infrastructure-reliability-lifecycle` authored, moved here byte for
  byte; that change's `## MODIFIED` block for this requirement is dropped in
  the same commit, so the requirement has exactly one author again.
- The four drift-composition-model scenarios (`First sighting does not
  escalate`, `Drift within the 24h threshold does not escalate`, `Drift past
  the 24h threshold escalates exactly once`, `An escalation attempt failure
  degrades, does not crash`) do not return. The first and third contradict
  their replacements; the second and fourth are restated by `Drift within the
  source-owned grace does not escalate` and `An escalation consequence failure
  degrades, does not crash` over the same 24-hour window and the same
  degrade-not-crash guarantee.
- **BREAKING** relative to the retired baseline: a continuing drift condition
  is no longer permanently silenced after its first escalation. This is the
  same break `define-infrastructure-reliability-lifecycle` already declares; it
  is restated here because this change is now the one that writes it into the
  baseline.

## Impact

- Affected specs: `deployment-and-drift`
- Affected code: none. The implementation work stays in
  `define-infrastructure-reliability-lifecycle` (tasks 2.1 and 2.2).
