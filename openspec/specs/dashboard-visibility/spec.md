# Dashboard Visibility and Traceability

## Purpose
Specifies the operator-facing dashboard surfaces that provide end-to-end visibility into the Butlers system: session history, distributed trace exploration, unified timeline, notification audit trail, audit log, issue detection, and topology visualization. Together these surfaces answer the operator's core questions: "What is every butler doing right now?", "What happened to this specific request?", and "Is the system healthy?" Every requirement below is grounded in the implemented frontend code and its backend data contracts.

## ADDED Requirements

### Requirement: Cross-Butler Session Explorer
The Sessions page (`/sessions`) provides a paginated, filterable table of session records aggregated across all butlers. This is the primary surface for answering "what work has the system done?" and is the entry point for drill-down into any individual execution.

#### Scenario: Default session listing
- **WHEN** an operator navigates to `/sessions` with no active filters
- **THEN** the page displays a paginated table of sessions ordered by `started_at` descending (most recent first)
- **AND** the table shows columns: Time, Butler, Trigger, Request ID, Prompt, Duration, Status, Tokens (in/out)
- **AND** page size is 20 rows per page
- **AND** the `showButlerColumn` flag is `true` (cross-butler view)

#### Scenario: Session filter bar
- **WHEN** the operator interacts with the filter bar
- **THEN** six filter controls are available: Butler (dropdown populated from `/api/butlers`), Trigger Source (free-text), Request ID (free-text, monospace), Status (dropdown: All / Success / Failed), From date, To date
- **AND** changing any filter resets pagination to page 0
- **AND** a "Clear filters" button appears when any filter departs from its default

#### Scenario: Butler dropdown populated dynamically
- **WHEN** the Sessions page loads
- **THEN** the Butler filter dropdown is populated by calling `useButlers()` and extracting `name` from each butler summary
- **AND** the default value is "All" (no butler filter applied)

#### Scenario: Request ID click-to-filter
- **WHEN** a request ID cell in the session table is clicked
- **THEN** the `request_id` filter is populated with the clicked value (via `onRequestIdClick` callback)
- **AND** the click event does not propagate to the row click handler (uses `e.stopPropagation()`)

#### Scenario: Session row click opens detail drawer
- **WHEN** the operator clicks a session row
- **THEN** a `SessionDetailDrawer` (right-side sheet) opens for the clicked session
- **AND** the drawer receives the session's `id` and `butler` name

### Requirement: Session Table Visual Treatment
The session table (`SessionTable`) applies visual affordances to communicate status at a glance without requiring the operator to read every cell.

#### Scenario: Failed session row highlighting
- **WHEN** a session has `success === false`
- **THEN** its table row receives the `bg-destructive/5` background class (subtle red tint)

#### Scenario: Status badge variants
- **WHEN** `success === true`
- **THEN** a green "Success" badge is rendered (`bg-emerald-600`)
- **WHEN** `success === false`
- **THEN** a red "Failed" badge is rendered (destructive variant)
- **WHEN** `success === null` (session in progress)
- **THEN** an outlined "Running" badge is rendered

#### Scenario: Butler badge color determinism
- **WHEN** a butler name is displayed in the Butler column
- **THEN** a color-coded badge is rendered using a deterministic hash of the butler name mod 8 colors (blue, violet, amber, teal, rose, indigo, cyan, orange)
- **AND** the same butler always gets the same color across all pages and sessions

#### Scenario: Timestamp display
- **WHEN** a session's `started_at` is rendered in the table
- **THEN** it shows as a relative string (e.g. "2h ago") with the absolute timestamp (e.g. "Feb 10, 2:30 PM") in the HTML `title` attribute

#### Scenario: Token compact formatting
- **WHEN** token counts are displayed in the table
- **THEN** values >= 1,000,000 render as `X.XM`, values >= 1,000 render as `X.XK`, smaller values render as plain integers, and null values render as an em-dash

#### Scenario: Prompt truncation
- **WHEN** the session prompt text exceeds 60 characters
- **THEN** it is truncated to 60 characters with a trailing ellipsis, and the full prompt is available in the `title` attribute

### Requirement: Session Detail Drawer
The `SessionDetailDrawer` is a slide-over sheet that provides full session context without leaving the sessions list. It is the operator's primary tool for understanding what happened in a single execution.

