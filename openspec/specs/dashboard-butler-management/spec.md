# Dashboard Butler Management

## Purpose
Defines the dashboard surfaces for managing butlers as first-class entities: a fleet-wide butler list page, a per-butler detail page with 10+ tabbed views, and switchboard-specific operational surfaces. Together these views give the operator full visibility into butler identity, health, configuration, scheduling, state, memory, MCP tooling, session history, and (for the switchboard) registry, routing, triage, and backfill management. The dashboard is both an observability surface and a write-capable control plane -- operators can create schedules, mutate state, trigger sessions, invoke MCP tools, manage triage rules, and control backfill jobs without leaving the browser.

## ADDED Requirements

### Requirement: Butler List Page
The `/butlers` page shows all registered butlers as a card grid with fleet-level summary statistics.

#### Scenario: Fleet summary cards
- **WHEN** the butler list page loads
- **THEN** two summary cards are displayed at the top: "Total Butlers" (count of all butlers) and "Healthy" (count of butlers with status `ok` or `online`, with percentage)

#### Scenario: Butler card grid
- **WHEN** butlers are loaded from the API
- **THEN** each butler is rendered as a card showing its name (linked to the detail page), a status badge (`Up`, `Down`, `Degraded`, or raw status), the MCP endpoint port, and an "Open details" button
- **AND** butlers are sorted alphabetically by name

#### Scenario: Status badge color mapping
- **WHEN** a butler's status is rendered as a badge
- **THEN** `ok`/`online` maps to an emerald "Up" badge, `error`/`down`/`offline` maps to a destructive "Down" badge, `degraded` maps to an amber outline "Degraded" badge, and any other value renders as a secondary badge with the raw status text

#### Scenario: Loading state
- **WHEN** the butler list API request is in flight
- **THEN** a skeleton loading grid of six placeholder cards is displayed

#### Scenario: Error resilience with stale data
- **WHEN** a refresh request fails but prior butler data exists in cache
- **THEN** the stale butler cards remain visible with an error banner explaining that the shown data is from the last successful fetch

#### Scenario: Empty state
- **WHEN** the API returns zero butlers
- **THEN** an empty-state message is displayed: "No butlers found" with guidance to check daemon status

#### Scenario: Auto-refresh polling
- **WHEN** the butler list page is mounted
- **THEN** the butler list data is polled every 30 seconds to keep status current

### Requirement: Butler Detail Page Structure
The `/butlers/:name` page is a tabbed detail view where each butler is treated as a first-class navigable entity.

#### Scenario: URL-driven tab routing
- **WHEN** a user navigates to `/butlers/:name?tab=<value>`
- **THEN** the active tab is set to the `tab` query parameter value
- **AND** when `tab` is absent or invalid, the default tab is `overview`
- **AND** tab changes update the URL via `replaceState` (no history entry)

#### Scenario: Breadcrumb navigation
- **WHEN** the butler detail page renders
- **THEN** a breadcrumb trail is shown: Overview > Butlers > {butler name}

#### Scenario: Base tabs always present
- **WHEN** any butler detail page loads
- **THEN** the following tabs are always visible: Overview, Sessions, Config, Skills, Schedules, Trigger, MCP, State, CRM, Memory

#### Scenario: Conditionally shown tabs -- switchboard
- **WHEN** the butler name is `switchboard`
- **THEN** two additional tabs are shown after the base tabs: "Routing Log" and "Registry"

#### Scenario: Conditionally shown tabs -- health
- **WHEN** the butler name is `health`
- **THEN** one additional tab is shown: "Health"

#### Scenario: Conditionally shown tabs -- general
- **WHEN** the butler name is `general`
- **THEN** two additional tabs are shown: "Collections" and "Entities"

#### Scenario: Lazy-loaded tabs for performance
- **WHEN** a non-default tab is selected for the first time
- **THEN** its component is loaded on demand via React `lazy()` with a centered "Loading {tab}..." fallback
- **AND** the following tabs are lazy-loaded: Skills, Schedules, Trigger, MCP, State, Memory, Routing Log, Registry

