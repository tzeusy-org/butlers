## 1. Cadence estimator

- [x] 1.1 Replace `_estimate_runs_per_day` with `_estimate_monthly_runs` in `src/butlers/core/sessions.py`: fixed `2001-01-01T00:00:00Z` anchor, `AVERAGE_MONTH_DAYS = 365.2425 / 12`, and public `CADENCE_BASIS_DESCRIPTION` carrying the literal `30.436875`. Return `0.0` (never raise) for an expression croniter cannot parse, one that `is_valid` accepts but that raises `CroniterBadDateError` while enumerating, or one too dense to sample.
- [x] 1.2 Derive the counting window from the expression rather than from the sample: `_cadence_cycle_days` classifies a cron by which calendar fields it restricts — dom/month (or `n#w`) → 365 days, dow only → 7 days, neither → 1 day — and the window is exactly one such cycle from the anchor. Bound the enumeration with `_CADENCE_MAX_OCCURRENCES = 10_100` (per-minute over a week is 10,080) and peek one occurrence past the cap so a fully-sampled expression is not mistaken for capped. Re-run a zero-count annual cycle against `_CADENCE_HORIZON_DAYS = 1461` for rare day-of-month expressions such as `0 9 29 2 *`.
- [x] 1.3 Emit `projected_monthly_runs` per schedule from `schedule_costs` alongside the measured totals, with `forecast_basis` stated once beside the rows rather than copied into each, documented at the call site as forecast input rather than measured history.
- [x] 1.4 Fixed-clock unit tests in `tests/core/test_schedule_cadence.py` (no DB, no Docker): weekly ~4.35, daily, monthly, yearly, Mon/Wed/Fri, hourly, per-minute, clock-invariance across four fixed references, invalid-cron zero, the seasonal cases `0 * * 1 *` (62.04, not 81.02) and `0 * * 1,7 *`, the classification table itself, per-minute on **all seven** weekdays at exactly 6261.30 (days-only truncation gives 4873 on Wednesday and 3374 on Sunday), `CroniterBadDateError` expressions returning `0.0`, the too-dense cases `* * * 1 *` and `* * 1 * *` returning `0.0`, the leap-day widening, and that `* * * * *` terminates in under 5 s at exactly 43,829.10.

## 2. API contract

- [x] 2.1 Rename `ScheduleCost.runs_per_day` to `projected_monthly_runs` in `src/butlers/api/models/__init__.py` and put `forecast_basis` on the response envelope (`ApiMeta`, which already allows extra keys) rather than on each row, documenting the measured and forecast groups on the model.
- [x] 2.2 Delete the hardcoded `* 30` in `_schedule_costs_from_data` (`src/butlers/api/routers/spend.py`); compute `projected_monthly_usd = avg_cost_per_run * projected_monthly_runs`. Carry `projected_monthly_runs` through the multi-model merge bucket once rather than summing it, and set `meta.forecast_basis` once on the response.
- [x] 2.3 Update `tests/api/test_spend.py` fixtures to the new key and add coverage that the forecast fields are separate from the measured fields, that `projected_monthly_usd` is exactly the product of the two exposed numbers, and that an unparseable cron projects zero while still reporting its measured history.

## 3. By-schedule presentation

- [x] 3.1 Split the By Schedule table into two visually separated column groups ("Measured · selected range", "Forecast · per month") in `frontend/src/pages/SpendPage.tsx`, surface `total_cost_usd`, and state the API's `meta.forecast_basis` once beneath the table.
- [x] 3.2 Render an em dash rather than `$0.00` for a schedule whose cadence could not be computed; format projected runs to one decimal, since the number is a cadence and not a count.
- [x] 3.3 Update `frontend/src/api/types.ts`: `ScheduleCost` gets the renamed field and the two documented groups, `SpendFanoutMeta` gets the optional `forecast_basis`.

## 4. Routing-rule deletion safety

- [x] 4.1 Gate Remove behind a `ConfirmDialog` showing the rule's exact condition and action, `position N of M`, and the first-match consequence (which rule inherits the traffic, or default routing); add an `onCloseAutoFocus` escape hatch to `ConfirmDialog` and restore focus to the Remove trigger when it survives.
- [x] 4.2 Guard against multi-activation within one tick with an in-flight ref, keep the dialog mounted through the mutation, and leave it open on failure.
- [x] 4.3 Add a persistent restore affordance posting the captured `condition`/`action`/`position`; clear it once used and never offer it when the delete failed.
- [x] 4.4 Keep the restore's copy and success message to what the request actually does — no claim that the previous first-match order was reinstated.
- [x] 4.5 When the restore fails, surface the removed rule *recoverably*, not merely visibly: render the complete `position`/`condition`/`action` preimage as selectable, copyable text equal to the request body that re-creates it, and leave the affordance retryable.
- [x] 4.6 Put delete, restore, and reorder in one `spend-rule-order` mutation scope.
- [x] 4.7 Vitest coverage in `frontend/src/pages/SpendPage.test.tsx`: no delete on one activation, dialog evidence and first-match consequence, default-routing wording for the last rule, cancel restores focus and deletes nothing, exactly one DELETE under repeat activation, restore posts the captured position, affordance cleared after use, no affordance after a failed delete, the copy makes no exact-order promise, a failed restore renders a copyable preimage that parses equal to the body the retry posts and stays retryable, and the restore serializes behind an in-flight reorder.

## 5. Documentation

- [x] 5.1 Update `docs/runtime/session-lifecycle.md`'s `schedule_costs` description and `src/butlers/core_tools/_scheduling.py`'s tool docstring to the new field and basis placement.