#### Scenario: Metadata section
- **WHEN** the drawer opens for a session
- **THEN** a Metadata section displays: Butler (name), Trigger (source), Started (absolute timestamp), Completed (absolute timestamp), Duration (human-formatted), Model (if present), and Parent Session ID (if present, displayed as a monospace string)

#### Scenario: Tool call timeline
- **WHEN** the session has tool calls recorded
- **THEN** a "Tool Calls (N)" section renders a vertical timeline (left-bordered ordered list) with one entry per tool call
- **AND** each entry shows: tool name (extracted via multi-strategy name detection from `name`, `tool`, `tool_name`, `toolName`, or nested `function.name` / `call.name`), outcome indicator (colored dot: green for success, red for failed, amber for pending, gray for unknown), and collapsible JSON blocks for Arguments, Result, and Error

#### Scenario: Tool call outcome inference
- **WHEN** a tool call record does not have an explicit `success` boolean
- **THEN** the outcome is inferred by inspecting: `error` field presence (implies failed), `is_error` / `isError` booleans, `success` / `ok` booleans, `exit_code` / `exitCode` (0 = success, non-zero = failed), and `status` / `state` / `outcome` strings matched against known status word sets (e.g. "completed" -> success, "timed_out" -> failed, "processing" -> pending)
- **AND** the inference checks the top-level record, nested containers (`function`, `call`, `tool_call`, `toolCall`), and result sub-objects

