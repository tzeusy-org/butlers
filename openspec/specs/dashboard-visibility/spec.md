# Dashboard Visibility and Traceability

## Purpose
Specifies the operator-facing dashboard surfaces that provide end-to-end visibility into the Butlers system: session history, unified timeline, notification audit trail, the issues page, and topology visualization. The audit-log contract is owned by `dashboard-audit-log`, the home page by `dashboard-overview`, and the `/ingestion` Timeline ledger by `dashboard-ingestion-dispatch-console`. Together these surfaces answer the operator's core questions: "What is every butler doing right now?", "What happened to this specific request?", and "Is the system healthy?" Every requirement below is grounded in the implemented frontend code and its backend data contracts.

## Requirements

### Requirement: Cross-Butler Session Explorer
The Sessions page (`/sessions`) SHALL provide a paginated, filterable table of session records aggregated across all butlers. This is the primary surface for answering "what work has the system done?" and is the entry point for drill-down into any individual execution.

#### Scenario: Default session listing
- **WHEN** an operator navigates to `/sessions` with no active filters
- **THEN** the page displays a paginated table of sessions ordered by `started_at` descending (most recent first)
- **AND** the table shows columns: Time, Butler, Trigger, Request ID, Prompt, Duration, Status, Tokens (in/out)
- **AND** page size is 20 rows per page
- **AND** the `showButlerColumn` flag is `true` (cross-butler view)

#### Scenario: Session filter bar
- **WHEN** the operator interacts with the filter bar
- **THEN** six filter controls are available: Butler (dropdown populated from `/api/butlers`), Trigger Source (free-text), Request ID (free-text, monospace), Status (dropdown: All / Success / Failed / Running), From date, To date
- **AND** changing any filter resets pagination (clears the keyset cursor)
- **AND** a "Clear filters" button appears when any filter departs from its default
- **AND** the active filters and cursor are mirrored to the URL query string, so a filtered view is shareable and survives refresh (state initializes from the URL)

#### Scenario: Butler dropdown populated dynamically
- **WHEN** the Sessions page loads
- **THEN** the Butler filter dropdown is populated by calling `useButlers()` and extracting `name` from each butler summary
- **AND** the default value is "All" (no butler filter applied)

#### Scenario: Request ID click-to-filter
- **WHEN** a request ID cell in the session table is clicked
- **THEN** the `request_id` filter is populated with the clicked value (via `onRequestIdClick` callback)
- **AND** the click event does not propagate to the row click handler (uses `e.stopPropagation()`)

#### Scenario: Session row click opens detail drawer
- **WHEN** the operator clicks a session row, OR focuses its session-detail control and presses Enter or Space
- **THEN** a `SessionDetailDrawer` (right-side sheet) opens for the clicked session
- **AND** the drawer receives the session's `id` and `butler` name
- **AND** the keyboard-accessible detail control is a native button in a static table cell with a visible focus ring, while the parent remains a semantic table row so the Request ID control is a separate real button

#### Scenario: Session volume visualization
- **WHEN** the Sessions page renders
- **THEN** a session-volume-over-time chart (`SessionStripeChart`, stacked per-butler bars) is shown as the page's primary visualization above the filter bar
- **AND** the chart is scoped to the visible active filter window (not the page cursor); if the paginated list debounces free-text input, the chart still refetches for the visible Trigger Source and Request ID so prior-query data is never presented as current
- **AND** the chart and the list are invalidated by the same session bus events and refresh on the shared cadence in `dashboard-shell` Requirement: Bus-Aware Poll Architecture, so the chart never lags the list

#### Scenario: Window-true KPI strip
- **WHEN** the Sessions page renders
- **THEN** a KPI strip (`SessionsKpiStrip`) shows window-true aggregates from `GET /api/sessions/aggregate` (sessions count, success rate, tokens in/out, top butler), scoped to the active filters across all butlers and labeled "Matching filters"
- **AND** the aggregate recomputes when visible filters change but NOT when the operator pages (it is never derived from the fetched page)
- **AND** when there are no completed sessions, the success rate renders a dash rather than a fabricated number

#### Scenario: Session list error state
- **WHEN** the cross-butler session fetch fails
- **THEN** the page renders an error region with a retry action (not the empty "No sessions found" state), so a failed read is never presented as "no sessions"

### Requirement: Sessions Verdict Withholds Partial Trigger Attribution
The sessions failure verdict SHALL distinguish incomplete trigger attribution from scalar aggregate degradation. It SHALL retain a truthful failed-session count while refusing to name a trigger source as the dominant cluster when `trigger_breakdown_degraded_sources` is non-empty.

#### Scenario: Partial trigger breakdown cannot support a trigger-dominance claim
- **WHEN** the failed-session aggregate has one or more `trigger_breakdown_degraded_sources` and matching scalar failures
- **THEN** the verdict names that trigger attribution is unavailable from the affected source or sources
- **AND** it retains the scalar failure count
- **AND** it SHALL NOT render a `clustered on {trigger_source}` claim or a trigger-filter link derived from the partial breakdown

#### Scenario: Complete trigger breakdown keeps existing trigger attribution
- **WHEN** the failed-session aggregate has trigger buckets and an empty `trigger_breakdown_degraded_sources` list
- **THEN** the verdict may use the most concentrated trigger source as its existing cluster label and link

