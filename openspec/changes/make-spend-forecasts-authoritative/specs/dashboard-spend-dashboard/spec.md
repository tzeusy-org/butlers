## ADDED Requirements

### Requirement: Schedule Cadence Forecast

The by-schedule projection SHALL be derived from the cron expression's own
cadence over an average Gregorian calendar month, and SHALL be presented
separately from the cost measured over the queried range, with the basis it was
computed on carried in the payload.

ID: REQ-dashboard-spend-dashboard-002
Source: dashboard-spend-dashboard Spend API "a code-level TODO marks the location of the smarter estimator for a future change"; bu-6jv4m.2; design.md Decisions 1-3
Scope: v1-mandatory

#### Scenario: Monthly cadence is the cron's own, on a stated basis

- **WHEN** `butlers.core.sessions.schedule_costs` builds a row for a schedule
  whose `cron` croniter can parse
- **THEN** it emits `runs_per_month`, the number of times that expression fires
  in an average Gregorian calendar month of `365.2425 / 12 = 30.436875` days,
  and `forecast_basis`, a human-readable statement of that basis containing the
  literal number `30.436875`
- **AND** `GET /api/spend/by-schedule` returns them as `projected_monthly_runs`
  and `forecast_basis` on each `ScheduleCost`, so a reader never has to infer
  what the projection was multiplied by
- **AND** `projected_monthly_usd = avg_cost_per_run × projected_monthly_runs`
  exactly — no other multiplier appears anywhere in the chain. The replaced
  behavior was a hardcoded `× 30` applied to a next-24-hours occurrence count,
  which reported the live weekly `0 9 * * 1` maintenance cron as thirty monthly
  runs instead of roughly 4.35: a sevenfold overstatement.

#### Scenario: The forecast does not change with the time of the request

- **WHEN** the same cron expression is projected at any two instants
- **THEN** the two projections are equal, because occurrences are enumerated
  from a fixed anchor (`2001-01-01T00:00:00Z`, the start of a Gregorian 400-year
  leap cycle) rather than from "now"
- **AND** the cadence is therefore a pure function of the cron string: a weekly
  schedule reads ~4.35 runs a month whether the owner opens the page on a Monday
  or a Thursday, where the replaced estimator read 1 run/day on Mondays and 0 on
  every other day.

#### Scenario: A capped high-frequency count is measured over a whole cycle

- **WHEN** an expression exhausts the occurrence cap before the four-year
  sampling horizon (an hourly or per-minute cron does)
- **THEN** the sampling window is truncated to the longest whole calendar cycle
  that fits inside the span actually sampled — whole years, else whole weeks,
  else whole days — and the occurrences are recounted inside that truncated
  window before the rate is taken
- **AND** a seasonal expression is therefore not measured over its dense season
  alone: `0 * * 1 *` (hourly, but only in January) projects `744 / 12 = 62` runs
  a month, not the ~81 that dividing by the raw capped span produces
- **AND** an expression that happens to fire exactly the cap's worth of times
  across the whole horizon is recognised as fully sampled and is not truncated.

#### Scenario: An unparseable cron projects nothing rather than a number

- **WHEN** a schedule's `cron` cannot be parsed by croniter
- **THEN** the cadence estimator returns `0.0` and does not raise —
  `_schedule_costs_from_data` builds every row of the by-schedule response, so a
  raised exception on one bad cron string would take out the whole response
- **AND** `projected_monthly_runs` and `projected_monthly_usd` are both `0`,
  while the measured `total_runs` and `total_cost_usd` for that schedule are
  still reported truthfully
- **AND** the UI renders the two forecast cells as an em dash rather than
  `$0.00`, because "this cadence cannot be forecast" and "this schedule costs
  nothing" are different claims.

### Requirement: Routing Rule Deletion Safety

Removing a spend routing rule SHALL require an explicit confirmation that shows
the owner the exact rule and what its removal does to first-match evaluation,
and a successful removal SHALL offer a restore whose promises are limited to
what it can actually deliver.

ID: REQ-dashboard-spend-dashboard-003
Source: dashboard-spend-dashboard Spend Dashboard Page routing-rules table; bu-6jv4m.2; design.md Decisions 4-6
Scope: v1-mandatory

#### Scenario: One activation opens a confirmation, it does not delete

- **WHEN** the owner activates a rule row's Remove control
- **THEN** no `DELETE /api/spend/rules/{id}` is issued and no rule is mutated;
  a confirmation dialog opens instead
- **AND** the dialog shows that rule's exact `condition` and `action` in the
  same chip vocabulary the table row uses, and its position as
  `position N of M`
- **AND** the dialog states the first-match consequence: which rule the removed
  rule's traffic will next be tested against (naming that rule's condition), or
  that it will fall through to default model routing when no rule follows it,
  and that every rule below moves up one position
