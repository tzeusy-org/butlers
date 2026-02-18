# Dashboard Overview

The overview page is the landing page of the dashboard. It provides a single-screen summary of the entire butler ecosystem: a topology graph, aggregate stats, active issues, cost summary, and a recent activity feed. Data is sourced from cross-butler fan-out queries, butler `status()` MCP calls, and the issues aggregation endpoint.

---

## ADDED Requirements

### Requirement: Issues aggregation API

The dashboard API SHALL expose `GET /api/issues` which aggregates active system issues from multiple sources into a unified list. The endpoint performs the following checks:

1. **Unreachable butlers** — for each configured butler, attempt an MCP `status()` call. If the call fails or times out (>5s), emit an issue with `severity="critical"`, `type="unreachable"`.
2. **Failing scheduled tasks** — query `scheduled_tasks` across all butler DBs for tasks where `consecutive_failures >= 3`. Emit an issue with `severity="warning"`, `type="failing_task"` for each.
3. **Module dependency failures** — from the `status()` response, check per-module health indicators. If any module reports `error` status, emit an issue with `severity="warning"`, `type="module_error"`.
4. **Cost anomalies (butler-level)** — for each butler, compute today's aggregate spend and compare to the butler's 7-day daily average. If today's spend > 2x the 7-day daily average, emit an issue with `severity="warning"`, `type="cost_anomaly"`.
5. **Failed notification deliveries** — query the `notifications` table in the Switchboard DB for notifications with `status='failed'` in the last 24 hours. Emit an issue with `severity="warning"`, `type="notification_failure"` for each distinct `source_butler + channel` combination.

The response SHALL be a JSON array of issue objects, each containing:
- `severity` (string) — `"critical"` or `"warning"`
- `type` (string) — one of `"unreachable"`, `"failing_task"`, `"module_error"`, `"cost_anomaly"`, `"notification_failure"`
- `butler` (string) — the butler name associated with the issue
- `description` (string) — human-readable description of the issue
- `link` (string, optional) — URL path to the relevant dashboard page for investigation

Issues SHALL be sorted by severity (critical first), then by butler name alphabetically.

#### Scenario: All butlers healthy, no issues

- **WHEN** `GET /api/issues` is called and all butlers are reachable, no tasks are failing, no cost anomalies exist, and no notification failures occurred
- **THEN** the API MUST return an empty array `[]`
- **AND** the response status MUST be 200

#### Scenario: Butler unreachable

- **WHEN** the `health` butler's MCP `status()` call fails with a connection error
- **THEN** the API MUST include an issue with `severity="critical"`, `type="unreachable"`, `butler="health"`, and a description like "Health butler is unreachable"
- **AND** `link` MUST point to the butler detail page (e.g., `/butlers/health`)

#### Scenario: Scheduled task with consecutive failures

- **WHEN** the `general` butler's `scheduled_tasks` table contains a task `daily-review` with `consecutive_failures=5`
- **THEN** the API MUST include an issue with `severity="warning"`, `type="failing_task"`, `butler="general"`, and a description like "Scheduled task 'daily-review' has failed 5 consecutive times"

#### Scenario: Butler-level cost anomaly detected

- **WHEN** the `health` butler's aggregate spend today is $4.50 and its 7-day daily average is $1.80 (ratio = 2.5x > 2x threshold)
- **THEN** the API MUST include an issue with `severity="warning"`, `type="cost_anomaly"`, `butler="health"`, and a description like "Health butler daily spend ($4.50) is 2.5x the 7-day average ($1.80)"
- **AND** `link` MUST point to the costs page (e.g., `/costs`)

#### Scenario: Failed notification delivery

- **WHEN** the Switchboard's `notifications` table contains 3 entries with `status='failed'`, `source_butler='relationship'`, `channel='telegram'` in the last 24 hours
- **THEN** the API MUST include an issue with `severity="warning"`, `type="notification_failure"`, `butler="relationship"`, and a description like "3 failed telegram notification deliveries in the last 24h"
- **AND** `link` MUST point to the notifications page (e.g., `/notifications`)

#### Scenario: Module reports error status

- **WHEN** the `switchboard` butler's `status()` response includes a module `telegram` with health status `error`
- **THEN** the API MUST include an issue with `severity="warning"`, `type="module_error"`, `butler="switchboard"`, and a description like "Module 'telegram' is reporting error status"

