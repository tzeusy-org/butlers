# dashboard-spend-dashboard

## Purpose

The `/settings/spend` page is the operator's view into system cost: total spend, breakdowns by butler/model/feature/purpose, a hand-rolled SVG forecast chart projecting month-end land, store-and-evaluate routing rules with per-rule 7-day savings, a monthly ceiling, and a live per-call spend stream. It is part of the Console-direction redesign of `/settings` and is rendered in the Dispatch design language already shipped on `/overview`, `/butlers`, and `/qa`. It is backed by the spend endpoints (`/api/spend/*`) served by `spend.py` (the renamed `costs.py` router), including rules CRUD and the monthly ceiling; the live per-call ticker is delivered over the unified fleet event bus (`WS /api/events/stream`), not a dedicated socket. No charting library is loaded for this page.

## Requirements

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

### Requirement: Spend API
The dashboard SHALL expose the spend endpoints.

#### Scenario: Spend totals
- **WHEN** `GET /api/spend?period=today|7d|30d` is called (or a custom range via `from`/`to` ISO date params)
- **THEN** the response is `ApiResponse[SpendSummary]` where `SpendSummary = {period, total_cost_usd, total_sessions, total_input_tokens, total_output_tokens, by_butler, by_model, unpriced_models, divergences, historical_attribution_note}`. There are no `total_usd`, `period_start`, or `period_end` fields.
- **AND** every dollar amount, token actual, `by_butler` value, and `by_model` value is grouped from `public.token_usage_ledger` joined to `public.model_catalog` by `catalog_entry_id`, so it describes the model that actually consumed the tokens rather than `sessions.model`.
- **AND** the router MUST NOT substitute per-butler session fan-out pricing when that ledger query is unavailable; it returns degraded source evidence instead of a plausible alternate dollar total.
- **AND** implicit calendar defaults derive `today` from UTC, so preset summaries, the daily default range, MTD breakdown, and forecast elapsed-day calculation align with the UTC month used by the monthly-ceiling ledger helper.

#### Scenario: Compatibility source errors are unavailable evidence in every summary and daily consumer
- **WHEN** a compatibility-success (`200`) Spend summary response has `source_error: true`, or a daily Spend response has `meta.source_error: true`
- **THEN** its compatibility totals, maps, and empty daily series MUST be treated as unavailable evidence, never as a genuine `$0`, empty-state, "all systems ok", mover, per-session, model-breakdown, or trend result.
- **AND** Dashboard/CostWidget, Sidebar, Butler spend/detail surfaces, and SpendPage/CostStripeChart render a source-degraded/unavailable state that identifies the unavailable evidence while preserving an explicitly known zero when `source_error` is absent or false.

#### Scenario: Spend breakdown
- **WHEN** `GET /api/spend/breakdown?by=butler|model|purpose` is called
- **THEN** the response is `ApiResponse[{by: str, breakdown: {key: cost_usd}, unpriced_models: [], billing_classes: {model: class}, source_error: bool, divergences: [], historical_attribution_note: str | null}]` for the current month (MTD), with no guaranteed map order.
- **AND** `butler`, `model`, and `purpose` values are grouped directly from `public.token_usage_ledger` and priced through the same executed-model pricing calculation as the monthly ceiling, not from requested session models or an MCP fan-out.
- **AND** `GET /api/spend/breakdown?by=feature` retains the by-schedule behavior defined below; it is metadata evidence outside this aggregate ledger-attribution change.

#### Scenario: Unpriced models remain visibly unpriced
- **WHEN** a ledger group uses a model absent from the pricing configuration
- **THEN** its numerical cost is omitted from the priced subtotal and it is included in `unpriced_models` as `{model, calls, input_tokens, output_tokens, cached_input_tokens, cache_creation_tokens}`.
- **AND** the API MUST NOT encode absent pricing as `0`, and the frontend renders the model as `—/unpriced` rather than a zero-valued bar or `$0.00` amount.
- **AND** an explicitly classified `subscription` or `local` model with zero marginal rates remains a known numeric zero and is identified by `billing_classes`, not placed in `unpriced_models`.
- **AND** when a relevant summary, daily, forecast, or comparison envelope has non-empty `unpriced_models`, Sidebar, Butler Spend/Overview, movers, and verdict consumers surface incomplete coverage and suppress partial dollar totals and calm pace/mover language; a numeric zero remains valid only when that envelope is empty.

