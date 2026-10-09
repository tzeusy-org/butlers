## MODIFIED Requirements

### Requirement: Session Detail Drawer
- The `SessionDetailDrawer` SHALL be a slide-over sheet that provides full session context without leaving the sessions list. It is the operator's primary tool for understanding what happened in a single execution.
- The existing Metadata Model is explicitly Requested/invoked. Beside the resolution receipt show ordered attempt serving evidence: authoritative Served, otherwise CLI-reported usage models with actual serving unknown, and distinct historical-null versus query-unavailable states. No raw error/denial/prompt is added by serving evidence. The existing content/tool/error/cost/trace/copy behaviors below remain in full. Multiple models, bounded truncation, reported/expected CLI version and drift are visible with static accessible text.

ID: REQ-dashboard-visibility-004
Source: bu-s11n0s.6 original outcome; protected 6cfc4eeac9003c0321a892d02b9865113cdccb72; serving protocol P1-P9
Scope: v1-mandatory

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
- The `SessionDetailPage` (`/sessions/:id`) SHALL provide a full-page view of a single session. It serves as the deep-link target for session references from other surfaces (notifications, timeline).
- The full-page SessionDossier and drawer use the same served_attempts DTO and named evidence states, without invoking providers. Requested and actual Served are never merged; report-only/fallback models remain visibly qualified. Current session content, breadcrumbs, global lookup, error cards and all previous scenarios remain. Spend evidence links to the existing attempt/dossier without adding action authority.

ID: REQ-dashboard-visibility-005
Source: bu-s11n0s.6 original outcome; protected 6cfc4eeac9003c0321a892d02b9865113cdccb72; serving protocol P1-P9
Scope: v1-mandatory

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