#### Scenario: Tab URL semantics and deep-linking
- **WHEN** the active tab is controlled by the `?tab=` query parameter
- **THEN** `overview` is the default tab and removes the query parameter from the URL
- **AND** accepted deep-link values include all base tab keys (`overview`, `sessions`, `config`, `skills`, `schedules`, `trigger`, `mcp`, `state`, `crm`, `memory`) plus conditional tab keys (`health`, `collections`, `entities`, `routing-log`, `registry`)
- **AND** tab changes update the URL via `replaceState` without creating browser history entries

### Requirement: Tab Structures Reference (Non-Butler Pages)

The following tab structures exist on pages outside the butler detail view. They are documented here as a consolidated reference.

#### Scenario: Memory browser tabs
- **WHEN** the `/memory` page or the butler detail Memory tab is active
- **THEN** a tabbed browser shows three tabs: Facts, Rules, Episodes
- **AND** when opened inside a butler detail page, all queries are scope-filtered to that butler

#### Scenario: Contact detail tabs
- **WHEN** `/contacts/:contactId` is visited
- **THEN** a tabbed view shows five tabs: Notes, Interactions, Gifts, Loans, Activity
- **AND** each tab loads its data lazily on first selection

#### Scenario: Approvals navigation integration
- **WHEN** the approvals section is accessed from the sidebar
- **THEN** two routes are available: `/approvals` (pending action queue with filters, metrics dashboard, and decision workflows) and `/approvals/rules` (standing rules list with detail, create, and revoke flows)
- **AND** the main approvals page provides: metrics dashboard with pending count and approval/rejection/auto-approval stats, filterable action queue by tool/status/butler, action detail dialog with approve/reject/rule creation, and stale action expiry management
- **AND** the rules page provides: filterable rules list by tool/active status/butler, rule detail dialog with constraint inspection, rule revocation capability, and use count and limit tracking

### Requirement: Overview Tab
The overview tab surfaces butler identity, module health, cost telemetry, eligibility, and recent notifications in a single glanceable view.

#### Scenario: Identity card
- **WHEN** the overview tab loads
- **THEN** a card displays the butler name, status badge, description (if present in butler data), and port number

#### Scenario: Eligibility display and restore action
- **WHEN** the butler has a registry entry from the switchboard
- **THEN** the identity card includes an eligibility row showing `Active` (emerald badge), `Quarantined` (destructive badge, clickable), or `Stale` (amber badge, clickable)
- **AND** clicking a `Quarantined` or `Stale` badge triggers a `setEligibility(name, "active")` mutation to restore the butler
- **AND** when a quarantine reason exists, it is shown as muted text next to the badge

#### Scenario: Module health badges
- **WHEN** the butler reports active modules
- **THEN** a "Module Health" card renders one badge per module, colored by status: `connected`/`ok` (emerald), `degraded` (amber), `error` (destructive), other (secondary)
- **AND** if no modules are registered, the card shows "No modules registered"

#### Scenario: Cost telemetry card
- **WHEN** cost summary data is available for today
- **THEN** a "Cost Today" card shows the butler's USD cost, its percentage share of the global total, and the global total
- **AND** costs below $0.01 display as "$0.00"

#### Scenario: Recent notifications feed
- **WHEN** the overview tab loads
- **THEN** the five most recent notifications for this butler are displayed in a notification feed component
- **AND** a "View all" link navigates to `/notifications?butler={name}`

### Requirement: Sessions Tab
The sessions tab shows paginated session history for the butler with drill-down capability.

#### Scenario: Paginated session table
- **WHEN** the sessions tab is active
- **THEN** sessions are loaded with offset-based pagination (page size 20) and displayed in a session table
- **AND** the butler column is hidden since the context is already butler-scoped

#### Scenario: Session detail drawer
- **WHEN** the operator clicks a session row
- **THEN** a drawer opens showing full session details for the selected session
- **AND** the drawer can be closed to return to the table

#### Scenario: Pagination controls
- **WHEN** the total session count exceeds one page
- **THEN** "Previous" and "Next" buttons are shown with the current page number and total pages
- **AND** "Previous" is disabled on the first page and "Next" is disabled when `has_more` is false

