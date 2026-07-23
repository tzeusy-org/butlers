## MODIFIED Requirements

### Requirement: Cost widget for dashboard overview

The dashboard MUST provide a `CostWidget` component for embedding on the overview page. The widget MUST display:
- Title "Cost Today" with a "View all" link to `/costs`.
- Total cost for the day formatted as currency when its direct summary query succeeds with priced data.
- Top butler name and cost (e.g., "Top: health ($3.50)") when its direct summary query succeeds with a top butler.
- A 7-bar sparkline placeholder showing a mock 7-day trend (pending replacement with Recharts).

The widget MUST distinguish a direct Overview summary-query failure from a successful
compatibility envelope with `source_error` and from a successful zero-cost summary.

#### Scenario: Widget with no data

- **WHEN** a successful summary has `totalCostUsd` 0 and `topButler` null
- **THEN** the widget MUST display "$0.00" and no top-butler line

#### Scenario: Direct summary reader failure is unavailable

- **WHEN** the Overview's direct `useSpendSummary("today")` query reports an error
- **THEN** `DashboardPage` MUST pass an explicit unavailable state to `CostWidget`
- **AND** the widget MUST render a named cost-summary-unavailable state
- **AND** it MUST NOT render a formatted cost total or a top-butler claim from fallback or retained data

#### Scenario: Successful compatibility summary remains degraded

- **WHEN** the direct summary request succeeds with `source_error: true`
- **THEN** the widget MUST render its existing source-degraded state
- **AND** it MUST NOT render the direct-summary-unavailable state or a calm "$0.00" total

### Requirement: Top sessions table

The dashboard MUST provide a `TopSessionsTable` component displaying the most expensive LLM sessions. The table MUST display columns: rank number (#), Butler (secondary badge), Model (muted text), Tokens (input/output formatted as abbreviated counts separated by "/"), Cost (right-aligned, bold, tabular-nums), Time (right-aligned, formatted as "MMM d, HH:mm").

#### Scenario: Session token display

- **WHEN** a session has 50,000 input tokens and 12,000 output tokens
- **THEN** the Tokens column MUST display "50.0K / 12.0K"

#### Scenario: Direct top-sessions reader failure is unavailable

- **WHEN** the Overview's direct `useTopSessions()` query reports an error
- **THEN** `DashboardPage` MUST pass an explicit unavailable state to `TopSessionsTable`
- **AND** the table MUST render a named top-sessions-unavailable state before its empty-state branch
- **AND** it MUST NOT render "No session data available"

#### Scenario: Successful empty top sessions remain calm

- **WHEN** the direct top-sessions query succeeds with an empty list
- **THEN** the table MUST render "No session data available"
- **AND** it MUST NOT render the top-sessions-unavailable state