#### Scenario: Spend breakdown by purpose
- **WHEN** `GET /api/spend/breakdown?by=purpose` is called
- **THEN** `purpose` keys are the dispatch `trigger_source` values (`route`/`schedule`/`classification`/`healing`/`qa`/`extraction`/`external`/`retry`/`tick`) plus `discretion` (connector discretion screening, which has no `trigger_source` equivalent); ledger rows with a `NULL` purpose (pre-migration or unattributed) are grouped under `"unknown"`.
- **AND** `source_error: true` when the DB-backed path is unavailable or the ledger query fails (no session or MCP fallback exists for this dimension) — the frontend renders a `SourceDegradedNote` instead of reading an empty breakdown as "no purpose-tagged spend this month".

#### Scenario: Spend forecast (naive estimator v1)
- **WHEN** `GET /api/spend/forecast` is called
- **THEN** the response is `{days: {date, cost_usd, projected}[], projected_eom_usd: float, days_in_month: int, days_elapsed: int, mtd_usd: float, ceiling_usd: float | null, projection_confidence: "low" | "normal", ceiling_source_error: bool, unpriced_models: [], ceiling_blind_to_unpriced_models: int, divergences: [], historical_attribution_note: str | null}` (the field is `days` not `daily`, and per-day cost is `cost_usd` not `usd`).
- **AND** `mtd_usd`, every actual `days[*].cost_usd`, and `projected_eom_usd` are priced from `public.token_usage_ledger` joined to the executed catalog entry; actual daily costs MUST NOT be reconstructed from per-butler session rows.
- **AND** `mtd_usd` and `ceiling_usd` share the `butlers.core.model_routing.price_mtd_from_ledger` calculation used by `check_monthly_ceiling`, so the priced portion can never diverge from the number that halts the fleet.
- **AND** `projected_eom_usd = mtd_usd / max(days_elapsed, 1) × days_in_month`, using that ledger-priced `mtd_usd`.
- **AND** `ceiling_blind_to_unpriced_models` equals the number of distinct unpriced model IDs in current-month ledger usage, and the frontend renders `"blind to N unpriced models"` whenever it is nonzero.
- **AND** `ceiling_source_error: true` when the ledger/ceiling query fails or no DB pool is wired — `mtd_usd`, `projected_eom_usd`, and `ceiling_usd` are then `0`/`0`/`null`, not a genuine "$0 MTD" reading, and the frontend renders a `SourceDegradedNote` in place of the KPI strip and suppresses the projected (dashed) chart segment instead of reading the zeros as truthful.
- **AND** `projection_confidence = "low"` when `days_elapsed < 3`, else `"normal"`. This signals to the Console aggregator NOT to fire a "spend near ceiling" attention item from a low-confidence projection.
- **AND** a code-level TODO marks the location of the smarter estimator for a future change. This applies to the month-end spend estimator only; the per-schedule cadence estimator it used to also cover is now specified by "Requirement: Schedule Cadence Forecast".

#### Scenario: Sessions-versus-ledger divergence is surfaced
- **WHEN** a spend response covers a date and butler for which session token totals and ledger token totals differ by more than five percent
- **THEN** the response includes a divergence record with the date, butler, both token totals, and relative difference; the frontend renders a `SourceDegradedNote` rather than presenting the aggregates as reconciled.
- **AND** session reads used for this detector are diagnostic only and MUST NOT supply a dollar amount or model price.
- **AND** if the detector cannot obtain enough session evidence to compare a source, the response identifies that degraded comparison rather than reporting an empty divergence list as a successful reconciliation.

#### Scenario: Historical requested-model attribution is labeled
- **WHEN** a requested response window begins before `2026-07-10`
- **THEN** its `historical_attribution_note` states that legacy session model labels in that portion of the range can be requested models while all ledger-derived dollar values use the executed model.