#### Scenario: Tool name fallback from result text
- **WHEN** a tool call has no extractable name from its JSON structure
- **THEN** tool names are extracted from the session result text by matching patterns like `` `tool_name(`` and ``- `tool_name`:`` and assigned in order to unnamed tool calls

#### Scenario: Prompt and result display
- **WHEN** the drawer shows session content
- **THEN** the Prompt section renders the full prompt in a monospace preformatted block (max-height 48 with scroll)
- **AND** the Result section (if present) renders the full result text similarly
- **AND** the Error section (if present) renders with destructive styling (red border, red text)

#### Scenario: Token usage breakdown
- **WHEN** the drawer shows token information
- **THEN** a "Token Usage" section displays Input Tokens, Output Tokens, and Total (sum of both) in a bordered metadata grid with locale-formatted numbers

#### Scenario: Cost breakdown
- **WHEN** the session has a non-empty `cost` JSONB object
- **THEN** a "Cost" section renders it as a collapsible JSON block labeled "Cost breakdown"

#### Scenario: Trace ID link
- **WHEN** the session has a `trace_id`
- **THEN** the drawer displays the trace ID as a clickable link navigating to `/traces/{trace_id}`
- **AND** a copy-to-clipboard button is adjacent to the link (using `navigator.clipboard.writeText`)

#### Scenario: Copyable text feedback
- **WHEN** the operator clicks the copy button next to a trace ID
- **THEN** a check icon replaces the copy icon for 2 seconds before reverting

### Requirement: Session Detail Full Page
The `SessionDetailPage` (`/sessions/:id?butler=<name>`) provides a full-page view of a single session. It serves as the deep-link target for session references from other surfaces (notifications, traces).

#### Scenario: Butler-scoped vs global session fetch
- **WHEN** the URL contains a `?butler=<name>` query parameter
- **THEN** the butler-scoped endpoint (`getButlerSession(butler, id)`) is used
- **WHEN** no `butler` query parameter is present
- **THEN** the global endpoint (`getSession(id)`) is used as a fallback

#### Scenario: Breadcrumb navigation
- **WHEN** the session detail page loads
- **THEN** a breadcrumb trail shows: Sessions (link to `/sessions`) > `{id.slice(0, 8)}` (current page)

#### Scenario: Full metadata display
- **WHEN** the page renders a session
- **THEN** it shows: Butler (link to `/butlers/{butler}`), Trigger Source (badge), Started, Completed, Duration, Model (if present), Tool Calls count (if present, showing array length or string representation), and Tokens in/out (if present)

#### Scenario: Error display
- **WHEN** the session has an `error` field
- **THEN** an "Error" card renders with `text-destructive` title and the error in a preformatted block with `bg-destructive/10` background

### Requirement: End-to-End Request Traceability via Distributed Traces
The Traces pages (`/traces` and `/traces/:traceId`) provide the distributed tracing view that lets operators follow a request from initial ingestion through switchboard routing, butler execution, sub-session delegation, tool calls, and final delivery. Traces are the mechanism for correlating work that spans multiple butlers.

#### Scenario: Trace list page
- **WHEN** an operator navigates to `/traces`
- **THEN** a paginated table displays traces ordered by `started_at` descending with columns: Trace ID (monospace, truncated to 12 chars), Root Butler (color-coded badge), Spans (count), Status (badge), Duration, Started (relative time)
- **AND** page size is 20 rows per page

#### Scenario: Trace status badges
- **WHEN** a trace has status "success"
- **THEN** a green badge is shown
- **WHEN** status is "failed"
- **THEN** a red destructive badge is shown
- **WHEN** status is "running"
- **THEN** a blue outlined badge is shown
- **WHEN** status is "partial"
- **THEN** an amber outlined badge is shown (indicating some spans succeeded and some failed or are still running)

#### Scenario: Trace row click navigates to detail
- **WHEN** the operator clicks a trace row
- **THEN** navigation occurs to `/traces/{trace_id}` using `useNavigate`

#### Scenario: Failed trace row highlighting
- **WHEN** a trace has `status === "failed"`
- **THEN** its table row receives the `bg-destructive/5` background class

### Requirement: Trace Detail and Span Waterfall
The `TraceDetailPage` (`/traces/:traceId`) is the core traceability surface. It shows a trace's metadata and renders a waterfall visualization of all spans (sessions) in the trace, revealing the parent-child execution tree across butler boundaries.

#### Scenario: Trace metadata card
- **WHEN** the trace detail page loads
- **THEN** a Metadata card displays: Trace ID (monospace), Root Butler (link to `/butlers/{root_butler}`), Span Count, Status (badge), Total Duration (human-formatted), and Started (absolute timestamp)

#### Scenario: Breadcrumb navigation
- **WHEN** the trace detail page loads
- **THEN** a breadcrumb trail shows: Traces (link to `/traces`) > `{traceId.slice(0, 8)}` (current page)

### Requirement: Trace Waterfall Visualization
The `TraceWaterfall` component renders a timeline-based waterfall diagram of spans within a trace. Each span maps to a session execution (potentially on a different butler), and child spans are nested to show the delegation tree.

#### Scenario: Waterfall layout structure
- **WHEN** the waterfall renders a trace with N spans
- **THEN** it displays a header row with "Span", "Timeline", and "Duration" columns
- **AND** each span row contains: a label section (butler badge + truncated prompt), a proportional timeline bar, and a duration label

#### Scenario: Span bar positioning and scaling
- **WHEN** a span row is rendered
- **THEN** the bar's left offset is calculated as `(spanStartMs - traceStartMs) / totalDurationMs * 100` percent
- **AND** the bar's width is calculated as `max(1%, spanDurationMs / totalDurationMs * 100)` percent (minimum 1% for visibility)
- **AND** the trace start time is derived from the earliest `started_at` across all root spans

#### Scenario: Span nesting via indentation
- **WHEN** a span has children (sub-sessions delegated to other butlers)
- **THEN** child span rows are indented by `depth * 24px` from the left
- **AND** children are rendered recursively using the `SpanRow` component

#### Scenario: Span bar color coding
- **WHEN** a span has `success === true`
- **THEN** the bar is `bg-emerald-500` (green)
- **WHEN** `success === false`
- **THEN** the bar is `bg-red-500` (red)
- **WHEN** `success === null` (still running)
- **THEN** the bar is `bg-blue-500` (blue)

#### Scenario: Span expandable detail panel
- **WHEN** the operator clicks a span row
- **THEN** an inline detail panel expands below the row, indented to match the span depth + 32px
- **AND** the panel shows: Session ID (monospace), Butler, Status (badge), Trigger source, Duration, Model (if present), Tokens in/out (if present), and full Prompt text

#### Scenario: Cross-butler delegation visibility
- **WHEN** a trace includes spans from multiple butlers (e.g. switchboard delegates to a domain butler which sub-delegates)
- **THEN** each span row shows its butler's color-coded badge
- **AND** the nesting visually reveals the delegation chain (switchboard -> domain butler -> sub-butler)
- **AND** the operator can see the full execution tree in a single waterfall view

#### Scenario: Keyboard accessibility
- **WHEN** a span row has focus
- **THEN** pressing Enter or Space toggles the expanded state (via `onKeyDown` handler)
- **AND** the row has `tabIndex={0}` and `role="button"` for accessibility

### Requirement: Unified Timeline
The Timeline page (`/timeline`) merges events from all butlers into a single reverse-chronological stream. It answers "what has been happening across the entire system?" and is the primary surface for detecting anomalous patterns spanning multiple butlers.

#### Scenario: Timeline event stream
- **WHEN** the operator navigates to `/timeline`
- **THEN** a vertical timeline renders events in reverse chronological order
- **AND** each event row shows: timestamp (absolute, MMM d h:mm:ss a), butler (outlined badge), event type (colored badge), and summary (truncated text)
- **AND** the initial page loads up to 50 events

#### Scenario: Event type variants
- **WHEN** an event has `type === "session"`
- **THEN** a blue "session" badge is rendered
- **WHEN** `type === "error"`
- **THEN** a red destructive "error" badge is rendered
- **WHEN** `type === "notification"`
- **THEN** a purple "notification" badge is rendered
- **WHEN** `type` is any other value
- **THEN** an outlined badge with the raw type string is rendered

#### Scenario: Butler and event type filters
- **WHEN** the operator interacts with the filter panel
- **THEN** two filter sections are available: "Filter by butler" (toggle badges for each butler name, multi-select) and "Filter by event type" (toggle badges for Session / Notification / Error, multi-select)
- **AND** toggling any filter resets cursor pagination and accumulated events
- **AND** selected filters are visually distinguished with `bg-primary text-primary-foreground`

#### Scenario: Heartbeat event collapsing
- **WHEN** consecutive heartbeat events (identified by "heartbeat" or "tick" in the summary or `trigger_source`) occur within 10 minutes of each other
- **THEN** they are collapsed into a single "Heartbeat: N butlers ticked" row with a dashed border badge
- **AND** if 3 or fewer unique butlers are involved, their names are shown inline
- **AND** clicking the collapsed row expands to show individual heartbeat events with their timestamps and butler names

#### Scenario: Event data expansion
- **WHEN** the operator clicks any non-heartbeat event row
- **THEN** a JSON detail block expands below the row showing `event.data` formatted with 2-space indentation
- **AND** the block has a max-height of 48 with vertical scroll overflow

#### Scenario: Cursor-based pagination (Load More)
- **WHEN** more events exist beyond the current page
- **THEN** a "Load More" button appears below the timeline
- **AND** clicking it appends the next page of events to the existing list (using cursor-based pagination via `response.meta.cursor`)
- **AND** previously loaded events are preserved in state

#### Scenario: Auto-refresh control
- **WHEN** the Timeline page loads
- **THEN** an `AutoRefreshToggle` is displayed in the page header
- **AND** it defaults to enabled with a 10-second interval
- **AND** the toggle shows a green "Live" badge when auto-refresh is active
- **AND** the interval can be changed to 5s, 10s, 30s, or 60s via a dropdown

### Requirement: Notification Audit Trail
The Notifications page (`/notifications`) provides a complete audit trail of every notification sent by any butler across all delivery channels. This surface is essential for verifying that user-facing communications were delivered successfully and diagnosing delivery failures.

#### Scenario: Notification stats bar
- **WHEN** the Notifications page loads
- **THEN** a four-card stats bar displays: Total Notifications (with bell icon), Sent count (green, with checkmark icon), Failed count (red, with X icon), and Failure Rate percentage (color-coded: green if 0%, amber if 0-10%, red if >10%)
- **AND** below the cards, a per-channel breakdown shows each channel name with its count as a badge

#### Scenario: Notification filter bar
- **WHEN** the operator interacts with the notification filter bar
- **THEN** five filter controls are available: Butler (free-text input), Channel (dropdown: All / Telegram / Email), Status (dropdown: All / Sent / Failed / Pending), Since (date input), Until (date input)
- **AND** a "Clear filters" button appears when any filter is active

#### Scenario: Notification feed table
- **WHEN** notifications are loaded
- **THEN** a table displays columns: Status (badge), Butler (source butler name), Channel (capitalized badge), Message (truncated to 60 chars), and Time (relative)
- **AND** failed notification rows receive `bg-destructive/5` background

#### Scenario: Notification-to-session and trace cross-links
- **WHEN** a notification has a `session_id`
- **THEN** a "Session {shortId}" link is displayed below the message, navigating to `/sessions/{session_id}?butler={source_butler}`
- **WHEN** a notification has a `trace_id`
- **THEN** a "Trace {shortId}" link is displayed below the message, navigating to `/traces/{trace_id}`
- **AND** both links are styled as primary-colored underlined text

#### Scenario: Notification status badges
- **WHEN** status is "sent"
- **THEN** a green "Sent" badge is rendered
- **WHEN** status is "failed"
- **THEN** a red destructive "Failed" badge is rendered
- **WHEN** status is "pending"
- **THEN** an amber outlined "Pending" badge is rendered

#### Scenario: Empty state with filter hint
- **WHEN** no notifications match the current filters
- **THEN** the empty state message reads "No notifications match the current filters. Try clearing the filters to see all notifications."
- **WHEN** no notifications exist at all (no filters active)
- **THEN** the empty state reads "Notifications will appear here as butlers send messages via Telegram, email, and other channels."

### Requirement: Audit Log
The Audit Log page (`/audit`) provides a tamper-evident record of every operation performed by every butler. It captures triggers, ticks, session lifecycle events, schedule mutations, and state mutations -- the authoritative record of "who did what, when, and what happened."

#### Scenario: Audit log filter bar
- **WHEN** the operator interacts with the audit log filter bar
- **THEN** four filter controls are available: Butler (dropdown populated from `/api/butlers`), Operation (dropdown with values: All, trigger, tick, session, schedule.create, schedule.update, schedule.delete, schedule.toggle, state.set, state.delete), From (date input), To (date input)

#### Scenario: Audit log table columns
- **WHEN** audit entries are displayed
- **THEN** the table shows columns: Time (relative), Butler (outlined badge), Operation (monospace code block), Result (badge: "default" variant for success, "destructive" for error), and Request Summary (truncated JSON)

#### Scenario: Expandable audit entry detail
- **WHEN** the operator clicks an audit entry row
- **THEN** an expanded detail row appears below showing: Request (full JSON, 2-space indented), User Context (full JSON), and Error (if result is "error", displayed with destructive styling)
- **AND** clicking the same row again collapses the detail
- **AND** only one entry can be expanded at a time

### Requirement: Issue Detection and Surfacing
The Issues page (`/issues`) and `IssuesPanel` component provide automated detection and grouping of errors and warnings across all butlers. Issues are the system's way of proactively alerting operators to problems that need attention.

#### Scenario: Issues page layout
- **WHEN** the operator navigates to `/issues`
- **THEN** the page header reads "Issues" with subtitle "Grouped errors and warnings across all butlers, newest first."
- **AND** the `IssuesPanel` renders below, showing all issues from `getIssues()`

#### Scenario: Issue card structure
- **WHEN** issues are displayed
- **THEN** each issue renders as a bordered card showing: severity badge (destructive variant for "critical", secondary for other severities), butler name (or "N butlers" if multiple butlers are affected), description text, occurrence count with first-seen and last-seen timestamps (both relative and absolute), and optional "View" link (if `issue.link` is set) and "Dismiss" button

#### Scenario: Multi-butler issue grouping
- **WHEN** an issue has a `butlers` array with more than one entry
- **THEN** the display shows "N butlers" (where N is the array length) instead of a single butler name
- **AND** this indicates the issue affects multiple butlers and is likely systemic

#### Scenario: Issue dismissal persistence
- **WHEN** the operator clicks "Dismiss" on an issue
- **THEN** the issue is removed from the visible list
- **AND** the dismissal is persisted to `localStorage` under the key `butlers-dismissed-issues`
- **AND** the issue key for dismissal is computed as `{issue.type}:{issue.error_message ?? issue.description}`
- **AND** dismissed issues remain hidden across page refreshes until localStorage is cleared

#### Scenario: Issue link navigation
- **WHEN** an issue has a non-null `link` field
- **THEN** a "View" button renders as a client-side link (using react-router `Link`)
- **AND** clicking navigates to the linked resource (typically a filtered session or notification view)

#### Scenario: Auto-refresh for issue detection
- **WHEN** the issues hook polls the backend
- **THEN** it uses a 30-second `refetchInterval` to detect new issues without manual refresh

### Requirement: System Topology Visualization
The `TopologyGraph` component renders a force-directed graph of butler nodes and their interconnections, providing at-a-glance system architecture visibility and health status.

#### Scenario: Butler node layout
- **WHEN** the topology graph renders
- **THEN** the Switchboard butler is positioned at the center (300, 200) with a large rounded node (16px/24px padding, bold text, 140px width)
- **AND** the Heartbeat butler is positioned top-right (550, 50) as a dashed-border circle (90x90px)
- **AND** all other butlers are arranged in a circle of radius 200px around the Switchboard

#### Scenario: Health status coloring
- **WHEN** a butler has status "ok" or "online"
- **THEN** its node or border is green (`#22c55e`)
- **WHEN** status is "down" or "offline"
- **THEN** its color is red (`#ef4444`)
- **WHEN** status is "degraded"
- **THEN** its color is yellow (`#eab308`)
- **AND** the Switchboard node's background is the status color; other butlers have dark backgrounds with colored borders