---

### Requirement: Topology graph

The overview page SHALL render a topology graph using React Flow that visualizes the butler ecosystem.

The graph MUST include:
- **Switchboard node** — positioned at the center of the graph, visually distinct (larger size or different shape) to indicate its role as the routing hub.
- **Butler nodes** — one node per configured butler, positioned around the Switchboard. Each butler node MUST display:
  - The butler name
  - A status badge (green dot = healthy/reachable, red dot = unreachable, yellow dot = degraded)
  - Module health dots — small colored dots for each module (green = healthy, yellow = degraded, red = error), sourced from the butler's `status()` MCP response
- **Heartbeat edges** — dashed edges from the Heartbeat butler to all other butlers (except Switchboard), representing the heartbeat tick relationship.
- **Active session pulse** — when a butler has an active runtime session (`completed_at IS NULL`), its node MUST display a pulsing animation or glow effect.
- **Edge labels** — edges between Switchboard and butlers MAY display the routing channel label (e.g., "telegram", "email") when derived from recent routing activity.
- **Click-to-navigate** — clicking a butler node MUST navigate to `/butlers/:name`.

#### Scenario: Topology graph renders with all butlers

- **WHEN** the overview page loads and 5 butlers are discovered (switchboard, general, health, relationship, heartbeat)
- **THEN** the topology graph MUST render 5 nodes with the Switchboard at the center
- **AND** the Heartbeat butler MUST have dashed edges to all other non-Switchboard butlers

#### Scenario: Butler with active session shows pulse

- **WHEN** the `health` butler has an active runtime session (a session record with `completed_at IS NULL`)
- **THEN** the `health` node in the topology graph MUST display a pulsing animation

#### Scenario: Clicking a butler node navigates to detail

- **WHEN** the user clicks the `relationship` node in the topology graph
- **THEN** the browser MUST navigate to `/butlers/relationship`

#### Scenario: Module health dots reflect status

- **WHEN** the `switchboard` butler's `status()` reports modules: `telegram` (healthy), `email` (error)
- **THEN** the `switchboard` node MUST display a green dot for `telegram` and a red dot for `email`

---

### Requirement: Aggregate stats bar

The overview page SHALL display an aggregate stats bar at the top showing key system-wide metrics.

The stats bar MUST include:
- **Total butlers** — count of all configured butlers
- **Healthy count** — count of butlers currently reachable (status() call succeeded)
- **Sessions today** — count of sessions across all butlers with `started_at` on today's date
- **Failed sessions** — count of sessions across all butlers with `success=false` and `started_at` on today's date
- **Estimated cost today** — sum of estimated cost for all sessions today, computed from token usage (input + output token counts) and applied pricing configuration

#### Scenario: Stats bar displays current metrics

- **WHEN** the overview page loads and there are 5 butlers (4 healthy, 1 unreachable), 23 sessions today (2 failed), and $3.45 estimated spend
- **THEN** the stats bar MUST display: Total Butlers: 5, Healthy: 4, Sessions Today: 23, Failed: 2, Cost Today: $3.45

---

### Requirement: Issues panel

The overview page SHALL display a prominent issues panel sourcing data from the `GET /api/issues` endpoint.

The issues panel MUST:
- Display each issue with a severity badge (`critical` = red, `warning` = yellow), the butler name, and the issue description.
- Support per-issue dismissal — clicking a dismiss control on an issue MUST hide it from the panel. Dismissed issue identity (type + butler combination) SHALL be persisted in `localStorage` so it remains hidden across page refreshes.
- Link each issue to the relevant dashboard page via the `link` field.
- Display a count of active (non-dismissed) issues in the panel header.

#### Scenario: Issues panel shows critical and warning issues

- **WHEN** the `GET /api/issues` endpoint returns 1 critical issue (unreachable butler) and 2 warning issues (cost anomaly, failing task)
- **THEN** the issues panel MUST display all 3 issues with appropriate severity badges
- **AND** the critical issue MUST appear first

#### Scenario: Dismissing an issue persists across refreshes

- **WHEN** the user dismisses the "cost_anomaly" issue for the "health" butler
- **THEN** the issue MUST disappear from the panel
- **AND** after a page refresh, the issue MUST still be hidden
- **AND** the active issue count in the header MUST decrease by 1