#### Scenario: Spend by-schedule aggregation
- **WHEN** `GET /api/spend/by-schedule` (or `?by=feature` on `/api/spend/breakdown`) is called and a schedule ran under 2+ models within the queried window
- **THEN** the response contains exactly one `ScheduleCost` entry per `(butler, schedule_name)` — the per-butler fan-out prices each `(schedule, model)` fragment individually (pricing is model-specific) but merges same-named fragments into a single bucket (summed `total_runs` and `total_cost_usd`; `projected_monthly_runs` taken once, since it derives only from the cron) before returning.
- **AND** `avg_cost_per_run` and `projected_monthly_usd` are computed from the merged totals, not from any individual model fragment — a multi-model schedule must never under-rank its true burn or produce duplicate `(butler, schedule_name)` rows (the frontend keys table rows on `${butler}-${schedule_name}`).
- **AND** `ScheduleCost` carries two groups of fields that are not interchangeable: `total_runs`, `total_cost_usd` and `avg_cost_per_run` are MEASURED over the queried range, while `projected_monthly_runs` and `projected_monthly_usd` are a FORECAST, specified by "Requirement: Schedule Cadence Forecast", computed on the basis stated once in `meta.forecast_basis`.
- **AND** the payload key is `projected_monthly_runs`, replacing `runs_per_day`. This is a contract change on the `schedule_costs` core MCP tool as well as on the HTTP response, since butler LLM sessions read that tool's output keys. The value is computed on read from a live query and never persisted, so no migration, dual-read, or backfill is involved.

#### Scenario: DB-first evidence for top-sessions and by-schedule
- **WHEN** `GET /api/spend/top-sessions` or `GET /api/spend/by-schedule` (or `?by=feature` on `/api/spend/breakdown`) is called with a DB pool wired
- **THEN** each butler's evidence is read DB-first via the `butlers.core.sessions.top_sessions` / `schedule_costs` pool helpers, with the corresponding MCP tool as a per-butler fallback used only when that butler's DB pool is absent or its query fails.
- **AND** because the DB read does not depend on MCP tool registration, the STAFFER butlers (`switchboard`/`messenger`/`qa`) that structurally lack these tools now contribute real evidence instead of a permanent empty result.
- **AND** a butler is added to `unavailable_butlers` only when BOTH the DB read and the MCP fallback fail; a butler whose sessions/scheduled_tasks tables do not exist yet (schema not provisioned) yields no DB rows and is classified via the fallback, never marked degraded on the DB miss alone.
- **AND** the DB and MCP paths share one pricing/merge builder, so a butler served by either path produces the identical `TopSession` / `ScheduleCost` shape (the by-schedule per-`(butler, schedule_name)` merge above applies to both).

