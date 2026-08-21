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
Both are public names in `butlers.core.sessions`, and `forecast_basis` carries
the description into every `ScheduleCost`.

Alternative rejected: a label like `"monthly"` or `"30-day basis"`. A label
still leaves the reader multiplying by a number they cannot see, which is the
defect being fixed. The number goes in the payload.

Alternative rejected: projecting over the *actual* current calendar month.
That reintroduces exactly the time-dependence the change is removing — the same
schedule would project differently in February and March.

### Decision 2: A fixed anchor, not "now"

Occurrences are enumerated from `2001-01-01T00:00:00Z`, the start of a
Gregorian 400-year leap cycle, over a 1461-day (four-year) horizon. `reference`
is injectable, but only so tests can demonstrate invariance across several fixed
clocks; production always uses the anchor.

The horizon is not a whole number of weeks (1461 days = 208.71 weeks), so a
weekly expression samples 208 or 209 firings against an exact 208.71 — an error
of about 0.14%. That is immaterial next to the per-run cost it multiplies, and
it is written down in the estimator's docstring rather than left for a reader to
rediscover.

### Decision 3: Truncate a capped window to a whole cycle before taking the rate

`_CADENCE_MAX_OCCURRENCES = 2000` bounds the work for a high-frequency
expression. Dividing the count by the raw span it was collected over is only
sound when firings are uniform across that span.

[Observed] `0 * * 1 *` — hourly, but only in January — fires 744 times each
January and exhausts the cap partway through a third January, 751.33 days in.
`2000 / 751.33 × 30.436875 = 81.02` against a true `744 / 12 = 62.0`: about 31%
high, and entirely plausible-looking on a page whose purpose is forecast
honesty.

`_truncate_cadence_window` snaps the sampled span back to the longest whole
calendar cycle that fits inside it — whole years, else whole weeks, else whole
days — and the occurrences are recounted inside that window. Every season the
window spans is then represented in full.

Weeks are a necessary tier, not a flourish: `* * * * 1` (per-minute, Mondays
only) exhausts the cap about 7.4 days in, and truncating that to whole days ends
mid-Monday and over-reports by roughly 20%. With the week tier it lands on
`1440 × 30.436875 / 7 = 6261.30` exactly.

One occurrence is peeked past the cap. Without it, an expression that happens to
fire exactly 2000 times across the whole horizon would be mistaken for capped and
truncated, distorting a case that was in fact fully sampled.

### Decision 4: Zero, never an exception, for an unparseable cron

`_schedule_costs_from_data` builds every row of the by-schedule response from one
shared helper. A raised exception on a single malformed cron string would take
out the whole response, so the estimator returns `0.0`. The UI renders the two
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
therefore destroys it and tells no one. The banner keeps the condition and
action on screen in the table's own chip vocabulary, and the restore stays
retryable.

### Decision 7: One mutation scope for every position-renumbering write

Delete, restore, and reorder all renumber `position`. They share the mutation
scope `spend-rule-order`; TanStack Query runs same-scope mutations one at a time
in call order, so a restore is never dispatched while a reorder it would have to
be consistent with is still in flight. The reorder mutation's existing
list-scoped id (established by bu-mmdef for the Escape-cancel race) is widened to
cover the other two rather than a second scope being introduced.

## Risks / Trade-offs

- **Contract break on a core MCP tool.** `schedule_costs` output keys change
  (`runs_per_day` → `projected_monthly_runs`, plus `forecast_basis`). Butler LLM
  sessions read these. Mitigation: the value is derived on read and never
  persisted, so there is no stored data to migrate; the rename is declared in
  the spec delta so the tool's contract is documented rather than folklore.
- **A four-year enumeration per schedule per request.** Bounded by the
  occurrence cap at 2000 iterations, plus at most one recount of the same size.
  The by-schedule endpoint is an operator-initiated page load, not a hot path.
- **The restore is in-session only.** Closing the page loses it. Accepted: a
  durable tombstone is out of scope, and the confirmation is what prevents most
  accidental deletions in the first place.