#### Scenario: Edge visualization
- **WHEN** the Switchboard butler is present
- **THEN** solid edges connect Switchboard to each domain butler
- **AND** edges to healthy butlers (`status === "ok" || "online"`) are animated (indicating active communication)
- **WHEN** the Heartbeat butler is present
- **THEN** dashed edges connect Heartbeat to every other butler (including Switchboard), representing health monitoring connections

#### Scenario: Node click navigation
- **WHEN** the operator clicks a butler node in the topology graph
- **THEN** navigation occurs to `/butlers/{node.id}` for the butler's detail page

#### Scenario: Interactive graph features
- **WHEN** the topology graph is rendered
- **THEN** nodes are draggable (`nodesDraggable={true}`)
- **AND** nodes are not connectable (`nodesConnectable={false}`)
- **AND** the graph auto-fits to the viewport (`fitView`)
- **AND** a subtle background grid pattern is rendered

### Requirement: Overview Dashboard
The `DashboardPage` (`/`) is the operator's landing page, aggregating the most critical visibility signals from all other surfaces into a single view.

#### Scenario: Aggregate stats bar
- **WHEN** the dashboard loads
- **THEN** four stat cards display: Total Butlers (count from `/api/butlers`), Healthy (count of butlers with `status === "ok"`, with percentage), Sessions Today (count from sessions API filtered to today, with 60-second auto-refresh), and Est. Cost Today (USD from cost summary API)