### Requirement: Session Table Visual Treatment
The session table (`SessionTable`) SHALL apply visual affordances to communicate status at a glance without requiring the operator to read every cell.

#### Scenario: Failed session row highlighting
- **WHEN** a session has `success === false`
- **THEN** its table row receives the `bg-destructive/5` background class (subtle red tint)

#### Scenario: Status badge variants
- **WHEN** `success === true`
- **THEN** a "Success" badge is rendered via the shared `StatusBadge` (a semantic-token green dot plus a mandatory "Success" label, since the green falls below the light-mode 3:1 non-text contrast floor and must not be the sole signal)
- **WHEN** `success === false`
- **THEN** a "Failed" badge is rendered via `StatusBadge` (destructive variant)
- **WHEN** `success === null` (session in progress)
- **THEN** a "Running" badge is rendered via `StatusBadge` (an amber/muted dot plus a "Running" label)
- **AND** the same `StatusBadge` is used by the session table, the detail drawer, and the session detail page (one token-reading source; no hardcoded color literals)

#### Scenario: Butler identity in the Butler column
- **WHEN** a butler name is displayed in the Butler column
- **THEN** a neutral `ButlerMark` letter-mark is rendered alongside the butler name in plain foreground text
- **AND** the butler hue resolves only onto the letter-mark (per the design-language butler-hue-scope rule), never as a badge background; the same butler maps to the same letter-mark hue across the dashboard

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
The `SessionDetailDrawer` SHALL be a slide-over sheet that provides full session context without leaving the sessions list. It is the operator's primary tool for understanding what happened in a single execution.

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
- **THEN** the drawer displays the trace ID as a clickable link navigating to `/timeline?trace={trace_id}`
- **AND** a copy-to-clipboard button is adjacent to the link (using `navigator.clipboard.writeText`)

#### Scenario: Copyable text feedback
- **WHEN** the operator clicks the copy button next to a trace ID
- **THEN** a check icon replaces the copy icon for 2 seconds before reverting

### Requirement: Session Detail Full Page
The `SessionDetailPage` (`/sessions/:id`) SHALL provide a full-page view of a single session. It serves as the deep-link target for session references from other surfaces (notifications, timeline).

#### Scenario: Global session fetch accepts legacy butler query state
- **WHEN** the URL is `/sessions/{id}` with or without a legacy `?butler=<name>` query parameter
- **THEN** the page SHALL ignore the query parameter and use the global endpoint (`getSession(id)`) for the same cross-butler lookup
- **AND** it SHALL NOT select or require a butler-scoped session-detail endpoint

#### Scenario: Breadcrumb navigation
- **WHEN** the session detail page loads
- **THEN** a breadcrumb trail shows: Sessions (link to `/sessions`) > `{id.slice(0, 8)}` (current page)

#### Scenario: Full metadata display
- **WHEN** the page renders a session
- **THEN** it shows: Butler (link to `/butlers/{butler}`), Trigger Source (badge), Started, Completed, Duration, Model (if present), Tool Calls count (if present, showing array length or string representation), and Tokens in/out (if present)

#### Scenario: Error display
- **WHEN** the session has an `error` field
- **THEN** an "Error" card renders with `text-destructive` title and the error in a preformatted block with `bg-destructive/10` background

### Requirement: Unified Timeline
The Timeline page (`/timeline`) SHALL merge events from all butlers into a single reverse-chronological stream. It answers "what has been happening across the entire system?" and is the primary surface for detecting anomalous patterns spanning multiple butlers.

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

#### Scenario: Trace-scoped session drill-down
- **WHEN** the operator follows a session trace link to `/timeline?trace={trace_id}`
- **THEN** the Timeline requests matching session events and trace-attributed notifications through `GET /api/timeline?trace={trace_id}`
- **AND** it visibly names the active trace scope and explains that matching sessions and trace-attributed notification rows are shown
- **AND** the operator can clear the trace scope without discarding other URL-backed filters

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
- **THEN** its data refreshes automatically on the shared cadence in `dashboard-shell` Requirement: Bus-Aware Poll Architecture
- **AND** there is no manual toggle or interval picker

#### Scenario: Human-readable event summary derivation
- **WHEN** Timeline or the Butler Activity Feed projects a session row, it SHALL derive the row's `summary` from its structured `trigger_source` before inspecting stored prompt text
- **THEN** `schedule:<task-name>` and `deadline:<task-name>` sources render bounded, humanized labels (for example, `schedule:daily_digest` → "Scheduled: daily digest" and `deadline:passport-renewal` → "Deadline: passport renewal")
- **AND** recognised exact sources, including heartbeat and classification sources, render their safe source label without interpreting prompt text
- **AND** only a session whose `trigger_source` is exactly `route` and whose prompt contains one complete, non-empty `<routed_message>` or `<user_message>` fence MAY render that fence's bounded inner text
- **AND** malformed, unknown, null, legacy, context-envelope, chat-envelope, and system-prompt cases SHALL use a generic safe label rather than dumping prompt text
- **AND** equivalent stored session rows SHALL yield the same summary through `GET /api/timeline` and `GET /api/butlers/{name}/activity-feed`

