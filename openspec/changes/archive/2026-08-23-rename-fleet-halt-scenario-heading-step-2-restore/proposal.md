## Why

Step 2 of the two-step scenario rename begun in
`rename-fleet-halt-scenario-heading-step-1-retire`. That change removed
`Fleet-Halt Visibility` from `dashboard-spend-dashboard`; this one re-adds it
unchanged except for one scenario heading, which now names the guarantee (the
owner is notified once per breach window) instead of the mechanism that
delivers it (an attention-ledger push).

The mechanism is what `harden-runtime-auth-and-breaker-attention` replaces with
a durable outbox episode. A guarantee-named heading survives that replacement;
a mechanism-named one cannot, and OpenSpec deltas cannot correct a heading once
it is in the baseline.

## What Changes

- Re-add `Fleet-Halt Visibility` to `dashboard-spend-dashboard`, byte for byte
  as step 1 removed it, with the single scenario heading
  "An attention-ledger push notifies the owner once per breach window" replaced
  by "The owner is notified exactly once per breach window". No WHEN, THEN, or
  AND clause changes.

## Impact

- Affected specs: `dashboard-spend-dashboard`
- Affected code: none. This is a spec-heading correction only.
