# Draft candidate: private rolling-summary custody

Status: NOT ADOPTED. Future additions require exact review/adoption and owning integrations. Full current requirements and prior scenarios are retained.

## MODIFIED Requirements

### Requirement: Session Detail Drawer
The `SessionDetailDrawer` SHALL be a slide-over sheet that provides full session context without leaving the sessions list. It is the operator's primary tool for understanding what happened in a single execution.

After independent exact adoption and all owned custody integrations, for a private-summary turn only, full Prompt SHALL mean the full permitted stored base prompt with an honest fixed private-context omission notice, not runtime overlay. This expressly qualifies the "Prompt and result display" full-prompt condition for this future path, while preserving that entire prior body and all other ordinary session views. It does not reduce the exact supplied system-prompt receipt or falsely call base-only client view the complete actual user input: a separate private content-blind composition/use receipt binds that full input, and actual adapter delivery must be verified. Session/global/butler list/detail/full-page/search/Timeline, tools/result/error/process-log/prompt doors, copy/export/SSE SHALL neither expose derivative/provenance/binding nor resolve private references. Copy admission precedes storage and read projection; unsafe/unknown state is omitted/unavailable, not successful full context. Exact system receipt stays derivative-free. No new summary door/client field is proposed; normal metadata/tool timeline/result/error/cost/a11y/navigation remain under owning API/UI integration.

ID: REQ-dashboard-visibility-006
Source: dashboard-visibility § Session Detail Drawer; private summary custody R1; RFC 0003/0007 unchanged wire/source identity
Scope: proposed-owner-gated


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

#### Scenario: Session list and detail show only permitted stored content

- **WHEN** session DTOs expose prompt/result/tools/error for a private-overlay invocation
- **THEN** all client doors receive only admitted base/raw-source content, no derivative/provenance/private binding
- **AND** full stored-prompt view honestly labels private runtime context omitted without resolving it

#### Scenario: Session read racing deletion cannot fetch an old overlay

- **WHEN** list/detail/process/prompt read races source/thread invalidation
- **THEN** no reader resolves old bindings or exposes retired captures, and unproven projection uses unavailable/omitted behavior
- **AND** ordinary admitted raw/base lifecycle evidence remains under existing retention