#### Scenario: Failed-delivery row honesty
- **WHEN** a notification event has `data.status === "failed"` (a bounced delivery, e.g. a repeatedly-failing owner alert)
- **THEN** its row renders the destructive mark — a `bg-destructive` dot and the word "failed" — instead of the calm neutral `bg-purple-500` "notification" dot reserved for delivered notifications
- **AND** a delivered (`sent`) notification keeps the calm neutral dot

#### Scenario: Errors lens includes failed deliveries
- **WHEN** the Errors-only view queries `GET /api/timeline?event_type=error`
- **THEN** the result includes failed notification deliveries (`status = 'failed'`) alongside failed sessions (`success = false`) — a bounced owner alert is an error the owner must see, not a calm notification hidden from the Errors lens
- **AND** the notification sub-query is restricted to failed deliveries in SQL (keeping keyset pagination truthful over the actually-matching set)
- **AND** `event_type=notification` (or an unfiltered stream) is unaffected — it returns notifications of all statuses

### Requirement: Notification Audit Trail
The Notifications page (`/notifications`) SHALL provide a complete audit trail of every notification sent by any butler across all delivery channels. This surface is essential for verifying that user-facing communications were delivered successfully and diagnosing delivery failures.

#### Scenario: Notification stats bar
- **WHEN** the Notifications page loads
- **THEN** a four-card stats bar displays: Total Notifications (with bell icon), Sent count (green, with checkmark icon), Failed count (red, with X icon), and Failure Rate percentage (color-coded: green if 0%, amber if 0-10%, red if >10%)
- **AND** below the cards, a per-channel breakdown shows each channel name with its count as a badge

#### Scenario: Notification filter bar
- **WHEN** the operator interacts with the notification filter bar
- **THEN** five filter controls are available: Butler (free-text input), Channel (dropdown: All / Telegram / Email), Status (dropdown: All / Sent / Failed / Terminal failures / Pending / Read / Retried), Since (date input), Until (date input)
- **AND** a "Clear filters" button appears when any filter is active

#### Scenario: Terminal-failure filter and stats breakdown

- **WHEN** the Notifications page requests `GET /api/notifications?status=terminal_failed`
- **THEN** the list includes failed notifications that have no later sent notification
  with the same session, channel, and message
- **AND** it excludes failed attempts that later matching deliveries retried successfully
- **AND** the Status filter visibly names this predicate as `Terminal failures`
- **AND** `GET /api/notifications/stats`'s `failed` count and `by_butler` breakdown use
  that same terminal-failure predicate
- **AND** verdict links that name a bounded notification count preserve that response's
  exact `since` and `until` interval

#### Scenario: Notification feed table
- **WHEN** notifications are loaded
- **THEN** a table displays columns: Status (badge), Butler (source butler name), Channel (capitalized badge), Message (truncated to 60 chars), and Time (relative)
- **AND** failed notification rows receive `bg-destructive/5` background

#### Scenario: Notification-to-session and trace cross-links
- **WHEN** a notification has a `session_id`
- **THEN** a "Session {shortId}" link is displayed below the message, navigating to `/sessions/{session_id}?butler={source_butler}`
- **WHEN** a notification has a `trace_id`
- **THEN** a "Trace {shortId}" link is displayed below the message, navigating to `/ingestion?tab=timeline`
- **AND** both links are styled as primary-colored underlined text

#### Scenario: Notification status badges
- **WHEN** status is "sent"
- **THEN** a green "Sent" badge is rendered
- **WHEN** status is "failed"
- **THEN** a red destructive "Failed" badge is rendered
- **WHEN** status is "pending"
- **THEN** an amber outlined "Pending" badge is rendered

#### Scenario: Inline retry and escalate verbs on a failed row
- **WHEN** a notification row's `effective_status` is `failed`
- **THEN** the row's triage column SHALL offer inline "Retry" and "Escalate" verbs alongside "Mark read"/"Dismiss", calling `POST /api/notifications/{id}/retry` and `POST /api/notifications/{id}/escalate` respectively
- **AND** a row whose `effective_status` is `sent`, `read`, `retried`, or `escalated` SHALL NOT offer either verb -- there is nothing left to re-deliver
- **AND** both verbs are honest-pending: the clicked row reads "Retrying..."/"Escalating..." and both verbs stay disabled until the real round trip settles, with no optimistic status flip

#### Scenario: Retry and escalate outcomes are reported to the operator
- **WHEN** a retry or escalate call returns 200 and the new attempt's `status` is `sent`
- **THEN** a success toast names the channel the attempt landed on
- **WHEN** the call returns 200 but the new attempt's own `status` is `failed`
- **THEN** an error toast is shown carrying that attempt's `error` as its description -- a re-send that did not deliver SHALL NOT be reported as a success
- **WHEN** the call is rejected (409 for a row that is no longer `failed`, 422 for a channel with no alternate or a missing owner contact, 404, 503, or a transport error)
- **THEN** an error toast is shown carrying the endpoint's `detail` as its description, so a stale list still offering "Retry" on a row another tab already actioned explains itself instead of clearing silently

#### Scenario: Empty state with filter hint
- **WHEN** no notifications match the current filters
- **THEN** the empty state message reads "No notifications match the current filters. Try clearing the filters to see all notifications."
- **WHEN** no notifications exist at all (no filters active)
- **THEN** the empty state reads "Notifications will appear here as butlers send messages via Telegram, email, and other channels."

### Requirement: System Topology Visualization
The `TopologyGraph` component SHALL render a force-directed graph of butler nodes and their interconnections, providing at-a-glance system architecture visibility and health status.

