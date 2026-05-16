## ADDED Requirements

### Requirement: Spend Dashboard Page
The dashboard SHALL have a page at `/settings/spend` rendered in the Dispatch design language showing total spend, breakdowns, a forecast chart, routing rules, and a monthly ceiling.

#### Scenario: Spend page layout
- **WHEN** a user navigates to `/settings/spend`
- **THEN** the page renders, in vertical order:
  - **Page header**: title "Spend", mono eyebrow "system · cost", clock (mono, tabular nums).
  - **4-cell KPI strip**: `mtd usd`, `today usd`, `forecast eom usd`, `ceiling usd`. Mega-number in sans 500 tabular-nums, mono sub-label with delta vs. prior period.
  - **Forecast chart**: hand-rolled SVG. Solid line for MTD daily series, dashed line for projection from today to month end, hairline horizontal at the ceiling. No charting library.
  - **Breakdown section**: bars by `butler`, `model`, `feature` via tabbed picker. Each bar is plain CSS (≤ 8 lines per bar), no library.
  - **Routing rules table**: rule rows in evaluation order with drag-to-reorder; columns `condition · action · saved 7d`. Order is top-to-bottom; first match wins at runtime.
  - **Anomaly section**: placeholder copy "Anomaly detection — TODO. See spend forecast.".
- **AND** no recharts or other chart library is loaded for this page.

### Requirement: Spend API
The dashboard SHALL expose the spend endpoints.

#### Scenario: Spend totals
- **WHEN** `GET /api/spend?period=24h|7d|30d|90d|ytd|all` is called
- **THEN** the response includes `total_usd`, `period_start`, `period_end`.

#### Scenario: Spend breakdown
- **WHEN** `GET /api/spend/breakdown?by=butler|model|feature` is called
- **THEN** the response is `ApiResponse[BreakdownRow[]]` where `BreakdownRow = {key: str, total_usd: float, share: float}` ordered by `total_usd DESC`.

#### Scenario: Spend forecast (naive estimator v1)
- **WHEN** `GET /api/spend/forecast` is called
- **THEN** the response is `{daily: {date, usd}[], projected_eom_usd: float, ceiling_usd: float | null}`
- **AND** `projected_eom_usd = mtd_total_usd / max(days_elapsed, 1) × days_in_month`
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
- **THEN** an event `{ts, butler, model, input_tokens, output_tokens, cost_cents}` is broadcast to `WS /api/spend/stream` subscribers
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
- `pr/overview/settings-refactor/settings-expanded.jsx :: SpendDashboard` is the visual reference.
- Reuses `audit.append()` from dashboard-audit-log.
