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
  - **Routing rules table**: rule rows in evaluation order with drag-to-reorder; columns `condition · action · saved 7d`. Order is top-to-bottom; first match wins at runtime.
  - **Anomaly section**: deferred. The page carries only a source-code TODO comment in the forecast section; no anomaly copy is rendered to the user.
- **AND** no recharts or other chart library is loaded for this page.

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
- **AND** a code-level TODO marks the location of the smarter estimator for a future change.

#### Scenario: Spend by-schedule aggregation
- **WHEN** `GET /api/spend/by-schedule` (or `?by=feature` on `/api/spend/breakdown`) is called and a schedule ran under 2+ models within the queried window
- **THEN** the response contains exactly one `ScheduleCost` entry per `(butler, schedule_name)` — the per-butler fan-out prices each `(schedule, model)` fragment individually (pricing is model-specific) but merges same-named fragments into a single bucket (summed `total_runs` and `total_cost_usd`; `runs_per_day` taken once, since it derives only from the cron) before returning
- **AND** `avg_cost_per_run` and `projected_monthly_usd` are computed from the merged totals, not from any individual model fragment — a multi-model schedule must never under-rank its true burn or produce duplicate `(butler, schedule_name)` rows (the frontend keys table rows on `${butler}-${schedule_name}`).

#### Scenario: MCP tool-absence is not a degraded source
- **WHEN** the per-butler MCP fan-out behind `/api/spend`, `/api/spend/daily`, `/api/spend/top-sessions`, or `/api/spend/by-schedule` calls a butler that has never registered the requested tool (`sessions_summary` / `sessions_daily` / `top_sessions` / `schedule_costs` are registered only for non-STAFFER butlers — see `core_tools/_sessions.py`, `core_tools/_scheduling.py` — so every STAFFER butler, e.g. `switchboard`/`messenger`/`qa`, structurally lacks all four regardless of its `core_groups` config)
- **THEN** the call fails with `fastmcp.exceptions.ToolError` whose message starts with `"Unknown tool:"`, and the router classifies this as legitimately absent — the butler contributes a truthful zero/empty result and is NOT added to `unavailable_butlers`
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
- **THEN** the rule is inserted at `position` (default: end)
- **AND** the call invokes `audit.append("spend.rule")`.
- **WHEN** `PUT /api/spend/rules/{id}` is called
- **THEN** the rule fields are updated atomically; if `position` changed, other rules' positions are shifted to maintain the order.
- **WHEN** `DELETE /api/spend/rules/{id}` is called
- **THEN** the rule is removed and remaining rules' positions are compacted (no gaps).

#### Scenario: Monthly ceiling
- **WHEN** `PUT /api/spend/ceiling {monthly_usd}` is called
- **THEN** the singleton ceiling row is updated
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

## Source References
- PLAN.md §5 `/settings/spend` API surface and §6 Phase 3 implementation order.
- Visual reference: the `SpendDashboard` redesign prototype (graduated; now shipped in `frontend/`).
- Reuses `audit.append()` from dashboard-audit-log.