#### Scenario: Butler node layout
- **WHEN** the topology graph renders
- **THEN** the Switchboard butler is positioned at the center (300, 200) with a large rounded node (16px/24px padding, bold text, 140px width)
- **AND** the Heartbeat butler is positioned top-right (550, 50) as a dashed-border circle (90x90px)
- **AND** all other butlers are arranged in a circle of radius 200px around the Switchboard

#### Scenario: Health status coloring
- **WHEN** a butler has status "ok" or "online"
- **THEN** its state border is green (`--green`)
- **WHEN** status is "down" or "offline"
- **THEN** its state border is red (`--red`)
- **WHEN** status is "degraded"
- **THEN** its state border is amber (`--amber`)
- **AND** every topology node retains a neutral background and a readable neutral foreground (`var(--fg)`) in both themes, exposing live state only through a state-colored border or compact `StateDot`; the Switchboard and Heartbeat nodes retain neutral backgrounds with state-colored borders, while ordinary Butler and connector nodes do the same on their neutral surface layers

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

### Requirement: Pagination Consistency
Offset-paginated dashboard surfaces, including domain pages, SHALL share the same offset-based pattern using backend `PaginationMeta` responses, per the envelope conventions in `docs/api_and_protocols/response-conventions.md`. The cross-butler Sessions list (`GET /api/sessions`) is the one exception: it uses keyset (cursor) pagination, to avoid the cross-butler count fan-out. On every paginated surface, changing any filter parameter SHALL reset the view to its first page.

#### Scenario: Offset-based pagination contract
- **WHEN** an offset-paginated surface (Notifications, Audit Log, and the per-butler `GET /api/butlers/{name}/sessions` list) renders data
- **THEN** it sends `offset` and `limit` parameters derived from `page * PAGE_SIZE`
- **AND** the response `meta` object contains `total`, `offset`, `limit`, and `has_more`
- **AND** Previous/Next buttons are disabled at the start/end of the result set
- **AND** a position indicator ("Page X of Y" or "Showing X-Y of Z") shows current position
- **AND** changing any filter parameter resets the page to the first page

#### Scenario: Cross-butler session keyset pagination
- **WHEN** the cross-butler Sessions list (`GET /api/sessions`) renders data
- **THEN** it sends `limit` and an opaque `cursor` (omitted on the first page); the response `meta` object contains `limit`, `next_cursor`, and `has_more` (no `total`, no `offset`)
- **AND** rows are ordered `started_at DESC, id DESC`; "Older" advances via `next_cursor` and is disabled when `has_more` is false; "Newer" steps back through the visited cursors
- **AND** no "Page X of Y" indicator is shown (a total is intentionally not computed)

#### Scenario: Timeline cursor pagination
- **WHEN** the Timeline page loads more events
- **THEN** it uses cursor-based pagination (sending `before` parameter from `response.meta.cursor`)
- **AND** new events are appended to the accumulated event list rather than replacing them
- **AND** while the operator is viewing that committed history, a background live-head refresh neither replaces nor visually dims the historical snapshot; newly arrived head events are surfaced through the new-events path instead

### Requirement: Cross-Surface Navigation and Linking
The visibility surfaces SHALL be interconnected through contextual links that allow operators to trace a request across multiple views without manually searching.

#### Scenario: Session to trace navigation
- **WHEN** a session detail drawer displays a `trace_id`
- **THEN** it is a clickable link to `/timeline?trace={trace_id}`

#### Scenario: Notification to session navigation
- **WHEN** a notification row has a `session_id`
- **THEN** a "Session {shortId}" link navigates to `/sessions/{session_id}?butler={source_butler}`
- **AND** the destination treats the legacy `?butler=` state as ignored input and resolves the global session detail

#### Scenario: Notification to trace navigation
- **WHEN** a notification row has a `trace_id`
- **THEN** a "Trace {shortId}" link navigates to `/ingestion?trace={trace_id}`

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
The system SHALL support tracing a request from initial ingestion through final delivery by correlating data across multiple surfaces. This requirement describes the complete traceability journey an operator follows.

#### Scenario: Message ingestion through delivery
- **GIVEN** a message arrives via an external connector (e.g. Telegram)
- **WHEN** the Switchboard butler receives the message, classifies it, and routes it to a domain butler
- **THEN** the following trace path is visible across dashboard surfaces:
  1. The **Timeline** shows a "session" event for the Switchboard butler's classification session
  2. The **Sessions** page shows the Switchboard session with `trigger_source="external"` and a `request_id` from the connector
  3. Filtering sessions by that `request_id` reveals all sessions in the request's lifecycle
  4. The session detail drawer for each session shows tool calls (e.g. route classification, state lookups)
  5. If the domain butler sends a notification, the **Notifications** page shows it with a link back to the session
  6. The **Audit Log** records each operation (trigger, session, etc.) with full request context

#### Scenario: Request ID as correlation key
- **WHEN** an operator has a request ID (e.g. from a user report or external system)
- **THEN** they can paste it into the Sessions page Request ID filter
- **AND** see all sessions involved in processing that request across all butlers
- **AND** from any session, open the session detail drawer to inspect tool calls and execution detail

