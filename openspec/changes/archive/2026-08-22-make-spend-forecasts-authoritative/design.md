## Context

[Observed] `_estimate_runs_per_day` sampled `datetime.now(UTC)` to
`now + 1 day` and returned the raw occurrence count. For any expression firing
less often than daily the answer was a coin flip on the request's timing:
`0 9 * * 1` returned `1.0` on a Monday and `0.0` otherwise.
`_schedule_costs_from_data` then computed `avg_cost * runs_per_day * 30`. The
`30` was a bare literal with no name and no statement in the response, and the
resulting figure was rendered in a column labelled `Projected/mo` beside
`Runs` and `Avg/run`, which are measured over the queried range.

[Observed] `POST /api/spend/rules` already accepts an optional `position` and,
when given one, runs `UPDATE public.spend_rules SET position = position + 1
WHERE position >= $2` inside the insert transaction.
`DELETE /api/spend/rules/{id}` runs `UPDATE ... SET position = position - 1
WHERE position > $2`. These are exact inverses, which is why a truthful restore
needs no new endpoint and no schema change.

## Goals / Non-Goals

**Goals:**
- A per-schedule projection that is a pure function of the cron string.
- A response in which a reader can tell measurement from forecast without
  knowing the implementation, and can read the forecast's basis off the payload.
- A destructive routing-rule mutation the owner can inspect before committing to
  and recover from afterwards.

**Non-Goals:**
- Timezone- or DST-aware cadence. Schedules are evaluated in UTC by the
  scheduler; a DST-shifted local cadence is a separate question from the
  30-vs-4.35 error this change fixes.
- Per-schedule cost *attribution* changes. The measured side of the payload is
  untouched.
- Making rule deletion recoverable after the page is closed. The restore is
  in-session; a durable tombstone is a different, larger contract.

## Decisions

### Decision 1: An average Gregorian month, stated in the payload

The basis is `AVERAGE_MONTH_DAYS = 365.2425 / 12 = 30.436875` days, and
`CADENCE_BASIS_DESCRIPTION` states it in prose containing that literal number.
Both are public names in `butlers.core.sessions`. The description is a
constant of the estimator, not a property of any one schedule, so it is stated
once on the response envelope as `meta.forecast_basis` rather than copied into
every `ScheduleCost` row. `ApiMeta` already allows extra keys and already
carries `unavailable_butlers`, so this needs no new envelope machinery. A
per-row copy would be payload bloat and, worse, a false signal that the basis
could differ between rows.

Alternative rejected: a label like `"monthly"` or `"30-day basis"`. A label
still leaves the reader multiplying by a number they cannot see, which is the
defect being fixed. The number goes in the payload.

Alternative rejected: projecting over the *actual* current calendar month.
That reintroduces exactly the time-dependence the change is removing — the same
schedule would project differently in February and March.

### Decision 2: A fixed anchor, not "now"

Occurrences are enumerated from `2001-01-01T00:00:00Z`, the start of a
Gregorian 400-year leap cycle. `reference` is injectable, but only so tests can
demonstrate invariance across several fixed clocks; production always uses the
anchor.

The anchor is a Monday, which is convenient but is deliberately not load-bearing
— see Decision 3. `test_per_minute_weekday_cron_is_measured_over_a_whole_week`
parametrises over all seven weekdays precisely so no single weekday, least of
all the anchor's own, can be the case that happens to work.

### Decision 3: Take the cycle length from the expression, not from the sample

A rate is `count / span`, and that is only sound when the span is a whole number
of the expression's own repeat cycles. The question is where the cycle length
comes from.

Rejected: infer it from the sampled window — count until an occurrence cap, then
snap the span back to the longest whole calendar cycle (years, else weeks, else
days) that fits inside it. This is what the first implementation did, and it is
wrong in both directions, because it picks the granularity from what *fits* in
the sample rather than from what the cron *requires*:

- `* * * 1 *` (per-minute, January only) exhausts a 2000-occurrence cap 1.39
  days in, entirely inside January. Every candidate cycle that fits is a whole
  day, so it truncates to days and reports **43,829/month against a true 3,720
  — 1078% high.**
- `0 * * 1 *` (hourly, January only) exhausts the cap 751 days in and truncates
  to whole years only if a year fits, giving **81.02 against a true 62.04.**
- `* * * * 3` (per-minute, Wednesdays) exhausts the cap 7.4 days in; whole days
  fit, whole weeks do not, so it truncates mid-Wednesday and reports **4,873
  against a true 6,261 — 22% low.**

Adopted: a 5-field cron is periodic in exactly one of three lengths, and which
one is decided by which calendar fields it restricts. `_cadence_cycle_days`
reads the expanded fields and returns:

| Restriction | Cycle | Example |
| --- | --- | --- |
| `dom` or `month` restricted, or an `n#w` nth-weekday | 365 days | `0 9 1 * *`, `0 * * 1 *`, `0 9 * * 5#3` |
| only `dow` restricted | 7 days | `0 9 * * 1`, `* * * * 3` |
| neither restricted | 1 day | `* * * * *`, `@hourly` |