#### Scenario: Topology graph inclusion
- **WHEN** the dashboard loads
- **THEN** a full-width `TopologyGraph` component is rendered showing all butlers and their health status

#### Scenario: Failed notifications panel
- **WHEN** the dashboard loads
- **THEN** a "Failed Notifications" card shows the 5 most recent failed notifications
- **AND** a badge shows the total count of failed notifications
- **AND** a "View all notifications" link navigates to `/notifications`
- **AND** if no failed notifications exist, a success message reads "No failed notifications. All systems healthy."

#### Scenario: Issues panel inclusion
- **WHEN** the dashboard loads
- **THEN** the `IssuesPanel` component is rendered alongside the failed notifications card in a 2-column grid
- **AND** it shows all current issues with dismiss capability

### Requirement: Real-Time Polling and Auto-Refresh
All visibility surfaces use TanStack Query (React Query) for data fetching with configurable polling intervals to provide near-real-time updates without WebSocket infrastructure.

#### Scenario: Default polling intervals per surface
- **WHEN** the Sessions page is active
- **THEN** sessions list data refetches every 30 seconds by default (overridable by auto-refresh control)
- **WHEN** the Timeline page is active
- **THEN** timeline data refetches at the user-selected interval (default 10 seconds)
- **WHEN** the Traces list is active
- **THEN** trace data refetches every 30 seconds
- **WHEN** the Audit Log is active
- **THEN** audit entries refetch every 30 seconds
- **WHEN** the Issues page is active
- **THEN** issues refetch every 30 seconds

