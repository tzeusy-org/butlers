## Context

`GET /api/issues` groups only audit rows whose persisted outcome is
`result="error"`. Credential probe failures and a model verification sweep can
record a failure action without that outcome, so the read-side failure spine
cannot observe them. The audit page's default `kind=privileged` filter is also
defined as two exclusions, which admits routine successful cadence rows rather
than selecting consequential events. Finally, its single bare-date `since`
filter has the same UTC-midnight truncation that session filters previously
fixed for owner-timezone day windows.

The audit log is append-only by policy. This change therefore needs a narrowly
bounded, idempotent repair exception that writes only a missing outcome on the
known historical failure action; it must not rewrite timestamps, actors,
actions, targets, notes, or errors.

## Goals / Non-Goals

**Goals:**

- Persist an explicit `success` or `error` outcome at the relevant credential,
  model, and approval writer boundaries, and attach a safe error summary for
  failed probes or verification sweeps.
- Repair legacy `action="failed"` rows without broad historical mutation.
- Make `kind=privileged` select consequence-bearing actions and all explicit
  errors, while leaving `?noise=all` as the complete-history opt-out.
- Reuse one owner-timezone calendar-day bound resolver for session and audit
  filters, with deterministic tests independent of the process clock.

**Non-Goals:**

- Backfill every null `result` row, infer outcome from free-text notes, or
  rewrite the audit log's other historical fields.
- Change issue grouping, retention duration, pagination, or the meaning of a
  legacy full ISO `since` filter.
- Add a generic audit trigger or make `audit.append()` require an outcome for
  unrelated callers.

## Decisions

### Write outcomes at the semantic writer boundary

Credential audit helpers map `verified` to `success` and `failed` to `error`;
the probe message is stored in the error field for failures. Credential lifecycle
successes, approval decisions, and model mutations record `success`. A model
verify-all run records `error` plus a bounded aggregate summary when one or
more model checks fail, otherwise `success`.

This preserves the generic `audit.append()` compatibility contract while
making the producers whose outcomes feed `/issues` explicit. A database-level
`NOT NULL` or action check was rejected: it would break existing valid
append-only producers before all action families have defined an outcome.

### Repair only legacy failed actions

An idempotent core migration uses a guarded update of rows where
`action = 'failed' AND result IS NULL`, setting `result = 'error'` only. It
does not manufacture an error message from a note and has no downgrade data
mutation. The final revision is selected only after the active core migration
frontier is rechecked immediately before publication.

This is preferred over a runtime read fallback because future issue grouping,
the audit API, and direct SQL all then see the same durable outcome. It is
intentionally narrower than a wholesale null-result backfill, which would be
speculation rather than a proven repair.

### Use an allowlist for the privileged view

`kind=privileged` is a fixed SQL predicate accepting `approval.*`, the existing
`approvals.policy` mutation, `model.*`, `permission.*`, `data.*`, `webhook.*`,
credential lifecycle actions, and every row with `result = 'error'`. It remains
a conjunct with caller filters and is not client-controlled SQL. Omitting
`kind` continues to return all rows, which keeps the existing `?noise=all`
behavior.

An expanded denylist was rejected because new cadence actions would continue
to leak into the default view without an explicit product decision.

### Extract owner-timezone bounds into an API utility

Move the owner timezone resolver and bare-day parsing from `sessions.py` to a
small shared API utility. Bare `YYYY-MM-DD` values become local start-of-day
for a lower bound and local end-of-day for an inclusive upper bound; full ISO
timestamps pass through unchanged and malformed values raise 422. Sessions
keeps its database-manager wrapper, while audit resolves the timezone through
its shared pool only when `from_date` or `to_date` is provided.

The audit API adds raw string `from_date` and `to_date` parameters and combines
them with any legacy ISO `since` condition. The page sends those new parameters
from URL-backed From/To date inputs; it does not reinterpret older `since`
links.

## Risks / Trade-offs

- **A core revision collides with another worker's migration** → defer number
  selection, inspect the shared frontier before committing, then rebase and
  rerun migration tests after the final chain is chosen.
- **A probe message contains sensitive material** → pass the existing
  user-facing probe diagnostic only; do not serialize a credential value or
  raw exception object.
- **The allowlist hides a newly introduced privileged family** → include
  `result='error'` unconditionally and add an action-family contract test; a
  new successful family must make an explicit allowlist decision.
- **Timezone regressions vary under the nightly faketime matrix** → tests use
  fixed date values and a concrete `Asia/Singapore` timezone, never
  `datetime.now()`.

## Migration Plan

1. Land the writer and API/UI changes with focused unit, integration, and
   migration tests.
2. Apply the guarded core migration after the current migration frontier.
3. Verify `action='failed'` rows with a missing result are now error rows and
   rerun the migration to prove it is a no-op.
4. If rollback is required, leave repaired audit evidence intact; code can roll
   back without deleting or reverting historical audit rows.

## Open Questions

- None. The existing owner timezone is the authority used by session filters,
  and the historical repair scope is limited to the proven `failed` action.
