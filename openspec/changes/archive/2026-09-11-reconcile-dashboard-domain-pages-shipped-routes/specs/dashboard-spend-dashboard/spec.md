## ADDED Requirements

### Requirement: Canonical Spend Dashboard Page
The dashboard SHALL have a page at `/spend` rendered in the Dispatch design language showing total spend, breakdowns, a forecast chart, routing rules, and a monthly ceiling.

#### Scenario: Spend page layout
- **WHEN** a user navigates to `/spend`
- **THEN** the page renders, in vertical order:
  - **Page header**: title "Spend" rendered via the shared `Page` overview shell. The page does not render a mono eyebrow "system · cost" or a clock.
  - **4-cell KPI strip**: `MTD Spend`, `Projected EOM`, `Monthly Ceiling`, `Days in Month`. Mega-number in sans 500 tabular-nums, mono sub-label. There is no `today` cell, and sub-labels show context such as days elapsed/remaining, not a delta vs. prior period.
  - **Forecast chart**: hand-rolled SVG. Solid line for MTD daily series, dashed line for projection from today to month end, hairline horizontal at the ceiling. No charting library.
  - **Breakdown section**: bars by `butler`, `model`, `feature`, `purpose` via tabbed picker. Each bar is plain CSS (≤ 8 lines per bar), no library.
  - **By Schedule table**: per-cron rows under two visually separated column groups — "Measured · selected range" (`Runs`, `Cost`, `Avg/run`) and "Forecast · per month" (`Runs`, `Cost`) — with the API's `forecast_basis` stated once beneath the table. A projection is never rendered in the same undifferentiated run of columns as measured history, and a schedule whose cadence could not be computed renders both forecast cells as an em dash rather than `$0.00`.
  - **Routing rules table**: rule rows in evaluation order with drag-to-reorder; columns `condition · action · saved 7d`. Order is top-to-bottom; first match wins at runtime. Removal is gated by the confirmation and restore contract in "Requirement: Routing Rule Deletion Safety" — a rule is never deleted on a single activation.
  - **Anomaly section**: deferred. The page carries only a source-code TODO comment in the forecast section; no anomaly copy is rendered to the user.
- **AND** no recharts or other chart library is loaded for this page.

## REMOVED Requirements

### Requirement: Spend Dashboard Page

**Reason**: The earlier requirement names `/settings/spend` as the page even though the shipped canonical destination is `/spend`.

**Migration**: Use `/spend`; `/costs` and `/settings/spend` remain compatibility aliases.
