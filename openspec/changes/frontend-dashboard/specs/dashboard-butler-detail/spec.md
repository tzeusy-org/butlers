# Dashboard Butler Detail

Butler detail page — the dashboard surface for a specific butler at `/butlers/:name`. Provides identity, status, observability, and configuration access through a multi-tab interface. This spec covers the tab structure, rendering logic, and URL semantics; individual tab contents are defined in their respective specs.

---

## Tab Structure

### Always-Rendered Tabs

The butler detail page MUST render these tabs for every butler:

1. **Overview** — Quick snapshot of identity, health, activity, and error summary (see "Overview Tab" section below)
2. **Sessions** — Cross-session history with filters and session detail drawer (see dashboard-sessions spec)
3. **Config** — Butler configuration: `butler.toml`, `MANIFESTO.md`, `CLAUDE.md`, `AGENTS.md` (see dashboard-config spec)
4. **Skills** — Available skills with descriptions and full SKILL.md content (see dashboard-skills spec)
5. **Schedules** — Scheduled tasks with cron expression, prompt, source, enabled state, and mutations (create/edit/delete/toggle) (see dashboard-schedules spec)
6. **Trigger** — Freeform prompt submission with skill-prefill support (see dashboard-trigger spec)
7. **State** — Key-value store browser with prefix filter and mutations (create/set/delete) (see dashboard-state spec)
8. **CRM** — Relationship/contact data (see dashboard-crm spec)
9. **Memory** — Memory tier cards and memory browser with facts/rules/episodes tabs (see dashboard-memory spec)

### Conditional Tabs

Additional tabs appear only for specific butlers:

- **Health** — Appears only when `name === "health"`. Health sub-routes and measurements (see dashboard-health spec)
- **Collections** — Appears only when `name === "general"`. General data collections and counts (see dashboard-collections spec)
- **Entities** — Appears only when `name === "general"`. General entity browser (see dashboard-entities spec)
- **Routing Log** — Appears only when `name === "switchboard"`. Cross-butler routing history (see dashboard-routing-log spec)
- **Registry** — Appears only when `name === "switchboard"`. Registered butlers and module state (see dashboard-registry spec)

---

## Conditional Rendering Rules

The butler detail page SHALL conditionally render tabs according to these rules:

| Tab | Always Rendered | Condition |
| --- | --- | --- |
| Overview | Yes | — |
| Sessions | Yes | — |
| Config | Yes | — |
| Skills | Yes | — |
| Schedules | Yes | — |
| Trigger | Yes | — |
| State | Yes | — |
| CRM | Yes | — |
| Memory | Yes | — |
| Health | No | `name === "health"` |
| Collections | No | `name === "general"` |
| Entities | No | `name === "general"` |
| Routing Log | No | `name === "switchboard"` |
| Registry | No | `name === "switchboard"` |

### Scenario: Health butler tab rendering

- **WHEN** navigating to `/butlers/health`
- **THEN** MUST render all 9 always-rendered tabs
- **AND** MUST render the `Health` tab
- **AND** MUST NOT render `Collections`, `Entities`, `Routing Log`, or `Registry` tabs

### Scenario: General butler tab rendering

- **WHEN** navigating to `/butlers/general`
- **THEN** MUST render all 9 always-rendered tabs
- **AND** MUST render `Collections` and `Entities` tabs
- **AND** MUST NOT render `Health`, `Routing Log`, or `Registry` tabs

### Scenario: Switchboard butler tab rendering

- **WHEN** navigating to `/butlers/switchboard`
- **THEN** MUST render all 9 always-rendered tabs
- **AND** MUST render `Routing Log` and `Registry` tabs
- **AND** MUST NOT render `Health`, `Collections`, or `Entities` tabs

### Scenario: Regular butler tab rendering

- **WHEN** navigating to `/butlers/relationship` or any other standard butler
- **THEN** MUST render all 9 always-rendered tabs
- **AND** MUST NOT render any conditional tabs

---

## Tab URL Semantics

The active tab is controlled via the `?tab=` query parameter:

- **Default behavior** — When no `?tab=` query param is present, the active tab defaults to `overview` and the URL does not include the query param.
- **Tab navigation** — Clicking a tab updates the URL to include `?tab={tab-name}` (e.g., `/butlers/health?tab=schedules`).
- **Overview exception** — When the active tab is `overview`, the URL remains `/butlers/:name` (no query param).
- **Deep linking** — The page supports deep-link navigation via `?tab=` for all valid tabs (always-rendered + conditional for the given butler).
- **Invalid tab handling** — If an invalid tab name is provided via `?tab=`, the page SHALL default to the overview tab.

### Accepted Tab Values

Tab names accepted in the `?tab=` query parameter:

- **Always-rendered:** `overview`, `sessions`, `config`, `skills`, `schedules`, `trigger`, `state`, `crm`, `memory`
- **Health butler:** `health`
- **General butler:** `collections`, `entities`
- **Switchboard butler:** `routing-log`, `registry`

