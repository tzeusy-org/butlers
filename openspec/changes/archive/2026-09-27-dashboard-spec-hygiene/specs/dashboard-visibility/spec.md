## MODIFIED Requirements

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

## REMOVED Requirements

### Requirement: Audit Log

**Reason**: The audit-log contract is owned by a single capability.

**Migration**: See `dashboard-audit-log` for the audit-log read API and the `/audit-log` page.

### Requirement: Issue Detection and Surfacing

**Reason**: Issue aggregation and acknowledgement semantics are owned by `dashboard-api`; the page presentation is restated without the duplicated API and polling copy.

**Migration**: See `dashboard-api` Requirement: Issues Aggregation and Requirement: Issues page presents grouped issues in this spec.

### Requirement: Overview Dashboard

**Reason**: The home page contract is owned by `dashboard-overview`.

**Migration**: See `dashboard-overview` (information hierarchy, briefing, attention list, KPI strip, operations index, Now list, and the cost band).

### Requirement: Real-Time Polling and Auto-Refresh

**Reason**: Refresh cadence is owned by the shell.

**Migration**: See `dashboard-shell` Requirement: Bus-Aware Poll Architecture.

### Requirement: Ingestion Timeline Status Column

**Reason**: The `/ingestion` Timeline ledger is owned by `dashboard-ingestion-dispatch-console`, whose status vocabulary supersedes the colored badges.

**Migration**: See `dashboard-ingestion-dispatch-console` Requirement: Timeline Ledger.

### Requirement: Ingestion Timeline Action Column

**Reason**: The `/ingestion` Timeline ledger is owned by `dashboard-ingestion-dispatch-console`.

**Migration**: See `dashboard-ingestion-dispatch-console` Requirement: Replay Controls Respect Server Policy and Requirement: Timeline Row Replay Lifecycle.

### Requirement: Ingestion Timeline Status Filter

**Reason**: The `/ingestion` Timeline ledger is owned by `dashboard-ingestion-dispatch-console`.

**Migration**: See `dashboard-ingestion-dispatch-console` Requirement: Timeline Ledger and Requirement: Timeline Merges Ingested and Filtered Events.

### Requirement: Ingestion Timeline Unified Data Source

**Reason**: The `/ingestion` Timeline ledger is owned by `dashboard-ingestion-dispatch-console`.

**Migration**: See `dashboard-ingestion-dispatch-console` Requirement: Timeline Merges Ingested and Filtered Events.

## ADDED Requirements

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