- **AND** cancelling deletes nothing and returns keyboard focus to the Remove
  control the owner activated.

#### Scenario: Repeat activation of confirm sends exactly one DELETE

- **WHEN** the dialog's confirm control is activated several times within one
  tick, before a re-render can disable it
- **THEN** exactly one `DELETE` is issued
- **AND** the dialog stays mounted while the delete is in flight, showing its
  pending state, and cannot be dismissed by Escape or an outside click while the
  request is on the wire
- **AND** a failed delete leaves the dialog open — nothing was destroyed, so the
  owner can retry or back out from there.

#### Scenario: A successful delete offers a restore that does not overclaim

- **WHEN** a delete succeeds
- **THEN** a persistent restore affordance appears, naming the position the rule
  was removed from. It does not expire on a timer: restoring a first-match rule
  is the difference between an undo and a second, quieter mistake
- **AND** activating it issues `POST /api/spend/rules` carrying the captured
  `condition`, `action`, and `position`, which the API's insert-shift
  (`position = position + 1 WHERE position >= p`) inverts exactly against the
  delete's compaction (`position = position - 1 WHERE position > p`)
- **AND** neither the affordance's copy nor its success message promises that
  the previous first-match order was restored. The rule is re-created at the
  position it was removed from, which is only where it previously sat if nothing
  else moved in the meantime; the affordance may claim what it does, not what it
  cannot guarantee
- **AND** the affordance is cleared once used, so it cannot restore twice, and
  is never offered when the delete failed.

#### Scenario: A failed restore surfaces the rule it could not put back

- **WHEN** the restore request fails
- **THEN** the removed rule's `condition` and `action` are displayed
  persistently, not only in a toast that expires, so the owner can re-create the
  rule by hand — the rule is already gone from the server, and a bare "failed"
  would have destroyed it and told no one
- **AND** the restore affordance remains active so the request can be retried.

#### Scenario: Delete, restore, and reorder are serialized against each other

- **WHEN** a restore is activated while a reorder of the same list is still in
  flight
- **THEN** the restore is not dispatched until the reorder settles, because all
  three position-renumbering mutations share one mutation scope
- **AND** a restore therefore never computes its position against ordering the
  server has already moved past.

## MODIFIED Requirements

### Requirement: Spend API
The dashboard SHALL expose the spend endpoints.

#### Scenario: Spend totals
- **WHEN** `GET /api/spend?period=today|7d|30d` is called (or a custom range via `from`/`to` ISO date params)
- **THEN** the response is `ApiResponse[SpendSummary]` where `SpendSummary = {period, total_cost_usd, total_sessions, total_input_tokens, total_output_tokens, by_butler, by_model}`. There are no `total_usd`, `period_start`, or `period_end` fields.

#### Scenario: Spend breakdown
- **WHEN** `GET /api/spend/breakdown?by=butler|model|feature` is called
- **THEN** the response is `ApiResponse[{by: str, breakdown: {key: cost_usd}}]`, a flat key-to-cost map for the current month (MTD). The client sorts descending and renders the bars; the API returns no `share` field and no guaranteed order.

#### Scenario: Spend breakdown by purpose
- **WHEN** `GET /api/spend/breakdown?by=purpose` is called
- **THEN** the response is `ApiResponse[{by: "purpose", breakdown: {key: cost_usd}, source_error: bool}]`, priced directly from `public.token_usage_ledger.purpose` (bu-qvnce.12/core_156) grouped with `model_catalog` for pricing — NOT a per-butler MCP fan-out like the other three dimensions
- **AND** `purpose` keys are the dispatch `trigger_source` values (`route`/`schedule`/`classification`/`healing`/`qa`/`extraction`/`external`/`retry`/`tick`) plus `discretion` (connector discretion screening, which has no `trigger_source` equivalent); ledger rows with a `NULL` purpose (pre-migration or unattributed) are grouped under `"unknown"`
- **AND** `source_error: true` when the DB-backed path is unavailable or the ledger query fails (no MCP fallback exists for this dimension) — the frontend renders a `SourceDegradedNote` instead of reading an empty breakdown as "no purpose-tagged spend this month".