### Requirement: Config Tab
The config tab provides full transparency into a butler's configuration files.

#### Scenario: butler.toml display with format toggle
- **WHEN** the config tab loads successfully
- **THEN** the `butler.toml` contents are shown in a card with a "Formatted" / "Raw" toggle button
- **AND** "Formatted" mode renders the TOML as a structured key-value tree, while "Raw" mode renders the JSON representation with 2-space indentation

#### Scenario: Markdown file sections
- **WHEN** the config response includes `claude_md`, `agents_md`, or `manifesto_md`
- **THEN** each is rendered in its own card with the filename as the title (CLAUDE.md, AGENTS.md, MANIFESTO.md)
- **AND** content is displayed in a monospace `<pre>` block, or "Not found" if the value is null

#### Scenario: Error and empty states
- **WHEN** the config API request fails
- **THEN** an error message is shown with the failure reason
- **AND** when the response has no config data, a "No configuration data available" message is displayed

### Requirement: Skills Tab
The skills tab shows all skills available to a butler with drill-down and trigger integration.

#### Scenario: Skill card grid
- **WHEN** skills are loaded
- **THEN** each skill is rendered as a card in a responsive grid (1/2/3 columns by breakpoint) showing the skill name, a "skill" badge, and the first non-heading, non-empty line of the SKILL.md content as a description (truncated to 120 characters)

#### Scenario: Skill detail dialog
- **WHEN** the operator clicks "View" on a skill card
- **THEN** a dialog opens showing the skill name as title and the full SKILL.md content in a scrollable monospace block

#### Scenario: Trigger integration
- **WHEN** the operator clicks "Trigger" on a skill card
- **THEN** the tab switches to the Trigger tab with the prompt pre-filled as "Use the {skill name} skill to "

### Requirement: Schedules Tab (CRUD)
The schedules tab provides full CRUD management of a butler's scheduled tasks.

#### Scenario: Schedule table columns
- **WHEN** schedules are loaded
- **THEN** a table displays: Name, Cron expression (monospace badge), Mode (prompt/job badge), Prompt/Job details (truncated to 80 chars), Enabled toggle (On/Off badge, clickable), Source, Next Run (relative time with absolute tooltip), Last Run (relative time with absolute tooltip), and Actions (Edit, Delete)

#### Scenario: Create schedule
- **WHEN** the operator clicks "Add Schedule"
- **THEN** a dialog opens with a form containing: Name (text input), Cron Expression (text input with standard 5-field hint), Mode selector (prompt or job), and mode-dependent fields
- **AND** in prompt mode: a Prompt textarea is shown
- **AND** in job mode: Job Name input and Job Args JSON textarea are shown
- **AND** the form validates that name and cron are non-empty, prompt is non-empty in prompt mode, and job name is non-empty with valid JSON args in job mode

#### Scenario: Edit schedule
- **WHEN** the operator clicks "Edit" on a schedule row
- **THEN** the same form dialog opens pre-filled with the schedule's existing values
- **AND** submission triggers an update mutation instead of create

#### Scenario: Delete schedule with confirmation
- **WHEN** the operator clicks "Delete" on a schedule row
- **THEN** a confirmation dialog appears with the schedule name and a warning that the action cannot be undone
- **AND** confirming the deletion triggers the delete mutation and shows a success toast

#### Scenario: Toggle schedule enabled state
- **WHEN** the operator clicks the enabled/disabled badge on a schedule row
- **THEN** the schedule's enabled state is toggled via mutation and a toast confirms the action

#### Scenario: Auto-refresh
- **WHEN** the schedules tab is mounted
- **THEN** schedule data is polled every 30 seconds

### Requirement: Trigger Tab (Manual Session Invocation)
The trigger tab allows operators to manually spawn a Claude Code session for a butler.

#### Scenario: Prompt input and submission
- **WHEN** the trigger tab is active
- **THEN** a card with a textarea and "Trigger Session" button is shown
- **AND** the button is disabled when the textarea is empty or a trigger is in flight

#### Scenario: Skill pre-fill from query parameter
- **WHEN** the URL contains a `skill` query parameter
- **THEN** the prompt textarea is pre-filled with "Use the {skill} skill to "