### Requirement: Loading and Error States
All visibility surfaces SHALL handle loading and error states consistently to prevent operator confusion, using the shared shell patterns in `dashboard-shell` (Requirement: Skeleton Loading Components; Requirement: Empty State Pattern; Requirement: Error Boundary). Loading and empty states SHALL be mutually exclusive: skeletons while loading, and the empty state only after loading completes with zero results.

#### Scenario: Skeleton loading states
- **WHEN** data is loading for any visibility surface (Sessions, Notifications, Audit Log, Timeline, Topology)
- **THEN** the surface renders the shell skeleton pattern shaped like its loaded layout
- **AND** it does not render its empty state

#### Scenario: Empty states
- **WHEN** no data matches the current view (after loading completes)
- **THEN** the shell empty-state pattern is shown with a descriptive title and explanation
- **AND** the message varies by surface (e.g. "No sessions found" with "Sessions will appear here as butlers process triggers and scheduled tasks.")

#### Scenario: Error states
- **WHEN** the session detail API call fails
- **THEN** a destructive-styled error message is shown without suggesting a `?butler=` remedy, because the global session lookup is authoritative
- **WHEN** the notification feed fails to load
- **THEN** a destructive-styled message reads "Failed to load notifications. Please try refreshing the page."

### Requirement: Data Model Contracts for Visibility Surfaces

The frontend TypeScript interfaces SHALL define the data contracts that all visibility surfaces depend on. These contracts MUST be satisfied by the backend API.

#### Scenario: SessionSummary contract (list views)

- **WHEN** the sessions list API responds
- **THEN** each item conforms to: `id` (string), `butler` (optional string), `prompt` (string), `trigger_source` (string), `request_id` (optional string | null), `success` (boolean | null), `cancelled_by_owner` (boolean), `started_at` (ISO 8601 string), `completed_at` (string | null), `duration_ms` (number | null), `input_tokens` (number | null), `output_tokens` (number | null)

#### Scenario: Owner-cancelled list status is distinct from a failure

- **WHEN** `SessionTable` or `SessionsPinnedStrip` renders a session summary
  with `success = false` and `cancelled_by_owner = true`
- **THEN** the status badge renders `Cancelled`, not `Failed`
- **AND** a failed summary with `cancelled_by_owner = false` renders `Failed`
- **AND** success and non-terminal rows retain their existing labels

#### Scenario: SessionDetail contract (drill-down views)

- **WHEN** the session detail API responds
- **THEN** the item retains its existing detail contract: `result` (string | null), `tool_calls` (array of unknown), `trace_id` (string | null), `cost` (object | null), `error` (string | null), `model` (string | null), and `parent_session_id` (string | null)
- **AND** it does not require the list-only `cancelled_by_owner` discriminator

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

### Requirement: Timeline minute density and historical interval selection
The dashboard SHALL display server-aggregated per-minute event density and SHALL load the complete selected minute through interval-filtered pagination, independently of which rows were previously loaded.

ID: REQ-dashboard-visibility-001
Source: dashboard-visibility Unified Timeline; frontend/src/pages/TimelinePage.tsx
Scope: v1-mandatory

#### Scenario: Density includes events beyond the loaded page
- **WHEN** more than one list page of matching events exists in a selected interval
- **THEN** GET /api/timeline/histogram returns counts for every matching event in each UTC minute, using the same source/type/butler/trace predicates as the list
- **AND** each count represents raw events before display grouping, labelled All matching events

#### Scenario: Bounded and stable interval semantics
- **WHEN** an interval is supplied to the histogram or timeline list
- **THEN** both since and until must be timezone-aware whole-minute bounds, strictly ordered and no more than 24 hours apart, otherwise the API returns 422
- **AND** results include events at since and exclude events at until
- **AND** list cursor pagination preserves the interval without losing events sharing a timestamp
- **AND** a list request without either bound retains its existing behavior

#### Scenario: Select and restore an unloaded minute
- **WHEN** the owner selects a histogram minute containing events not in the currently loaded page
- **THEN** one atomic URL update records the resolved chart interval and selected minute, including when the chart hour was previously implicit and the list fetches that minute from the server with a reset cursor
- **AND** back, forward and reload after an hour rollover restore the same explicit interval and filters
- **AND** clearing the selection or invoking Jump to latest restores live stream behavior

#### Scenario: Invalid URL scope is visible
- **WHEN** chart bounds are malformed or a selected minute is unpaired, unaligned, longer than one minute or outside its chart interval
- **THEN** the page shows an invalid-interval message and Clear interval action
- **AND** it does not silently replace the requested interval with an unfiltered request

#### Scenario: Partial density is not a quiet fleet
- **WHEN** a selected event source fails
- **THEN** healthy-source counts remain available with named degraded sources and butlers, explicit expected_sources and healthy_sources counts, and availability complete, partial or unavailable
- **AND** complete zero counts are shown only when all selected sources are healthy
- **AND** all-source failure or an absent sole selected notifications pool renders unavailable with Retry rather than an empty histogram

#### Scenario: Availability distinguishes partial fan-out and absent configuration
- **WHEN** the selected session scope contains two butlers and only one answers
- **THEN** expected_sources is 2, healthy_sources is 1 and availability is partial

#### Scenario: Selected sources are unavailable
- **WHEN** neither selected session butler answers, or notifications alone are selected with no configured Switchboard pool
- **THEN** availability is unavailable with zero healthy sources and a positive expected source count