#### Scenario: Spend forecast (naive estimator v1)
- **WHEN** `GET /api/spend/forecast` is called
- **THEN** the response is `{days: {date, cost_usd, projected}[], projected_eom_usd: float, days_in_month: int, days_elapsed: int, mtd_usd: float, ceiling_usd: float | null, projection_confidence: "low" | "normal", ceiling_source_error: bool, unavailable_butlers: str[]}` (the field is `days` not `daily`, and per-day cost is `cost_usd` not `usd`)
- **AND** `mtd_usd` (and the `ceiling_usd` fetch alongside it) is priced from `public.token_usage_ledger` via the shared `butlers.core.model_routing.price_mtd_from_ledger` helper — the exact helper `check_monthly_ceiling` uses to gate spawns (bu-7o89u.1) — so this figure can never diverge from the number that halts the fleet; it is NOT summed from the per-butler daily-actuals fan-out
- **AND** `projected_eom_usd = mtd_usd / max(days_elapsed, 1) × days_in_month`, using that same ledger-priced `mtd_usd`
- **AND** `ceiling_source_error: true` when the ledger/ceiling query fails or no DB pool is wired (no MCP fallback exists for the ledger) — `mtd_usd`, `projected_eom_usd`, and `ceiling_usd` are then `0`/`0`/`null`, not a genuine "$0 MTD" reading, and the frontend renders a `SourceDegradedNote` in place of the KPI strip and suppresses the projected (dashed) chart segment instead of reading the zeros as truthful
- **AND** the per-day `days` breakdown (needed only for the chart's solid-actuals series, since the ledger's MTD query has no per-day granularity) still comes from the per-butler daily-actuals fan-out; butlers dropped from that fan-out are named in `unavailable_butlers`, independently of `ceiling_source_error`
- **AND** `projection_confidence = "low"` when `days_elapsed < 3`, else `"normal"`. This signals to the Console aggregator NOT to fire a "spend near ceiling" attention item from a low-confidence projection.
- **AND** a code-level TODO marks the location of the smarter estimator for a future change. This applies to the month-end spend estimator only; the per-schedule cadence estimator it used to also cover is now specified by "Requirement: Schedule Cadence Forecast".

#### Scenario: Spend by-schedule aggregation
- **WHEN** `GET /api/spend/by-schedule` (or `?by=feature` on `/api/spend/breakdown`) is called and a schedule ran under 2+ models within the queried window
- **THEN** the response contains exactly one `ScheduleCost` entry per `(butler, schedule_name)` — the per-butler fan-out prices each `(schedule, model)` fragment individually (pricing is model-specific) but merges same-named fragments into a single bucket (summed `total_runs` and `total_cost_usd`; `projected_monthly_runs` and `forecast_basis` taken once, since they derive only from the cron) before returning
- **AND** `avg_cost_per_run` and `projected_monthly_usd` are computed from the merged totals, not from any individual model fragment — a multi-model schedule must never under-rank its true burn or produce duplicate `(butler, schedule_name)` rows (the frontend keys table rows on `${butler}-${schedule_name}`)
- **AND** `ScheduleCost` carries two groups of fields that are not interchangeable: `total_runs`, `total_cost_usd` and `avg_cost_per_run` are MEASURED over the queried range, while `projected_monthly_runs`, `projected_monthly_usd` and `forecast_basis` are a FORECAST, specified by "Requirement: Schedule Cadence Forecast"
- **AND** the payload key is `projected_monthly_runs`, replacing `runs_per_day`. This is a contract change on the `schedule_costs` core MCP tool as well as on the HTTP response, since butler LLM sessions read that tool's output keys. The value is computed on read from a live query and never persisted, so no migration, dual-read, or backfill is involved.

#### Scenario: DB-first evidence for top-sessions and by-schedule
- **WHEN** `GET /api/spend/top-sessions` or `GET /api/spend/by-schedule` (or `?by=feature` on `/api/spend/breakdown`) is called with a DB pool wired
- **THEN** each butler's evidence is read DB-first via the `butlers.core.sessions.top_sessions` / `schedule_costs` pool helpers, with the corresponding MCP tool as a per-butler fallback used only when that butler's DB pool is absent or its query fails
- **AND** because the DB read does not depend on MCP tool registration, the STAFFER butlers (`switchboard`/`messenger`/`qa`) that structurally lack these tools now contribute real evidence instead of a permanent empty result
- **AND** a butler is added to `unavailable_butlers` only when BOTH the DB read and the MCP fallback fail; a butler whose sessions/scheduled_tasks tables do not exist yet (schema not provisioned) yields no DB rows and is classified via the fallback, never marked degraded on the DB miss alone
- **AND** the DB and MCP paths share one pricing/merge builder, so a butler served by either path produces the identical `TopSession` / `ScheduleCost` shape (the by-schedule per-`(butler, schedule_name)` merge above applies to both)