The window is then exactly one cycle from the anchor, so it is a whole cycle by
construction. There is no truncation step, no zero-length-window case to guard,
and no dependence on the anchor's weekday: one week from any Wednesday contains
exactly one Wednesday. All three cases above come out exact.

It is also cheaper. `* * * * *` is a 1-day cycle — 1,440 enumeration steps,
against the old design's 2,000-step count plus a 2,000-step recount. At the
measured ~54 µs per `croniter.get_next` in this environment, a weekly cron costs
0.52 ms and the worst case (`* * * * 3`, 10,080 steps) 98 ms, on an
operator-initiated page load.

`_CADENCE_MAX_OCCURRENCES = 10_100` is sized for the densest expression the
classification admits — per-minute over a 7-day cycle is 10,080. Hitting it
means the classification was wrong for that expression, so the estimator reports
`0.0` (cadence unknown) rather than a number derived from a partial window. One
occurrence is peeked past the cap, so an expression that fires exactly at the
cap is not mistaken for truncated.

A cron restricted to a rare day-of-month can miss a 365-day window entirely:
`0 9 29 2 *` fires zero times from 2001-01-01. A count of zero from an annual
cycle, and only that case, re-runs against `_CADENCE_HORIZON_DAYS = 1461` — a
widened leap cycle — before concluding the expression never fires.

### Decision 4: Zero, never an exception, for an unparseable cron

`_schedule_costs_from_data` builds every row of the by-schedule response from one
shared helper. A raised exception on a single malformed cron string would take
out the whole response, so the estimator returns `0.0`. Two distinct failures
reach that path: `croniter.is_valid` rejects the expression outright, and —
less obviously — an expression `is_valid` *accepts* raises
`CroniterBadDateError` while being enumerated, because it can never fire
(`0 0 30 2 *`). `_count_occurrences` catches it. A third case, an expression too
dense for its classified cycle, reports `0.0` for the same reason. The UI renders the two
forecast cells as an em dash: "we cannot forecast this" and "this is free" are
different claims and must not share a rendering.

### Decision 5: Restore, not "undo to the exact order"

The restore posts the captured `condition`, `action`, and `position`, and the
API's insert-shift inverts the delete's compaction exactly. In an otherwise
quiet list this does put the rule back where it was.

It cannot promise that in general. If the owner creates or reorders a rule
between the delete and the restore, position *p* no longer denotes the same
place in the first-match order. So the affordance's copy and its success message
say the rule was restored and describe what the request does; they do not claim
the previous first-match order was reinstated. An undo that overclaims on a
first-match routing table is a second, quieter mistake.

The affordance is persistent rather than a timed toast action, for the same
reason: the owner should not be racing a countdown to decide whether their
routing table is still correct.

Alternative rejected: a tombstone table restoring the original row id. That
needs a migration, a retention policy, and audit semantics, and buys nothing the
position arithmetic does not already give within a session.

### Decision 6: A failed restore must surface the rule

By the time a restore can fail, the rule is already gone from the server. A
`toast.error("Failed to restore rule")` that discards the captured rule
therefore destroys it and tells no one.

Visible is not sufficient: a banner the owner can read but not carry away still
loses the rule the moment they navigate. The banner renders the complete
preimage — `position`, `condition`, `action` — as selectable, copyable text
equal to the request body that re-creates the rule, so reconstructing it by hand
is a transcription rather than a guess. The test asserts that: it parses the
rendered text and requires it to equal the body the retry actually posts. The
restore stays retryable.

### Decision 7: One mutation scope for every position-renumbering write

Delete, restore, and reorder all renumber `position`. They share the mutation
scope `spend-rule-order`; TanStack Query runs same-scope mutations one at a time
in call order, so a restore is never dispatched while a reorder it would have to
be consistent with is still in flight. The reorder mutation's existing
list-scoped id (established by bu-mmdef for the Escape-cancel race) is widened to
cover the other two rather than a second scope being introduced.

## Risks / Trade-offs

- **Contract break on a core MCP tool.** `schedule_costs` output keys change
  (`runs_per_day` → `projected_monthly_runs`, plus a top-level
  `forecast_basis`). Butler LLM sessions read these. Mitigation: the value is derived on read and never
  persisted, so there is no stored data to migrate; the rename is declared in
  the spec delta so the tool's contract is documented rather than folklore.
- **A whole-cycle enumeration per schedule per request.** Bounded by the cycle
  length: 1,440 steps for a daily-cycle expression, at most 10,080 for a weekly
  one, and no recount. Measured at 0.52 ms for `0 9 * * 1` and 98 ms for the
  worst case. The by-schedule endpoint is an operator-initiated page load, not a
  hot path.
- **The restore is in-session only.** Closing the page loses it. Accepted: a
  durable tombstone is out of scope, and the confirmation is what prevents most
  accidental deletions in the first place.