#### Scenario: Healthy selected sources have no events
- **WHEN** every selected source answers with zero events
- **THEN** availability is complete and zero counts are trustworthy for that selection

#### Scenario: Filters select no recognized sources
- **WHEN** the event-type selection names no recognized source family
- **THEN** expected_sources and healthy_sources are zero and the UI says No matching event sources rather than fleet all-clear

#### Scenario: Accessible and stable density interaction
- **WHEN** the owner operates the density control by keyboard or changes filters rapidly
- **THEN** arrow keys and Enter or Space select a minute with accessible time/count labels and without requiring one Tab per bucket
- **AND** stale requests cannot replace the current selection
- **AND** live arrivals do not reset historical selection or steal focus

### Requirement: Timeline bounded recent failures strip
The dashboard SHALL show a collapsible strip above the timeline listing recent records created in the last 24 hours that are currently marked failed, with server-derived counts independent of loaded timeline rows and no claim of unresolved work.

ID: REQ-dashboard-visibility-002
Source: frontend/src/pages/TimelinePage.tsx
Scope: v1-mandatory

#### Scenario: Counts and rows reflect canonical failures
- **WHEN** the strip loads for selected butlers and trace
- **THEN** it counts sessions whose success is false and notification attempts whose current status is failed in one server-chosen last-24h interval
- **AND** it lists at most five newest matching identifiers in stable order, displays separate source counts and total, and states truncation
- **AND** chart time/type filters do not change this explicitly labelled 24h scope
- **AND** its response exposes only identifiers, kind, butler, timestamps, aggregate counts and degradation metadata

#### Scenario: Acknowledgement and retry claim are not recovery evidence
- **WHEN** acknowledgement or retry claiming changes a notification status from failed to read before any successful delivery
- **THEN** the next strip read excludes that record and updates its count
- **AND** neither the strip nor its empty state claims historical failure absence or successful recovery
- **AND** labels describe records currently marked failed that were created in the last 24 hours

#### Scenario: Failure inspection preserves the target
- **WHEN** the owner activates a failed-run or failed-delivery-attempt row
- **THEN** a real link opens its session detail or timeline event drawer by persisted identifier
- **AND** notification navigation carries butler/trace scope and clears historical interval selection so an off-page event remains reachable
- **AND** inspection does not acknowledge, resolve or retry anything

#### Scenario: Collapse and unavailable state remain honest
- **WHEN** the owner collapses the strip
- **THEN** counts and source degradation remain visible and the disclosure exposes its expanded state accessibly
- **AND** remounting defaults to expanded without storing a new preference

#### Scenario: Failure strip sources are unavailable
- **WHEN** any source is unavailable
- **THEN** the strip marks partial or unavailable data using explicit expected/healthy source counts and availability, offers Retry and never claims all-clear

#### Scenario: Healthy failure sources have no matching records
- **WHEN** healthy sources return no failures
- **THEN** it states that no matching records are currently marked failed within the creation window

### Requirement: Timeline keyboard row traversal and existing-view actions
The dashboard SHALL support j/k traversal of rendered timeline disclosure controls and expose existing view presets through the page action registry while preserving shell search and current timeline shortcuts.

ID: REQ-dashboard-visibility-003
Source: dashboard-shell Keyboard Shortcuts; frontend/src/pages/TimelinePage.tsx
Scope: v1-mandatory

#### Scenario: Real focus and one activation
- **WHEN** the owner presses j or k outside editable fields and modal contexts
- **THEN** actual DOM focus moves forward or backward through rendered primary row disclosures, entering at the first or last row and clamping at the ends
- **AND** Enter activates the focused disclosure exactly once through its native button behavior
- **AND** traversal does not implicitly fetch another page

#### Scenario: Refetch preserves focus and input ownership
- **WHEN** rows refresh while a disclosure is focused
- **THEN** the same row retains focus if present, otherwise the nearest surviving row at the prior index receives focus
- **AND** typing, IME composition and modal keyboard handling are not intercepted by page traversal

#### Scenario: Existing shortcuts and preset semantics remain intact
- **WHEN** the owner uses slash, Cmd or Ctrl plus K, r, or n
- **THEN** global command search, refresh and conditional Jump to latest retain their existing owners and behavior
- **AND** Escape from global search returns focus under the existing shell modal contract

#### Scenario: Palette-only presets preserve built-in view selection
- **WHEN** the owner selects All, Errors only or Notifications from page actions
- **THEN** the existing built-in view selection path updates the URL and data, with presets available as palette-only commands and actual keybindings discoverable in shortcut help

### Requirement: Machine-class Timeline presentation

The Timeline API SHALL attach a presentation-only `machine_class` of `owner`,
`heartbeat`, or `maintenance` to every event. Session classification SHALL use
only exact structured `trigger_source` values from the reviewed presentation
taxonomy; it SHALL NOT inspect prompt text or classify all `schedule:*` values
as maintenance. The API SHALL retain `is_heartbeat`, set to true exactly when
`machine_class` is `heartbeat`, for compatibility.

#### Scenario: Exact maintenance taxonomy classifies a maintenance session

- **WHEN** a session has an exact reviewed maintenance trigger source such as
  `schedule:consolidation` or `schedule:memory_decay_sweep`
- **THEN** its Timeline event has `machine_class` equal to `maintenance`
- **AND** its safe summary remains derived by the structured summary boundary
- **AND** its legacy `is_heartbeat` value is false