#### Scenario: MCP tool-absence is not a degraded source
- **WHEN** the per-butler fallback behind `/api/spend/top-sessions` or `/api/spend/by-schedule` calls a butler that has never registered the requested tool (`top_sessions` / `schedule_costs` are registered only for non-STAFFER butlers — see `core_tools/_sessions.py`, `core_tools/_scheduling.py`)
- **THEN** the call fails with `fastmcp.exceptions.ToolError` whose message starts with `"Unknown tool:"`, and the router classifies this as legitimately absent — the butler contributes a truthful zero/empty result and is NOT added to `unavailable_butlers`.
- **AND** any other failure on the same call (unreachable butler, timeout, malformed JSON, or a `ToolError` raised by the tool's own body for a different reason) is still tracked and the butler IS added to `unavailable_butlers`, so a genuine failure is never mistaken for "this butler doesn't have that feature".

#### Scenario: Spend rule condition dimensions
- **WHEN** a rule `condition` is created or updated
- **THEN** the supported dimensions are `butler` (identity name), `complexity`/`tier` (alias pair — canonical complexity tier), and `trigger`/`purpose` (alias pair — the dispatch `trigger_source`, matching the same vocabulary `/spend/breakdown?by=purpose` and `token_usage_ledger.purpose` use for this dimension).
- **AND** each dimension accepts a scalar (exact match) or a list (membership match); all supplied dimensions are ANDed; an unknown key is rejected at create/update time (`422`), and at dispatch-evaluation time causes the rule to fail-closed (never match).
- **AND** `trigger`/`purpose` cannot match when the dispatch has no trigger-source context (fail-closed, not catch-all).
- **AND** a condition MUST NOT set both `trigger` and `purpose` (they alias the same underlying value and could never legitimately hold two different values at once) — rejected at create/update time (`422`).

#### Scenario: Spend rules CRUD
- **WHEN** `GET /api/spend/rules` is called
- **THEN** rules are returned ordered by `position ASC` (top-to-bottom evaluation order).
- **WHEN** `POST /api/spend/rules` is called with `{condition, action, position?}`
- **THEN** the rule is inserted at `position` (default: end), and existing rules at `position >= p` are shifted down by one inside the insert transaction.
- **AND** the call invokes `audit.append("spend.rule")`.
- **WHEN** `PUT /api/spend/rules/{id}` is called
- **THEN** the rule fields are updated atomically; if `position` changed, other rules' positions are shifted to maintain the order.
- **WHEN** `DELETE /api/spend/rules/{id}` is called
- **THEN** the rule is removed and remaining rules' positions are compacted (no gaps).
- **AND** the insert-shift and the delete-compaction are exact inverses, which is what makes the restore in "Requirement: Routing Rule Deletion Safety" possible without a new endpoint, a tombstone table, or a migration.

#### Scenario: Monthly ceiling
- **WHEN** `PUT /api/spend/ceiling {monthly_usd}` is called
- **THEN** the singleton ceiling row is updated.
- **AND** the call invokes `audit.append("spend.ceiling")`.

### Requirement: Fleet-Halt Visibility
The dashboard SHALL surface the monthly spend ceiling's enforcement action —
dispatches being denied fleet-wide — as a loud, explicit state on the Spend page,
not silence. The ceiling is enforced by `check_monthly_ceiling` / the spawner
(`spawner.py:1179-1202`), which writes an `outcome='quota_skip'` row to
`public.model_dispatch_attempts` with `failure_reason` starting `"Monthly spend
ceiling reached"` for every denied dispatch (see model-failover spec, Failover
Attempt Provenance).

#### Scenario: A red fleet-halt banner renders while the ceiling is breached
- **WHEN** `GET /api/dispatch/attempts?outcome=quota_skip&reason_prefix=Monthly+spend+ceiling+reached`
  (scoped to the current calendar month) returns one or more rows
- **THEN** the Spend page renders a red state reading "Monthly ceiling reached —
  N dispatches denied since `<timestamp>`", where N is the total matching count
  for the current month and `<timestamp>` is the earliest matching row's `ts`
- **AND** the banner additionally shows a denied-today count (rows since the
  start of the current owner-tz day)
- **AND** the banner does not render when no such rows exist for the current month

#### Scenario: An attempts drawer lists recent denials with session doors
- **WHEN** the fleet-halt banner is active
- **THEN** an expandable drawer lists the most recent denied attempts (butler,
  timestamp, failure reason)
- **AND** each row whose `session_id` is non-null links to that session's detail
  page (`/sessions/:id`), mirroring the session-door pattern the Top Sessions
  table already uses
- **AND** rows with no `session_id` (pre-session ceiling denials) render without
  a session door instead of a dead or broken link

#### Scenario: An attention-ledger push notifies the owner once per breach window
- **WHEN** the monthly ceiling transitions from not-breached to breached — the
  first `quota_skip` dispatch denial with `failure_reason` prefix `"Monthly
  spend ceiling reached"` in the current calendar month, detected inline in the
  spawner's ceiling-deny branch (`spawner.py`, `maybe_push_fleet_halt_attention`
  in `butlers.core.fleet_halt_attention`)