#### Scenario: Result display
- **WHEN** a trigger completes
- **THEN** a result card shows a Success (emerald) or Failed (destructive) badge
- **AND** successful results show the output in a monospace block with a link to the session
- **AND** failed results show the error message

#### Scenario: Ephemeral trigger history
- **WHEN** triggers have been issued during the current page session
- **THEN** a "Trigger History" card lists all previous triggers with their status badge, prompt text (truncated), timestamp, and session link
- **AND** this history is not persisted and resets on page reload

### Requirement: MCP Debug Tab
The MCP tab provides a debugging interface for directly invoking MCP tools on a butler.

#### Scenario: Tool enumeration
- **WHEN** the MCP tab loads
- **THEN** it fetches the butler's available MCP tools and displays the count (e.g., "12 tools available")
- **AND** a "Refresh Tools" button allows manual re-fetch

#### Scenario: Tool selection and description
- **WHEN** tools are loaded
- **THEN** a dropdown select lists all tool names alphabetically
- **AND** selecting a tool displays its description below the dropdown

#### Scenario: Tool invocation with JSON arguments
- **WHEN** the operator selects a tool and optionally enters a JSON arguments object
- **THEN** clicking "Call Tool" sends the invocation to the butler's MCP server
- **AND** the arguments textarea validates JSON format before submission, rejecting non-object values, arrays, and invalid syntax

#### Scenario: Response display
- **WHEN** a tool call completes
- **THEN** a "Last Response" card shows: OK/Tool Error badge, the tool name, arguments (collapsible JSON viewer), parsed result (collapsible JSON viewer), and raw text (monospace block, when present)

#### Scenario: Error handling
- **WHEN** the tool list fetch or tool call fails
- **THEN** the error message is displayed inline without crashing the tab

### Requirement: State Tab (CRUD)
The state tab provides a browser and editor for the butler's key-value state store.

#### Scenario: State browser table
- **WHEN** state entries are loaded
- **THEN** a table displays: Key (monospace), Value (compact JSON preview, click to expand/collapse to full pretty-printed JSON), Updated timestamp, and Actions (Edit, Delete)

#### Scenario: Key prefix filter
- **WHEN** the operator types in the filter input
- **THEN** only entries whose key starts with the filter text (case-insensitive) are shown
- **AND** when no entries match, a message distinguishes between "no entries exist" and "no entries match the filter"

#### Scenario: Set new value
- **WHEN** the operator clicks "Set Value"
- **THEN** a dialog opens with Key (text input) and Value (JSON textarea) fields
- **AND** the value must be valid JSON; parse errors are shown inline
- **AND** submitting triggers a state set mutation with a success toast

#### Scenario: Edit existing value
- **WHEN** the operator clicks "Edit" on a state row
- **THEN** a dialog opens pre-filled with the entry's key (disabled) and pretty-printed JSON value
- **AND** saving triggers a state set mutation

#### Scenario: Delete with confirmation
- **WHEN** the operator clicks "Delete" on a state row
- **THEN** a confirmation dialog shows the key name and warns the action is irreversible
- **AND** confirming triggers a state delete mutation with a success toast

#### Scenario: Auto-refresh
- **WHEN** the state tab is mounted
- **THEN** state entries are polled every 30 seconds

### Requirement: Memory Tab
The memory tab shows the three-tier memory system health and a browsable memory store.

#### Scenario: Memory tier summary cards
- **WHEN** the memory tab loads
- **THEN** three cards are displayed in a row, one per memory tier:
  - **Episodes (Eden):** total, unconsolidated, consolidated counts with a health badge (healthy >= 80% consolidated, warning >= 50%, needs attention < 50%)
  - **Facts (Mid-term):** total, active, fading counts with a health badge (healthy >= 80% active)
  - **Rules (Long-term):** total, candidate, established, proven, anti-pattern counts with a health badge (healthy >= 80% established+proven)

#### Scenario: Memory browser
- **WHEN** the tier cards load
- **THEN** a tabbed memory browser appears below, scoped to the current butler, allowing navigation between episodes, facts, and rules with pagination and search