#### Scenario: Unknown or owner-value schedule remains owner activity

- **WHEN** a session has an unknown, malformed, or ordinary scheduled trigger
  source, including a suffix of a known maintenance source
- **THEN** its Timeline event has `machine_class` equal to `owner`
- **AND** the session remains visible in the default Timeline lens

#### Scenario: Existing heartbeat compatibility remains intact

- **WHEN** a session has a recognised heartbeat trigger source
- **THEN** its Timeline event has `machine_class` equal to `heartbeat`
- **AND** `is_heartbeat` remains true

### Requirement: Internal maintenance Timeline lens

The Timeline SHALL default to an owner-focused lens that suppresses only
maintenance events whose `data.success` is exactly `true`. A `data.success`
value of `false` SHALL be a failed run; `null` SHALL be a running run; and a
missing or nonboolean value SHALL be unknown. Running and unknown maintenance
events SHALL remain visible in the default lens. The Timeline SHALL offer a
keyboard-operable Internal control with a visible pressed state and accessible
name; `internal=1` SHALL enable the lens. When enabled, the Timeline SHALL
render maintenance events as expandable, per-butler rollups within their hour
group, using only loaded event data for the displayed count. Each expanded run
SHALL present its strict outcome state and SHALL NOT label a running or unknown
run as completed. Failed maintenance sessions SHALL remain visible as errors
when the Internal lens is disabled.

#### Scenario: Default Timeline hides successful maintenance activity

- **WHEN** the Timeline is loaded without `internal=1`
- **THEN** successful maintenance events do not render as ordinary Timeline
  rows
- **AND** owner and heartbeat behavior remains unchanged
- **AND** a failed maintenance event remains visible as an error

#### Scenario: Internal Timeline lens exposes an expandable maintenance rollup

- **WHEN** the operator enables the Internal lens
- **THEN** maintenance events for the same butler and hour render as one
  rollup with their exact loaded-event count and failed-run count
- **AND** the rollup can be expanded by keyboard to inspect its safe event
  summaries

#### Scenario: Running and unknown maintenance remains truthful in both lenses

- **WHEN** a maintenance event has `data.success` equal to `null`, absent, or
  nonboolean
- **THEN** the default Timeline renders it as ordinary activity rather than
  suppressing it
- **AND** an expanded Internal rollup labels `null` as running and absent or
  nonboolean values as unknown, never completed

### Requirement: Timeline partial-source evidence

The Timeline SHALL preserve every event returned by reachable sources while
making any unavailable Timeline subread explicit. Its response metadata SHALL
retain the existing `degraded_sources: string[]` contract and SHALL
additively expose `degraded_butlers: string[]` for named failed session
fan-out pools. A non-empty degraded list means the displayed evidence is
partial and SHALL NOT be described as a complete fleet history, a genuine
empty state, or an exhausted historical boundary.

#### Scenario: Partial session fan-out names failed butlers

- **WHEN** the Timeline session fan-out succeeds for at least one requested
  butler and fails for one or more other requested butlers
- **THEN** the response preserves events from reachable butlers
- **AND** `meta.degraded_sources` retains `sessions`
- **AND** `meta.degraded_butlers` names the failed session pools
- **AND** the Timeline renders the generic partial-source state plus the named
  unavailable butlers without claiming a complete fleet history

#### Scenario: Failed butler facets remain unavailable rather than empty

- **WHEN** the Timeline's butler-facet reader fails
- **THEN** the Timeline renders a named butler-facet-unavailable state with a
  retry control
- **AND** it SHALL NOT render "No butlers available" as though the failed
  reader completed successfully
- **AND** Timeline rows, source facets, and other reachable controls remain
  usable

#### Scenario: Failed saved-view reader remains unavailable rather than empty

- **WHEN** the Timeline's custom saved-view reader fails
- **THEN** the built-in views and current Timeline filters remain usable
- **AND** the page renders a named saved-views-unavailable state with a retry
  control
- **AND** it SHALL NOT describe the failed reader as having no custom saved
  views

#### Scenario: Failed Load older retries the same historical boundary

- **WHEN** the operator requests an older Timeline page and that request fails
- **THEN** the already rendered events remain visible
- **AND** the Timeline renders a named older-page-unavailable state with a
  retry control
- **AND** the retry sends the same cursor as the failed request
- **AND** the Timeline SHALL NOT advance or erase the cursor, claim the end of
  history, or replace the committed snapshot with a live-head refresh

### Requirement: Pinned session error excerpt states

The Sessions pinned strip SHALL distinguish the bounded session-detail query
state for each recent failed session. A detail read that is loading, fails, or
succeeds with a null error field SHALL be visibly distinct; an unavailable
detail is not evidence that the session has no error detail.

#### Scenario: Loading error excerpt is not presented as null detail

- **WHEN** a pinned recent-failure detail query is pending
- **THEN** that row identifies its error detail as loading
- **AND** it SHALL NOT render "no error detail" before a successful response

#### Scenario: Failed error excerpt offers row-local retry

- **WHEN** one pinned recent-failure detail query fails
- **THEN** its row remains visible with a named unavailable-detail state
- **AND** that row offers a keyboard-operable retry control for its own detail
  query
- **AND** other pinned rows retain their independent loaded or loading states