#### Scenario: Auto-refresh toggle (Sessions, Timeline)
- **WHEN** the operator uses the `AutoRefreshToggle` on the Sessions or Timeline page
- **THEN** a "Live" badge (green) indicates active auto-refresh
- **AND** the operator can select an interval from a dropdown (5s, 10s, 30s, 60s)
- **AND** clicking "Pause" sets `refetchInterval` to `false`, stopping polling
- **AND** clicking "Resume" restores polling at the selected interval
- **AND** the enabled state and interval are persisted to `localStorage` across sessions

#### Scenario: Dashboard overview refresh
- **WHEN** the dashboard is active
- **THEN** the "Sessions Today" stat card refreshes every 60 seconds
- **AND** butler list, cost summary, issues, and failed notifications use their respective default intervals

### Requirement: Pagination Consistency
All paginated surfaces follow the same offset-based pagination pattern using backend `PaginationMeta` responses.

#### Scenario: Offset-based pagination contract
- **WHEN** any paginated surface (Sessions, Traces, Notifications, Audit Log) renders data
- **THEN** it sends `offset` and `limit` parameters derived from `page * PAGE_SIZE`
- **AND** the response `meta` object contains `total`, `offset`, `limit`, and `has_more`
- **AND** Previous/Next buttons are disabled at the start/end of the result set
- **AND** a "Page X of Y" indicator shows current position

