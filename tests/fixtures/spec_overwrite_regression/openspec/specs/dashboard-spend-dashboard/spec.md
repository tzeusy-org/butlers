# dashboard-spend-dashboard Specification

## Purpose

Trimmed copy of the live Spend dashboard baseline as it stood after
`spend-ledger-truth` archived. Only the requirement this fixture exercises is
kept.

## Requirements

### Requirement: Spend API
The dashboard SHALL expose the spend endpoints.

#### Scenario: Spend totals
- **WHEN** `GET /api/spend?period=today|7d|30d` is called (or a custom range via `from`/`to` ISO date params)
- **THEN** the response is `ApiResponse[SpendSummary]` where `SpendSummary = {period, total_cost_usd, total_sessions, total_input_tokens, total_output_tokens, by_butler, by_model, unpriced_models, divergences, historical_attribution_note}`. There are no `total_usd`, `period_start`, or `period_end` fields.
- **AND** every dollar amount, token actual, `by_butler` value, and `by_model` value is grouped from `public.token_usage_ledger` joined to `public.model_catalog` by `catalog_entry_id`, so it describes the model that actually consumed the tokens rather than `sessions.model`.
- **AND** the router MUST NOT substitute per-butler session fan-out pricing when that ledger query is unavailable; it returns degraded source evidence instead of a plausible alternate dollar total.
- **AND** implicit calendar defaults derive `today` from UTC, so preset summaries, the daily default range, MTD breakdown, and forecast elapsed-day calculation align with the UTC month used by the monthly-ceiling ledger helper.

#### Scenario: Spend breakdown
- **WHEN** `GET /api/spend/breakdown?by=butler|model|purpose` is called
- **THEN** the response is `ApiResponse[{by: str, breakdown: {key: cost_usd}, unpriced_models: [], billing_classes: {model: class}, source_error: bool, divergences: [], historical_attribution_note: str | null}]` for the current month (MTD), with no guaranteed map order.
- **AND** `butler`, `model`, and `purpose` values are grouped directly from `public.token_usage_ledger` and priced through the same executed-model pricing calculation as the monthly ceiling, not from requested session models or an MCP fan-out.
- **AND** `GET /api/spend/breakdown?by=feature` retains the by-schedule behavior defined below; it is metadata evidence outside this aggregate ledger-attribution change.

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