#### Scenario: No issues to display

- **WHEN** the `GET /api/issues` endpoint returns an empty array
- **THEN** the issues panel MUST display a positive status message (e.g., "All systems healthy")

---

### Requirement: Failed Notifications panel

The overview page SHALL display a Failed Notifications panel providing a quick view of recent notification delivery failures.

The panel MUST:
- Query the `notifications` table in the Switchboard DB for notifications with `status='failed'` in the last 24 hours.
- Group failures by `source_butler` and `channel` combination.
- Display a count of failed notifications per butler-channel pair (e.g., "2 failed telegram notifications from health").
- Provide a quick link to the full Notifications page (`/notifications`) for detailed investigation.
- Display an empty state message when no failures have occurred in the last 24 hours (e.g., "No failed notifications").

#### Scenario: Failed notifications from multiple butlers

- **WHEN** the Switchboard's `notifications` table contains:
  - 2 failed `email` notifications from `general` butler
  - 3 failed `telegram` notifications from `relationship` butler
- **THEN** the Failed Notifications panel MUST display:
  - "2 failed email notifications from general"
  - "3 failed telegram notifications from relationship"
- **AND** the panel MUST include a link to `/notifications`

#### Scenario: No failed notifications

- **WHEN** the Switchboard's `notifications` table contains no failed notifications in the last 24 hours
- **THEN** the Failed Notifications panel MUST display "No failed notifications in the last 24h"

#### Scenario: Clicking link navigates to notifications page

- **WHEN** the user clicks "View all notifications" in the Failed Notifications panel
- **THEN** the browser MUST navigate to `/notifications`

---

### Requirement: Cost summary widget

The overview page SHALL display a cost summary widget providing a quick view of today's spending.

The widget MUST include:
- **Today's spend** — total estimated cost for today, formatted as currency
- **7-day sparkline** — a small inline Recharts line/area chart showing daily spend for the last 7 days
- **Top spender** — the butler with the highest estimated cost today, with its spend amount

Data is sourced from the `GET /api/costs/summary` and `GET /api/costs/daily` endpoints (defined in the `dashboard-costs` spec).

#### Scenario: Cost widget displays today's metrics

- **WHEN** today's total spend is $5.20, the 7-day trend shows increasing spend, and the top spender is "health" at $2.80
- **THEN** the widget MUST display "$5.20" as today's spend, a sparkline showing the upward trend, and "health ($2.80)" as the top spender

#### Scenario: No cost data available

- **WHEN** no sessions have token data (e.g., before the token tracking migration runs)
- **THEN** the widget MUST display "$0.00" for today's spend and "No data" for the sparkline and top spender

---

### Requirement: Recent activity feed

The overview page SHALL display a recent activity feed showing the last 10 cross-butler events, sourced from the `GET /api/timeline?limit=10` endpoint.

Each event in the feed MUST display:
- Relative timestamp (e.g., "2 minutes ago")
- Butler badge
- Event type icon
- One-line summary (same format as the full timeline page)

**Heartbeat tick collapsing:** Consecutive heartbeat tick events (type `schedule` where `task_name` matches a heartbeat pattern) within the same 10-minute cycle SHALL be collapsed into a single entry displaying: "Heartbeat: N butlers ticked, M failures". This collapsing is a frontend rendering concern — the API returns individual events and the UI groups them before display.

#### Scenario: Activity feed loads with recent events

- **WHEN** the overview page loads and the timeline API returns 10 events
- **THEN** the activity feed MUST display all 10 events in reverse chronological order with timestamp, butler badge, type icon, and summary

#### Scenario: Heartbeat ticks are collapsed

- **WHEN** the timeline API returns 5 consecutive heartbeat tick schedule events (for butlers general, health, relationship, switchboard, heartbeat) all within a 10-minute window, with 1 failure
- **THEN** the activity feed MUST collapse them into a single entry: "Heartbeat: 5 butlers ticked, 1 failure"
- **AND** the collapsed entry MUST be expandable to reveal the individual tick events

#### Scenario: Activity feed shows mixed event types

- **WHEN** the timeline API returns events including a completed session, a routing event, a notification, and several heartbeat ticks
- **THEN** the activity feed MUST display each non-heartbeat event individually and collapse heartbeat ticks as specified