### Scenario: Deep link to schedules tab

- **WHEN** a user navigates to `/butlers/health?tab=schedules`
- **THEN** the page MUST render the Schedules tab as active
- **AND** the URL MUST remain `/butlers/health?tab=schedules`

### Scenario: Tab navigation updates URL

- **WHEN** a user is viewing `/butlers/health` and clicks the `Sessions` tab
- **THEN** the URL MUST update to `/butlers/health?tab=sessions`

### Scenario: Clicking Overview clears query param

- **WHEN** a user is viewing `/butlers/health?tab=schedules` and clicks the `Overview` tab
- **THEN** the URL MUST update to `/butlers/health` (query param removed)

---

## Overview Tab

Butler detail overview tab — the default view when navigating to `/butlers/:name`. Provides a quick snapshot of the butler's identity, health, and current activity.

### Requirement: Butler identity card

The butler detail overview tab SHALL display an identity card at the top containing:
- **Butler name** — the butler's configured name from `butler.toml`
- **Description** — the first line of the butler's `MANIFESTO.md` file (the one-line value proposition)
- **Port** — the MCP server port from `butler.toml`
- **Uptime** — the time elapsed since the butler daemon started, formatted as a human-readable duration (e.g., "3d 4h 12m"). Derived from the `status()` MCP response `started_at` field. If the butler is unreachable, display "Unreachable" with a red badge.

#### Scenario: Identity card displays for a healthy butler

- **WHEN** a user navigates to `/butlers/health` and the health butler is reachable
- **THEN** the identity card MUST display the butler name "health", the first line of its MANIFESTO.md, its port number, and the uptime duration

#### Scenario: Identity card for an unreachable butler

- **WHEN** a user navigates to `/butlers/health` and the health butler's `status()` call fails
- **THEN** the identity card MUST display the butler name and config-derived fields (description, port)
- **AND** the uptime field MUST display "Unreachable" with a red visual indicator

---

### Requirement: Module health badges

The butler detail overview tab SHALL display module health badges — one colored dot per module — showing the health status of each module registered with the butler.

Health is determined from the `status()` MCP response per D13:
- **Green** — module reports `connected` or `healthy` status
- **Yellow** — module reports `degraded` status
- **Red** — module reports `error` status
- **Gray** — module status is `unknown` (e.g., status() response did not include the module)

Each badge MUST display the module name as a label next to the dot.

#### Scenario: All modules healthy

- **WHEN** the `switchboard` butler's `status()` reports modules `telegram` (healthy) and `email` (healthy)
- **THEN** the overview tab MUST display two green dots labeled "telegram" and "email"

#### Scenario: Module in error state

- **WHEN** the `switchboard` butler's `status()` reports module `telegram` with health status `error`
- **THEN** the overview tab MUST display a red dot labeled "telegram"

#### Scenario: Butler is unreachable

- **WHEN** the butler's `status()` call fails
- **THEN** the module health badges MUST display gray dots for all configured modules (from `butler.toml`) with status "unknown"

---

### Requirement: Active session indicator

The butler detail overview tab SHALL display an active session indicator showing whether the butler is currently running a runtime session.

- **When active:** Display "Currently running runtime session" with the elapsed time since `started_at`, updating every second. Derived from querying the butler's `sessions` table for a row where `completed_at IS NULL`.
- **When idle:** Display "Idle" with the time since the last completed session (e.g., "Last session 15 minutes ago").

#### Scenario: Butler has an active session

- **WHEN** the `health` butler's `sessions` table contains a row with `completed_at IS NULL` and `started_at = 2 minutes ago`
- **THEN** the indicator MUST display "Currently running runtime session" with "2m elapsed" (updating live)

#### Scenario: Butler is idle

- **WHEN** the `health` butler has no sessions with `completed_at IS NULL` and the most recent session completed 15 minutes ago
- **THEN** the indicator MUST display "Idle — last session 15 minutes ago"

#### Scenario: Butler has no sessions at all

- **WHEN** the butler's `sessions` table is empty
- **THEN** the indicator MUST display "Idle — no sessions recorded"

---

### Requirement: Error summary

The butler detail overview tab SHALL display an error summary showing the count of failed sessions in the last 24 hours.

The error summary MUST include:
- **Failed session count** — number of sessions with `success=false` and `completed_at` within the last 24 hours
- **Link to filtered sessions** — clicking the error count MUST navigate to the sessions tab with a pre-applied filter for `success=false` and the last 24-hour date range

#### Scenario: Butler has recent failures

- **WHEN** the `health` butler has 3 failed sessions in the last 24 hours
- **THEN** the error summary MUST display "3 failed sessions in the last 24h"
- **AND** clicking it MUST navigate to the sessions tab filtered to show only those failed sessions

#### Scenario: No recent failures

- **WHEN** the butler has zero failed sessions in the last 24 hours
- **THEN** the error summary MUST display "No failures in the last 24h" with a positive visual indicator (e.g., green text or checkmark)