#### Scenario: Loaded null error detail remains an honest known-null value

- **WHEN** a pinned recent-failure detail query succeeds and its `error` field
  is null
- **THEN** that row renders the known-null "no error detail" state
- **AND** it does not render a loading or unavailable state

### Requirement: Pending-approvals visibility follows pending-actions availability

The dashboard's Pending approvals KPI SHALL use its numeric value and
`/approvals` door only when the approval metrics query succeeds and
`meta.pending_actions_sources_degraded` is absent or empty. A query failure or
pending-actions degradation SHALL render the unavailable value with no
interactive door, name the unavailable sources, and offer a safe metrics-read
retry. `approval_rules` degradation alone SHALL not make a complete pending
approvals KPI unavailable.

The Sidebar's existing `/approvals` navigation link SHALL render a visible,
accessible amber unavailable marker instead of a numeric zero when the metrics
query fails or `meta.pending_actions_sources_degraded` is non-empty. A
truthful complete zero remains quiet.

#### Scenario: Pending-actions source is partial

- **WHEN** the metrics response names one or more
  `pending_actions_sources_degraded`
- **THEN** the Pending approvals KPI renders `—` with unavailable semantics
- **AND** it is not a link, button, or other interactive door
- **AND** the page names the unavailable source(s) and offers a retry of the
  metrics query.

#### Scenario: Rule source alone is partial

- **WHEN** the metrics response names only
  `approval_rules_sources_degraded` and contains a complete pending count
- **THEN** the Pending approvals KPI renders that count, including a genuine
  zero
- **AND** its `/approvals` door remains available.

#### Scenario: Sidebar pending badge is unavailable

- **WHEN** the metrics query fails or names one or more
  `pending_actions_sources_degraded`
- **THEN** the Sidebar keeps its existing `/approvals` link
- **AND** renders an accessible amber unavailable marker instead of `0`.

### Requirement: Issues page presents grouped issues
The Issues page (`/issues`) and `IssuesPanel` SHALL present the grouped issues feed from `GET /api/issues` with per-issue verbs. Aggregation, grouping keys, and acknowledge-until-recurrence semantics are owned by `dashboard-api` Requirement: Issues Aggregation; refresh follows `dashboard-shell` Requirement: Bus-Aware Poll Architecture.

#### Scenario: Issues page layout
- **WHEN** the operator navigates to `/issues`
- **THEN** the page header reads "Issues" with a subtitle stating that it groups errors and warnings across all butlers, newest first
- **AND** the `IssuesPanel` renders below it with every issue from the feed

#### Scenario: Issue card structure
- **WHEN** issues are displayed
- **THEN** each issue shows its severity, the affected butler (or "N butlers" when several are affected), description, occurrence count with first-seen and last-seen timestamps, an optional "View" link when `issue.link` is set, and an "Acknowledge" action
- **AND** when the issue names a single real butler, a "Run schedule now" action forces that butler's scheduler to run via `POST /api/butlers/{name}/tick`
- **AND** when the issue's `type` is `"unreachable"` and it names a single real butler, a "Ping butler" action rechecks reachability via a live `GET /api/butlers/{name}`

#### Scenario: Multi-butler issue grouping
- **WHEN** an issue has a `butlers` array with more than one entry
- **THEN** the display shows "N butlers" instead of a single butler name
- **AND** neither "Run schedule now" nor "Ping butler" is shown, since there is no single butler to target

#### Scenario: Acknowledging removes the issue until it recurs
- **WHEN** the operator acknowledges an issue
- **THEN** the issue leaves the active list and the acknowledgement is persisted server-side
- **AND** the acknowledged-issues view offers a "Restore" action that undoes the acknowledgement

#### Scenario: Issue link navigation
- **WHEN** an issue has a non-null `link` field
- **THEN** the "View" action is a client-side link to the linked resource (typically a filtered session or notification view)

### Requirement: Protected effective-prompt session door

The dashboard SHALL expose `GET /api/sessions/{id}/prompt` through the existing dashboard
authentication boundary. It SHALL return the exact stored effective system prompt, verified digest,
total UTF-8 bytes, and ordered content-free provenance only on demand. Session list, aggregate, and
ordinary detail responses SHALL NOT include effective prompt content.

#### Scenario: Prompt receipt is retrieved across butlers
- **WHEN** an authenticated dashboard caller requests a known session prompt receipt
- **THEN** the API returns the stored effective prompt and N provenance entries
- **AND** total bytes equals the UTF-8 byte length of the returned prompt and its SHA-256 equals the returned digest

#### Scenario: Prompt receipt source is degraded
- **WHEN** the session is absent from reachable schemas and at least one schema is unreachable
- **THEN** the API returns an unavailable response rather than a definitive not-found result

#### Scenario: Legacy or corrupt receipt is not fabricated
- **WHEN** a legacy session has no receipt or stored receipt verification fails
- **THEN** the API returns an explicit unavailable or corrupt state without synthesizing prompt bytes or provenance

### Requirement: Purpose lane visibility

Session and spend projections that identify an individual dispatch SHALL render its closed purpose
lane as an accessible badge without rendering private source identities or message content.

#### Scenario: Private-content dispatch is visible
- **WHEN** a session or spend row carries `purpose_lane=private_content`
- **THEN** the row renders a `Private content` badge with a non-color-only accessible label