### Requirement: CRM Tab (Butler-Specific)
The CRM tab shows relationship management features scoped to the relationship butler.

#### Scenario: Relationship butler context
- **WHEN** the CRM tab is viewed for the `relationship` butler
- **THEN** an "Upcoming Dates" card shows birthdays, anniversaries, and other important dates in the next 30 days
- **AND** each entry shows the date type badge, contact name (linked to contact detail), date, and a days-until badge (destructive styling when <= 3 days, "Today" / "Tomorrow" labels)
- **AND** a "Quick Links" card provides navigation to `/contacts` and `/groups`

#### Scenario: Non-relationship butler
- **WHEN** the CRM tab is viewed for any butler other than `relationship`
- **THEN** a centered message states "CRM features are only available for the relationship butler."

### Requirement: Health Tab (Butler-Specific)
The health tab shows navigation to health management sub-pages, available only for the health butler.

#### Scenario: Health butler context
- **WHEN** the Health tab is viewed for the `health` butler
- **THEN** a card grid shows links to six health sub-pages: Measurements, Medications, Conditions, Symptoms, Meals, and Research
- **AND** each card has a title, description, and a "View" link button

#### Scenario: Non-health butler
- **WHEN** the Health tab is viewed for any butler other than `health`
- **THEN** a centered message states "Health features are only available for the health butler."

### Requirement: Switchboard Registry Tab
The registry tab (switchboard-only) shows the authoritative butler registry with liveness information.

#### Scenario: Registry table columns
- **WHEN** the registry tab loads on the switchboard butler
- **THEN** a table displays: Name, Endpoint URL (monospace), Modules (badge per module, parsed from comma-separated strings, JSON arrays, or nested string arrays), Description (truncated), and Last Seen (relative time via `formatDistanceToNow`)

#### Scenario: Module normalization
- **WHEN** the registry data contains modules in various formats (comma-separated string, JSON array string, nested arrays)
- **THEN** modules are normalized to a flat list of badge-rendered module names with a recursion depth limit of 10

#### Scenario: Empty registry
- **WHEN** no butlers are registered in the switchboard
- **THEN** a centered empty state message is shown

### Requirement: Switchboard Routing Log Tab
The routing log tab (switchboard-only) shows inter-butler request routing activity.

#### Scenario: Routing log table columns
- **WHEN** the routing log tab loads
- **THEN** a table displays: Timestamp (formatted as "MMM d, HH:mm:ss"), Source butler, Target butler, Tool name (monospace), Status (OK/Failed badge), Duration in milliseconds, and Error message (truncated, destructive text)

#### Scenario: Source and target filters
- **WHEN** the operator enters text in the "Source butler" or "Target butler" filter inputs
- **THEN** the query is filtered server-side by those values
- **AND** a "Clear filters" button appears when any filter is active

#### Scenario: Pagination
- **WHEN** the routing log has more entries than one page (25 per page)
- **THEN** Previous/Next pagination controls are shown with page count

### Requirement: Switchboard Triage Filters
The filters surface (accessible from the ingestion page) manages pre-classification triage rules, thread affinity settings, and Gmail label filters.

#### Scenario: Rules table with CRUD
- **WHEN** the filters surface loads
- **THEN** a rules table shows each triage rule with: Priority, Rule Type badge (sender_domain, sender_address, header_condition, mime_type), Condition summary, Action, Match Count, Enabled toggle, and action buttons (Edit, Test, Delete)

#### Scenario: Rule editor drawer
- **WHEN** the operator creates or edits a rule
- **THEN** a sheet/drawer opens with type-specific condition fields:
  - `sender_domain`: domain input, match mode (suffix/exact)
  - `sender_address`: address input, match mode
  - `header_condition`: header name, operation (present/equals/contains), value
  - `mime_type`: MIME type input
- **AND** action selection allows static actions (skip, metadata_only, low_priority_queue, pass_through) or `route_to:{butler}` with butler selection from a predefined list

#### Scenario: Test rule dry-run
- **WHEN** the operator clicks "Test" on a rule
- **THEN** a test form appears allowing a sample envelope to be entered
- **AND** submitting performs a dry-run and shows pass/fail result with matched rule details

