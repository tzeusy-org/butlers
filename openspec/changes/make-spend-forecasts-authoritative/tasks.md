## 1. Cadence estimator

- [x] 1.1 Replace `_estimate_runs_per_day` with `_estimate_runs_per_month` in `src/butlers/core/sessions.py`: fixed `2001-01-01T00:00:00Z` anchor, 1461-day horizon, `AVERAGE_MONTH_DAYS = 365.2425 / 12`, and public `CADENCE_BASIS_DESCRIPTION` carrying the literal `30.436875`. Return `0.0` (never raise) for an expression croniter cannot parse.
- [x] 1.2 Bound the enumeration with `_CADENCE_MAX_OCCURRENCES = 2000` and truncate a cap-shortened window to the longest whole calendar cycle that fits (years, else weeks, else days) via `_truncate_cadence_window`, recounting inside it. Peek one occurrence past the cap so a fully-sampled expression is not mistaken for capped.
- [x] 1.3 Emit `runs_per_month` and `forecast_basis` from `schedule_costs` alongside the measured totals, documented at the call site as forecast input rather than measured history.
- [x] 1.4 Fixed-clock unit tests in `tests/core/test_schedule_cadence.py` (no DB, no Docker): weekly ~4.35, daily, monthly, yearly, Mon/Wed/Fri, hourly, per-minute, clock-invariance across four fixed references, invalid-cron zero, and the seasonal cases `0 * * 1 *` (~62, not ~81), `0 * * 1,7 *`, and `* * * * 1`.

## 2. API contract

- [x] 2.1 Rename `ScheduleCost.runs_per_day` to `projected_monthly_runs` and add `forecast_basis` in `src/butlers/api/models/__init__.py`, documenting the measured and forecast groups on the model.
- [x] 2.2 Delete the hardcoded `* 30` in `_schedule_costs_from_data` (`src/butlers/api/routers/spend.py`); compute `projected_monthly_usd = avg_cost_per_run * projected_monthly_runs`. Carry `runs_per_month`/`forecast_basis` through the multi-model merge bucket once rather than summing them.
- [x] 2.3 Update `tests/api/test_spend.py` fixtures to the new key and add coverage that the forecast fields are separate from the measured fields, that `projected_monthly_usd` is exactly the product of the two exposed numbers, and that an unparseable cron projects zero while still reporting its measured history.

## 3. By-schedule presentation

- [x] 3.1 Split the By Schedule table into two visually separated column groups ("Measured · selected range", "Forecast · per month") in `frontend/src/pages/SpendPage.tsx`, surface `total_cost_usd`, and state the API's `forecast_basis` once beneath the table.
- [x] 3.2 Render an em dash rather than `$0.00` for a schedule whose cadence could not be computed; format projected runs to one decimal, since the number is a cadence and not a count.
- [x] 3.3 Update `frontend/src/api/types.ts` `ScheduleCost` with the new fields and document the two groups for callers.

## 4. Routing-rule deletion safety

- [x] 4.1 Gate Remove behind a `ConfirmDialog` showing the rule's exact condition and action, `position N of M`, and the first-match consequence (which rule inherits the traffic, or default routing); add an `onCloseAutoFocus` escape hatch to `ConfirmDialog` and restore focus to the Remove trigger when it survives.
- [x] 4.2 Guard against multi-activation within one tick with an in-flight ref, keep the dialog mounted through the mutation, and leave it open on failure.
- [x] 4.3 Add a persistent restore affordance posting the captured `condition`/`action`/`position`; clear it once used and never offer it when the delete failed.
- [x] 4.4 Keep the restore's copy and success message to what the request actually does — no claim that the previous first-match order was reinstated.
- [x] 4.5 Surface the removed rule's condition and action persistently when the restore fails, and leave the affordance retryable.
- [x] 4.6 Put delete, restore, and reorder in one `spend-rule-order` mutation scope.
- [x] 4.7 Vitest coverage in `frontend/src/pages/SpendPage.test.tsx`: no delete on one activation, dialog evidence and first-match consequence, default-routing wording for the last rule, cancel restores focus and deletes nothing, exactly one DELETE under repeat activation, restore posts the captured position, affordance cleared after use, no affordance after a failed delete, the copy makes no exact-order promise, a failed restore surfaces the preimage and stays retryable, and the restore serializes behind an in-flight reorder.

## 5. Documentation

- [x] 5.1 Update `docs/runtime/session-lifecycle.md`'s `schedule_costs` description to the new field and basis.
