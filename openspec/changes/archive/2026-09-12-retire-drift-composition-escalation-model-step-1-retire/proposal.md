## Why

`openspec/specs/deployment-and-drift/spec.md` states `QA Escalation After
Sustained Drift` in the drift-composition model: a first-detected marker keyed
by a fingerprint of the whole drifted set, one escalation per composition.
`define-infrastructure-reliability-lifecycle` replaces that outright with a
per-`(schema, chain)` episode on `infrastructure-reliability`'s L0/L1/L2/L3
schedule, and two of its scenarios assert the opposite of two baseline ones
(listed below).

A `## MODIFIED Requirements` block cannot retire them. OpenSpec 1.9.0's
`findMissingCurrentScenarios` matches scenario NAMES only, so a MODIFIED block
must reproduce every baseline scenario name verbatim -- bu-uu9w8 / PR #3796
carried them back for exactly that reason -- and archive writes them straight
back. Retiring a baseline requirement takes two changes archived in order:
this one removes it, and
`retire-drift-composition-escalation-model-step-2-restore` re-adds it carrying
the episode model alone.

## What Changes

- Remove `QA Escalation After Sustained Drift` from `deployment-and-drift` so
  that step 2 can re-add it stating exactly one escalation model. The
  escalation behaviour is not being retired; it is restated in step 2.

The two flat contradictions this resolves:

- baseline `First sighting does not escalate` mandates "a first-detected marker
  is persisted (keyed by a stable fingerprint of that composition)", while
  `First sighting opens L0 evidence` states it "does not use a composition-wide
  audit marker as current-state authority";
- baseline `Drift past the 24h threshold escalates exactly once` persists an
  escalated marker "so subsequent ticks do not re-escalate it", while
  `Continuing drift re-escalates without additional healing attempts` requires
  exactly that re-escalation. That baseline scenario also closes with "this is
  an accepted simplification, not a continuously-tracked single episode" -- a
  direct repudiation of the model that replaces it.

The other two baseline scenarios are not contradictions, only the retired
vocabulary: `Drift within the 24h threshold does not escalate` and `An
escalation attempt failure degrades, does not crash` are restated by their
episode-model counterparts over the same 24-hour window and the same
degrade-not-crash guarantee. They go with the rest because retire/restore
replaces the whole requirement.

## Impact

- Affected specs: `deployment-and-drift`
- Affected code: none. This is a spec-model retirement step only; the
  implementation work lives in `define-infrastructure-reliability-lifecycle`.