#### Scenario: Thread affinity panel
- **WHEN** the thread affinity settings are loaded
- **THEN** a panel shows: global enable/disable toggle, TTL input in hours, and a save button
- **AND** toggling and saving trigger the `updateThreadAffinitySettings` mutation

#### Scenario: Import seed rules
- **WHEN** the operator clicks "Import defaults"
- **THEN** a preview dialog shows the seed rules that will be created
- **AND** confirming triggers batch rule creation from the predefined seed rule set

### Requirement: Switchboard Backfill Management
The backfill surface manages historical replay jobs across connectors.

#### Scenario: Backfill job list with live polling
- **WHEN** the backfill history tab loads
- **THEN** a paginated table shows all backfill jobs with: ID (truncated), Connector type, Endpoint identity, Status badge (with spinner for active/pending), Rows processed, Cost/Cap display, Created (relative time), and lifecycle action buttons

#### Scenario: Job status state machine
- **WHEN** a backfill job is displayed
- **THEN** action buttons are gated by the job's current status:
  - **Pause:** available when `pending` or `active` and connector is online
  - **Resume:** available when `paused` and connector is online
  - **Cancel:** available when `pending`, `active`, `paused`, `cost_capped`, or `error`
- **AND** all action buttons are disabled when any mutation is in flight

#### Scenario: Expandable job detail row
- **WHEN** the operator clicks a job row
- **THEN** an expanded detail section shows: date range, rate limit, rows skipped, target categories, start/completion timestamps, error details, and connector offline warnings

#### Scenario: Create backfill job dialog
- **WHEN** the operator clicks "New Backfill Job"
- **THEN** a dialog opens with: connector selector (only online connectors listed), date range (from/to date inputs), rate limit per hour (numeric, default 100), daily cost cap in dollars (numeric, default $5.00), and optional target categories (comma-separated)
- **AND** when no connectors are online, manual connector type and endpoint identity inputs are shown as fallback

#### Scenario: Active job progress polling
- **WHEN** a backfill job has status `pending` or `active`
- **THEN** its progress is polled every 5 seconds for live row count and cost updates
- **AND** inactive jobs are polled every 30 seconds

#### Scenario: Cost cap enforcement display
- **WHEN** a job reaches its daily cost cap
- **THEN** the status badge shows "cost capped" (destructive variant)
- **AND** the cost/cap display shows both the spent amount and the cap limit

### Requirement: Data Fetching Architecture
All butler management surfaces use TanStack Query for data fetching with consistent patterns.

#### Scenario: Query key hierarchy
- **WHEN** butler-scoped data is fetched
- **THEN** query keys follow the pattern `["butlers", butlerName, resource]` for cache isolation and targeted invalidation

#### Scenario: Mutation invalidation
- **WHEN** a write mutation succeeds (create, update, delete, toggle)
- **THEN** the relevant query key family is invalidated to trigger a re-fetch
- **AND** toast notifications confirm success or surface error messages

#### Scenario: Optimistic polling intervals
- **WHEN** list-type queries are mounted
- **THEN** they use a 30-second `refetchInterval` by default
- **AND** backfill job progress uses an accelerated 5-second interval for active jobs

#### Scenario: Conditional query enabling
- **WHEN** a query depends on a butler name parameter
- **THEN** the query is disabled (`enabled: false`) when the butler name is empty or undefined

### Requirement: Loading and Error State Consistency
All butler management tabs follow consistent loading and error patterns.

#### Scenario: Skeleton loading states
- **WHEN** any tab's data is loading
- **THEN** purpose-specific skeleton layouts are shown (card skeletons for overview, table row skeletons for lists, content block skeletons for config)
- **AND** skeleton shapes approximate the final content layout

#### Scenario: Error display pattern
- **WHEN** a tab's data fetch fails
- **THEN** the error is shown inline within a card using destructive text styling
- **AND** the error message includes the exception message when available, falling back to "Unknown error"

#### Scenario: Empty state messaging
- **WHEN** a data set is empty (no schedules, no skills, no state entries)
- **THEN** a centered, muted message describes the empty condition and, where applicable, guides the operator toward the creation action