- **THEN** exactly one `public.attention_ledger` row is written with
  `source="notify"`, `outcome="delivered"`, `priority_label="high"` (the
  same lever `notify()` itself uses to always bypass quiet-hours/context-bus
  suppression — a fleet halt is expressed via the ledger's own severity dial,
  not a bespoke bypass), a `dedup_key` identifying the calendar-month halt
  window (e.g. `ceiling_halt:2026-07`), and `metadata` carrying the current
  denied-dispatch count for the month plus a door URL into the Spend page's
  attempts drawer (`/spend?openDrawer=fleet-halt`, which auto-expands the
  drawer from Scenario "An attempts drawer lists recent denials with session
  doors" above)
- **AND** the owner is paged through the same notify-boundary gating/dispatch
  primitives `notify()` uses (quiet hours via `public.approvals_policy`,
  context-bus dnd/sleeping, Switchboard `deliver()`)
- **AND** every subsequent ceiling denial in the same calendar month writes
  NEITHER another `attention_ledger` row NOR another page — debounced by a
  durable per-window marker in `public.audit_log`
  (`action="ceiling_halt_notified"`, `note=<the window>`), mirroring the same
  debounce mechanism `butlers.jobs.secrets_lifecycle` already uses for its own
  once-per-state-transition owner pushes
- **AND** the entire push is best-effort and failure-isolated: any failure
  (ledger write, debounce lookup, delivery) is logged and swallowed, and never
  blocks or delays the spawner's deny decision

#### Scenario: Degraded attempts source never renders as "no denials"
- **WHEN** `GET /api/dispatch/attempts` fails (network error, non-2xx)
- **THEN** the Spend page SHALL render a degraded-source note for the fleet-halt
  state (per the fleet degraded-source convention) instead of silently omitting
  the banner, which would read as a false "the fleet is not halted"

### Requirement: Spend Live Stream
The dashboard SHALL fan per-call spend events onto the unified fleet event bus (`WS /api/events/stream`) (the earlier dedicated `WS /api/spend/stream` route was retired in bu-01r64.2 once the bus fully covered this traffic).

#### Scenario: Stream event shape
- **WHEN** the runtime records a completed LLM call
- **THEN** an event `{type: "spend", data: {kind: "call", ts, butler, model, tokens_in, tokens_out, tokens_cached, tokens_cache_write, cost_usd, session_id, extra}}` is broadcast on `WS /api/events/stream` (token fields are `tokens_in`/`tokens_out` for the uncached buckets plus `tokens_cached`/`tokens_cache_write` for prompt-cache reads/writes, and cost is `cost_usd` in dollars, not `cost_cents`)
- **AND** the frontend appends events to the forecast chart series without re-fetching.

#### Scenario: Cache invalidation on live spend events
- **WHEN** a `"spend"` event is broadcast on `WS /api/events/stream`
- **THEN** the shared cache-patch registry (`event-cache-registry.ts`'s `spendPatch`) invalidates `["cost-summary"]`, `["daily-costs"]`, `["top-sessions"]`, `["costs-by-schedule"]`, `["spend-breakdown"]`, `["spend-rules"]`, and `["spend-forecast"]` (bu-01r64.4 added the last three — the page's own breakdown/rules/forecast queries — closing a coverage-manifest gap where they polled a raw 60-120s literal instead of riding the bus like the other four)
- **AND** each of those queries' own `refetchInterval` is `useBusAwarePollInterval` (a reconciliation sweep while the bus is connected, a fast fallback while it is down), not the primary update path

### Requirement: Spend Rules Savings Job
The system SHALL compute `spend_rules.saved_7d` daily by comparing the cost of each rule's chosen action against the baseline (default tier model).

#### Scenario: Daily savings computation
- **WHEN** the daily savings job runs
- **THEN** for each rule with `enabled` and at least one matching call in the prior 7 days, `saved_7d = baseline_cost - actual_cost`
- **AND** `saved_7d` is stored on the rule row
- **AND** the UI surfaces this value in the rules table.

### Requirement: Schedule Cadence Forecast

The by-schedule projection SHALL be derived from the cron expression's own
cadence over an average Gregorian calendar month, measured over a whole number
of that expression's own repeat cycles, and SHALL be presented separately from
the cost measured over the queried range, with the basis it was computed on
stated once per response.

ID: REQ-dashboard-spend-dashboard-002
Source: dashboard-spend-dashboard Spend API "a code-level TODO marks the location of the smarter estimator for a future change"; bu-6jv4m.2; design.md Decisions 1-3
Scope: v1-mandatory

#### Scenario: Monthly cadence is the cron's own, on a stated basis

- **WHEN** `butlers.core.sessions.schedule_costs` builds a row for a schedule
  whose `cron` croniter can parse
- **THEN** it emits `projected_monthly_runs`, the number of times that
  expression fires in an average Gregorian calendar month of
  `365.2425 / 12 = 30.436875` days, and states `forecast_basis` once beside the
  rows: a human-readable statement of that basis containing the literal number
  `30.436875`
- **AND** `GET /api/spend/by-schedule` returns `projected_monthly_runs` on each
  `ScheduleCost` and `forecast_basis` on the response envelope
  (`meta.forecast_basis`), so a reader never has to infer what the projection
  was multiplied by. The basis is a constant of the estimator: it is stated once
  per response rather than copied onto every row, where the repetition would
  imply it could vary by schedule
- **AND** the quantity carries the same name on both sides of the boundary --
  `projected_monthly_runs` in the `schedule_costs` payload and in
  `ScheduleCost` -- so one name locates every producer and consumer of it
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

#### Scenario: The counting window is a whole number of the expression's own cycles

- **WHEN** the estimator sizes the window it will count occurrences in
- **THEN** the length is decided by which calendar fields the expression
  restricts, not by how much sampling happened to fit: a restricted
  day-of-month or month (or an `L`/`#` day-of-week form) repeats annually and is
  measured over a calendar year; a restricted day-of-week repeats weekly and is
  measured over seven days; an expression restricting neither repeats daily and
  is measured over one day
- **AND** a seasonal expression is therefore never measured over its dense
  season alone: `0 * * 1 *` (hourly, but only in January) projects `744 / 12 =
  62` runs a month, where taking the rate over a window that stops inside a
  January reports ~81, and `* * * 1 *` (per-minute, January only) would report
  ~43,829 against a true 3,720
- **AND** a day-of-week-restricted expression is measured over a whole week
  regardless of which weekday it names, since a window shorter than its cycle
  under-reports every weekday except the anchor's own: `* * * * 3` measured over
  whole days alone reads ~4,873 against a true 6,261
- **AND** an annual expression that a single calendar year misses entirely
  (`0 9 29 2 *`) widens to a whole four-year leap cycle rather than being
  reported as never firing.

#### Scenario: A cadence that cannot be established projects nothing rather than a number

- **WHEN** a schedule's `cron` cannot be parsed by croniter, can never occur
  (`0 0 30 2 *` is well-formed and 30 February is not a date), or is too dense
  for a whole cycle of it to be enumerated within the estimator's occurrence
  ceiling
- **THEN** the cadence estimator returns `0.0` and does not raise —
  `_schedule_costs_from_data` builds every row of the by-schedule response, so a
  raised exception on one pathological cron string would take out the whole
  response, and croniter raises `CroniterBadDateError` while enumerating an
  expression its own validity check accepts
- **AND** `projected_monthly_runs` and `projected_monthly_usd` are both `0`,
  while the measured `total_runs` and `total_cost_usd` for that schedule are
  still reported truthfully
- **AND** the UI renders the two forecast cells as an em dash rather than
  `$0.00`, because "this cadence cannot be forecast" and "this schedule costs
  nothing" are different claims. Declining to forecast is the required
  behaviour for a too-dense expression: a rate taken over a fraction of its
  cycle is a plausible-looking wrong number.

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
- **THEN** the removed rule's `position`, `condition` and `action` are displayed
  persistently, not only in a toast that expires — the rule is already gone from
  the server, and a bare "failed" would have destroyed it and told no one
- **AND** the display is recoverable, not merely visible: the complete
  definition is rendered as selectable, copyable text equal to the request body
  that re-creates the rule, so reconstructing it by hand is a transcription
  rather than a guess. A prose description the owner can read but cannot act on
  does not satisfy this
- **AND** the restore affordance remains active so the request can be retried.

#### Scenario: Delete, restore, and reorder are serialized against each other

- **WHEN** a restore is activated while a reorder of the same list is still in
  flight
- **THEN** the restore is not dispatched until the reorder settles, because all
  three position-renumbering mutations share one mutation scope
- **AND** a restore therefore never computes its position against ordering the
  server has already moved past.

## Source References
- PLAN.md §5 `/settings/spend` API surface and §6 Phase 3 implementation order.
- Visual reference: the `SpendDashboard` redesign prototype (graduated; now shipped in `frontend/`).
- Reuses `audit.append()` from dashboard-audit-log.
