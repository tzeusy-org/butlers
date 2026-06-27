# dashboard-spend-dashboard

## Purpose

The `/settings/spend` page is the operator's view into system cost: total spend, breakdowns by butler/model/feature, a hand-rolled SVG forecast chart projecting month-end land, store-and-evaluate routing rules with per-rule 7-day savings, a monthly ceiling, and a live per-call spend stream. It is part of the Console-direction redesign of `/settings` and is rendered in the Dispatch design language already shipped on `/overview`, `/butlers`, and `/qa`. It is backed by the spend endpoints (`/api/spend/*`) served by `spend.py` (the renamed `costs.py` router), including rules CRUD, the monthly ceiling, and the `WS /api/spend/stream` ticker. No charting library is loaded for this page.

## Requirements

### Requirement: Spend Dashboard Page
The dashboard SHALL have a page at `/settings/spend` rendered in the Dispatch design language showing total spend, breakdowns, a forecast chart, routing rules, and a monthly ceiling.

#### Scenario: Spend page layout
- **WHEN** a user navigates to `/settings/spend`
- **THEN** the page renders, in vertical order:
  - **Page header**: title "Spend" rendered via the shared `Page` overview shell. The page does not render a mono eyebrow "system · cost" or a clock.
  - **4-cell KPI strip**: `MTD Spend`, `Projected EOM`, `Monthly Ceiling`, `Days in Month`. Mega-number in sans 500 tabular-nums, mono sub-label. There is no `today` cell, and sub-labels show context such as days elapsed/remaining, not a delta vs. prior period.
  - **Forecast chart**: hand-rolled SVG. Solid line for MTD daily series, dashed line for projection from today to month end, hairline horizontal at the ceiling. No charting library.
  - **Breakdown section**: bars by `butler`, `model`, `feature` via tabbed picker. Each bar is plain CSS (≤ 8 lines per bar), no library.
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

#### Scenario: Spend forecast (naive estimator v1)
- **WHEN** `GET /api/spend/forecast` is called
- **THEN** the response is `{days: {date, cost_usd, projected}[], projected_eom_usd: float, days_in_month: int, days_elapsed: int, mtd_usd: float, ceiling_usd: float | null, projection_confidence: "low" | "normal"}` (the field is `days` not `daily`, and per-day cost is `cost_usd` not `usd`)
- **AND** `projected_eom_usd = mtd_total_usd / max(days_elapsed, 1) × days_in_month`
- **AND** `projection_confidence = "low"` when `days_elapsed < 3`, else `"normal"`. This signals to the Console aggregator NOT to fire a "spend near ceiling" attention item from a low-confidence projection.
- **AND** a code-level TODO marks the location of the smarter estimator for a future change.

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

### Requirement: Spend Live Stream
The dashboard SHALL emit per-call spend events over `WS /api/spend/stream`.

#### Scenario: Stream event shape
- **WHEN** the runtime records a completed LLM call
- **THEN** an event `{kind: "call", ts, butler, model, tokens_in, tokens_out, cost_usd, session_id, extra}` is broadcast to `WS /api/spend/stream` subscribers (token fields are `tokens_in`/`tokens_out`, and cost is `cost_usd` in dollars, not `cost_cents`)
- **AND** the frontend appends events to the forecast chart series without re-fetching.

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
