## Why

The Spend page's whole claim on the owner's attention is that its numbers can
direct action. Two things on it currently cannot.

[Observed] `GET /api/spend/by-schedule` reported the live weekly
relationship-maintenance cron `0 9 * * 1` as `runs_per_day=1` and
`projected_monthly_usd=$3.560701`. The estimator in
`butlers.core.sessions._estimate_runs_per_day` counted occurrences in the next
24 hours, so a weekly cron read as `1` when the request happened to land on a
Monday and `0` on every other day; the router then multiplied that by a
hardcoded `30`. A weekly schedule was therefore presented as thirty monthly
runs against a true ~4.35 — a sevenfold overstatement, and one that silently
changed depending on which day the owner opened the page. Nothing in the
response said what the projection had been multiplied by, and the projected
figure sat in the same undifferentiated run of columns as the measured range
cost, so a forecast read as history.

[Observed] The routing-rules table deleted a live first-match rule directly from
the row's Remove control. The API returns `204` and compacts the positions
below, so one mis-aimed click permanently removed a rule and silently re-pointed
every dispatch that rule used to catch — at the next rule down, or at default
model routing — with no confirmation, no record of what was removed, and no way
back.

## What Changes

- Replace the next-24-hours occurrence count with a cadence estimator that
  enumerates a cron expression's firings from a fixed anchor and scales them to
  an average Gregorian calendar month (30.436875 days). The estimate becomes a
  pure function of the cron string: it no longer moves with the time of the
  request.
- Take the counting window from the expression rather than from the sample: a
  5-field cron is periodic in exactly one of three lengths, decided by which
  calendar fields it restricts (dom/month → a year, dow only → a week, neither
  → a day), and occurrences are counted over exactly one such cycle. A window
  chosen from what *fits* in a capped sample instead of from what the cron
  *requires* reports `* * * 1 *` at 43,829/month against a true 3,720.
- Delete the hardcoded `× 30` in `_schedule_costs_from_data`.
  `projected_monthly_usd` becomes exactly `avg_cost_per_run ×
  projected_monthly_runs`, with no free-floating multiplier anywhere in the
  chain.
- **Contract change:** rename the payload key `runs_per_day` to
  `projected_monthly_runs`, and add `forecast_basis` — a human-readable
  statement of the basis carrying the literal number `30.436875` — once on the
  response envelope (`meta`), since it is a constant of the estimator rather
  than a property of any one schedule. This is a contract
  change on the `schedule_costs` **core MCP tool**
  (`src/butlers/core_tools/_scheduling.py`), not only on the HTTP response — butler LLM sessions read those output keys. No
  migration, dual-read, or backfill is involved: the value is computed on read
  from a live query in `schedule_costs` and never persisted.
- Render the by-schedule table under two visually separated column groups,
  measured and forecast, with the basis stated beneath the table, and render an
  em dash rather than `$0.00` for a schedule whose cadence could not be
  computed.
- Gate routing-rule deletion behind a confirmation showing the exact condition,
  action, `position N of M`, and the first-match consequence of removing it.
- Offer a persistent restore after a successful delete, built on the existing
  `POST /api/spend/rules` insert-shift, which is the exact inverse of the
  delete's compaction. Its copy claims only that the rule was restored, never
  that the previous first-match order was: an intervening create or reorder
  changes what the captured position means.
- Surface the removed rule's condition and action persistently when a restore
  fails, so a failure cannot destroy a rule and tell no one.
- Put delete, restore, and reorder in one mutation scope so a restore cannot
  overtake an in-flight reorder and land at a position computed from stale
  ordering.

## Capabilities

### Modified Capabilities

- `dashboard-spend-dashboard` — adds `Requirement: Schedule Cadence Forecast`
  and `Requirement: Routing Rule Deletion Safety`; modifies
  `Requirement: Spend API` (by-schedule field rename and measured/forecast
  separation; the "smarter estimator" TODO is narrowed to the month-end
  estimator) and `Requirement: Spend Dashboard Page` (by-schedule column groups,
  gated rule removal).

### Out of Scope

- The month-end spend estimator behind `GET /api/spend/forecast`. Its
  code-level TODO stands; only the per-schedule cadence moves here.
- Any new endpoint, tombstone table, retention policy, or audit semantics for
  rule deletion. The restore is built entirely on the existing create/delete
  position arithmetic; no migration is added by this change.
- Rule *editing* safety. Only deletion is gated here.
