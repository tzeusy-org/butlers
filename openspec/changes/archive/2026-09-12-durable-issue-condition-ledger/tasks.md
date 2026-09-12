## 1. Condition ledger

- [x] 1.1 Add core migration `core_200_butler_reachability_conditions` creating
  `public.butler_reachability_conditions` with a PARTIAL unique index on
  `(butler) WHERE resolved_at IS NULL`, a `(butler, started_at DESC)` history
  index, and per-butler-role grants.
- [x] 1.2 Add `butlers.api.reachability_ledger` with an atomic open-or-extend
  upsert that never moves an open episode's onset, a terminal recovery close,
  and an open-onset read.
- [x] 1.3 Record each `GET /api/issues` probe into the ledger and project the
  episode's onset as the issue's `first_seen_at`/`recurrence_at`, keeping
  `last_seen_at` as the probe clock.
- [x] 1.4 Flag a genuine ledger write failure through `meta.sources_degraded`
  as `reachability-ledger`; do not flag a pre-migration table.

## 2. Acknowledgement epochs

- [x] 2.1 Add `Issue.recurrence_at` and hold acknowledge-until-recurrence
  against it, falling back to `last_seen_at`.
- [x] 2.2 Derive a reachability ack's watermark from the open episode in
  `POST /api/issues/dismiss`, and return 503 when the ledger is unreadable.

## 3. Exact Audit evidence door

- [x] 3.1 Add `build_audit_group_for_row_query` resolving one `audit_log` row id
  to its group through the shared `normalized_errors` CTE.
- [x] 3.2 Add `GET /api/issues/group-for-audit/{audit_id}` returning
  `AuditIssueGroupRef` with found / stated-absence / 503 as three distinct
  outcomes, and a window auto-widened to contain the row.
- [x] 3.3 Replace the Audit Log's `?q=` link with `AuditIssuesDoor`, mounted
  only inside the expanded detail row.

## 4. Scoped Issues copy

- [x] 4.1 Filter the Issues feed on an exact `?group=` `issue_key` with a
  clearable chip.
- [x] 4.2 Name the active scope in the panel's empty state and the verdict
  opener's all-clear.

## 5. Verification

- [x] 5.1 Router tests: continuous-down, recovery, recurrence, degraded ledger,
  ack watermark derivation, and the door's three outcomes.
- [x] 5.2 Migrated-Postgres tests for the partial index, episode lifecycle, and
  historical/normalized row resolution.
- [x] 5.3 Frontend tests for the door's three renderings, the `?group=` pin, the
  scoped empty copy, and the clear affordance's accessible name.