#### Scenario: MCP tool-absence is not a degraded source
- **WHEN** the per-butler MCP fan-out behind `/api/spend`, `/api/spend/daily`, `/api/spend/top-sessions`, or `/api/spend/by-schedule` calls a butler that has never registered the requested tool (`sessions_summary` / `sessions_daily` / `top_sessions` / `schedule_costs` are registered only for non-STAFFER butlers — see `core_tools/_sessions.py`, `core_tools/_scheduling.py` — so every STAFFER butler, e.g. `switchboard`/`messenger`/`qa`, structurally lacks all four regardless of its `core_groups` config)
- **THEN** the call fails with `fastmcp.exceptions.ToolError` whose message starts with `"Unknown tool:"`, and the router classifies this as legitimately absent — the butler contributes a truthful zero/empty result and is NOT added to `unavailable_butlers` (for `/top-sessions` and `/by-schedule` this MCP path is the fallback reached only when the DB-first read above missed, so a STAFFER butler with a provisioned schema is served real evidence and never reaches this branch)
- **AND** any other failure on the same call (unreachable butler, timeout, malformed JSON, or a `ToolError` raised by the tool's own body for a different reason) is still tracked and the butler IS added to `unavailable_butlers`, so a genuine failure is never mistaken for "this butler doesn't have that feature".

#### Scenario: Spend rule condition dimensions
- **WHEN** a rule `condition` is created or updated
- **THEN** the supported dimensions are `butler` (identity name), `complexity`/`tier` (alias pair — canonical complexity tier), and `trigger`/`purpose` (alias pair — the dispatch `trigger_source`, matching the same vocabulary `/spend/breakdown?by=purpose` and `token_usage_ledger.purpose` use for this dimension)
- **AND** each dimension accepts a scalar (exact match) or a list (membership match); all supplied dimensions are ANDed; an unknown key is rejected at create/update time (`422`), and at dispatch-evaluation time causes the rule to fail-closed (never match)
- **AND** `trigger`/`purpose` cannot match when the dispatch has no trigger-source context (fail-closed, not catch-all).
- **AND** a condition MUST NOT set both `trigger` and `purpose` (they alias the same underlying value and could never legitimately hold two different values at once) — rejected at create/update time (`422`).

#### Scenario: Spend rules CRUD
- **WHEN** `GET /api/spend/rules` is called
- **THEN** rules are returned ordered by `position ASC` (top-to-bottom evaluation order)
- **WHEN** `POST /api/spend/rules` is called with `{condition, action, position?}`
- **THEN** the rule is inserted at `position` (default: end), and existing rules at `position >= p` are shifted down by one inside the insert transaction
- **AND** the call invokes `audit.append("spend.rule")`.
- **WHEN** `PUT /api/spend/rules/{id}` is called
- **THEN** the rule fields are updated atomically; if `position` changed, other rules' positions are shifted to maintain the order.
- **WHEN** `DELETE /api/spend/rules/{id}` is called
- **THEN** the rule is removed and remaining rules' positions are compacted (no gaps)
- **AND** the insert-shift and the delete-compaction are exact inverses, which is what makes the restore in "Requirement: Routing Rule Deletion Safety" possible without a new endpoint, a tombstone table, or a migration.

#### Scenario: Monthly ceiling
- **WHEN** `PUT /api/spend/ceiling {monthly_usd}` is called
- **THEN** the singleton ceiling row is updated
- **AND** the call invokes `audit.append("spend.ceiling")`.

### Requirement: Spend Dashboard Page
The dashboard SHALL have a page at `/settings/spend` rendered in the Dispatch design language showing total spend, breakdowns, a forecast chart, routing rules, and a monthly ceiling.

#### Scenario: Spend page layout
- **WHEN** a user navigates to `/settings/spend`
- **THEN** the page renders, in vertical order:
  - **Page header**: title "Spend" rendered via the shared `Page` overview shell. The page does not render a mono eyebrow "system · cost" or a clock.
  - **4-cell KPI strip**: `MTD Spend`, `Projected EOM`, `Monthly Ceiling`, `Days in Month`. Mega-number in sans 500 tabular-nums, mono sub-label. There is no `today` cell, and sub-labels show context such as days elapsed/remaining, not a delta vs. prior period.
  - **Forecast chart**: hand-rolled SVG. Solid line for MTD daily series, dashed line for projection from today to month end, hairline horizontal at the ceiling. No charting library.
  - **Breakdown section**: bars by `butler`, `model`, `feature`, `purpose` via tabbed picker. Each bar is plain CSS (≤ 8 lines per bar), no library.
  - **By Schedule table**: per-cron rows under two visually separated column groups — "Measured · selected range" (`Runs`, `Cost`, `Avg/run`) and "Forecast · per month" (`Runs`, `Cost`) — with the API's `forecast_basis` stated once beneath the table. A projection is never rendered in the same undifferentiated run of columns as measured history, and a schedule whose cadence could not be computed renders both forecast cells as an em dash rather than `$0.00`.
  - **Routing rules table**: rule rows in evaluation order with drag-to-reorder; columns `condition · action · saved 7d`. Order is top-to-bottom; first match wins at runtime. Removal is gated by the confirmation and restore contract in "Requirement: Routing Rule Deletion Safety" — a rule is never deleted on a single activation.
  - **Anomaly section**: deferred. The page carries only a source-code TODO comment in the forecast section; no anomaly copy is rendered to the user.
- **AND** no recharts or other chart library is loaded for this page.