#### Scenario: Timeline cursor pagination
- **WHEN** the Timeline page loads more events
- **THEN** it uses cursor-based pagination (sending `before` parameter from `response.meta.cursor`)
- **AND** new events are appended to the accumulated event list rather than replacing them

### Requirement: Cross-Surface Navigation and Linking
The visibility surfaces are interconnected through contextual links that allow operators to trace a request across multiple views without manually searching.

#### Scenario: Session to trace navigation
- **WHEN** a session detail drawer displays a `trace_id`
- **THEN** it is a clickable link to `/traces/{trace_id}`

#### Scenario: Notification to session navigation
- **WHEN** a notification row has a `session_id`
- **THEN** a "Session {shortId}" link navigates to `/sessions/{session_id}?butler={source_butler}`

#### Scenario: Notification to trace navigation
- **WHEN** a notification row has a `trace_id`
- **THEN** a "Trace {shortId}" link navigates to `/traces/{trace_id}`

#### Scenario: Trace detail to butler navigation
- **WHEN** a trace detail page shows the root butler
- **THEN** the butler name is a link to `/butlers/{root_butler}`

#### Scenario: Session detail to butler navigation
- **WHEN** a session detail page shows the butler name
- **THEN** the butler name is a link to `/butlers/{butler}`

#### Scenario: Topology to butler navigation
- **WHEN** the operator clicks a node in the topology graph
- **THEN** navigation occurs to the butler's detail page

#### Scenario: Issue view link
- **WHEN** an issue has a `link` field
- **THEN** the "View" button navigates to the linked resource (e.g. a filtered sessions or notifications page)

#### Scenario: Dashboard to notification list navigation
- **WHEN** the operator clicks "View all notifications" on the dashboard
- **THEN** navigation occurs to `/notifications`

### Requirement: End-to-End Request Trace Story
The system supports tracing a request from initial ingestion through final delivery by correlating data across multiple surfaces. This requirement describes the complete traceability journey an operator follows.

#### Scenario: Message ingestion through delivery
- **GIVEN** a message arrives via an external connector (e.g. Telegram)
- **WHEN** the Switchboard butler receives the message, classifies it, and routes it to a domain butler
- **THEN** the following trace path is visible across dashboard surfaces:
  1. The **Timeline** shows a "session" event for the Switchboard butler's classification session
  2. The **Sessions** page shows the Switchboard session with `trigger_source="external"` and a `request_id` from the connector
  3. Filtering sessions by that `request_id` reveals all sessions in the request's lifecycle
  4. The **Traces** page shows a trace with the Switchboard as root butler
  5. The **Trace Detail** waterfall shows: Switchboard span (root) -> domain butler span (child), with each span showing its butler badge, duration bar, and status
  6. The session detail drawer for each span shows tool calls (e.g. route classification, state lookups)
  7. If the domain butler sends a notification, the **Notifications** page shows it with links back to both the session and the trace
  8. The **Audit Log** records each operation (trigger, session, etc.) with full request context

#### Scenario: Request ID as correlation key
- **WHEN** an operator has a request ID (e.g. from a user report or external system)
- **THEN** they can paste it into the Sessions page Request ID filter
- **AND** see all sessions involved in processing that request across all butlers
- **AND** from any session, navigate to the trace waterfall to see the full execution tree

### Requirement: Loading and Error States
All visibility surfaces handle loading and error states consistently to prevent operator confusion.

