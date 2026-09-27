## 1. Failure Outcome Boundaries

- [x] 1.1 Add focused failing tests for credential probes, model verification,
  approval decisions, and model mutations to persist explicit outcomes and safe
  failure diagnostics.
- [x] 1.2 Implement the outcome assignments at those audit append boundaries
  without changing unrelated generic append callers.
- [x] 1.3 Add a regression contract test that enumerates the affected
  failure-semantic action families.

## 2. Historical Outcome Repair

- [x] 2.1 Select the current core migration successor immediately before
  creating the revision and add the guarded idempotent `action='failed'`
  outcome repair.
- [x] 2.2 Add migration tests proving matching rows are repaired, all other
  fields and rows are preserved, absence of the table is safe, and a rerun is
  a no-op.

## 3. Audit Read Semantics

- [x] 3.1 Replace the privileged-view denylist with the documented
  consequence allowlist and cover action/error/noise behavior.
- [x] 3.2 Extract the owner-timezone time-bound resolver from sessions and use
  it for audit `from_date`/`to_date` filters while retaining ISO `since`.
- [x] 3.3 Add deterministic owner-timezone API coverage, including same-day
  From/To, ISO pass-through, invalid dates, and faketime-safe fixed bounds.

## 4. Dashboard and Verification

- [x] 4.1 Add URL-backed From and To audit inputs and typed API parameters,
  retaining the `?noise=all` opt-out.
- [x] 4.2 Run focused Python, migration, frontend, formatting, lint, and
  OpenSpec validation gates; fetch fresh `origin/main` and repeat the
  migration-frontier check before publishing. Rebase only if that check
  demonstrates a migration conflict.