#### Scenario: Skeleton loading states
- **WHEN** data is loading for any table (Sessions, Traces, Notifications, Audit Log)
- **THEN** skeleton rows are displayed with animated placeholder bars matching the column layout
- **WHEN** data is loading for the Timeline
- **THEN** 8 skeleton rows with timestamp, badge, and text placeholders are shown
- **WHEN** data is loading for the Topology
- **THEN** a `h-96` animated pulse placeholder is shown

#### Scenario: Empty states
- **WHEN** no data matches the current view (after loading completes)
- **THEN** a centered empty state message is shown with a descriptive title and explanation
- **AND** the message varies by surface (e.g. "No sessions found" with "Sessions will appear here as butlers process triggers and scheduled tasks.")

#### Scenario: Error states
- **WHEN** the session detail API call fails
- **THEN** a destructive-styled error message is shown with a suggestion to add `?butler=name` if the butler parameter is missing
- **WHEN** the trace detail API call fails
- **THEN** a destructive-styled error message is shown with a "Back to traces" navigation button
- **WHEN** the notification feed fails to load
- **THEN** a destructive-styled message reads "Failed to load notifications. Please try refreshing the page."

### Requirement: Data Model Contracts for Visibility Surfaces
The frontend TypeScript interfaces define the data contracts that all visibility surfaces depend on. These contracts must be satisfied by the backend API.

#### Scenario: SessionSummary contract (list views)
- **WHEN** the sessions list API responds
- **THEN** each item conforms to: `id` (string), `butler` (optional string), `prompt` (string), `trigger_source` (string), `request_id` (optional string | null), `success` (boolean | null), `started_at` (ISO 8601 string), `completed_at` (string | null), `duration_ms` (number | null), `input_tokens` (number | null), `output_tokens` (number | null)

#### Scenario: SessionDetail contract (drill-down views)
- **WHEN** the session detail API responds
- **THEN** the item conforms to: all `SessionSummary` fields plus `result` (string | null), `tool_calls` (array of unknown), `trace_id` (string | null), `cost` (object | null), `error` (string | null), `model` (string | null), `parent_session_id` (string | null)

#### Scenario: TraceSummary contract
- **WHEN** the traces list API responds
- **THEN** each item conforms to: `trace_id` (string), `root_butler` (string), `span_count` (number), `total_duration_ms` (number | null), `started_at` (ISO 8601 string), `status` (string: "success" | "failed" | "running" | "partial")

#### Scenario: SpanNode contract (trace waterfall)
- **WHEN** the trace detail API responds
- **THEN** each span conforms to: `id` (string, session ID), `butler` (string), `prompt` (string), `trigger_source` (string), `success` (boolean | null), `started_at` (ISO 8601 string), `completed_at` (string | null), `duration_ms` (number | null), `model` (string | null), `input_tokens` (number | null), `output_tokens` (number | null), `parent_session_id` (string | null), `children` (recursive array of `SpanNode`)

#### Scenario: TimelineEvent contract
- **WHEN** the timeline API responds
- **THEN** each event conforms to: `id` (string), `type` (string), `butler` (string), `timestamp` (ISO 8601 string), `summary` (string), `data` (object)
- **AND** the response meta includes `cursor` (string | null) and `has_more` (boolean) for pagination

#### Scenario: NotificationSummary contract
- **WHEN** the notifications API responds
- **THEN** each item conforms to: `id` (string), `source_butler` (string), `channel` (string), `recipient` (string | null), `message` (string), `metadata` (object | null), `status` (string), `error` (string | null), `session_id` (string | null), `trace_id` (string | null), `created_at` (ISO 8601 string)

#### Scenario: NotificationStats contract
- **WHEN** the notification stats API responds
- **THEN** the data conforms to: `total` (number), `sent` (number), `failed` (number), `by_channel` (object mapping channel name to count), `by_butler` (object mapping butler name to count)

#### Scenario: AuditEntry contract
- **WHEN** the audit log API responds
- **THEN** each entry conforms to: `id` (string), `butler` (string), `operation` (string), `request_summary` (object), `result` (string: "success" | "error"), `error` (string | null), `user_context` (object), `created_at` (ISO 8601 string)

#### Scenario: Issue contract
- **WHEN** the issues API responds
- **THEN** each issue conforms to: `severity` (string), `type` (string), `butler` (string), `description` (string), `link` (string | null), `error_message` (optional string | null), `occurrences` (optional number), `first_seen_at` (optional string | null), `last_seen_at` (optional string | null), `butlers` (optional string array for multi-butler issues)
