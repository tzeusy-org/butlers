# Existing authoritative contract retention


This correction proposes no REMOVED or MODIFIED blocks for existing capabilities. Every baseline and active contract below remains normative in its current source. These are actual KEEP dispositions, not claims that old unit mechanics run inside the future goal runtime. Dark future contracts coexist until the exact named owner integration before M6.


## dashboard-api — Standard Response Envelopes


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. API schemas, owner authorization, unavailable/partial-source composition, historical identifiers and safe cross-domain reads. Goal reads use the single goal ledger. Domain fan-out remains for domain records. Retiring per-unit trigger/MCP proxy use requires its exact owner-approved implementation amendment; descriptive catalog data confers no broker grant. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Standard Response Envelopes
All API responses SHALL use consistent wrapper types. Backend Pydantic models and frontend TypeScript interfaces are kept in sync.

#### Scenario: ApiResponse wrapper
- **WHEN** a single-resource or aggregate endpoint succeeds
- **THEN** the response body is `{ "data": T, "meta": {} }` where `meta` is an extensible object

#### Scenario: PaginatedResponse wrapper
- **WHEN** a list endpoint with offset/limit pagination succeeds
- **THEN** the response body is `{ "data": T[], "meta": { "total": number, "offset": number, "limit": number, "has_more": boolean } }`
- **AND** `has_more` is a computed field: `offset + limit < total`

#### Scenario: ErrorResponse envelope
- **WHEN** any request fails
- **THEN** the response body is `{ "error": { "code": string, "message": string, "butler": string | null, "details": object | null } }`
- **AND** the HTTP status code reflects the error type (400, 404, 500, 502, 503)

#### Scenario: Unwrapped response exceptions
- **WHEN** certain domain endpoints return data
- **THEN** the following endpoints use unwrapped typed payloads instead of the standard `ApiResponse<T>` wrapper:
  - Timeline: `GET /api/timeline` returns `TimelineResponse` (unwrapped)
  - Relationship domain: `GET /api/relationship/contacts/{contactId}` returns `ContactDetail` (unwrapped), and other relationship sub-resource endpoints return unwrapped arrays
  - Trigger: `POST /api/butlers/{name}/trigger` returns `TriggerResponse` (unwrapped)
  - Conversation create and follow-up: `POST /api/butlers/{name}/conversations` and `POST /api/butlers/{name}/conversations/{conversation_id}/messages` return `text/event-stream`
  - Conversation Stop: canonical `POST /api/butlers/{name}/conversation-turns/{message_id}/cancel` (and the legacy conversation-scoped compatibility route) returns raw `ConversationCancelResponse`
- **AND** the frontend type layer accounts for these exceptions with dedicated response interfaces

#### Scenario: TypeScript mirror types
- **WHEN** the frontend imports from `frontend/src/api/types.ts`
- **THEN** `ApiResponse<T>`, `PaginatedResponse<T>`, `ErrorResponse`, `ErrorDetail`, and `PaginationMeta` are available as generic interfaces matching the backend Pydantic shapes



## dashboard-api — Cross-Butler Fan-Out


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. API schemas, owner authorization, unavailable/partial-source composition, historical identifiers and safe cross-domain reads. Goal reads use the single goal ledger. Domain fan-out remains for domain records. Retiring per-unit trigger/MCP proxy use requires its exact owner-approved implementation amendment; descriptive catalog data confers no broker grant. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Cross-Butler Fan-Out
The `DatabaseManager.fan_out()` method SHALL execute a SQL query concurrently across multiple butler databases, enabling cross-butler aggregate endpoints.

#### Scenario: Fan-out across all butlers
- **WHEN** `fan_out(query, args)` is called without `butler_names`
- **THEN** the query is executed concurrently on every registered butler pool via `asyncio.gather`
- **AND** results are returned as `dict[str, list[Record]]` keyed by butler name

#### Scenario: Fan-out with butler filter
- **WHEN** `fan_out(query, args, butler_names=["atlas"])` is called
- **THEN** only the specified butler(s) are queried

#### Scenario: Partial failure resilience
- **WHEN** one butler's query fails during fan-out
- **THEN** that butler's entry is an empty list in the result
- **AND** the error is logged as a warning
- **AND** successful results from other butlers are still returned

#### Scenario: Sessions cross-butler merge
- **WHEN** `GET /api/sessions` is called
- **THEN** fan-out queries every butler DB for sessions
- **AND** results are merged, sorted by `started_at DESC`, and paginated with correct cross-butler total count

#### Scenario: Sessions degraded pools are named, not rendered as an all-clear
- **WHEN** `GET /api/sessions` or `GET /api/sessions/aggregate` fans out across
  each butler's pool and one or more pools fail their query (a genuine error —
  the request still returns HTTP 200 with the rows/scalars from the pools that
  answered). `sessions` is a core table present in every schema, so a fan-out
  failure is always a genuine source fault (classify-before-flagging resolves
  to "flag"), never a legitimate table-absence
- **THEN** the list response includes `meta.sources_degraded: string[]` naming
  the dropped pools (following the fleet-wide degraded-envelope convention); the
  aggregate response carries the same list on its extensible `meta` bag. The
  field is absent/`null` when every queried pool answered
- **AND** the frontend sessions verdict opener SHALL NOT render the calm "No
  sessions failed in the last 24h" all-clear while a pool is degraded — it names
  the dropped pools inline as a clause that suppresses the all-clear line
- **AND** the sessions KPI strip SHALL name the dropped pools via a
  `SourceDegradedNote` rather than presenting the undercounted totals as
  window-true
- **AND** the sessions LIST surface SHALL consume `meta.sources_degraded` from
  its OWN keyset response — distinct from the aggregate-level flag the KPI strip
  and verdict opener read: the main table renders a `SourceDegradedNote` above
  the rows AND gates the "No sessions found" empty state; the pinned
  running/recent-failure strips name any dropped pool as a partial-fan-out note
  (separate from their whole-request error notes); and the session-activity
  stripe chart names the dropped pool above the bars AND gates its "No sessions
  in the selected window" empty state — a degraded partial page or degraded zero
  MUST NOT read as a complete list or a calm empty window

#### Scenario: Session detail splits not-found from source-degraded
- **WHEN** `GET /api/sessions/{id}` fans out the detail lookup and no reachable
  pool owns the id
- **THEN** the response is **404** ("Session not found") only when every queried
  pool answered (the id is genuinely unknown across all reachable schemas)
- **AND** the response is **503** — naming the unreachable pool(s) in `detail` —
  when one or more pools failed their query, because the session may live in a
  pool that could not be queried (a 404 there would fabricate a definitive
  absence from a partial fan-out)
- **AND** a found row wins over a degraded sibling pool: the endpoint returns
  **200** for a session that lives in a reachable pool even while another pool
  is down
- **AND** the frontend session drawer renders a distinct, named pool-down state
  for the 503 case, separate from the not-found state

#### Scenario: Fan-out failure reporting for degraded envelopes
- **WHEN** `fan_out_with_status(query, args)` is called
- **THEN** it returns `(results, failed_butler_names)` where every failed butler also has an empty-list entry in `results`
- **AND** callers that must surface a degraded-source flag use this instead of `fan_out()`, which discards the failed list



## dashboard-api — Session Trigger-Breakdown Degradation Is Distinct from Scalar Degradation


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. API schemas, owner authorization, unavailable/partial-source composition, historical identifiers and safe cross-domain reads. Goal reads use the single goal ledger. Domain fan-out remains for domain records. Retiring per-unit trigger/MCP proxy use requires its exact owner-approved implementation amendment; descriptive catalog data confers no broker grant. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Session Trigger-Breakdown Degradation Is Distinct from Scalar Degradation
When `GET /api/sessions/aggregate` receives `include_trigger_breakdown=true`, the API SHALL preserve the failed source list from the optional `GROUP BY trigger_source` fan-out in `data.trigger_breakdown_degraded_sources: string[]`. The existing `meta.sources_degraded` field SHALL continue to represent only failures of the scalar aggregate fan-out.

#### Scenario: Trigger breakdown loses a pool after a complete scalar aggregate
- **WHEN** the scalar aggregate answers from every queried pool but the opt-in trigger-breakdown query drops one or more pools
- **THEN** the response remains HTTP 200 with scalar counts from the complete scalar aggregate and trigger buckets from reachable pools
- **AND** `data.trigger_breakdown_degraded_sources` names the dropped trigger-breakdown pools
- **AND** `meta.sources_degraded` remains absent or empty

#### Scenario: Scalar and trigger-breakdown failures remain independently attributable
- **WHEN** either or both aggregate fan-outs drop pools
- **THEN** `meta.sources_degraded` names only pools dropped from the scalar aggregate fan-out
- **AND** `data.trigger_breakdown_degraded_sources` names only pools dropped from the trigger-breakdown fan-out
- **AND** the API SHALL NOT merge one failure list into the other

#### Scenario: Complete or unrequested trigger breakdown has no degraded sources
- **WHEN** the trigger breakdown is healthy or is not requested
- **THEN** `data.trigger_breakdown_degraded_sources` is an empty list



## dashboard-api — Cross-Butler Search Degraded-Source Honesty


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. API schemas, owner authorization, unavailable/partial-source composition, historical identifiers and safe cross-domain reads. Goal reads use the single goal ledger. Domain fan-out remains for domain records. Retiring per-unit trigger/MCP proxy use requires its exact owner-approved implementation amendment; descriptive catalog data confers no broker grant. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Cross-Butler Search Degraded-Source Honesty
`GET /api/search` returns four result groups: the per-butler `sessions` and `state` fan-outs across every butler DB, plus the shared-schema `entities` and `contacts` searches (`public.entities` / `relationship.entity_facts` on a single pool). A source that fails MUST NOT be silently zero-filled into a truthful-looking empty result. `sessions` and `state` are core tables present in every butler schema, so a fan-out failure is always a genuine transport/permission error (never a legitimately-absent schema) and is always reported. The `entities` / `contacts` groups apply classify-before-flagging: a legitimately-absent schema (a pre-migration DB, or a pool that never provisioned `public.entities` / `relationship.entity_facts`) is NOT a degraded source and yields a clean empty, whereas a genuine failure (dropped connection, timeout, permission) IS flagged. The response envelope SHALL carry `meta.sources_degraded` (the shared `ApiMeta` bag convention) naming every degraded source, so the finder renders a named note rather than a clean "no results".

#### Scenario: Healthy fan-out
- **WHEN** every butler's sessions/state query succeeds
- **THEN** `meta.sources_degraded` is absent and results are returned normally

#### Scenario: Partial per-butler failure
- **WHEN** one butler's sessions or state query fails during fan-out
- **THEN** that butler's rows are omitted but its name appears in `meta.sources_degraded`
- **AND** the endpoint still returns HTTP 200 with the surviving results

#### Scenario: Structural fan-out failure
- **WHEN** the whole sessions or state fan-out raises before any per-butler status is available
- **THEN** the affected source is flagged with the sentinel name (`"sessions"` / `"state"`) in `meta.sources_degraded`
- **AND** the endpoint returns HTTP 200 with empty results for that source rather than a 500 or a deceptive clean empty

#### Scenario: Shared-schema entity/contact failure is classified before flagging
- **WHEN** the `entities` or `contacts` shared-schema query fails because the table/schema is legitimately absent (a pre-migration DB, or a pool without `public.entities` / `relationship.entity_facts`)
- **THEN** that group returns an empty result and its name (`"entities"` / `"contacts"`) does NOT appear in `meta.sources_degraded`
- **AND WHEN** the same query fails for a genuine reason (dropped connection, timeout, permission)
- **THEN** the group name IS added to `meta.sources_degraded` so the failure never renders as a clean empty

#### Scenario: Finder names the degraded source
- **WHEN** `meta.sources_degraded` is non-empty
- **THEN** the command finder renders a `SourceDegradedNote` naming the source(s) and suppresses the plain "No results" empty copy so a half-down fleet never reads as a clean empty result



## dashboard-api — MCP Client Proxy


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. API schemas, owner authorization, unavailable/partial-source composition, historical identifiers and safe cross-domain reads. Goal reads use the single goal ledger. Domain fan-out remains for domain records. Retiring per-unit trigger/MCP proxy use requires its exact owner-approved implementation amendment; descriptive catalog data confers no broker grant. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: MCP Client Proxy
`src/butlers/api/deps.py` SHALL provide `MCPClientManager` for lazy FastMCP client connections to running butler MCP daemons. Write operations (state set/delete, schedule CRUD, triggers) are proxied through MCP to preserve the architectural constraint that only the butler mutates its own database.

#### Scenario: Lazy client connection
- **WHEN** `get_client(butler_name)` is called for the first time
- **THEN** a FastMCP SSE client is created, connected to `http://localhost:{port}/sse`, and cached
- **AND** subsequent calls return the cached client if still connected

#### Scenario: Client reconnection
- **WHEN** a cached client is found to be disconnected
- **THEN** the old client is closed and a new connection is established

#### Scenario: Butler unreachable
- **WHEN** a butler's MCP server is not running or connection fails
- **THEN** `ButlerUnreachableError` is raised with the butler name and cause

#### Scenario: Tool invocation proxy
- **WHEN** the dashboard calls `POST /api/butlers/{name}/mcp/call` with `{ "tool_name": "...", "arguments": {...} }`
- **THEN** the backend connects to the butler via MCP and calls `client.call_tool(tool_name, arguments)`
- **AND** the MCP result is parsed (JSON when possible, raw text as fallback) and returned in an `MCPToolCallResponse` envelope

#### Scenario: State write via MCP
- **WHEN** `PUT /api/butlers/{name}/state/{key}` is called
- **THEN** the backend calls the butler's MCP `state_set` tool with `{ "key": key, "value": value }`
- **AND** an audit log entry is recorded on success or failure

#### Scenario: State write/delete to a group-gated butler returns 409
- **WHEN** `PUT` or `DELETE /api/butlers/{name}/state/{key}` targets a butler whose `state` core group is not enabled (e.g. switchboard), so `state_set` / `state_delete` are not registered and `client.call_tool` raises `ToolError("Unknown tool: ...")`
- **THEN** the endpoint returns **409** (not 500), with `detail` naming the missing tool and the disabled core group (e.g. `tool 'state_set' not available on butler 'switchboard' (core group 'state' not enabled)`) — a structural configuration state, not a transient fault or retryable condition
- **AND** a genuine `ToolError` raised by an ENABLED `state_set` / `state_delete` tool is NOT masked — it still surfaces as a 5xx

#### Scenario: Module toggle to a group-gated butler returns 409
- **WHEN** `PUT /api/butlers/{name}/module-states/{module}/enabled` targets a butler whose `module_mgmt` core group is not enabled (e.g. finance, relationship), so `module.set_enabled` is not registered and the call raises `ToolError("Unknown tool: ...")`
- **THEN** the endpoint returns **409** (not 500), with `detail` naming the missing tool and the disabled core group (`... (core group 'module_mgmt' not enabled)`)
- **AND** a genuine `ToolError` from an ENABLED `module.set_enabled` tool still surfaces as a 5xx

#### Scenario: Module runtime states degrade gracefully when module_mgmt is gated
- **WHEN** `GET /api/butlers/{name}/module-states` targets a butler whose `module_mgmt` core group is not enabled, so `module.states` is not registered and the call raises `ToolError("Unknown tool: ...")`
- **THEN** the endpoint returns **200** with an empty `data` list and `meta.module_states_unavailable: true` (following the fleet-wide degraded-envelope convention), rather than the 502 a raw tool-absent error would produce
- **AND** a genuine `ToolError` from an ENABLED `module.states` tool still surfaces as a 502 (not a truthful-looking empty 200)
- **AND** frontends SHALL gate the module list on `meta.module_states_unavailable` and render an "unavailable" note instead of an empty module list

#### Scenario: Schedule CRUD via MCP
- **WHEN** schedule create/update/delete/toggle endpoints are called
- **THEN** the backend proxies to the butler's `schedule_create`, `schedule_update`, `schedule_delete`, or `schedule_toggle` MCP tools respectively
- **AND** each operation records an audit log entry



## dashboard-api — Session List Owner-Cancellation Discriminator


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. API schemas, owner authorization, unavailable/partial-source composition, historical identifiers and safe cross-domain reads. Goal reads use the single goal ledger. Domain fan-out remains for domain records. Retiring per-unit trigger/MCP proxy use requires its exact owner-approved implementation amendment; descriptive catalog data confers no broker grant. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Session List Owner-Cancellation Discriminator

The session-list API SHALL include `cancelled_by_owner: boolean` on every
`SessionSummary` returned by `GET /api/sessions` and `GET
/api/butlers/{name}/sessions`.
The field SHALL be true only when the row is terminally unsuccessful and its
stored outcome is the exact canonical owner-cancellation marker written by
`Spawner.cancel_session()`. List responses SHALL NOT expose the raw `error`
string to derive this presentation state.

#### Scenario: Canonical owner cancellation is projected without error text

- **WHEN** a session row has `success = false` and the canonical
  owner-cancellation outcome
- **THEN** both session-list routes return `cancelled_by_owner: true` for that
  summary
- **AND** neither response item contains an `error` field

#### Scenario: Generic failure remains distinct

- **WHEN** a terminal unsuccessful session has any error outcome other than
  the canonical owner-cancellation marker
- **THEN** both session-list routes return `cancelled_by_owner: false`

#### Scenario: Non-terminal session is never labelled cancelled

- **WHEN** a session is non-terminal (`success = null`)
- **THEN** both session-list routes return `cancelled_by_owner: false`
- **AND** their existing pagination envelopes and semantics remain unchanged



## dashboard-api — Cross-Domain API Route Rules


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. API schemas, owner authorization, unavailable/partial-source composition, historical identifiers and safe cross-domain reads. Goal reads use the single goal ledger. Domain fan-out remains for domain records. Retiring per-unit trigger/MCP proxy use requires its exact owner-approved implementation amendment; descriptive catalog data confers no broker grant. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Cross-Domain API Route Rules

The API SHALL publish its complete route inventory through the FastAPI-generated OpenAPI schema (`/openapi.json`); each route's request and response contract is owned by the per-domain requirement or capability spec that names it. This requirement carries only the cross-domain route rules below.

#### Scenario: Single-session detail is served only by the cross-butler route
- **WHEN** a client needs the detail of one session
- **THEN** it calls the cross-butler fan-out `GET /api/sessions/{id}`, which resolves pinned rows and deep links without a `?butler=` hint because session ids are globally unique
- **AND** no butler-scoped session detail route is mounted; `GET /api/butlers/{name}/sessions` serves only the butler-scoped list

#### Scenario: Ingestion-events list uses the cursor envelope and channels filter
- **WHEN** `GET /api/ingestion/events` is called with `limit` and an optional opaque `cursor` taken from the preceding response
- **THEN** the response uses the cursor envelope `{ "data": T[], "meta": { "next_cursor": string | null, "has_more": boolean } }` and returns no `total` or `offset`
- **AND** `channels` is the primary comma-separated source-channel filter
- **AND** there is no single-value channel alias; an empty `channels=` applies no channel filter

#### Scenario: Schedule Execution Semantics
- **WHEN** the dashboard displays or interprets schedule data
- **THEN** `Schedule.source` describes the schedule origin (`toml` for TOML-defined, `db` for dashboard-created); it is NOT the execution mode
- **AND** runtime-mode schedules (those with a `prompt`) execute through `spawner.trigger(..., trigger_source="schedule:<task-name>")` and correlate with `sessions` rows
- **AND** native-mode schedules (those with `dispatch_mode = "job"` and `job_name`) execute deterministic Python jobs directly and may not create `sessions` rows
- **AND** the dashboard treats schedule status fields (`enabled`, `next_run_at`, `last_run_at`) as authoritative regardless of execution mode
- **AND** schedule failures for both execution modes surface through `GET /api/issues` as `scheduled_task_failure:<schedule-name>`

#### Scenario: Dashboard conversation stream and Stop semantics
- **WHEN** the dashboard submits a user turn
- **THEN** the API opens durable control keyed by the immutable `message_id` before external ingress, and only the caller with the `dispatch` claim may invoke `ingest.v1`
- **AND** a caller observing `accepted` observes the original request, while `pending` or `cancelling` yields `INGEST_IN_PROGRESS` plus `done` and never creates a replay
- **AND** confirmed cancellation yields `SESSION_CANCELLED`; an unprovable recovered predecessor yields `TURN_OUTCOME_UNKNOWN`; both suppress automatic replay
- **AND** the raw `ConversationCancelResponse` documents exactly one truthful Stop result (`cancelled`, `already_finished`, or unconfirmed), while non-2xx failures retain `ErrorResponse`

#### Scenario: Notifications degraded source is named, not rendered as an all-clear
- **WHEN** `GET /api/notifications` or `GET /api/notifications/stats` returns
  HTTP 200 but the Switchboard notifications source was unreachable (so the
  counts are zero placeholders and the list page is empty)
- **THEN** the response carries `source_available: false` (following the
  fleet-wide degraded-envelope convention); the field is absent or `true` when
  the source answered
- **AND** the frontend notifications page SHALL NOT render the fabricated zeros
  as a truthful tally — the stats tiles show an em-dash (not a green `0.0%`
  failure rate) and the feed shows a named `SourceDegradedNote` naming the
  unreachable source rather than the calm "No notifications found" empty state
- **AND** a reachable-but-empty source (`source_available` absent or `true`
  with genuine zeros) keeps its honest zeros and empty state

#### Scenario: Manual retry re-sends a failed notification and links the new attempt
- **WHEN** `POST /api/notifications/{id}/retry` is called on a notification
  whose stored `status` is `failed`
- **THEN** the backend re-invokes delivery in-process (the same
  approval-push-runtime pattern the dashboard-API process already uses to
  call `deliver()` without a `switchboard_client` MCP connection), using the
  envelope persisted at delivery time (`metadata.notify_request`) or, for
  older rows that predate it, a synthetic envelope built from the row's own
  `channel`/`recipient`/`message` columns
- **AND** the original notification is flipped to `status = 'read'` with a
  `metadata.retried_to` marker pointing at the new attempt's id, regardless
  of whether the new attempt itself succeeds or fails again — a human has
  acted on it either way, and a retry that fails again is its own new,
  independently actionable row rather than a reason to leave the original
  stuck in `failed`
- **AND** the response reports the new attempt's own `status` (`sent` or
  `failed`) and `error`, never a fabricated success for the original
- **AND** a best-effort `public.attention_ledger` event is recorded
  (`source="notify"`, `outcome="delivered"` or `"failed"`,
  `notification_ref` set to the new attempt's id) so the manual action is
  traceable the same way an automatic notify() outcome is
- **AND** `GET /api/notifications?status=terminal_failed` and the aggregate
  `failed` count in `GET /api/notifications/stats` no longer include the
  original row afterward, since it is no longer `status = 'failed'`
- **AND** `effective_status` on the original row reports `"retried"` (not
  the generic `"read"`) so the UI communicates what happened, not just that
  it was acknowledged

#### Scenario: Manual retry rejects a non-failed notification
- **WHEN** `POST /api/notifications/{id}/retry` or `.../escalate` is called
  on a notification whose stored `status` is not `failed`
- **THEN** the endpoint returns HTTP 409 without attempting delivery
- **AND** HTTP 404 is returned when `{id}` does not exist, and HTTP 503 when
  the Switchboard pool is unavailable

#### Scenario: Concurrent or replayed retry/escalate never double-delivers
- **WHEN** two `POST .../retry` (or two `.../escalate`) requests for the same
  `{id}` race — a double-click, two open tabs, or a client resending after a
  perceived timeout — and both observe `status = 'failed'` on their initial
  read
- **THEN** immediately before invoking real delivery, the backend atomically
  claims the row with a single conditional `UPDATE notifications SET status
  = 'read' WHERE id = $1 AND status = 'failed' RETURNING *`; only the first
  request's `UPDATE` can match a `'failed'` row, so exactly one request
  proceeds to redeliver and the loser's claim affects zero rows
- **AND** the losing request returns HTTP 409 without invoking delivery — the
  user is never sent the notification twice
- **AND** because the claim (not delivery success) is what leaves `'failed'`
  status, a delivery-adjacent failure after the claim — including
  `_finalize_manual_action`'s own metadata-merge write failing after a
  successful send — cannot leave the row re-claimable: a subsequent client
  retry after such a failure also observes a non-`'failed'` status and gets
  HTTP 409, never a second real send. The tradeoff is a possible orphaned row
  (already `status = 'read'` but missing its `retried_to`/`escalated_to`
  forward-link marker) rather than a duplicate delivery

#### Scenario: Manual escalate re-sends on the owner's alternate channel
- **WHEN** `POST /api/notifications/{id}/escalate` is called on a failed
  `telegram` or `email` notification
- **THEN** the backend swaps to the other channel (`telegram` -> `email`,
  `email` -> `telegram`) and resolves the owner's contact for it via
  `resolve_owner_entity_info` — the same owner-credential lookup the
  dashboard's connector actions already use for owner-directed delivery
- **AND** HTTP 422 is returned, without attempting delivery, when the
  channel is neither `telegram` nor `email` (no alternate-channel resolver
  is wired for other channels), or when the owner has no contact configured
  for the alternate channel
- **AND** on success the original notification is flipped to `status =
  'read'` with a `metadata.escalated_to` marker, following the same
  forward-link and ledger-recording contract as manual retry, and
  `effective_status` reports `"escalated"`

#### Scenario: Approvals degraded pools are named, not rendered as an empty queue
- **WHEN** `GET /api/approvals` or `GET /api/approvals/history` fans out across
  each butler's pool and one or more pools fail their query (a genuine error —
  the request still returns HTTP 200 with the summaries from the pools that
  answered)
- **THEN** the response includes `meta.sources_degraded: string[]` naming the
  dropped pools (following the fleet-wide degraded-envelope convention); the
  field is absent or empty when every queried pool answered
- **AND** the frontend approvals verdict opener SHALL NOT render the calm "No
  approvals waiting." all-clear while a pool is degraded — it names the dropped
  pools inline as a clause that suppresses the all-clear line
- **AND** the approvals queue rail SHALL NOT render the "No pending approvals."
  empty state as an all-clear while a pool is degraded — it names the dropped
  pools via a `SourceDegradedNote` (in place of the empty state when zero rows
  survived, above the rows when some did)
- **AND** a reachable queue with `meta.sources_degraded` absent or empty keeps
  its honest empty state and calm verdict



## dashboard-visibility — Cross-Butler Session Explorer


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. historical session/trace evidence, redaction, null provenance, navigation, filtering, cost and request-trace reconciliation. New goal/invocation doors use stored foreign keys only. Old session IDs remain historical read-only evidence and are never guessed into goal identities. Session Explorer geometry can retire only with a complete goal/trace navigation migration. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


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



## dashboard-visibility — Sessions Verdict Withholds Partial Trigger Attribution


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. historical session/trace evidence, redaction, null provenance, navigation, filtering, cost and request-trace reconciliation. New goal/invocation doors use stored foreign keys only. Old session IDs remain historical read-only evidence and are never guessed into goal identities. Session Explorer geometry can retire only with a complete goal/trace navigation migration. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


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



## dashboard-visibility — Session Detail Drawer


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. historical session/trace evidence, redaction, null provenance, navigation, filtering, cost and request-trace reconciliation. New goal/invocation doors use stored foreign keys only. Old session IDs remain historical read-only evidence and are never guessed into goal identities. Session Explorer geometry can retire only with a complete goal/trace navigation migration. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


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



## dashboard-visibility — Session Detail Full Page


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. historical session/trace evidence, redaction, null provenance, navigation, filtering, cost and request-trace reconciliation. New goal/invocation doors use stored foreign keys only. Old session IDs remain historical read-only evidence and are never guessed into goal identities. Session Explorer geometry can retire only with a complete goal/trace navigation migration. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


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



## dashboard-visibility — Unified Timeline


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. historical session/trace evidence, redaction, null provenance, navigation, filtering, cost and request-trace reconciliation. New goal/invocation doors use stored foreign keys only. Old session IDs remain historical read-only evidence and are never guessed into goal identities. Session Explorer geometry can retire only with a complete goal/trace navigation migration. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


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



## dashboard-visibility — Notification Audit Trail


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. historical session/trace evidence, redaction, null provenance, navigation, filtering, cost and request-trace reconciliation. New goal/invocation doors use stored foreign keys only. Old session IDs remain historical read-only evidence and are never guessed into goal identities. Session Explorer geometry can retire only with a complete goal/trace navigation migration. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


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



## dashboard-visibility — Cross-Surface Navigation and Linking


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. historical session/trace evidence, redaction, null provenance, navigation, filtering, cost and request-trace reconciliation. New goal/invocation doors use stored foreign keys only. Old session IDs remain historical read-only evidence and are never guessed into goal identities. Session Explorer geometry can retire only with a complete goal/trace navigation migration. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


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



## dashboard-visibility — End-to-End Request Trace Story


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. historical session/trace evidence, redaction, null provenance, navigation, filtering, cost and request-trace reconciliation. New goal/invocation doors use stored foreign keys only. Old session IDs remain historical read-only evidence and are never guessed into goal identities. Session Explorer geometry can retire only with a complete goal/trace navigation migration. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


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



## dashboard-visibility — Data Model Contracts for Visibility Surfaces


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. historical session/trace evidence, redaction, null provenance, navigation, filtering, cost and request-trace reconciliation. New goal/invocation doors use stored foreign keys only. Old session IDs remain historical read-only evidence and are never guessed into goal identities. Session Explorer geometry can retire only with a complete goal/trace navigation migration. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


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



## dashboard-visibility — Machine-class Timeline presentation


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. historical session/trace evidence, redaction, null provenance, navigation, filtering, cost and request-trace reconciliation. New goal/invocation doors use stored foreign keys only. Old session IDs remain historical read-only evidence and are never guessed into goal identities. Session Explorer geometry can retire only with a complete goal/trace navigation migration. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


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



## dashboard-visibility — Protected effective-prompt session door


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. historical session/trace evidence, redaction, null provenance, navigation, filtering, cost and request-trace reconciliation. New goal/invocation doors use stored foreign keys only. Old session IDs remain historical read-only evidence and are never guessed into goal identities. Session Explorer geometry can retire only with a complete goal/trace navigation migration. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


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



## dashboard-conversations — Conversation Data Model


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. immutable message/thread identity, owner-authenticated cancellation and recovery, persistence, search, stream observation, safe citations and honest terminal receipts. The new input is admitted deterministically into a goal or its actual named terminal action. Classifier route lanes and provider resume execution retire at M6 under D3/D9; durable anchors, cancellation, reply idempotency and historical data remain. The full pre-admission and citation contracts below govern the successor; active terminal-recovery and continuity owners retain integration authority. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Conversation Data Model

The `public.dashboard_conversations` table SHALL store conversation thread metadata. Each conversation belongs to exactly one butler and progresses through a defined lifecycle.

#### Scenario: Conversation table schema

- **WHEN** the migration creates the `public.dashboard_conversations` table
- **THEN** the table SHALL contain the following columns:
  - `id` (UUID7, primary key) — time-ordered unique identifier
  - `butler_name` (TEXT, NOT NULL) — the butler this conversation belongs to
  - `title` (TEXT, nullable): auto-generated or user-edited title; the API always populates it from the first user message (no DB-level default)
  - `status` (TEXT, NOT NULL, default `'active'`) — one of `active`, `archived`
  - `created_at` (TIMESTAMPTZ, NOT NULL, default `now()`) — when the conversation was started
  - `updated_at` (TIMESTAMPTZ, NOT NULL, default `now()`) — when the last message was added
  - `message_count` (INTEGER, NOT NULL, default `0`) — denormalized count of messages
  - `routed_butler` (TEXT, nullable): the butler this conversation's first message was routed to by Switchboard classification; NULL for pinned per-butler conversations (already deterministic) and for classification-routed conversations that haven't routed yet (e.g. a bug-lane report, which never targets a domain butler)
  - `source_channel` (TEXT, NOT NULL, default `'dashboard'`): origin channel for the conversation (`'dashboard'`, `'telegram'`, `'email'`, ...); every pre-existing row backfills as `'dashboard'`
  - `source_thread_identity` (TEXT, nullable): legacy anchor key, written with the same value as `external_conversation_id` so both unique indexes stay congruent; NULL for dashboard-created rows, which are already anchored 1:1 on `id`
  - `external_conversation_id` (TEXT, nullable): the channel-stable conversation key from `event.external_conversation_id` (for example `telegram:<chat_id>`), distinct from any per-message reply target; NULL for dashboard-created rows
  - `provider_session_id` (TEXT, nullable): the most recent provider-native session/resume handle minted for this conversation
  - `provider_runtime_type` (TEXT, nullable): which runtime adapter type minted `provider_session_id` — a handle is only resumable by the same adapter type
  - `provider_session_updated_at` (TIMESTAMPTZ, nullable): when `provider_session_id` was last refreshed; governs TTL-based resume eligibility

#### Scenario: Conversation table indexes

- **WHEN** the migration creates indexes
- **THEN** a composite index on `(butler_name, status, updated_at DESC)` SHALL exist for listing active conversations per butler
- **AND** a composite index on `(butler_name, updated_at DESC)` SHALL exist for chronological listing
- **AND** a unique index on `(butler_name, source_channel, source_thread_identity)` WHERE `source_thread_identity IS NOT NULL` SHALL exist
- **AND** a partial unique index on `(butler_name, source_channel, external_conversation_id)` WHERE `external_conversation_id IS NOT NULL` SHALL exist and SHALL be the anchor upsert target, so concurrent ingress for the same conversation converges on one anchor row

#### Scenario: Legacy per-message Telegram anchors collapse reversibly

- **WHEN** the conversation-identity migration (core_265) finds several Telegram bot anchors for one butler and chat, whether keyed by a legacy `<chat_id>:<message_id>`, a bare `<chat_id>`, or the earlier stable `telegram:<chat_id>`
- **THEN** it SHALL keep one anchor per chat, choosing the row with the newest provider-session handle, and key it `telegram:<chat_id>` in both identity columns
- **AND** it SHALL re-link `dashboard_messages` and `dashboard_conversation_turns` onto the survivor before deleting the other rows, and recount the survivor's `message_count`
- **AND** Telegram user-client and WhatsApp anchors SHALL gain the `telegram:` and `whatsapp:` namespaces their connectors emit, collapsing the same way when a bare and an already namespaced identity name one chat, and every other anchored row SHALL copy its identity verbatim
- **AND** the transform SHALL run once, on the first schema to install the column, because core revisions replay per schema against shared `public` tables
- **AND** the rollout SHALL stop every conversation-anchor writer before the upgrade; no mixed-version compatibility trigger exists

#### Scenario: Identity split downgrade restores without reverting later activity

- **WHEN** the last schema at or past core_265 downgrades
- **THEN** it SHALL restore the deleted anchors, their message and turn links, and every original identity from private `core_265_*` snapshots
- **AND** it SHALL keep each survivor's other post-upgrade columns (title, status, provider handle) and recount its `message_count`
- **AND** it SHALL verify every snapshotted anchor and link was restored, and SHALL otherwise fail the downgrade with nothing changed and the snapshots kept
- **AND** only a complete restore SHALL drop the column, the index, and the snapshot tables, so a later upgrade snapshots afresh and refuses to reuse a leftover snapshot

#### Scenario: Sticky routed_butler stamping

- **WHEN** a classification-routed (Switchboard-addressed) conversation's message is submitted and Switchboard's triage produces a `route_to` decision with a target butler, and the conversation has no `routed_butler` yet
- **THEN** `routed_butler` is set to that target butler
- **AND** a later `route_to` decision for the same conversation (e.g. from a follow-up that still goes through classification) does NOT overwrite an already-set `routed_butler` — the first successful route wins



## dashboard-conversations — Message Data Model


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. immutable message/thread identity, owner-authenticated cancellation and recovery, persistence, search, stream observation, safe citations and honest terminal receipts. The new input is admitted deterministically into a goal or its actual named terminal action. Classifier route lanes and provider resume execution retire at M6 under D3/D9; durable anchors, cancellation, reply idempotency and historical data remain. The full pre-admission and citation contracts below govern the successor; active terminal-recovery and continuity owners retain integration authority. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Message Data Model

The `public.dashboard_messages` table SHALL store individual messages within a conversation, including both user inputs and assistant responses with full attribution. Structured citations and per-message butler attribution SHALL be nullable so legacy and non-session-authored rows remain truthful without backfilled guesses.

ID: REQ-dashboard-conversations-009
Source: dashboard-conversations § Message Data Model and Conversation Reply Channel; heart-and-soul/security.md § Session Sandboxing; design.md Decisions 2-4
Scope: v1-mandatory

#### Scenario: Message table schema

- **WHEN** the migration creates the `public.dashboard_messages` table
- **THEN** the table SHALL contain the following columns:
  - `id` (UUID, primary key) — immutable dashboard user-turn identity; dashboard UI generates it before submission and reuses it for retry and Stop, while server generation remains legacy API compatibility only
  - `conversation_id` (UUID, NOT NULL, FK to `public.dashboard_conversations.id` ON DELETE CASCADE) — parent conversation
  - `role` (TEXT, NOT NULL) — one of `user`, `assistant`
  - `content` (TEXT, NOT NULL) — message text (markdown for assistant responses)
  - `created_at` (TIMESTAMPTZ, NOT NULL, default `now()`) — when the message was created
  - `session_id` (UUID, nullable) — FK to the butler's `sessions.id` for assistant responses; NULL for user messages
  - `model_name` (TEXT, nullable) — the LLM model used for this response; NULL for user messages
  - `input_tokens` (INTEGER, nullable) — tokens consumed reading input; NULL for user messages
  - `output_tokens` (INTEGER, nullable) — tokens produced in response; NULL for user messages
  - `duration_ms` (INTEGER, nullable) — response generation time in milliseconds; NULL for user messages
  - `tool_calls` (JSONB, nullable) — array of tool calls made during response; NULL for user messages
  - `error` (TEXT, nullable) — error message if the response failed; NULL on success and for user messages
  - `request_id` (UUID, nullable) — the Switchboard request_id for lineage; NULL for user messages
  - `sources` (JSONB, nullable) — deprecated compatibility array of source strings named by an answer-lane `conversation_reply` call; NULL when omitted and retained only through the bounded migration window
  - `citations` (JSONB, nullable) — server-normalized array of structured citation objects; NULL for user messages and assistant replies that did not pass `sources`
  - `routed_butler` (TEXT, nullable) — server-derived butler that authored this assistant message through `conversation_reply`; NULL for user, legacy, and non-session-authored messages

#### Scenario: Message table indexes

- **WHEN** the migration creates indexes
- **THEN** an index on `(conversation_id, created_at ASC)` SHALL exist for chronological message listing within a conversation



## dashboard-conversations — Conversation List API


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. immutable message/thread identity, owner-authenticated cancellation and recovery, persistence, search, stream observation, safe citations and honest terminal receipts. The new input is admitted deterministically into a goal or its actual named terminal action. Classifier route lanes and provider resume execution retire at M6 under D3/D9; durable anchors, cancellation, reply idempotency and historical data remain. The full pre-admission and citation contracts below govern the successor; active terminal-recovery and continuity owners retain integration authority. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Conversation List API

The dashboard API SHALL provide an endpoint to list conversations for a butler with pagination and filtering.

#### Scenario: List active conversations

- **WHEN** `GET /api/butlers/{name}/conversations?status=active&limit=20&offset=0` is called
- **THEN** conversations are returned ordered by `updated_at DESC` with pagination metadata
- **AND** each conversation includes `id`, `title`, `status`, `created_at`, `updated_at`, `message_count`, `latest_assistant_reply_at`

#### Scenario: latest_assistant_reply_at reflects the most recent assistant message

- **WHEN** a conversation list entry is built
- **THEN** `latest_assistant_reply_at` is the `MAX(created_at)` of that conversation's `public.dashboard_messages` rows where `role = 'assistant'`, or `null` if it has none
- **AND** this field is the freshness signal for detecting a new reply because `conversation_reply_create` persists a new assistant message before the routed session's accounting is known

#### Scenario: List all conversations

- **WHEN** `GET /api/butlers/{name}/conversations?status=all` is called
- **THEN** both active and archived conversations are returned

#### Scenario: Default status filter

- **WHEN** `GET /api/butlers/{name}/conversations` is called without a `status` parameter
- **THEN** only `active` conversations are returned



## dashboard-conversations — Conversation Creation


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. immutable message/thread identity, owner-authenticated cancellation and recovery, persistence, search, stream observation, safe citations and honest terminal receipts. The new input is admitted deterministically into a goal or its actual named terminal action. Classifier route lanes and provider resume execution retire at M6 under D3/D9; durable anchors, cancellation, reply idempotency and historical data remain. The full pre-admission and citation contracts below govern the successor; active terminal-recovery and continuity owners retain integration authority. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Conversation Creation

Starting a new conversation SHALL create a conversation record and send the first user message through the Switchboard ingestion pipeline.

#### Scenario: Create conversation with first message

- **WHEN** `POST /api/butlers/{name}/conversations` is called with `{ "message": "Hello butler" }` and an optional `page_context`
- **THEN** a new conversation row is inserted in `public.dashboard_conversations` with `butler_name = {name}`, `status = 'active'`, and a default title
- **AND** a user message row is inserted in `public.dashboard_messages` **before** Switchboard submission is attempted; the dashboard UI MUST provide one immutable UUID as `message_id` and reuse it for every retry and Stop of that user turn (API callers that omit it are compatibility-only and cannot offer pre-SSE Stop)
- **AND** the message is submitted to the Switchboard's `ingest` MCP tool as an `ingest.v1` envelope with `source.channel = "dashboard"`, `source.provider = "internal"`, `source.endpoint_identity = "dashboard:web:{conversation_id}"`
- **AND** the response is streamed back via SSE on the same request (see SSE Streaming requirement)
- **AND** the response includes the `conversation_id` in the initial SSE event

#### Scenario: Auto-generated title

- **WHEN** a conversation is created
- **THEN** the title is set to the first 80 characters of the first user message, truncated at word boundary with ellipsis if needed

#### Scenario: Retry initial conversation before its SSE response is received

- **WHEN** a client retries `POST /api/butlers/{name}/conversations` with the same `message_id` after losing the initial SSE response
- **THEN** the API SHALL reuse that message's existing conversation rather than insert a second conversation or user-message row
- **AND** the retried envelope SHALL retain the original `source.endpoint_identity` and `event.external_event_id`
- **AND** a request that reuses a `message_id` for another butler, role, or message content SHALL return `409` with `code: "MESSAGE_ID_CONFLICT"`



## dashboard-conversations — Continue Conversation


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. immutable message/thread identity, owner-authenticated cancellation and recovery, persistence, search, stream observation, safe citations and honest terminal receipts. The new input is admitted deterministically into a goal or its actual named terminal action. Classifier route lanes and provider resume execution retire at M6 under D3/D9; durable anchors, cancellation, reply idempotency and historical data remain. The full pre-admission and citation contracts below govern the successor; active terminal-recovery and continuity owners retain integration authority. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Continue Conversation

Sending a follow-up message in an existing conversation SHALL preserve the thread context.

#### Scenario: Send follow-up message

- **WHEN** `POST /api/butlers/{name}/conversations/{conversation_id}/messages` is called with `{ "message": "Follow up question" }` and an optional `page_context`
- **THEN** a user message row is inserted in `public.dashboard_messages`
- **AND** the message is submitted to the Switchboard's `ingest` MCP tool as an `ingest.v1` envelope with the same `endpoint_identity` as the original conversation and `event.external_thread_id = {conversation_id}`
- **AND** the envelope's `payload.normalized_text` includes prior conversation context (last N messages as summarized context, configurable, default last 5 exchange pairs)
- **AND** the response is streamed back via SSE
- **AND** `updated_at` and `message_count` on the conversation are updated

#### Scenario: Continue archived conversation

- **WHEN** a message is sent to a conversation with `status = 'archived'`
- **THEN** the conversation status is changed to `active` before processing
- **AND** the message is processed normally

#### Scenario: Continue conversation for wrong butler

- **WHEN** `POST /api/butlers/{name}/conversations/{conversation_id}/messages` is called but the conversation belongs to a different butler
- **THEN** a 404 response with `code: "CONVERSATION_NOT_FOUND"` is returned



## dashboard-conversations — Durable Dashboard Turn Control


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. immutable message/thread identity, owner-authenticated cancellation and recovery, persistence, search, stream observation, safe citations and honest terminal receipts. The new input is admitted deterministically into a goal or its actual named terminal action. Classifier route lanes and provider resume execution retire at M6 under D3/D9; durable anchors, cancellation, reply idempotency and historical data remain. The full pre-admission and citation contracts below govern the successor; active terminal-recovery and continuity owners retain integration authority. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Durable Dashboard Turn Control

Each dashboard UI user message SHALL have one durable control record keyed by
its immutable `message_id`. It makes Stop and retry behavior truthful across
the API, Switchboard, target butler, and route-inbox recovery boundaries.

#### Scenario: Open the durable turn before external ingress

- **WHEN** the dashboard API persists a dashboard UI user message
- **THEN** it opens or loads that message's durable turn before it returns its
  SSE response or calls external `ingest.v1`
- **AND** the same `message_id` is used as `event.external_event_id`
- **AND** Stop can address that turn even before a newly-created conversation
  has delivered `conversation_id` over SSE

#### Scenario: Only one caller may cross the ingress boundary

- **WHEN** one or more callers submit the same `message_id`
- **THEN** exactly one caller whose durable ingress claim is `dispatch` MAY
  invoke `ingest.v1`
- **AND** a caller that observes `accepted` SHALL observe the original request
  and SHALL NOT invoke `ingest.v1` again
- **AND** a caller that observes `pending` or `cancelling` SHALL receive SSE
  `error {code: "INGEST_IN_PROGRESS"}` followed by `done`, retain the same
  logical message, and SHALL NOT offer automatic replay
- **AND** a caller that observes `cancelled` SHALL receive `SESSION_CANCELLED`
  followed by `done`
- **AND** a caller that observes `ambiguous` SHALL receive
  `TURN_OUTCOME_UNKNOWN` followed by `done` and SHALL NOT automatically replay
  the turn
- **AND** a durable `retryable_error` permits a later retry with the same
  immutable `message_id`; a deterministic rejected submission remains a
  rejection rather than a new logical message

#### Scenario: Message-scoped Stop is canonical

- **WHEN** the dashboard calls `POST
  /api/butlers/{name}/conversation-turns/{message_id}/cancel`
- **THEN** the API returns the raw typed `ConversationCancelResponse` with
  HTTP 200 and exactly one truthful domain outcome: `cancelled`,
  `already_finished`, or unconfirmed cancellation described by `message`
- **AND** `{name}` retains the established butler-route namespace, while
  `message_id` is the exact durable control key and not a second target selector
- **AND** `POST /api/butlers/{name}/conversations/{conversation_id}/cancel`
  is compatibility-only; dashboard UI clients SHALL NOT use it as their normal
  Stop path

#### Scenario: Dashboard route recovery never infers a safe replay

- **WHEN** a recovery worker claims a stale `processing` `route_inbox` row
  sourced from a durable dashboard turn
- **THEN** it SHALL reconcile the predecessor as unprovable and mark the turn
  ambiguous rather than automatically replaying it
- **AND** it MAY still address any exact, durably registered predecessor
  session with Stop
- **AND** ordinary non-dashboard route recovery retains its normal replay
  behavior subject to its route-inbox ownership lease



## dashboard-conversations — Conversation Lifecycle Management


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. immutable message/thread identity, owner-authenticated cancellation and recovery, persistence, search, stream observation, safe citations and honest terminal receipts. The new input is admitted deterministically into a goal or its actual named terminal action. Classifier route lanes and provider resume execution retire at M6 under D3/D9; durable anchors, cancellation, reply idempotency and historical data remain. The full pre-admission and citation contracts below govern the successor; active terminal-recovery and continuity owners retain integration authority. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Conversation Lifecycle Management

The dashboard API SHALL allow operators to archive, unarchive, and rename conversations.

#### Scenario: Archive conversation

- **WHEN** `PATCH /api/butlers/{name}/conversations/{conversation_id}` is called with `{ "status": "archived" }`
- **THEN** the conversation status is set to `archived`

#### Scenario: Unarchive conversation

- **WHEN** `PATCH /api/butlers/{name}/conversations/{conversation_id}` is called with `{ "status": "active" }`
- **THEN** the conversation status is set to `active`

#### Scenario: Rename conversation

- **WHEN** `PATCH /api/butlers/{name}/conversations/{conversation_id}` is called with `{ "title": "New title" }`
- **THEN** the conversation title is updated

#### Scenario: Update non-existent conversation

- **WHEN** `PATCH /api/butlers/{name}/conversations/{conversation_id}` is called for a conversation that does not exist or belongs to a different butler
- **THEN** a 404 response with `code: "CONVERSATION_NOT_FOUND"` is returned



## dashboard-conversations — Conversation Messages List


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. immutable message/thread identity, owner-authenticated cancellation and recovery, persistence, search, stream observation, safe citations and honest terminal receipts. The new input is admitted deterministically into a goal or its actual named terminal action. Classifier route lanes and provider resume execution retire at M6 under D3/D9; durable anchors, cancellation, reply idempotency and historical data remain. The full pre-admission and citation contracts below govern the successor; active terminal-recovery and continuity owners retain integration authority. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Conversation Messages List

The dashboard API SHALL retrieve the full message history for a conversation, including the same canonical answer provenance that completes over SSE.

ID: REQ-dashboard-conversations-012
Source: dashboard-conversations § Conversation Messages List and Conversation Pydantic Response Models; RFC 0007 § API Surface; design.md Decisions 3-4
Scope: v1-mandatory

#### Scenario: List messages

- **WHEN** `GET /api/butlers/{name}/conversations/{conversation_id}/messages?limit=50&offset=0` is called
- **THEN** messages are returned ordered by `created_at ASC` with pagination metadata
- **AND** each message includes `id`, `role`, `content`, `created_at`, `session_id`, `model_name`, `input_tokens`, `output_tokens`, `duration_ms`, `tool_calls`, `error`, `request_id`, `citations`, and `routed_butler`
- **AND** `citations` is always an array on the wire (`[]` when persistence is NULL), while `routed_butler` remains nullable
- **AND** during the bounded compatibility window, assistant rows also include the deprecated `sources` string array (`[]` when persistence is NULL) defined by the compatibility requirement

#### Scenario: Messages for non-existent conversation

- **WHEN** messages are requested for a conversation that does not exist or belongs to a different butler
- **THEN** a 404 response with `code: "CONVERSATION_NOT_FOUND"` is returned

#### Scenario: Stored and streamed provenance are equivalent

- **WHEN** a client receives an assistant reply's `message_complete` event and later retrieves that message through the messages list after reload or reconnect
- **THEN** the message ID, content, canonical `citations`, nullable `routed_butler`, and nullable `session_id` are equivalent to the persisted row
- **AND** the history response does not reconstruct citation trust or authorship from conversation-level routing, live phase events, or model text



## dashboard-conversations — Conversation Search


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. immutable message/thread identity, owner-authenticated cancellation and recovery, persistence, search, stream observation, safe citations and honest terminal receipts. The new input is admitted deterministically into a goal or its actual named terminal action. Classifier route lanes and provider resume execution retire at M6 under D3/D9; durable anchors, cancellation, reply idempotency and historical data remain. The full pre-admission and citation contracts below govern the successor; active terminal-recovery and continuity owners retain integration authority. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Conversation Search

The dashboard API SHALL search across conversation history for a butler.

#### Scenario: Search conversations by content

- **WHEN** `GET /api/butlers/{name}/conversations/search?q=keyword&limit=20` is called
- **THEN** conversations whose messages contain the search term are returned, ordered by relevance (most recent match first)
- **AND** each result includes the conversation metadata plus a `snippet` field with the matching message content (the first 200 characters of the matching message)

#### Scenario: Empty search query

- **WHEN** the `q` parameter is empty or missing
- **THEN** a 400 response with `code: "VALIDATION_ERROR"` is returned



## dashboard-conversations — Message-Level Search


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. immutable message/thread identity, owner-authenticated cancellation and recovery, persistence, search, stream observation, safe citations and honest terminal receipts. The new input is admitted deterministically into a goal or its actual named terminal action. Classifier route lanes and provider resume execution retire at M6 under D3/D9; durable anchors, cancellation, reply idempotency and historical data remain. The full pre-admission and citation contracts below govern the successor; active terminal-recovery and continuity owners retain integration authority. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Message-Level Search

The dashboard API SHALL provide owner-scoped full-text search across every butler's dashboard messages, distinct from the per-butler conversation search above: one row per matching message (not one per conversation), ranked by text relevance, and not restricted to a single butler unless the caller filters to one. This backs both `GET /api/conversations/messages/search` and the always-on `conversation_recall` MCP tool (any butler can recall a turn the owner had with a different butler).

#### Scenario: Search messages across every butler

- **WHEN** `GET /api/conversations/messages/search?q=keyword&limit=20` is called
- **THEN** messages matching `keyword` across every butler's conversations are returned, ordered by text relevance (`ts_rank`) then recency
- **AND** each result includes `message_id`, `conversation_id`, `role`, `created_at`, `butler_name`, `session_id`, a `snippet` excerpt, `highlight_ranges` (`[start, end)` offsets into `snippet` for each matched term), and a `deep_link` navigation path
- **AND** the response follows cursor pagination (`data`/`meta.next_cursor`/`meta.has_more`, no `total`/`offset`) per the dashboard API response conventions

#### Scenario: Cursor stable across a concurrent insert

- **WHEN** a new matching message is inserted after a page has been fetched but before the next page is requested with that page's `next_cursor`
- **THEN** the next page is neither missing nor duplicating any row from the already-returned page

#### Scenario: Optional filters

- **WHEN** `channel`, `butler`, `from`, or `to` query parameters are supplied
- **THEN** results are restricted to that source channel, that butler's conversations, and/or that `created_at` time range respectively

#### Scenario: Empty or overlong query

- **WHEN** `q` is empty/blank, or exceeds 512 characters
- **THEN** an empty/blank `q` returns a `422` (missing required parameter); a `q` over 512 characters returns a `422` with a validation error — neither case infers or fabricates a result

#### Scenario: conversation_recall MCP tool

- **WHEN** any butler calls the always-on `conversation_recall(query, since, until, limit, channel, butler)` tool
- **THEN** it returns ranked excerpts backed by the same index and search primitive as the router endpoint above, scoped to the owner (not to the calling butler)
- **AND** a blank `query` or a query with no matches returns `[]` — the tool never infers or fabricates a recollection
- **AND** the companion `conversation_thread_read(conversation_id, around_message_id)` tool returns a window of messages around a recalled hit for context



## dashboard-conversations — SSE Response Streaming


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. immutable message/thread identity, owner-authenticated cancellation and recovery, persistence, search, stream observation, safe citations and honest terminal receipts. The new input is admitted deterministically into a goal or its actual named terminal action. Classifier route lanes and provider resume execution retire at M6 under D3/D9; durable anchors, cancellation, reply idempotency and historical data remain. The full pre-admission and citation contracts below govern the successor; active terminal-recovery and continuity owners retain integration authority. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: SSE Response Streaming

Assistant responses SHALL be streamed to the dashboard via Server-Sent Events on the conversation creation and message continuation endpoints. The reply text and attribution MUST come from the routed butler's `conversation_reply` call (see the Conversation Reply Channel requirement), not from the raw completion of its spawned session. `message_complete` SHALL project the persisted message's canonical citations and per-message butler attribution rather than a client-derived interpretation.

ID: REQ-dashboard-conversations-013
Source: dashboard-conversations § SSE Response Streaming and Conversation Reply Channel; RFC 0007 § API Surface; design.md Decisions 3-4
Scope: v1-mandatory

#### Scenario: SSE stream for new conversation

- **WHEN** `POST /api/butlers/{name}/conversations` is called
- **THEN** the response is a `StreamingResponse` with `media_type: "text/event-stream"`
- **AND** the first event is `event: conversation_created` with `data: {"conversation_id": "...", "title": "..."}`
- **AND** one or more `event: token` events with `data: {"content": "..."}` carry the `conversation_reply` message text — a single event carrying the full text when the routed runtime cannot stream incrementally (every runtime adapter today), or several events whose concatenated `content` fields are byte-for-byte identical to the persisted reply row's content when a streaming-capable producer publishes incremental deltas on the turn's chat-stream channel (see the Real-Time Processing Phase Events requirement's Trust boundary)
- **AND** a final `event: message_complete` carries the persisted `message_id`, nullable session/model/token/duration fields, `tool_calls`, canonical `citations` (`[]` when persistence is NULL), and nullable `routed_butler`; during the bounded compatibility window it also carries the deprecated `sources` string array (`[]` when persistence is NULL)
- **AND** an `event: done` is sent to signal the stream is finished

#### Scenario: SSE stream for follow-up message

- **WHEN** `POST /api/butlers/{name}/conversations/{conversation_id}/messages` is called
- **THEN** the same SSE streaming pattern as conversation creation is used, without the `conversation_created` event

#### Scenario: No conversation_reply arrives within the poll window

- **WHEN** the routed butler session's spawned process does not call `conversation_reply` before the poll window (300s) elapses
- **THEN** an `event: error` with `data: {"code": "SESSION_TIMEOUT", "message": "...", "session_id": "..."}` is sent, followed by `event: done`
- **AND** `session_id` is the routed butler's session row for this request when it could be resolved (best-effort by `request_id`), or omitted when it could not
- **AND** the conversation is NOT marked failed and the thread stays open — a `conversation_reply` that lands after the SSE stream has closed is a normal message row, visible on the next history fetch or unread-badge poll

#### Scenario: Switchboard unreachable during submission

- **WHEN** the Switchboard MCP server cannot be reached while submitting the ingest envelope
- **THEN** an `event: error` with `data: {"code": "SWITCHBOARD_UNAVAILABLE", "message": "Switchboard offline — retry"}` is sent, followed by `event: done`
- **AND** the user message row inserted before submission is preserved (not rolled back)
- **AND** a client retry resubmits the original `message_id`, so Switchboard deduplicates by the stable `event.external_event_id` even when the retry crosses an hourly content-hash bucket or its rebuilt conversation-context preamble differs (no duplicate user row, route, or session is created)

#### Scenario: Switchboard rejects the envelope

- **WHEN** the Switchboard's `ingest` MCP tool rejects the envelope (e.g. an invalid `pinned_target`)
- **THEN** an `event: error` with `data: {"code": "INGEST_REJECTED", "message": "..."}` is sent, followed by `event: done`
- **AND** this is a deterministic rejection distinct from `SWITCHBOARD_UNAVAILABLE`: retrying the identical envelope will fail the same way

#### Scenario: A durable turn is still being observed or settled

- **WHEN** a same-message submission sees a durable `pending` or `cancelling`
  ingress state
- **THEN** the API emits `event: error` with
  `data: {"code": "INGEST_IN_PROGRESS", "message": "..."}`, followed by
  `event: done`
- **AND** the client treats it as an observer/check-again state, not a
  retryable send failure

#### Scenario: A durable Stop or uncertain recovery becomes terminal on SSE

- **WHEN** the durable turn records confirmed cancellation
- **THEN** the API emits `event: error` with
  `data: {"code": "SESSION_CANCELLED", "message": "..."}`, followed by
  `event: done`
- **WHEN** route recovery cannot prove the prior dashboard runtime stopped
- **THEN** the API emits `event: error` with
  `data: {"code": "TURN_OUTCOME_UNKNOWN", "message": "..."}`, followed by
  `event: done` and no automatic replay

#### Scenario: SSE keepalive during processing

- **WHEN** the butler session is processing but no tokens have been emitted for 15 seconds
- **THEN** a `: keepalive` SSE comment is sent to prevent connection timeout

#### Scenario: Message completion is persistence-authoritative

- **WHEN** display-only token events disagree with the eventual persisted reply or a client reconnects after missing `message_complete`
- **THEN** the persisted row remains authoritative for content, `citations`, `routed_butler`, and `session_id`
- **AND** the client reconciles to the stored message without inventing citations or message authorship from the token stream



## dashboard-conversations — Real-Time Processing Phase Events


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. immutable message/thread identity, owner-authenticated cancellation and recovery, persistence, search, stream observation, safe citations and honest terminal receipts. The new input is admitted deterministically into a goal or its actual named terminal action. Classifier route lanes and provider resume execution retire at M6 under D3/D9; durable anchors, cancellation, reply idempotency and historical data remain. The full pre-admission and citation contracts below govern the successor; active terminal-recovery and continuity owners retain integration authority. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Real-Time Processing Phase Events

The conversation streaming endpoints SHALL emit `event: phase` with `data: {"phase": "...", "target"?: "...", "tool"?: "..."}` whenever the API can truthfully observe a real-time processing transition for the current turn. A phase is never fabricated or guessed to fill a gap in the sequence — only a phase the backend can actually observe is emitted, and any phase a given turn's runtime cannot observe (e.g. `thinking`, which requires a streaming-capable producer) is simply not emitted for that turn.

Trust boundary: `phase` and `token` events are display-only, sourced from the routed butler's live processing where observable. The persisted `conversation_reply` row (see the Conversation Reply Channel requirement) remains the sole source of truth — `message_complete` is always emitted from that row, and a turn with no streaming producer at all still completes normally via a single `token` event followed by `message_complete`, exactly as before this requirement existed.

#### Scenario: Classifying and routed phases

- **WHEN** a Switchboard-addressed (widget) conversation turn begins
- **THEN** an `event: phase` with `data: {"phase": "classifying"}` is sent before the classification request is submitted
- **AND** once classification resolves, an `event: phase` with `data: {"phase": "routed", "target": "<butler_name>"}` is sent naming the butler now handling the turn
- **WHEN** a pinned per-butler conversation turn begins (no classification occurs)
- **THEN** an `event: phase` with `data: {"phase": "routed", "target": "<butler_name>"}` is sent immediately, naming the pinned butler

#### Scenario: Starting-session phase

- **WHEN** the API has registered the turn as cancellable and is about to begin polling for the routed butler's `conversation_reply`
- **THEN** an `event: phase` with `data: {"phase": "starting_session", "target": "<routed_butler>"}` is sent

#### Scenario: Writing phase precedes streamed content

- **WHEN** the first `event: token` of a turn (whether a single full-text event or the first of several incremental deltas) is about to be sent
- **THEN** an `event: phase` with `data: {"phase": "writing"}` is sent immediately before it, at most once per turn



## dashboard-conversations — Dashboard Ingestion Envelope Construction


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. immutable message/thread identity, owner-authenticated cancellation and recovery, persistence, search, stream observation, safe citations and honest terminal receipts. The new input is admitted deterministically into a goal or its actual named terminal action. Classifier route lanes and provider resume execution retire at M6 under D3/D9; durable anchors, cancellation, reply idempotency and historical data remain. The full pre-admission and citation contracts below govern the successor; active terminal-recovery and continuity owners retain integration authority. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Dashboard Ingestion Envelope Construction

Dashboard conversations SHALL construct `ingest.v1` envelopes that flow through the standard Switchboard ingestion pipeline, submitted to the Switchboard's `ingest` MCP tool. RFC 0003 §"ingest.v1 Envelope Format" defines `dashboard` / `internal` as direct owner-dashboard ingress: the dashboard API, rather than a connector startup probe, SHALL assign `dashboard:web:{conversation_id}` as the endpoint identity.

#### Scenario: Envelope structure for dashboard messages

- **WHEN** a dashboard message is submitted for ingestion
- **THEN** the envelope SHALL have:
  - `schema_version`: `"ingest.v1"`
  - `source.channel`: `"dashboard"`
  - `source.provider`: `"internal"`
  - `source.endpoint_identity`: `"dashboard:web:{conversation_id}"`
  - `event.external_event_id`: `"{message_id}"`, where dashboard UI provides one immutable client-generated ID for a new user message and reuses it for retry and Stop
  - `event.external_thread_id`: `"{conversation_id}"`
  - `event.observed_at`: current timestamp
  - `sender.identity`: `"dashboard:operator"`
  - `payload.normalized_text`: the user's message content (with conversation context for follow-ups)
  - `payload.raw`: `{"source": "dashboard", "conversation_id": "...", "message_id": "...", "message": "...", "page_context": {...}}` — `page_context` is present only when the client supplied one
  - `control.policy_tier`: `"interactive"`
  - `control.ingestion_tier`: `"full"`
  - `control.pinned_target`: present per the routing-pin scenarios below

#### Scenario: Dashboard messages bypass discretion

- **WHEN** a dashboard message is ingested by the Switchboard
- **THEN** the `"dashboard"` channel SHALL NOT be subject to discretion evaluation (operator messages are always intentional)

#### Scenario: Per-butler conversation envelope carries a routing pin

- **WHEN** a message is submitted via `POST /api/butlers/{name}/conversations` (or a follow-up on an existing per-butler conversation) and `{name}` is a routable domain butler (not the Switchboard staffer)
- **THEN** the constructed envelope SHALL set `control.pinned_target` to `{name}`
- **AND** the Switchboard SHALL route the message to `{name}` deterministically, without LLM classification choosing a different target

#### Scenario: Switchboard-addressed conversations are unpinned until routed

- **WHEN** a message is submitted via `POST /api/butlers/switchboard/conversations` (the dashboard chat widget's classification-routed conversation) and the conversation has no `routed_butler` yet
- **THEN** the constructed envelope SHALL NOT set `control.pinned_target` — the Switchboard staffer is never a registered, routable target
- **AND** the message proceeds through Switchboard's ordinary classify -> route pipeline

#### Scenario: Sticky follow-up pinning for classification-routed conversations

- **WHEN** a follow-up message is submitted via `POST /api/butlers/switchboard/conversations/{conversation_id}/messages` and the conversation already has a `routed_butler` set (from an earlier successful `route_to` decision)
- **THEN** the constructed envelope SHALL set `control.pinned_target` to `routed_butler`, bypassing classification entirely
- **AND** a conversation whose `routed_butler` is still NULL (not yet routed, or a bug-lane report with no domain-butler target) continues through classification as in the "unpinned until routed" scenario above

#### Scenario: Optional page context on dashboard messages

- **WHEN** a dashboard message is submitted with a `page_context` object (`route`, `query_params`, optional `entity_ref`, optional `visible_resource` {`kind`, `id`, `filters`, `window`}, optional `visible_summary`) on the request body
- **THEN** the API SHALL strip any query-param key containing a secret-ish marker (`token`, `key`, `secret`, `password`, `authorization`) before persisting or forwarding it, regardless of what the client sent
- **AND** the API SHALL reject a `visible_resource.kind` outside the closed registry vocabulary
- **AND** a payload exceeding the size budget SHALL be truncated (dropping `visible_resource.filters`, then `query_params`, then trimming `visible_summary`, in that order) with `truncated=true` set, never silently dropped or rejected outright
- **AND** the persisted user message row SHALL store the (possibly redacted/truncated) `page_context` plus a `captured_at` timestamp
- **AND** the envelope's `payload.raw.page_context` SHALL carry that object unchanged, grounding the statement for the routed butler
- **AND** when no `page_context` is provided, `payload.raw` SHALL NOT contain a `page_context` key

#### Scenario: A retry reuses the originally-captured page context

- **WHEN** a dashboard message is retried with the same client-generated `message_id` (`message_create_idempotent`'s conflict path)
- **THEN** the API SHALL forward the `page_context` stored on the original write, not a `page_context` on the retry request body, into the ingest envelope
- **AND** no new capture SHALL occur for the retried message



## dashboard-conversations — Conversation Summary Queries


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. immutable message/thread identity, owner-authenticated cancellation and recovery, persistence, search, stream observation, safe citations and honest terminal receipts. The new input is admitted deterministically into a goal or its actual named terminal action. Classifier route lanes and provider resume execution retire at M6 under D3/D9; durable anchors, cancellation, reply idempotency and historical data remain. The full pre-admission and citation contracts below govern the successor; active terminal-recovery and continuity owners retain integration authority. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Conversation Summary Queries

The dashboard API SHALL provide conversation-count summary statistics.

#### Scenario: Conversation summary per butler

- **WHEN** `GET /api/butlers/{name}/conversations/summary` is called
- **THEN** the response includes: `total_conversations`, `active_conversations`, `total_messages`



## dashboard-conversations — Conversation Pydantic Response Models


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. immutable message/thread identity, owner-authenticated cancellation and recovery, persistence, search, stream observation, safe citations and honest terminal receipts. The new input is admitted deterministically into a goal or its actual named terminal action. Classifier route lanes and provider resume execution retire at M6 under D3/D9; durable anchors, cancellation, reply idempotency and historical data remain. The full pre-admission and citation contracts below govern the successor; active terminal-recovery and continuity owners retain integration authority. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Conversation Pydantic Response Models

Conversation endpoint API response models SHALL provide typed response shapes.

#### Scenario: ConversationSummary model

- **WHEN** a conversation list response is serialized
- **THEN** each entry includes: `id`, `butler_name`, `title`, `status`, `created_at`, `updated_at`, `message_count`, `routed_butler`, `latest_assistant_reply_at`

#### Scenario: ConversationMessage model

- **WHEN** a message response is serialized
- **THEN** each entry includes: `id`, `conversation_id`, `role`, `content`, `created_at`, `session_id`, `model_name`, `input_tokens`, `output_tokens`, `duration_ms`, `tool_calls`, `error`, `request_id`, `page_context`, `captured_at`
- **AND** `page_context`/`captured_at` are both `null` for assistant-role rows and for any user row sent without a page context

#### Scenario: ConversationSearchResult model

- **WHEN** a search result is serialized
- **THEN** each entry includes the `ConversationSummary` fields plus `snippet` (the matching message content excerpt)



## dashboard-conversations — Conversation Reply Channel


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. immutable message/thread identity, owner-authenticated cancellation and recovery, persistence, search, stream observation, safe citations and honest terminal receipts. The new input is admitted deterministically into a goal or its actual named terminal action. Classifier route lanes and provider resume execution retire at M6 under D3/D9; durable anchors, cancellation, reply idempotency and historical data remain. The full pre-admission and citation contracts below govern the successor; active terminal-recovery and continuity owners retain integration authority. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Conversation Reply Channel

A routed butler session SHALL confirm its interpretation of a dashboard
statement (or acknowledge a filed bug report, or answer a question) by
calling the `conversation_reply` MCP tool, which persists an assistant-role
message directly into the conversation it was routed from. The SSE poller
MUST watch for this message rather than the routed session's raw completion
(see the SSE Response Streaming requirement).

#### Scenario: conversation_reply persists the confirm-loop message

- **WHEN** a routed butler session calls `conversation_reply(conversation_id, message)` for a `conversation_id` that references an existing conversation
- **THEN** an assistant-role row is inserted into `public.dashboard_messages` with `content = message`
- **AND** the conversation's `message_count` is incremented and `updated_at` is refreshed
- **AND** the tool returns `{"status": "ok", "message_id": "...", "conversation_id": "..."}`

#### Scenario: conversation_reply rejects an unknown conversation_id

- **WHEN** `conversation_reply` is called with a `conversation_id` that does not reference an existing conversation
- **THEN** no message row is inserted
- **AND** the tool returns `{"status": "error", "error": "..."}` (never raises) so the calling model can see and correct its own mistake

#### Scenario: conversation_reply is available to every butler

- **WHEN** any butler's MCP server registers its core tools
- **THEN** `conversation_reply` SHALL be registered regardless of `core_groups` configuration — any butler can be the classification or pinned-target destination of a dashboard conversation, so the tool cannot be scoped to a subset of butlers

#### Scenario: conversation_reply accepts an optional sources list for an answer-lane reply

- **WHEN** a routed butler session calls `conversation_reply(conversation_id, message, sources=[...])` with a non-empty list of strings
- **THEN** the inserted message row's `sources` column SHALL persist the given list
- **AND** the tool's success response SHALL be unaffected in shape otherwise

#### Scenario: conversation_reply is unaffected when sources is omitted

- **WHEN** `conversation_reply` is called without a `sources` argument (the existing confirm-loop, action-proposal, and bug-report call sites)
- **THEN** the inserted message row's `sources` column SHALL be NULL
- **AND** behavior SHALL be identical to before `sources` existed

#### Scenario: conversation_reply rejects empty or blank source names

- **WHEN** `conversation_reply` is called with `sources=[]` or with any blank source name
- **THEN** no message row is inserted
- **AND** the tool returns `{"status": "error", "error": "..."}` guiding the caller to either name what it consulted or omit `sources` entirely and give an honest decline instead of fabricating a citation



## dashboard-conversations — Dashboard Message Intent Lanes


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. immutable message/thread identity, owner-authenticated cancellation and recovery, persistence, search, stream observation, safe citations and honest terminal receipts. The new input is admitted deterministically into a goal or its actual named terminal action. Classifier route lanes and provider resume execution retire at M6 under D3/D9; durable anchors, cancellation, reply idempotency and historical data remain. The full pre-admission and citation contracts below govern the successor; active terminal-recovery and continuity owners retain integration authority. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Dashboard Message Intent Lanes

A dashboard chat-widget turn SHALL be classified into exactly one of STATEMENT, ACTION REQUEST, QUESTION, or ambiguous before it produces any effect. Consent MUST precede effect for an ACTION REQUEST (`about/heart-and-soul/security.md`, "Approval gates must never be bypassable by the LLM session"): a dashboard turn that asks the routed butler to DO something with a real-world or hard-to-reverse effect SHALL never apply a write before the owner has approved it, and SHALL never be reported to the owner as already done. A QUESTION turn SHALL never apply a write and SHALL never be reported as an action taken.

#### Scenario: Classifier offers a distinct ACTION lane alongside statement and bug lanes

- **WHEN** the Switchboard's dashboard classification prompt is built for a chat-widget message
- **THEN** it SHALL present four lanes: LANE A (data statement/correction, routed via `route_to_butler`), LANE B (bug/system report, filed via `file_bug_report`), LANE C (action request, also routed via `route_to_butler` — the classifier's job is only to pick the target butler; the propose-don't-apply contract is enforced by the routed envelope's injected instructions, not by the classifier itself), and LANE D (question, answered via `answer_question` or dead-lettered via `cannot_answer`)

#### Scenario: The routed envelope carries distinct STATEMENT and ACTION-REQUEST instructions

- **WHEN** `route_to_butler` injects the deterministic dashboard confirm-loop block into a routed envelope's `input.context`
- **THEN** the block SHALL contain a STATEMENT instruction set (interpret, apply the write, then call `conversation_reply` to confirm) and a distinct ACTION-REQUEST instruction set
- **AND** the ACTION-REQUEST set SHALL instruct the routed session to route the write through its normal approval-gated tool (never an ungated path) so the gate parks it before anything happens, and to call `conversation_reply` describing the action as proposed and awaiting approval — never as already completed
- **AND** the block SHALL state the failure mode explicitly: applying an action's write before the gate parks it, or claiming completion for a pending action, is never acceptable
- **WHEN** `answer_question(scope="domain")` injects the deterministic dashboard answer block into a routed envelope's `input.context` instead of the confirm-loop block
- **THEN** the block SHALL instruct the routed session to answer strictly read-only, from its own tools only, and to call `conversation_reply` citing what it consulted via `sources` when grounded, or to give an honest decline (never fabricate a citation) when it cannot ground the answer

#### Scenario: A parked action request produces zero domain writes and no completion claim

- **WHEN** a routed butler session follows the ACTION-REQUEST instructions for a gated write tool whose target contact is unresolvable or requires review
- **THEN** exactly one `pending_actions` row is created with `status = 'pending'`
- **AND** the underlying domain tool function is not invoked
- **AND** the `conversation_reply` text describes the action as proposed/queued, not completed

#### Scenario: An ambiguous dashboard turn yields a clarifying reply, never a best-guess route

- **WHEN** the dashboard classification session cannot confidently place a message into LANE A, B, C, or D
- **THEN** it SHALL call neither `route_to_butler` nor `file_bug_report` rather than guessing a target butler, and SHALL likewise not call `answer_question` or `cannot_answer` while the turn remains ambiguous
- **AND** the pipeline's existing dashboard dead-letter path (see the Durable Dashboard Turn Control requirement's failure handling) SHALL capture the turn and reply in-thread asking the owner to clarify, with no route to any domain butler



## dashboard-conversations — Channel-Agnostic Conversation Anchor


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. immutable message/thread identity, owner-authenticated cancellation and recovery, persistence, search, stream observation, safe citations and honest terminal receipts. The new input is admitted deterministically into a goal or its actual named terminal action. Classifier route lanes and provider resume execution retire at M6 under D3/D9; durable anchors, cancellation, reply idempotency and historical data remain. The full pre-admission and citation contracts below govern the successor; active terminal-recovery and continuity owners retain integration authority. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Channel-Agnostic Conversation Anchor

`conversation_get_or_create_by_thread` SHALL let any inbound channel that
supplies a stable `external_conversation_id` at ingest (Telegram, email, ...)
obtain a durable `public.dashboard_conversations` anchor row for that
conversation, without needing its own separate conversation-identity concept.
This generalizes conversation creation beyond the dashboard-only
`conversation_create` path. The key SHALL be the connector's conversation
identity, never its per-message reply target; the helper SHALL NOT rewrite
the key it receives. A dashboard-channel route SHALL instead resolve its
existing conversation by id and never call this helper.

#### Scenario: First ingress for a conversation creates the anchor

- **WHEN** `conversation_get_or_create_by_thread` is called with a
  `(butler_name, source_channel, external_conversation_id)` combination that
  has no existing row
- **THEN** a new `dashboard_conversations` row is inserted with that
  `source_channel`, the key in both `external_conversation_id` and
  `source_thread_identity`, an auto-generated title from `first_message`,
  `status = 'active'`, and `message_count = 0`
- **AND** the function returns `(conversation, is_new=True)`

#### Scenario: Repeat ingress for the same conversation reuses the anchor

- **WHEN** `conversation_get_or_create_by_thread` is called again with the
  same `(butler_name, source_channel, external_conversation_id)` combination
- **THEN** no new row is inserted
- **AND** the function returns the existing row with `is_new=False`, even if
  a different `first_message` was supplied on the repeat call

#### Scenario: Consecutive Telegram messages share one provider lineage

- **WHEN** two routed Telegram ingests from one chat carry distinct
  `reply_target_ref` values and the same `external_conversation_id`
- **THEN** both calls return the same conversation anchor in either arrival order
- **AND** a provider session stored after the first turn is resumed by the
  second turn

#### Scenario: Concurrent anchor conflict resolution is connection-bound

- **WHEN** callers for one conversation overlap, including while an insert
  conflict commits or rolls back
- **THEN** insert, conflict fallback, and result selection execute under one
  transaction-scoped advisory lock keyed by the conversation identity
- **AND** the insert and fallback select use one acquired PostgreSQL connection
- **AND** rollback leaves no anchor from the failed transaction, while retry
  creates or resolves exactly one anchor without timing-based sleeps

#### Scenario: Different channel or conversation identity never collides

- **WHEN** two calls share a `butler_name` but differ in `source_channel` or
  `external_conversation_id`
- **THEN** each gets its own distinct anchor row

#### Scenario: Pre-existing dashboard-created rows are unaffected

- **WHEN** a conversation was created via `conversation_create` (the
  dashboard-only path, `external_conversation_id IS NULL` and
  `source_thread_identity IS NULL`)
- **THEN** it is never matched or overwritten by
  `conversation_get_or_create_by_thread`, and neither partial unique index
  (each governs only rows whose key is non-null) conflicts with it



## dashboard-conversations — Provider Resume Ledger


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. immutable message/thread identity, owner-authenticated cancellation and recovery, persistence, search, stream observation, safe citations and honest terminal receipts. The new input is admitted deterministically into a goal or its actual named terminal action. Classifier route lanes and provider resume execution retire at M6 under D3/D9; durable anchors, cancellation, reply idempotency and historical data remain. The full pre-admission and citation contracts below govern the successor; active terminal-recovery and continuity owners retain integration authority. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Provider Resume Ledger

Each conversation SHALL carry at most one provider-native resume handle
("one memory per thread") that a runtime adapter capable of resuming a prior
session (see `RuntimeAdapter.supports_resume`) can use to continue that
conversation's provider-side session state instead of cold-starting.

#### Scenario: Recording a provider session handle

- **WHEN** `conversation_set_provider_session` is called with a
  `provider_session_id` and `provider_runtime_type` for a conversation
- **THEN** those values are stored along with a fresh
  `provider_session_updated_at = now()`
- **AND** a later call for the same conversation overwrites the prior handle
  entirely — only the most recent provider session is ever resumable

#### Scenario: Resolving a usable resume handle

- **WHEN** `resolve_resume_handle` is evaluated for a conversation's stored
  provider session against a target `runtime_type`
- **THEN** it returns the handle only if one is present, it was minted by
  that same `runtime_type`, and `provider_session_updated_at` is within the
  TTL window (24 hours) of the evaluation time
- **AND** it returns `None` in every other case (absent, runtime-type
  mismatch, or expired) — callers MUST treat `None` as an ordinary cold
  start, never as an error

#### Scenario: Evicting a stale or rejected handle

- **WHEN** `conversation_clear_provider_session` is called for a conversation
  (e.g. because a resume attempt was rejected by the provider as expired or
  unknown)
- **THEN** `provider_session_id`, `provider_runtime_type`, and
  `provider_session_updated_at` are all cleared to `NULL`
- **AND** the next call to `resolve_resume_handle` for that conversation
  returns `None`, so the next turn cold-starts cleanly



## dashboard-conversations — Assistant Citation Normalization and Trust


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. immutable message/thread identity, owner-authenticated cancellation and recovery, persistence, search, stream observation, safe citations and honest terminal receipts. The new input is admitted deterministically into a goal or its actual named terminal action. Classifier route lanes and provider resume execution retire at M6 under D3/D9; durable anchors, cancellation, reply idempotency and historical data remain. The full pre-admission and citation contracts below govern the successor; active terminal-recovery and continuity owners retain integration authority. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Assistant Citation Normalization and Trust

The server SHALL normalize the existing `conversation_reply(..., sources=...)` input into one canonical `citations` representation before persistence. A canonical citation SHALL contain a non-empty plain-text `label`, a nullable `target`, and exactly one `kind` of `internal`, `external`, or `unlinked`. Citation labels SHALL NOT be parsed as markdown. A structurally valid citation establishes only a safe navigation target, never the truth, completeness, availability, or evidentiary quality of the model's claim.

ID: REQ-dashboard-conversations-010
Source: heart-and-soul/security.md § Session Sandboxing; dashboard-conversations § Conversation Reply Channel; design.md Decisions 2-3
Scope: v1-mandatory

#### Scenario: Structured internal source is accepted by server authority

- **WHEN** `conversation_reply` receives a `sources` entry with a non-empty label and an internal target matching a server-held citation-eligible shell route pattern
- **THEN** the server normalizes it to `{label, target, kind: "internal"}` and persists it in `citations`
- **AND** the client cannot expand the accepted route set by changing its own route registry or request payload
- **AND** target validation does not assert that the referenced resource exists or proves the answer text

#### Scenario: Structured external source is accepted safely

- **WHEN** `conversation_reply` receives a `sources` entry with a non-empty label and an absolute HTTPS target with no embedded credentials
- **THEN** the server normalizes it to `{label, target, kind: "external"}` and persists it in `citations`
- **AND** HTTP, script, data, file, protocol-relative, credential-bearing, or malformed targets are invalid
- **AND** acceptance does not assert that the remote destination is reachable or trustworthy

#### Scenario: Legacy source string remains compatible but unlinked

- **WHEN** `conversation_reply` receives an existing non-empty string entry in `sources`
- **THEN** the server normalizes it to `{label: <trimmed string>, target: null, kind: "unlinked"}`
- **AND** the string cannot become a link by resembling a path or URL
- **AND** the reply success shape remains compatible with the existing tool contract

#### Scenario: Invalid structured targets are removed content-blindly

- **WHEN** a non-empty `sources` input contains at least one valid entry and one structured entry with a malformed, unsafe, or non-allowlisted target
- **THEN** only the valid normalized entries are persisted in `citations`
- **AND** the server emits a content-blind warning with reason code and rejected count, without labels, targets, answer text, request arguments, session IDs, or sensitive payloads
- **AND** the tool success response does not echo the rejected entry

#### Scenario: Empty blank or over-budget sources are rejected

- **WHEN** `sources` is explicitly empty, contains any blank label, exceeds an item or total payload budget, or leaves no usable entry after structured-target validation
- **THEN** no assistant message is inserted
- **AND** the tool returns a structured error directing the caller to supply usable sources or omit `sources` and give an honest decline
- **AND** no rejected label or target is copied into logs, telemetry, or the error response

#### Scenario: Omitted sources preserve refusal and non-answer semantics

- **WHEN** `conversation_reply` is called without `sources` for a confirm loop, action proposal, bug report, or honest answer decline
- **THEN** the message persists with `citations = null` and `sources = null`
- **AND** omission is not relabeled as grounded, invalid, or verified



## dashboard-conversations — Assistant Message Author Attribution


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. immutable message/thread identity, owner-authenticated cancellation and recovery, persistence, search, stream observation, safe citations and honest terminal receipts. The new input is admitted deterministically into a goal or its actual named terminal action. Classifier route lanes and provider resume execution retire at M6 under D3/D9; durable anchors, cancellation, reply idempotency and historical data remain. The full pre-admission and citation contracts below govern the successor; active terminal-recovery and continuity owners retain integration authority. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Assistant Message Author Attribution

Every assistant message created by `conversation_reply` SHALL persist the authoring butler name from server-held tool registration context. The system MUST NOT accept caller-asserted message authorship or infer a missing author from conversation-level routing, model text, phase events, or session lookup.

ID: REQ-dashboard-conversations-011
Source: heart-and-soul/security.md § Session Sandboxing; heart-and-soul/vision.md § Domain specialization; dashboard-conversations § Conversation Reply Channel; design.md Decision 4
Scope: v1-mandatory

#### Scenario: conversation_reply records its registered butler

- **WHEN** a butler's registered `conversation_reply` tool persists an assistant message
- **THEN** that message's `routed_butler` equals the server-held butler identity bound when the tool was registered
- **AND** a `sources` object, message text, conversation field, request field, or browser value cannot override it
- **AND** the tool's existing success and unknown-conversation error shapes remain otherwise unchanged

#### Scenario: Classification and sticky routing are not message authorship

- **WHEN** a Switchboard-owned conversation has a sticky conversation `routed_butler` or the current SSE stream names a routed target
- **THEN** that value does not populate or repair an assistant message's `routed_butler`
- **AND** only the server-held identity at the assistant-message write boundary can establish message authorship

#### Scenario: Legacy and non-session-authored rows remain unknown

- **WHEN** an existing assistant row predates per-message attribution or a deterministic API failure row is created outside a registered butler's `conversation_reply`
- **THEN** its `routed_butler` remains NULL
- **AND** migration, read, SSE, and frontend paths do not guess or backfill an author



## dashboard-conversations — Assistant Message Compatibility Lifecycle


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. immutable message/thread identity, owner-authenticated cancellation and recovery, persistence, search, stream observation, safe citations and honest terminal receipts. The new input is admitted deterministically into a goal or its actual named terminal action. Classifier route lanes and provider resume execution retire at M6 under D3/D9; durable anchors, cancellation, reply idempotency and historical data remain. The full pre-admission and citation contracts below govern the successor; active terminal-recovery and continuity owners retain integration authority. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Assistant Message Compatibility Lifecycle

The citation and attribution rollout SHALL be additive and reversible until all repository consumers read the canonical fields. The `citations` field is the sole forward representation; `sources` is a bounded read-compatibility projection, not a second evidence contract.

ID: REQ-dashboard-conversations-014
Source: craft-and-care/interfaces-and-dependencies.md § Compatibility Rules; design.md Decision 6 and Migration Plan
Scope: v1-mandatory

#### Scenario: Mixed-version reads remain safe during rollout

- **WHEN** the additive migration is deployed while a repository consumer still reads `sources`
- **THEN** new writes persist canonical `citations` and a string-only `sources` projection sufficient for that verified consumer, and both read APIs serialize absent arrays as `[]`
- **AND** canonical readers use `citations` and do not merge, rank, or infer additional trust from the deprecated projection
- **AND** nullable new fields allow old rows to remain readable without a guessed author or fabricated target

#### Scenario: Same-repo compatibility projection is retired

- **WHEN** every verified repository consumer reads `citations` and `routed_butler`
- **THEN** the same implementation change removes the `sources` response projection and its compatibility branches
- **AND** the retained database column is removed only after downgrade safety and deployment ordering no longer depend on it

#### Scenario: Downgrade preserves legacy readers

- **WHEN** runtime code is rolled back during the compatibility window
- **THEN** the previous code can still read the retained `sources` strings and ignore nullable `citations` and message `routed_butler`
- **AND** downgrade never converts a structured target into a string that appears server-validated


## dashboard-chat-ui — Chat Panel on Butler Detail Page


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. safe message rendering, accessible recall/search/composer, immutable retry identity, honest streaming, draft retention and in-thread errors. A single assistant goal/thread surface replaces the two unit chat postures. Public unit identity and current-unit switching retire; server-held invocation/region provenance remains inspectable. Sanitized partial rendering, safe citations and persistence are specified in dashboard-goal-message-rendering. The current chat remains until an allocated migration implements every case. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Chat Panel on Butler Detail Page

The chat interface SHALL render as a slide-out panel on the butler detail page (`/butlers/:name`), toggled by a dedicated button in the butler detail header area. This is a per-butler thread view, distinct from the global "Talk to Butlers" surface's three postures (docked rail, full page, popover) described under Requirement: Global Chat Postures — `MessageThread`, `MessageInput`, and the send/stream/stop turn logic (`useConversationTurn`) are shared, but this panel is scoped to one butler's own conversations rather than the Switchboard-routed set.

#### Scenario: Chat panel toggle

- **WHEN** the user clicks the "Chat" button on the butler detail page
- **THEN** the panel opens as a right-side drawer built on the shared sheet primitive
- **AND** the panel header shows the butler name and a close button
- **AND** the main content area (butler detail) remains visible and scrollable behind the panel

#### Scenario: Chat panel persistence across tabs

- **WHEN** the chat panel is open and the user switches between butler detail tabs (Overview, Sessions, State, etc.)
- **THEN** the chat panel remains open and retains its state

#### Scenario: Chat panel responsive behavior

- **WHEN** the viewport is narrow
- **THEN** the drawer opens full-width instead of as a side panel
- **AND** the standard close button is retained for navigation



## dashboard-chat-ui — Global Chat Postures


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. safe message rendering, accessible recall/search/composer, immutable retry identity, honest streaming, draft retention and in-thread errors. A single assistant goal/thread surface replaces the two unit chat postures. Public unit identity and current-unit switching retire; server-held invocation/region provenance remains inspectable. Sanitized partial rendering, safe citations and persistence are specified in dashboard-goal-message-rendering. The current chat remains until an allocated migration implements every case. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Global Chat Postures

The global "Talk to Butlers" surface (Switchboard-routed conversations, distinct from the per-butler panel above) SHALL present exactly one of three postures at a time, chosen by viewport width and an explicit collapse/expand toggle, never stacked:

- **Docked rail** — `ChatDock`, rendered in the shell's `chatDock` slot at or above the `xl` breakpoint while open; the default posture there. The shell owns only the slot (see `dashboard-shell`, Requirement: Chat Dock Rail (>= xl breakpoint)). The dock is resizable within bounds, and both its width and its open/collapsed state are persisted per viewer.
- **Full page** — `/chat` and `/chat/:conversationId`, reached via the dock/popover's "Open in full page" action, a copy-link, or cmdk recall.
- **Popover** — the floating bottom-right widget (`FloatingChatWidget`), the only posture below `xl` or while the dock is collapsed.

The dock and the popover share the same Switchboard-routed conversation set. All three postures render the same `MessageThread`/`MessageInput` components and drive their send/stream/stop turn state through the one shared `useConversationTurn` hook (`frontend/src/hooks/use-conversation-turn.ts`) — there is exactly one implementation of that turn logic, not a per-posture copy.

#### Scenario: Postures are mutually exclusive

- **WHEN** any dashboard route is rendered
- **THEN** at most one of {docked rail, full page, popover} is mounted at a time
- **AND** the docked rail and the popover in particular are never both mounted (the dock's presence is what decides whether the popover renders at all)

#### Scenario: Each mounted instance owns its own turn state

- **WHEN** a conversation is actively streaming in one posture (e.g. the dock) and the operator navigates to `/chat/{id}` for that same conversation in another tab or after the dock collapses
- **THEN** the full-page instance calls `useConversationTurn` independently and does not receive a live mirror of the other posture's in-flight stream — it resumes from the persisted message history once the turn completes
- **AND** this is a deliberate scope boundary (cross-posture live-turn mirroring is out of scope for this capability)

#### Scenario: Popover trigger reopens a collapsed dock

- **WHEN** the dock has been collapsed while the viewport is at or above the `xl` breakpoint and the operator activates the popover trigger
- **THEN** the dock reopens instead of the popover opening



## dashboard-chat-ui — Full-Page Chat Route


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. safe message rendering, accessible recall/search/composer, immutable retry identity, honest streaming, draft retention and in-thread errors. A single assistant goal/thread surface replaces the two unit chat postures. Public unit identity and current-unit switching retire; server-held invocation/region provenance remains inspectable. Sanitized partial rendering, safe citations and persistence are specified in dashboard-goal-message-rendering. The current chat remains until an allocated migration implements every case. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Full-Page Chat Route

`/chat` and `/chat/:conversationId` SHALL render the full-page chat posture (`ChatPage`), resolving a specific conversation cross-butler by id.

#### Scenario: Bare /chat starts or resumes composing for the Switchboard butler

- **WHEN** `/chat` is visited with no `:conversationId`
- **THEN** the page renders the composer for a not-yet-created conversation against the Switchboard butler
- **AND** sending the first message creates the conversation and the URL updates (via a replace navigation) to `/chat/{newId}` once the id is known

#### Scenario: /chat/:conversationId resolves cross-butler

- **WHEN** `/chat/:conversationId` is visited
- **THEN** the frontend calls `GET /api/conversations/{id}` to resolve the owning `butler_name` before fetching messages — the conversation may belong to any butler, not only the Switchboard (any assistant message's copy-link, wherever it renders, points here)
- **AND** once resolved, messages are fetched via the existing per-butler `GET /api/butlers/{name}/conversations/{id}/messages` route using that resolved `butler_name`

#### Scenario: Unknown conversation id renders an explicit not-found state

- **WHEN** `GET /api/conversations/{id}` returns 404 for the `:conversationId` in the URL
- **THEN** the page renders an explicit "Conversation not found" empty state with an action to start a new conversation
- **AND** it SHALL NOT render a blank or infinitely-loading thread

#### Scenario: A `#m-{messageId}` fragment scrolls to and focuses that message

- **WHEN** `/chat/:conversationId#m-{messageId}` is visited and the thread finishes loading
- **THEN** the named message bubble is scrolled into view and receives keyboard focus (the bubble carries `tabIndex={-1}` for this purpose)
- **AND** if no message with that id is in the loaded thread, the thread still renders normally with no scroll and no error — a stale or incorrect fragment is a silent no-op

#### Scenario: Copy-link produces a full-page deep link

- **WHEN** the operator clicks the copy-link action on an assistant message (any posture, any butler's conversation)
- **THEN** the clipboard receives `{origin}/chat/{conversationId}#m-{messageId}`, matching the anchor `scrollToMessageAnchor` looks for



## dashboard-chat-ui — Recent-Thread Recall (Command Palette)


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. safe message rendering, accessible recall/search/composer, immutable retry identity, honest streaming, draft retention and in-thread errors. A single assistant goal/thread surface replaces the two unit chat postures. Public unit identity and current-unit switching retire; server-held invocation/region provenance remains inspectable. Sanitized partial rendering, safe citations and persistence are specified in dashboard-goal-message-rendering. The current chat remains until an allocated migration implements every case. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Recent-Thread Recall (Command Palette)

Recently-updated Switchboard conversations SHALL be reachable through the command palette (cmdk), alongside the existing "Talk to Butlers" command, navigating directly to `/chat/{id}`.

#### Scenario: Recent threads appear as palette commands

- **WHEN** the command palette is opened
- **THEN** recent Switchboard conversations each appear as a command labeled with the conversation's title (or "Untitled conversation")
- **AND** selecting one navigates to `/chat/{conversationId}`
- **AND** this recall list is registered once, mounted regardless of which chat posture (dock or popover) is currently showing



## dashboard-chat-ui — Conversation List Sidebar


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. safe message rendering, accessible recall/search/composer, immutable retry identity, honest streaming, draft retention and in-thread errors. A single assistant goal/thread surface replaces the two unit chat postures. Public unit identity and current-unit switching retire; server-held invocation/region provenance remains inspectable. Sanitized partial rendering, safe citations and persistence are specified in dashboard-goal-message-rendering. The current chat remains until an allocated migration implements every case. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Conversation List Sidebar

Within the chat panel, a conversation list SHALL allow switching between threads or starting new ones.

#### Scenario: Conversation list renders

- **WHEN** the chat panel opens
- **THEN** a collapsible column beside the thread shows the conversation list for the current butler
- **AND** conversations are sorted by `updated_at DESC` (most recent first)
- **AND** each conversation shows the title (truncated to 2 lines) and a relative timestamp (e.g., "2h ago")
- **AND** the active conversation is visibly highlighted
- **AND** a "New conversation" action appears at the top of the list

#### Scenario: Conversation list empty state

- **WHEN** the butler has no conversations
- **THEN** an `EmptyState` component renders with message "No conversations yet" and a "Start a conversation" action button

#### Scenario: Conversation list collapsed mode

- **WHEN** the user clicks the collapse toggle on the conversation list
- **THEN** the list collapses to a narrow column showing only the first letter of each conversation title
- **AND** the chat area expands to fill the available width
- **AND** the collapse state is persisted per viewer



## dashboard-chat-ui — Message Thread Display


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. safe message rendering, accessible recall/search/composer, immutable retry identity, honest streaming, draft retention and in-thread errors. A single assistant goal/thread surface replaces the two unit chat postures. Public unit identity and current-unit switching retire; server-held invocation/region provenance remains inspectable. Sanitized partial rendering, safe citations and persistence are specified in dashboard-goal-message-rendering. The current chat remains until an allocated migration implements every case. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Message Thread Display

The active conversation SHALL render as a scrollable message thread with user and assistant messages differentiated by alignment and styling. Assistant answers SHALL use one safe rendering path for stored and streaming content, and SHALL present citations and authorship without implying trust the server cannot establish.

ID: REQ-dashboard-chat-ui-004
Source: heart-and-soul/vision.md § What Butlers Is Not (Not an experiment); dashboard-design-language § Type System, Voice Surface, Butler Letter-Mark, and Interaction Affordances; design.md Decisions 1-4
Scope: v1-mandatory

#### Scenario: User message rendering

- **WHEN** a user message is displayed in the thread
- **THEN** it renders right-aligned with `bg-primary text-primary-foreground` styling
- **AND** it shows the message content and a relative timestamp below

#### Scenario: Assistant message rendering

- **WHEN** an assistant message is displayed in the thread
- **THEN** it renders left-aligned with `bg-muted` styling
- **AND** sanitized markdown renders headings, lists, inline code, emphasis, blockquotes, fenced code, and tables while raw HTML and executable markup render no active element
- **AND** answer prose uses the Voice type role, structural labels use Body, and code plus tabular numeric cells use Mono with tabular numerals
- **AND** butler, time, session, model, token, duration, and cost metadata follow the attribution and disclosure scenarios below

#### Scenario: Streaming markdown remains safe and stable

- **WHEN** an assistant answer is rendered before the stream has completed, including an unterminated fenced code block or other half-open markdown construct
- **THEN** the partial content uses the same sanitized renderer as the stored answer and cannot create raw HTML, an executable element, or an unsafe link
- **AND** an unterminated fence renders as code without breaking the surrounding layout
- **AND** when later tokens close the construct, the answer re-renders into its completed structure without duplicated or lost text
- **AND** `message_complete` renders the persisted content byte-for-byte as renderer input, with no duplicated, lost, or synthetic fence text; rendered semantics remain governed by the allowed-markdown clauses above

#### Scenario: Internal citation navigation

- **WHEN** an assistant message carries a citation whose `kind` is `internal`
- **THEN** the citation renders as a labeled, keyboard-focusable link and activates through the dashboard router
- **AND** activation performs in-SPA navigation to the server-validated target without assigning `window.location` or causing a full page reload
- **AND** focus is visible according to the Interaction Affordances contract

#### Scenario: External citation navigation

- **WHEN** an assistant message carries a citation whose `kind` is `external`
- **THEN** the citation renders as a labeled HTTPS link with `rel="noopener noreferrer"`
- **AND** the external destination is visually and accessibly distinguishable from an internal dashboard destination
- **AND** the citation does not claim that the destination's content or availability was verified

#### Scenario: Unlinked legacy citation remains honest

- **WHEN** an assistant message carries a citation whose `kind` is `unlinked` or whose once-valid destination is unavailable when followed
- **THEN** an unlinked citation renders as text rather than as a fabricated destination
- **AND** a destination that later returns a not-found or unavailable state uses that destination surface's existing recovery behavior
- **AND** neither state is labeled as verified evidence

#### Scenario: Per-message butler attribution

- **WHEN** an assistant message carries a non-null server-derived `routed_butler`
- **THEN** its attribution line renders the existing `ButlerMark`, the butler name, and the relative message time
- **AND** a non-null `session_id` adds a keyboard-focusable `Session ->` navigation link beside that attribution
- **AND** the line describes only that message and does not infer authorship from the conversation's sticky route or a live phase event

#### Scenario: Unknown message author is not guessed

- **WHEN** an assistant message has `routed_butler = null`, including a legacy or non-session-authored row
- **THEN** the thread renders no butler mark or butler name for that message
- **AND** it does not substitute the conversation owner, sticky `routed_butler`, current route phase, model name, or session lookup as the author
- **AND** any available timestamp, error, and session navigation remain usable without an attribution claim

#### Scenario: Secondary details are disclosed accessibly

- **WHEN** an assistant message has model, token, duration, or cost details
- **THEN** one disclosure control contains those secondary details and is closed by default
- **AND** the control exposes its expanded state, has an accessible name, and toggles by keyboard
- **AND** the primary butler, time, and available session navigation remain visible without opening it

#### Scenario: Narrow viewport contains wide answers

- **WHEN** an answer containing a table or long code line is displayed at a 360 CSS-pixel viewport
- **THEN** the message and page do not acquire horizontal overflow
- **AND** each wide table or code block scrolls within its own named region
- **AND** the overflow region is keyboard focusable and keyboard scrollable
- **AND** citation and disclosure controls retain at least the active Viewport and Modality Contract's coarse-pointer target floor

#### Scenario: Auto-scroll to latest message

- **WHEN** a new message is added to the thread (user or assistant)
- **THEN** the thread scrolls to the bottom to show the latest message
- **AND** if the user has manually scrolled up (more than 100px from bottom), auto-scroll is suppressed until they scroll back to the bottom

#### Scenario: Tool call visibility

- **WHEN** an assistant message includes `tool_calls`
- **THEN** a collapsible "Tool calls" section renders below the message content
- **AND** each tool call shows the tool name and a truncated argument summary
- **AND** clicking a tool call expands to show full arguments and result as formatted JSON

#### Scenario: Error message rendering

- **WHEN** an assistant message has a non-null `error` field
- **THEN** the message renders with a `destructive` border-left accent
- **AND** the error text is shown below any partial content in `text-destructive text-sm`



## dashboard-chat-ui — Message Input Area


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. safe message rendering, accessible recall/search/composer, immutable retry identity, honest streaming, draft retention and in-thread errors. A single assistant goal/thread surface replaces the two unit chat postures. Public unit identity and current-unit switching retire; server-held invocation/region provenance remains inspectable. Sanitized partial rendering, safe citations and persistence are specified in dashboard-goal-message-rendering. The current chat remains until an allocated migration implements every case. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Message Input Area

The message input area SHALL occupy the bottom of the chat panel with a text input and send controls, including cancellation of an active response.

#### Scenario: Text input

- **WHEN** the chat panel is active with a conversation (or ready to start a new one)
- **THEN** a textarea renders at the bottom of the panel with placeholder "Type a message..."
- **AND** the textarea auto-grows with content up to a bounded height and scrolls internally beyond that
- **AND** pressing `Enter` sends the message (without Shift)
- **AND** pressing `Shift+Enter` inserts a newline

#### Scenario: Send button

- **WHEN** the textarea has non-empty content
- **THEN** a send button renders at the right edge of the input area
- **AND** clicking the button sends the message and clears the input

#### Scenario: Input disabled during streaming

- **WHEN** an assistant response is currently streaming
- **THEN** the textarea and send button are disabled
- **AND** a "Stop" button replaces the send button, which calls the
  server-side cancel endpoint (see Stream cancellation below) rather than
  only detaching the client's own stream watch

#### Scenario: Starting a new conversation from empty state

- **WHEN** no conversation is selected and the user types a message and sends it
- **THEN** a new conversation is created via `POST /api/butlers/{name}/conversations` with the message
- **AND** the new conversation appears in the conversation list and is selected



## dashboard-chat-ui — Typing Indicator


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. safe message rendering, accessible recall/search/composer, immutable retry identity, honest streaming, draft retention and in-thread errors. A single assistant goal/thread surface replaces the two unit chat postures. Public unit identity and current-unit switching retire; server-held invocation/region provenance remains inspectable. Sanitized partial rendering, safe citations and persistence are specified in dashboard-goal-message-rendering. The current chat remains until an allocated migration implements every case. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Typing Indicator

A typing indicator SHALL provide visual feedback while the butler is processing a response.

#### Scenario: Typing indicator during processing

- **WHEN** a user message has been sent and the assistant response has not started streaming
- **THEN** a typing indicator renders at the bottom of the message thread, left-aligned (assistant position)
- **AND** the indicator shows three animated dots with a bounce animation (staggered `animation-delay`)

#### Scenario: Typing indicator during streaming

- **WHEN** the assistant response is actively streaming tokens
- **THEN** the typing indicator is replaced by the growing assistant message content



## dashboard-chat-ui — Cost Indicator


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. safe message rendering, accessible recall/search/composer, immutable retry identity, honest streaming, draft retention and in-thread errors. A single assistant goal/thread surface replaces the two unit chat postures. Public unit identity and current-unit switching retire; server-held invocation/region provenance remains inspectable. Sanitized partial rendering, safe citations and persistence are specified in dashboard-goal-message-rendering. The current chat remains until an allocated migration implements every case. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Cost Indicator

Each conversation and message SHALL display cost-related metrics for operator awareness.

#### Scenario: Per-message cost display

- **WHEN** an assistant message has `input_tokens` and `output_tokens`
- **THEN** a cost estimate is displayed alongside the token counts using the dashboard's existing `PricingConfig` model-to-price mapping
- **AND** the estimate renders as muted mono metadata in the form `~$0.0400`

#### Scenario: Conversation total cost

- **WHEN** a conversation is selected and has messages with token counts
- **THEN** the conversation header shows the total estimated cost for the conversation
- **AND** the total is the sum of per-message cost estimates



## dashboard-chat-ui — Conversation Quick-Switch


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. safe message rendering, accessible recall/search/composer, immutable retry identity, honest streaming, draft retention and in-thread errors. A single assistant goal/thread surface replaces the two unit chat postures. Public unit identity and current-unit switching retire; server-held invocation/region provenance remains inspectable. Sanitized partial rendering, safe citations and persistence are specified in dashboard-goal-message-rendering. The current chat remains until an allocated migration implements every case. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Conversation Quick-Switch

The chat UI SHALL allow operators to quickly switch between conversations using keyboard shortcuts.

#### Scenario: Keyboard navigation in conversation list

- **WHEN** the chat panel is open and the user presses `Ctrl+Shift+Up` or `Ctrl+Shift+Down`
- **THEN** the active conversation changes to the previous or next conversation in the list
- **AND** the message thread updates to show the newly selected conversation

#### Scenario: Quick-switch does not conflict with text input

- **WHEN** the cursor is focused in the message textarea
- **THEN** `Ctrl+Shift+Up/Down` still triggers conversation switching (not text editing)



## dashboard-chat-ui — Conversation Search UI


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. safe message rendering, accessible recall/search/composer, immutable retry identity, honest streaming, draft retention and in-thread errors. A single assistant goal/thread surface replaces the two unit chat postures. Public unit identity and current-unit switching retire; server-held invocation/region provenance remains inspectable. Sanitized partial rendering, safe citations and persistence are specified in dashboard-goal-message-rendering. The current chat remains until an allocated migration implements every case. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Conversation Search UI

A search input in the conversation list SHALL enable full-text search across conversation history.

#### Scenario: Search input

- **WHEN** the user types in the search input at the top of the conversation list
- **THEN** a debounced search request is sent to `GET /api/butlers/{name}/conversations/search?q={query}` after 300ms of inactivity
- **AND** the conversation list is replaced with search results showing conversation title and a highlighted snippet

#### Scenario: Clear search

- **WHEN** the user clears the search input (empty text or clicks X)
- **THEN** the conversation list reverts to the standard chronological listing



## dashboard-chat-ui — SSE Client Integration


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. safe message rendering, accessible recall/search/composer, immutable retry identity, honest streaming, draft retention and in-thread errors. A single assistant goal/thread surface replaces the two unit chat postures. Public unit identity and current-unit switching retire; server-held invocation/region provenance remains inspectable. Sanitized partial rendering, safe citations and persistence are specified in dashboard-goal-message-rendering. The current chat remains until an allocated migration implements every case. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: SSE Client Integration

The frontend SHALL connect to the SSE streaming endpoints for real-time response delivery.

#### Scenario: SSE connection for new conversation

- **WHEN** `POST /api/butlers/{name}/conversations` is called
- **THEN** the frontend reads the SSE stream using the Fetch API with `ReadableStream`
- **AND** `conversation_created` events create the conversation in local state
- **AND** `token` events append content to the active assistant message
- **AND** `message_complete` events finalize the message with metadata (model, tokens, duration, tool calls)
- **AND** `error` events display the error in the message thread
- **AND** `done` events close the stream and re-enable the input

#### Scenario: SSE connection for follow-up

- **WHEN** `POST /api/butlers/{name}/conversations/{id}/messages` is called
- **THEN** the same SSE event handling applies as for new conversations (without `conversation_created`)

#### Scenario: Stream cancellation is a real server-side stop

- **WHEN** the user clicks the "Stop" button during streaming
- **THEN** the frontend calls `POST
  /api/butlers/{name}/conversation-turns/{message_id}/cancel`, where
  `message_id` is the client-created immutable user-turn identifier; this
  SHALL work before a new conversation has delivered its `conversation_id`
  over SSE
- **AND** the Stop button enters a pending ("Stopping…") state that prevents
  a second request and exposes its state to assistive technology
- **AND** the backend records cancellation intent against that durable turn,
  preventing any later Switchboard ingress claim, classifier, target-route,
  recovery, or runtime-invoke transition from starting work for it
- **AND** if a runtime already crossed the invocation boundary, the backend
  resolves every exact registered runtime session and kills the subprocesses
  through the `cancel_session` MCP tool (not merely detaching a watcher)
- **AND** only once the server confirms `cancelled: true` **or** emits the
  terminal `SESSION_CANCELLED` SSE outcome from the durable turn record does
  the frontend abort its own SSE watch (`AbortController`) and render the
  partial assistant message with a "Cancelled by owner" indicator, distinct
  from the generic "Interrupted" indicator used for unrelated client-side
  aborts (e.g. component unmount, switching conversations)
- **AND** the input is re-enabled

#### Scenario: Stop click on an already-finished turn is a benign no-op

- **WHEN** the user clicks "Stop" but the turn already completed on the
  routed butler (`already_finished: true` in the cancel response)
- **THEN** the frontend stops watching the stream without rendering
  "Cancelled by owner" or any other claim that it stopped something —
  the (already-arrived or arriving) reply is unaffected

#### Scenario: A failed cancel attempt is never rendered as calm

- **WHEN** the cancel request itself fails (e.g. the routed butler is
  unreachable), an already-ended runtime is still settling, or an irreversible
  action was already committed, so the server cannot confirm cancellation
- **THEN** the frontend surfaces the returned explanation inline in the thread
  and gives the owner an actionable, truthful Stop state
- **AND** it SHALL NOT render "Cancelled by owner", "Interrupted", or any
  other terminal-state indicator implying the session actually stopped

#### Scenario: A same-message ingress is still in progress

- **WHEN** the SSE stream reports `INGEST_IN_PROGRESS` because another caller
  owns or is settling the same immutable dashboard turn
- **THEN** the frontend retains that logical message and presents a
  "Check again" or history-refresh affordance
- **AND** it SHALL NOT offer Retry or issue a new ingestion request for that
  `message_id`
- **AND** `SESSION_CANCELLED` remains a confirmed terminal Stop outcome, while
  `TURN_OUTCOME_UNKNOWN` suppresses automatic replay and surfaces the
  uncertainty honestly



## dashboard-chat-ui — Conversation React Query Hooks


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. safe message rendering, accessible recall/search/composer, immutable retry identity, honest streaming, draft retention and in-thread errors. A single assistant goal/thread surface replaces the two unit chat postures. Public unit identity and current-unit switching retire; server-held invocation/region provenance remains inspectable. Sanitized partial rendering, safe citations and persistence are specified in dashboard-goal-message-rendering. The current chat remains until an allocated migration implements every case. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Conversation React Query Hooks

TanStack Query hooks SHALL manage conversation data fetching and caching.

#### Scenario: useConversations hook

- **WHEN** `useConversations(butlerName, status)` is called
- **THEN** it returns a paginated list of conversations using `useQuery` with key `["conversations", butlerName, "list", params]`
- **AND** `staleTime` is 10 seconds (conversations update frequently during active chat)

#### Scenario: useConversationMessages hook

- **WHEN** `useConversationMessages(butlerName, conversationId)` is called
- **THEN** it returns the message list using `useQuery` with key `["conversation-messages", butlerName, conversationId]`
- **AND** `staleTime` is 0 (always refetch when switching conversations)

#### Scenario: Mutation invalidation

- **WHEN** a new message is sent or a conversation is created
- **THEN** the `["conversations", butlerName]` query is invalidated to refresh the list
- **AND** the `["conversation-messages", butlerName, conversationId]` query is invalidated after `message_complete`



## dashboard-chat-ui — Cross-Butler Conversation Lookup


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. safe message rendering, accessible recall/search/composer, immutable retry identity, honest streaming, draft retention and in-thread errors. A single assistant goal/thread surface replaces the two unit chat postures. Public unit identity and current-unit switching retire; server-held invocation/region provenance remains inspectable. Sanitized partial rendering, safe citations and persistence are specified in dashboard-goal-message-rendering. The current chat remains until an allocated migration implements every case. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Cross-Butler Conversation Lookup

`GET /api/conversations/{conversation_id}` SHALL resolve a conversation's identity by id alone, independent of which butler owns it, for the `/chat/:conversationId` deep link and cmdk recall.

#### Scenario: Lookup succeeds for any owning butler

- **WHEN** `GET /api/conversations/{id}` is called for a conversation owned by any butler's schema
- **THEN** the response returns that conversation's `id`, `butler_name`, `title`, `status`, `created_at`, `updated_at`, `message_count`, and `routed_butler` (when set) as a raw JSON object, not paginated or wrapped in a `data` envelope — this is a single-resource lookup, not a list
- **AND** no butler-scoped filter is required — `public.dashboard_conversations` is a shared, mount-boundary-safe table keyed by its UUID7 primary key

#### Scenario: Unknown id 404s

- **WHEN** `GET /api/conversations/{id}` is called for an id with no matching row
- **THEN** the response is HTTP 404
- **AND** this endpoint being a single-resource, non-aggregating lookup, the fleet-wide degraded-mode/cursor-pagination envelope conventions do not apply — an unreachable shared pool SHALL instead fail with a hard 503, consistent with other single-resource endpoints that have no partial/degraded rendering to offer

#### Scenario: useConversationById hook

- **WHEN** `useConversationById(conversationId)` is called
- **THEN** it returns the lookup using `useQuery` with key `["conversations", "by-id", conversationId]`
- **AND** `retry` is disabled — a 404 here is a legitimate terminal state, not a transient failure worth retrying



## dashboard-chat-ui — Session Linkage Navigation


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. safe message rendering, accessible recall/search/composer, immutable retry identity, honest streaming, draft retention and in-thread errors. A single assistant goal/thread surface replaces the two unit chat postures. Public unit identity and current-unit switching retire; server-held invocation/region provenance remains inspectable. Sanitized partial rendering, safe citations and persistence are specified in dashboard-goal-message-rendering. The current chat remains until an allocated migration implements every case. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Session Linkage Navigation

Assistant messages SHALL link to their corresponding butler sessions for drill-down, and a
session detail with a linked dashboard message SHALL link back to that exact chat message.

#### Scenario: Session link on assistant message

- **WHEN** an assistant message has a non-null `session_id`
- **THEN** a small link icon renders next to the message metadata
- **AND** clicking it navigates to `/sessions/{session_id}` in a new tab (or the same tab with a back-navigation path)

#### Scenario: Request lineage link

- **WHEN** an assistant message has a non-null `request_id`
- **THEN** a "View lineage" link navigates to the ingestion event detail view at `/ingestion?event={request_id}`

#### Scenario: Session detail links back to the originating chat message

- **WHEN** a session detail has a non-null `linked_message` containing a
  `conversation_id` and `message_id`
- **THEN** an "Asked in chat" link navigates to the canonical full-page chat
  route at `/chat/{conversation_id}#m-{message_id}` using both persisted IDs
- **AND** the link does not require the session's owning butler because the
  destination resolves the conversation cross-butler
- **WHEN** `linked_message` is absent or null, or either persisted identifier
  is absent
- **THEN** no "Asked in chat" link is rendered

#### Scenario: A cancelled session's detail renders a first-class "Cancelled" state

- **WHEN** a session's `sessions.error` is the owner-cancellation marker
  (written by `Spawner.cancel_session()`)
- **THEN** the session detail status badge (`/sessions/{id}` and the session
  detail drawer) renders "Cancelled", not the generic destructive "Failed"
  badge used for other error outcomes


## dashboard-butler-management — Butler List Page


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. actual domain records, schedules, source availability, configuration inspection, prompt provenance, memory facts, and unrelated memory/contact navigation. Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Butler List Page

The `/butlers` page SHALL render as a status board: a page-level header strip
showing fleet health at a glance, a unified 4-column grid of butler cells
sorted by recent activity, and a footer KPI band summarising fleet state.

The page SHALL use `<Page archetype="status-board">` as its outer shell. The
archetype shell owns the header strip, the cell grid slot, the footer band
slot, and the page-level loading and error states. Pages MUST NOT reinvent
those surfaces.

Implementation source constraints:

- **No new `ButlerSummary` fields.** Every cell field MUST be composed from
  existing data surfaces. `ButlerSummary` is defined in
  `src/butlers/api/models/__init__.py:101-120` and exposes `name`, `status`,
  `port`, `type`, `description`, and `sessions_24h`.
  The list router constructs summaries in
  `src/butlers/api/routers/butlers.py:124-131`.
  Note: `active_session_count` is NOT a `ButlerSummary` field; it comes from
  `useButlerHeartbeats` (the `ButlerHeartbeat.active_session_count` field,
  `frontend/src/api/types.ts:3626`).
- Cell data MUST be composed exclusively from these five existing data surfaces:
  1. `useButlers` -- butler list (`ButlerSummary` fields)
  2. `useRegistry` -- eligibility state (`RegistryEntry.eligibility_state`)
     via `frontend/src/hooks/use-general.ts:24-30`,
     `frontend/src/api/client.ts:1137-1140`,
     `frontend/src/api/types.ts:1055-1063`
  3. `useButlerHeartbeats` -- last-seen / heartbeat age / active session count
     via `frontend/src/hooks/use-system.ts:71-78`
  4. `useSpendSummary('today').by_butler` -- per-butler spend today via
     `frontend/src/hooks/use-spend.ts:31-47`
  5. Sessions for the last 24h -- fetched via `useQuery(getSessions({ since: <ISO> }))`
     (no new endpoint; the existing sessions endpoint filtered by a rolling ISO
     timestamp, bucketed client-side for the activity stripe)
  - Per-butler load% denominator (`max_concurrent`) comes from the existing
    `GET /api/butlers/{name}/runtime-config` endpoint.
- The cell identity mark MUST use the existing `ButlerMark` component from
  `frontend/src/components/ui/ButlerMark.tsx`.
- The list MUST render API-provided butler and staffer rows only. No butler
  names or types may be hardcoded in the grid render path.

**Doctrine citations** (`about/heart-and-soul/design-language.md`):

- Non-negotiable 2: "The `Page` is a primitive." The status-board page uses
  `<Page archetype="status-board">` and does not reinvent its chrome.
- Non-negotiable 1: "One token system or none." No raw `oklch(...)` values,
  hex literals, or ad-hoc inline styles in cell JSX. All colors use design
  tokens.
- Non-negotiable 4: "Time is a typed primitive." The clock display in the
  header strip and all `last` timestamps in cells MUST render via `<Time>`.
- Non-negotiable 6: "No em-dashes in prose." Cell copy, chip labels, KPI
  labels, and empty-state text MUST NOT contain em-dashes.

#### Scenario: Header strip

- **WHEN** the butler list page is not showing an initial request failure with
  no cached board rows
- **THEN** a header strip SHALL be displayed containing:
  - An eyebrow label (e.g., "Fleet status")
  - An `h1` reading "The staff, at a glance" styled `text-2xl font-bold tracking-tight`
  - A healthy/total pill (count of healthy butlers over total registered count,
    where healthy = total minus `offline`, `quarantined`, `overdue`, and
    activity-derived `unknown` counts, derived from `StatusBoardAggregates`)
  - A clock and date display rendered via `<Time mode="clock-24h-mono">` that
    updates every minute (aligned to minute boundaries via a 60-second interval)
- **AND** the `unknown` count SHALL be derived from canonical `BoardRow.activity`
  values, not from registry eligibility availability

#### Scenario: Unified cell grid sorted by activity

- **WHEN** butler and staffer list rows are loaded from the API
- **THEN** all butlers and staffers SHALL be rendered in a single 4-column grid of
  butler cells without any grouping by type
- **AND** cells SHALL be sorted by `sessions_24h` descending; ties are broken by
  name ascending
- **AND** no butler or staffer SHALL be hidden from the grid; unavailable registry
  rows SHALL render a dim `--` activity verb without removing the cell

Note: the previous Butler List Page requirement asserted "the page preserves
the existing butlers and staffers grouping." That constraint is removed by
this change. The butler/staffer distinction is preserved in the footer KPI
band composition addendum and visually in each cell's `ButlerMark` component.

#### Scenario: Butler cell composition

- **WHEN** a butler cell is rendered
- **THEN** the cell SHALL display:
  - `ButlerMark` component representing the butler's identity
  - The butler's name, capitalized
  - A role tagline sourced from `ButlerSummary.description`
  - An activity chip showing the derived activity verb (see Activity Verb
    Derivation scenario)
  - A KPI quartet: sessions in the last 24h (`sessions_24h`), spend today
    (from `useSpendSummary('today').by_butler`), load% (derived client-side),
    and last active (last heartbeat timestamp from `useButlerHeartbeats`,
    rendered via `<Time>`)
  - A 24h activity stripe pinned to the bottom of the cell, derived from
    the sessions query (`getSessions({ since: <ISO> })`) bucketed client-side
  - A hover affordance (open arrow or equivalent) linking to the butler detail
    page

#### Scenario: Activity stripe is its own nested door (bu-27dxl.8.3)

- **WHEN** a butler cell renders
- **THEN** the 24h activity stripe region (label + bars/skeleton/error) is a
  nested `<button>`, keyboard-accessible and semantically valid HTML (never a
  nested anchor), with an accessible name naming the butler and the activity
  destination
- **AND** activating it routes to `/butlers/<name>?tab=activity`, independent
  of the root tile's own destination, and does not also trigger the root
  tile's navigation (the click does not propagate to the root)
- **AND** the root tile's own Enter/Space keyboard activation continues to
  route to `/butlers/<name>` (Overview), unaffected by the nested control
- **AND** this door remains present and reachable even while the stripe's
  underlying data is loading or has errored -- a sparse/incomplete stripe
  stays navigable, it just makes no claim of completeness in what it visually
  shows

#### Scenario: Activity verb derivation

- **WHEN** a butler's board row is assembled by `GET /api/butlers/board`
- **THEN** the activity verb (`BoardRow.activity`) and chip color
  (`BoardRow.cell_tone`) SHALL be derived SERVER-SIDE by the canonical
  `_derive_board_activity` function (`src/butlers/api/routers/butlers.py`) --
  this endpoint is the single source of the liveness verdict for every
  butler-status surface, replacing the former client-side per-cell derivation
  (bu-86c4c.17) -- using this first-match-wins priority order:
  1. `status = down` (the raw MCP probe result): verb `offline`, tone `red`
  2. `eligibility = quarantined`: verb `quarantined`, tone `red`
  3. `heartbeat_unavailable` is true, OR the registry `last_seen_at` is
     clock-skewed more than 5 minutes into the future (`clock_skewed`): verb
     `unknown`, tone `neutral` (an untrustworthy heartbeat is not a confidently
     healthy one, the bu-y1am9 clock-skew fold)
  4. `active_session_count > 0`: verb `running`, tone `green`
  5. `cadence_status = overdue` (silent longer than twice the butler's own cron
     cadence per `_CADENCE_OVERDUE_FACTOR`, or longer than 5 minutes when no
     cadence is known): verb `overdue`, tone `amber`
  6. otherwise: verb `idle`, tone `neutral`
- **AND** the complete verb set is `running` / `idle` / `overdue` / `offline` /
  `quarantined` / `unknown`, and the complete tone set is `green` / `amber` /
  `red` / `neutral` (there is no `dim` tone).
- **AND** `eligibility` may also be `unavailable` when the butler registry source
  errored (`_fetch_board_row` sets it when `registry_source_error` is true or no
  registry row exists); `unavailable` is NOT `quarantined`, so it does not
  trigger rule 2, and the row's verb is derived from the remaining heartbeat /
  session / cadence signals.
- **AND** the raw `_probe_butler` status (`BoardRow.status`) is only `ok` or
  `down`; the richer `overdue` / `unknown` verbs are produced by the derivation
  above, not by the probe, and no `degraded` or `waiting` status value is
  produced.
- **AND** the mockup verbs `patrol`, `consolidating`, and `ingesting` MUST NOT
  be used. These verbs imply butler-specific semantic knowledge not carried by
  the board row, and are explicitly rejected.

#### Scenario: Canonical cadence labels

- **WHEN** the board derives a human-facing cadence label from a butler's
  shortest enabled cron interval
- **THEN** exactly one hour, one day, and seven days SHALL be labeled `hourly`,
  `daily`, and `weekly` respectively
- **AND** any other positive interval, including two hours, SHALL be labeled
  `custom`
- **AND** a butler with no enabled schedule SHALL retain a null cadence label
- **AND** the raw `cadence_seconds` and cadence-overdue calculation SHALL remain
  authoritative and unchanged

#### Scenario: Load percentage

- **WHEN** the KPI quartet's load field is rendered
- **THEN** load% SHALL be derived client-side as
  `active_session_count / max_concurrent * 100`
- **AND** `max_concurrent` comes from the per-butler `runtime-config`
  (`GET /api/butlers/{name}/runtime-config`), which is the existing runtime
  config endpoint
- **AND** when `max_concurrent` is unknown or zero, the load field SHALL render as
  `--` rather than a percentage

#### Scenario: Eligibility state rail

- **WHEN** a butler cell is rendered with registry data available
- **THEN** a left-edge state rail on the cell SHALL be colored by eligibility:
  - `active` eligibility: emerald rail
  - `stale` eligibility: amber rail, chip is clickable to restore
  - `quarantined` eligibility: red rail, chip is clickable to restore
  - No matching registry entry or unavailable registry response: dim rail
- **AND** clicking a `quarantined` or `stale` chip SHALL schedule the existing
  `setEligibility(name, "active")` mutation (`frontend/src/hooks/use-general.ts:36-53`)
  a fixed undo window (5s) out behind an "Undo" toast action, rather than
  firing it instantly (restore-with-reason-and-undo, JARVIS audit move 6,
  bu-86c4c.15) -- clicking Undo before the window elapses cancels the
  mutation entirely; letting the window elapse fires it exactly as before
- **AND** the cell SHALL NOT be hidden for any eligibility state, including
  unavailable

#### Scenario: Footer KPI band

- **WHEN** the butler list page is not showing an initial request failure with
  no cached board rows
- **THEN** a footer KPI band SHALL be displayed below the cell grid containing:
  - Active butler count (with emerald status-tone dot, shown only when count > 0)
  - Offline butler count (with red status-tone dot, shown only when count > 0)
  - Quarantined butler count (with red status-tone dot, shown only when count > 0)
  - Fleet sessions in the last 24h
  - Fleet spend today (from `useSpendSummary('today')`)
  - Fleet average load% (mean of all per-butler load% values where
    `max_concurrent` is known)
  - A composition addendum showing "Nb butlers, Ns staffers" where Nb and Ns
    are the counts of butlers and staffers respectively in the API response

#### Scenario: Loading state

- **WHEN** any butler list data request is in flight on initial load
- **THEN** the page-level skeleton SHALL render:
  - A header strip skeleton line
  - A 2x4 grid of cell skeletons (8 placeholder cells)
  - A footer band skeleton
- **AND** this skeleton SHALL be owned by the status-board archetype shell, not by
  the page component directly

#### Scenario: Error resilience with stale data

- **WHEN** a refresh request fails but prior butler data exists in cache
- **THEN** the stale butler cells SHALL remain visible in the grid
- **AND** an error banner SHALL be displayed explaining that the shown data is from
  the last successful fetch
- **AND** the header strip and footer KPI band SHALL remain visible with the cached
  board context

#### Scenario: Initial request failure does not fabricate board context

- **WHEN** the initial butler list request fails and no cached board rows exist
- **THEN** the Page error surface and retry control SHALL render
- **AND** the header strip and footer KPI band SHALL NOT render around the error

#### Scenario: Empty state

- **WHEN** the API returns zero butler list rows
- **THEN** an empty-state message SHALL be displayed: "No butlers found" with guidance
  to check daemon status

#### Scenario: Auto-refresh polling

- **WHEN** the butler list page is mounted
- **THEN** the following polling cadences SHALL be maintained:
  - Butler list (`useButlers`): every 30 seconds
  - Registry and heartbeats (`useRegistry`, `useButlerHeartbeats`): every 30
    seconds
  - Cost summary (`useSpendSummary`): every 60 seconds
  - Header strip clock: updates every minute via `<Time mode="clock-24h-mono">`,
    which aligns to the next minute boundary then fires a 60-second interval



## dashboard-butler-management — Butler detail page outer chrome uses status-board archetype


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. actual domain records, schedules, source availability, configuration inspection, prompt provenance, memory facts, and unrelated memory/contact navigation. Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Butler detail page outer chrome uses status-board archetype

The Butler detail page at `/butlers/:name` SHALL use `<Page archetype="status-board">`
as its outer chrome, with the tab block as the sole page body. No footer KPI
band, breadcrumb trail, or heartbeat tile renders at the page layer.

- **Title and description.** The page title is the butler's name titleized
  (e.g., `"relationship"` → `"Relationship"`); the description is the butler
  summary's description when available.
- **Header.** The page header is the butler detail header: the butler identity
  block (letter-mark, name, description, activity status, port, uptime, schedule
  facts) with the page actions inside it. The actions are, in order: a status
  pill, the command bar (see Butler Detail Command Bar), a Logs link to
  `?tab=activity&section=logs`, a Config link to `?tab=system&section=config`, a
  Chat button that opens the butler chat panel, and a Pause/Resume control.
- **Pause/Resume.** Pausing sets the butler's eligibility to `quarantined` and
  resuming sets it to `active`, each behind an undo window. While paused, the
  actions show when and why the butler was quarantined. The command palette
  offers the same Pause or Resume verb.
- **Loading and errors.** The page's loading state reflects the butler record
  fetch; tab-level lazy loading is separate. A failed butler fetch sets the page
  error with a retry that refetches the butler record. Tab errors stay
  tab-scoped.

#### Scenario: Page shell uses status-board archetype

- **WHEN** the butler detail page renders for any butler name
- **THEN** `<Page archetype="status-board">` SHALL be the outer shell
- **AND** no breadcrumb trail, heartbeat tile, or footer KPI band SHALL render
  at the page layer

#### Scenario: Butler name as page title

- **WHEN** the butler detail page renders for butler `"relationship"`
- **THEN** the page title SHALL be `"Relationship"` (titleized)
- **AND** the description SHALL come from the butler summary's description
  when available

#### Scenario: Header slot composition

- **WHEN** the butler detail page renders for a resolved butler
- **THEN** the page header SHALL be the butler detail header, with the page
  actions rendered inside the header rather than as a separate page actions slot
- **AND** the actions SHALL render, in order: status pill, command bar (prompt,
  complexity, Run), Logs link, Config link, Chat, and Pause/Resume
- **AND** the Logs link SHALL target `?tab=activity&section=logs` and the Config
  link SHALL target `?tab=system&section=config`

#### Scenario: Tabs body is the page body

- **WHEN** the butler detail page renders the tab group
- **THEN** the complete tab block (tab rail and every tab body) SHALL be the
  direct body content of the page, with no additional page-layer wrapper

#### Scenario: Top-level loading delegates to Page shell

- **WHEN** the butler record fetch is in flight
- **THEN** the page SHALL be in its loading state
- **AND** the Page shell SHALL render its own built-in loading treatment; the
  page component SHALL NOT pass a bespoke skeleton

#### Scenario: Unknown butler shows shell error

- **WHEN** a user navigates to `/butlers/nonexistent` and the butler record fetch
  returns 404 or an error
- **THEN** the page error SHALL be set
- **AND** its retry SHALL invalidate the butler query and trigger a refetch



## dashboard-butler-management — Overview Tab


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. actual domain records, schedules, source availability, configuration inspection, prompt provenance, memory facts, and unrelated memory/contact navigation. Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Overview Tab

The Butler detail Overview tab SHALL be the operational overview for the
selected butler: a responsive panel grid with up to four KPI columns on wide
viewports. The grid contains, in order: four single-column KPI panels (status,
sessions, spend, awaiting), then full-width-pair panels for 24-hour activity,
recent events, awaiting-your-action, and config, followed by the delegations and
domain-events panels. While the butler record is loading, the tab renders a
matching panel-grid skeleton so the layout does not shift; each panel resolves
its own loading and error state independently. Butler identity lives in the page
header, not in this tab.

#### Scenario: Status KPI panel

- **WHEN** the Overview tab loads for a butler
- **THEN** the "status" panel SHALL show a status dot and label derived from the
  butler status (`ok`/`healthy` → green "online"; `error`/`down` → red; otherwise
  dim), optionally suffixed with the activity verb from the butler's status-board
  row
- **AND** the panel SHALL show a "last run" relative timestamp from the
  status-board row, rendering "--" when unavailable

#### Scenario: Sessions KPI panel

- **WHEN** the "sessions" panel renders
- **THEN** it SHALL show the 24-hour session count from the status-board row,
  falling back to the butler record's 24-hour session count

#### Scenario: Spend KPI panel

- **WHEN** today's spend summary is available
- **THEN** the "spend" panel SHALL show the butler's USD cost for today with a
  per-session cost sub-line, and costs below $0.01 SHALL display as "$0.00"
- **AND** while the spend query is loading the panel SHALL render a skeleton in
  place of the value

#### Scenario: Awaiting KPI panel

- **WHEN** the "awaiting" panel renders
- **THEN** it SHALL show the count of this butler's pending approval actions,
  toned amber when greater than zero, with sub-text "pending review" or "nothing
  pending"

#### Scenario: 24-hour activity stripe panel

- **WHEN** the "activity" panel renders
- **THEN** it SHALL render a 24-bucket activity stripe with a rolling
  relative-time axis (`-24h`, `-12h`, `now`) rather than clock-of-day labels
- **AND** the bucket values SHALL be the status-board row's hourly stripe,
  defaulting to 24 zero buckets when unavailable

#### Scenario: Recent events panel

- **WHEN** the "recent" panel renders
- **THEN** it SHALL show up to five newest activity-feed events for the butler
  from `GET /api/butlers/{name}/activity-feed`, each row showing a relative
  timestamp, the event summary, and an event-kind label
  (session/approval/memory/other)
- **AND** each `session_completed` row SHALL render the API-provided safe summary
  from the same structured-trigger-first projection as Timeline, without a
  client-side raw-prompt or envelope fallback
- **AND** loading SHALL render skeleton rows, errors SHALL render "Could not load
  recent events.", and an empty feed SHALL render "no recent events"

#### Scenario: Awaiting-your-action panel

- **WHEN** the "awaiting your action" panel renders
- **THEN** it SHALL list the pending approval actions (agent summary or tool
  name, relative request time) each with a "review" link to `/approvals`
- **AND** loading SHALL render skeleton rows, errors SHALL render "Could not load
  approvals.", and an empty list SHALL render "no items pending review"
- **AND** the list SHALL come from the same pending-approvals query as the
  awaiting KPI panel

#### Scenario: Config panel

- **WHEN** the "config" panel renders
- **THEN** it SHALL show key/value rows for `port`, `registered` (hours derived
  from `registered_duration_seconds`), `modules` count, `schedules` count, and
  `skills` count, with the panel sub-title set to `config_path` when available
- **AND** the process facts (`port`, `registered_duration_seconds`,
  `config_path`) SHALL NOT render, type, or request a `pid` field, consistent
  with the Config Tab "process" panel
- **AND** missing process-facts source data SHALL render as explicit unavailable
  values ("--") rather than hiding the row



## dashboard-butler-management — Sessions Tab


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. actual domain records, schedules, source availability, configuration inspection, prompt provenance, memory facts, and unrelated memory/contact navigation. Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Sessions Tab
The sessions tab SHALL show paginated session history for the butler with drill-down capability, including model resolution metadata.

#### Scenario: Paginated session table
- **WHEN** the sessions tab is active
- **THEN** sessions are loaded with offset-based pagination (page size 20) and displayed in a session table
- **AND** the butler column is hidden since the context is already butler-scoped
- **AND** each session row shows the model used and complexity tier as a badge

#### Scenario: Session detail drawer
- **WHEN** the operator clicks a session row
- **THEN** a drawer opens showing full session details for the selected session
- **AND** the drawer includes model resolution metadata: model alias, runtime type, complexity tier, and resolution source (catalog or toml_fallback)

#### Scenario: Pagination controls
- **WHEN** the total session count exceeds one page
- **THEN** "Previous" and "Next" buttons are shown with the current page number and total pages
- **AND** "Previous" is disabled on the first page and "Next" is disabled when `has_more` is false



## dashboard-butler-management — Config Tab


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. actual domain records, schedules, source availability, configuration inspection, prompt provenance, memory facts, and unrelated memory/contact navigation. Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Config Tab

The System tab's Config sub-section SHALL provide full transparency into a
butler's configuration: a two-by-two panel grid followed by a collapsed
accordion of the butler's configuration files.

- **Panel grid.** "process" shows container name, port, registered duration,
  and config path (read-only, the same facts as the Overview config panel, never
  a `pid`). "schedule" lists active schedules by name with a relative next-run
  time (empty: "No schedules."). "scopes and oauth" lists each module's OAuth
  status as authorized, unauthorized, or not required (empty: "No modules with
  OAuth."). "integrations" lists enabled modules as badges (empty: "No modules
  enabled.").
- **Accordion.** butler.toml, CLAUDE.md, AGENTS.md, and MANIFESTO.md each render
  as a collapsed item that expands to the file content in a monospace block,
  showing "Not found" when the file is absent. The butler.toml item keeps a
  Formatted/Raw toggle.

#### Scenario: Config 2x2 panel grid

- **WHEN** the Config sub-section loads
- **THEN** four panels SHALL render in two rows of two: process, schedule,
  scopes and oauth, integrations
- **AND** the panels SHALL form a single ruled grid per the shared panel
  vocabulary in dashboard-design-language

#### Scenario: Schedule panel relative timestamps

- **WHEN** the schedule panel renders a schedule's next-run time
- **THEN** the time SHALL be rendered through the shared time primitive in
  relative mode
- **AND** no raw locale formatting or manual date arithmetic SHALL appear

#### Scenario: Config markdown accordion collapsed by default

- **WHEN** the Config sub-section renders
- **THEN** the butler.toml, CLAUDE.md, AGENTS.md, and MANIFESTO.md items SHALL
  be collapsed by default
- **AND** expanding an item SHALL reveal the full file content in a monospace
  block
- **AND** the butler.toml item SHALL keep the "Formatted" / "Raw" toggle, where
  "Formatted" renders the TOML as a structured key-value tree and "Raw" renders
  the JSON representation with 2-space indentation

#### Scenario: Config error and null states

- **WHEN** a config file value is null (e.g., no MANIFESTO.md present)
- **THEN** the accordion item SHALL display "Not found" as its expanded content
- **AND** the item SHALL still be present and expandable
- **AND** when the config API request fails, an error message SHALL be shown with
  the failure reason
- **AND** when the response has no config data, a "No configuration data
  available" message SHALL be displayed



## dashboard-butler-management — Schedules Tab (CRUD)


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. actual domain records, schedules, source availability, configuration inspection, prompt provenance, memory facts, and unrelated memory/contact navigation. Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Schedules Tab (CRUD)
The schedules tab SHALL provide full CRUD management of a butler's scheduled tasks, including complexity tier configuration.

The enabled badge SHALL send an explicit requested `enabled` state through the dashboard schedules API and the canonical `schedule_toggle` MCP action. The tab SHALL wait for the server response rather than claim an optimistic state. A successful response SHALL provide the observed state and safe `schedule.toggle` audit evidence. Missing or managed refusals SHALL appear as errors, never as pause/resume successes. Each row SHALL remain pending until its own toggle request settles, even when another row's request settles first.

The dashboard SHALL attribute each schedule-toggle audit row to the authenticated owner, not the addressed butler. It SHALL retain the butler and schedule as target context and record the observed outcome for success or the bounded refusal code for failure.

#### Scenario: Schedule table columns
- **WHEN** schedules are loaded
- **THEN** a table displays: Name, Cron expression (monospace badge), Mode (prompt/job badge), Prompt/Job details (truncated to 80 chars), Complexity (tier badge), Enabled toggle (On/Off badge, clickable), Source, Next Run (relative time with absolute tooltip), Last Run (relative time with absolute tooltip), and Actions (Edit, Delete)

#### Scenario: Create schedule
- **WHEN** the operator clicks "Add Schedule"
- **THEN** a dialog opens with a form containing: Name (text input), Cron Expression (text input with standard 5-field hint), Mode selector (prompt or job), Complexity (dropdown: trivial, medium, high, extra_high; default medium), and mode-dependent fields
- **AND** in prompt mode: a Prompt textarea is shown
- **AND** in job mode: Job Name input and Job Args JSON textarea are shown
- **AND** the form validates that name and cron are non-empty, prompt is non-empty in prompt mode, and job name is non-empty with valid JSON args in job mode

#### Scenario: Edit schedule
- **WHEN** the operator clicks "Edit" on a schedule row
- **THEN** the same form dialog opens pre-filled with the schedule's existing values including complexity
- **AND** submission triggers an update mutation instead of create

#### Scenario: Delete schedule with confirmation
- **WHEN** the operator clicks "Delete" on a schedule row
- **THEN** a confirmation dialog appears with the schedule name and a warning that the action cannot be undone
- **AND** confirming the deletion triggers the delete mutation and shows a success toast

#### Scenario: Toggle schedule enabled state
- **WHEN** the operator clicks the enabled badge on an active schedule row
- **THEN** the schedule's enabled state is toggled via mutation and a toast confirms the action
- **AND** the mutation sends `enabled: false` to the canonical toggle action
- **AND** only that row's toggle control remains disabled until its request settles
- **AND** a pause success toast appears only after the response reports `observed_enabled=false`
- **WHEN** the operator clicks the disabled badge on a paused schedule row
- **THEN** the mutation sends `enabled: true`
- **AND** a resume success toast appears only after the response reports `observed_enabled=true`

#### Scenario: Server-observed toggle receipt is visible
- **WHEN** a toggle response reports an observed state and `audit.action="schedule.toggle"`
- **THEN** the schedules tab renders the observed enabled/disabled state
- **AND** it exposes the safe audit action/result as a local receipt

#### Scenario: Schedule toggle audit distinguishes actor from target
- **WHEN** the owner requests a schedule toggle that succeeds or receives a managed refusal
- **THEN** the audit row records the authenticated owner as actor
- **AND** it records the addressed butler and schedule separately as target context
- **AND** it records the observed outcome on success or the bounded refusal code on failure

#### Scenario: Managed or missing toggle refusal remains an error
- **WHEN** the canonical action returns `SCHEDULE_NOT_FOUND`, `SCHEDULE_TOML_MANAGED`, or `SCHEDULE_MANAGED`
- **THEN** the schedules tab shows the typed failure
- **AND** it does not show a success toast or optimistic state change for the refused row
- **AND** that row's toggle control becomes available again after the refusal

#### Scenario: Overlapping row toggles settle independently
- **WHEN** toggles for two different schedule rows are pending at the same time
- **AND** either request succeeds or is refused before the other settles
- **THEN** the settled row's toggle control becomes available
- **AND** the other row's toggle control remains disabled until its own request settles

#### Scenario: Repeated requested state does not double-flip
- **WHEN** the operator retries a toggle after the server already observes the requested state
- **THEN** the schedules tab accepts the `already_requested` receipt as the observed truth
- **AND** it does not imply that a second state transition occurred

#### Scenario: Auto-refresh
- **WHEN** the schedules tab is mounted
- **THEN** schedule data is polled every 30 seconds



## dashboard-butler-management — MCP Debug Tab


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. actual domain records, schedules, source availability, configuration inspection, prompt provenance, memory facts, and unrelated memory/contact navigation. Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: MCP Debug Tab
The MCP tab SHALL provide a debugging interface for directly invoking MCP tools on a butler.

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



## dashboard-butler-management — State Tab (CRUD)


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. actual domain records, schedules, source availability, configuration inspection, prompt provenance, memory facts, and unrelated memory/contact navigation. Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: State Tab (CRUD)
The state tab SHALL provide a browser and editor for the butler's key-value state store.

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



## dashboard-butler-management — Activity tab


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. actual domain records, schedules, source availability, configuration inspection, prompt provenance, memory facts, and unrelated memory/contact navigation. Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Activity tab

The Activity tab's Analytics sub-section SHALL be the per-butler session
analytics surface: a KPI quartet, a range-switchable activity chart, and a
session-kind breakdown. A range toggle (`24h`, `7d`, `30d`, default `24h`) sets
the window for every panel.

Data comes from the butler-scoped session analytics endpoints
`GET /api/butlers/{name}/analytics/hourly-activity`, `.../daily-activity`,
`.../latency-stats`, and `.../session-kinds`, plus the session aggregate for the
failed-session count.

#### Scenario: Activity tab KPI quartet

- **WHEN** the Activity tab loads
- **THEN** four KPI cells SHALL render: Sessions (the sum of the session-kind
  counts in the window), p50 ms and p95 ms (session latency percentiles for the
  window), and Errors (sessions that did not succeed in the window)
- **AND** the Errors value SHALL use the high-severity tone when greater than
  zero
- **AND** when the metrics fail to load, the quartet SHALL show "Could not load
  activity metrics."

#### Scenario: Activity stripe for 24h range

- **WHEN** the range is `24h`
- **THEN** the activity panel SHALL render a 24-column hourly activity stripe
  from the hourly-activity endpoint
- **AND** a failed request SHALL show "Could not load hourly activity."

#### Scenario: Day bars for 7d or 30d range

- **WHEN** the range is `7d` or `30d`
- **THEN** the activity panel SHALL render 7 or 30 daily bars from the
  daily-activity endpoint
- **AND** a failed request SHALL show "Could not load daily activity."

#### Scenario: Kind breakdown panel

- **WHEN** the session-kinds endpoint returns results
- **THEN** the "By kind" panel SHALL list each session kind with its count in
  tabular numerals
- **AND** a failed request SHALL show "Could not load session kind breakdown."

#### Scenario: Activity tab empty state

- **WHEN** no sessions exist for the butler in the selected window
- **THEN** the kind breakdown SHALL show "No sessions in this window."
- **AND** KPI cells whose value is unavailable SHALL render a placeholder rather
  than a fabricated `0`



## dashboard-butler-management — Logs tab


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. actual domain records, schedules, source availability, configuration inspection, prompt provenance, memory facts, and unrelated memory/contact navigation. Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Logs tab

The Activity tab's Logs sub-section SHALL be a structured viewer over the
butler's daemon log, read from `GET /api/butlers/{name}/logs` and polled every
five seconds while visible. It renders one full-width scroll panel with level
filter chips and log lines in fixed timestamp, level, and message columns.

#### Scenario: Log level filter chips

- **WHEN** the Logs sub-section is active
- **THEN** filter chips SHALL render for All, DEBUG, INFO, WARN, and ERROR, with
  All selected by default
- **AND** exactly one chip SHALL be active at a time
- **AND** selecting a level chip SHALL refetch the log list at that level

#### Scenario: Log line column widths

- **WHEN** log lines render
- **THEN** each line SHALL show a fixed-width millisecond-precision timestamp, a
  fixed-width level, and a message filling the remaining width, all in the
  monospace family
- **AND** timestamps and levels SHALL stay column-aligned across lines

#### Scenario: Log level color tokens

- **WHEN** a log line has level WARN
- **THEN** the level text SHALL use the amber token
- **AND** no oklch literal or hex color SHALL be used

- **WHEN** a log line has level ERROR
- **THEN** the level text SHALL use the destructive token

#### Scenario: Logs tab auto-scroll

- **WHEN** auto-scroll is on (the default)
- **THEN** the list SHALL scroll to the newest entry as polls deliver new lines
- **AND** scrolling up SHALL pause following until the operator re-enables
  auto-scroll

#### Scenario: Logs tab empty state

- **WHEN** the logs endpoint returns no entries for the selected level
- **THEN** the panel SHALL display "No logs yet." in muted text



## dashboard-butler-management — Approvals tab


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. actual domain records, schedules, source availability, configuration inspection, prompt provenance, memory facts, and unrelated memory/contact navigation. Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Approvals tab

The Approvals tab SHALL list the pending approval actions for the current
butler in one full-width scroll panel, each row deep-linking into that action.

#### Scenario: Approvals list with pending items

- **WHEN** the Approvals tab loads for a butler with pending approval actions
- **THEN** each pending action SHALL render a severity dot, the tool name as
  title, a sub-line with the agent summary and relative age, and a "Review" link
  to `/approvals/{action_id}`
- **AND** severity SHALL derive from the action's expiry: high (destructive fill)
  when it expires within one hour or has expired, medium (amber fill) within 24
  hours, and low (muted fill) otherwise or with no expiry

#### Scenario: Approvals empty state

- **WHEN** no pending approvals exist for the butler
- **THEN** the panel SHALL display "No items pending review." in muted text

#### Scenario: Approvals age rendering

- **WHEN** a pending approval item is rendered
- **THEN** its age SHALL render through the shared time primitive in compact
  relative form (e.g., "3m ago")
- **AND** no raw locale formatting or manual date arithmetic SHALL be used



## dashboard-butler-management — Spend tab


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. actual domain records, schedules, source availability, configuration inspection, prompt provenance, memory facts, and unrelated memory/contact navigation. Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Spend tab

The Spend tab SHALL be the per-butler cost surface: a KPI quartet, a daily spend
trend with a range toggle (`24h`, `7d`, `30d`), and a 30-day model breakdown.
When the spend source is unavailable or some model usage is unpriced, the tab
SHALL say so in a degraded-source note rather than presenting partial totals as
complete.

#### Scenario: Spend KPI quartet

- **WHEN** the Spend tab loads
- **THEN** four KPI cells SHALL render: Spend today, Spend 30d, Cost / session ·
  30d, and Tokens today
- **AND** cost values SHALL be formatted as USD (e.g., "$0.04")
- **AND** zero or unavailable spend values SHALL render dimmed

#### Scenario: Spend trend bar chart

- **WHEN** the range is `24h`, `7d`, or `30d`
- **THEN** the trend panel SHALL render one bar per UTC day over the last 1, 7,
  or 30 days respectively
- **AND** a failed request SHALL show "Could not load spend trend."

#### Scenario: Model breakdown KV list

- **WHEN** 30-day model cost data is available
- **THEN** each model SHALL be listed with its cost, sorted by cost descending,
  in tabular numerals
- **AND** a failed request SHALL show "Could not load model breakdown."

#### Scenario: Spend tab empty state

- **WHEN** no spend data exists for the window
- **THEN** the trend panel SHALL show "No spend data for this period." and the
  model breakdown SHALL show "No model usage data available."



## dashboard-butler-management — Health Tab (Butler-Specific)


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. actual domain records, schedules, source availability, configuration inspection, prompt provenance, memory facts, and unrelated memory/contact navigation. Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Health Tab (Butler-Specific)
The health butler's bespoke tab SHALL be labeled "Measurements" and render a
panel-grid health data surface, appended only for the `health` butler.

#### Scenario: Health butler context
- **WHEN** the "Measurements" tab is viewed for the `health` butler
- **THEN** a panel grid renders health KPIs and trend panels (glucose, heart rate, HRV, weight, sleep) plus active medications and recent conditions
- **AND** a drilldown link to `/health/measurements` is preserved

#### Scenario: Non-health butler
- **WHEN** any butler other than `health` is viewed
- **THEN** no "Measurements" tab is appended (the tab is conditionally rendered only for `health`); no placeholder message is shown



## dashboard-butler-management — Switchboard Registry Tab


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. actual domain records, schedules, source availability, configuration inspection, prompt provenance, memory facts, and unrelated memory/contact navigation. Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Switchboard Registry Tab
The registry tab (switchboard-only) SHALL show the authoritative butler registry with liveness information.

#### Scenario: Registry table columns
- **WHEN** the registry tab loads on the switchboard butler
- **THEN** a table displays: Name, Endpoint URL (monospace), Modules (badge per module, parsed from comma-separated strings, JSON arrays, or nested string arrays), Description (truncated), and Last Seen (relative time via `formatDistanceToNow`)

#### Scenario: Module normalization
- **WHEN** the registry data contains modules in various formats (comma-separated string, JSON array string, nested arrays)
- **THEN** modules are normalized to a flat list of badge-rendered module names with a recursion depth limit of 10

#### Scenario: Empty registry
- **WHEN** no butlers are registered in the switchboard
- **THEN** a centered empty state message is shown



## dashboard-butler-management — Switchboard Routing Log Tab


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. actual domain records, schedules, source availability, configuration inspection, prompt provenance, memory facts, and unrelated memory/contact navigation. Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Switchboard Routing Log Tab
The routing log tab (switchboard-only) SHALL show inter-butler request routing activity.

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



## dashboard-butler-management — Butler Detail Page — Dispatch Fold-In


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. actual domain records, schedules, source availability, configuration inspection, prompt provenance, memory facts, and unrelated memory/contact navigation. Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Butler Detail Page — Dispatch Fold-In
The existing `/butlers/{name}` detail page SHALL fold in the `ButlersExpanded` design, with sections for fallback chain, system prompt, tools, memory access, activity, and kill switch.

#### Scenario: Page structure post-fold-in
- **WHEN** a user navigates to `/butlers/{name}`
- **THEN** the page renders the existing tab archetype plus, on the "Configuration" or equivalently-named tab, sections in this order:
  - **§1 Identity & routing** — fallback chain (primary + ordered fallbacks; `+ add fallback` link), schedule, `$/day ceiling`, approvals policy, timeout, concurrency.
  - **§2 System prompt** — serif prompt body, mono caption (`tokens · NNN · last edit · <actor>`), links `history · N versions →` and `diff vs vN-1 →`.
  - **§3 Tools & integrations** — table of `tool · description · scope · on` rows with toggles.
  - **§4 Memory access** — three tiles for short / mid / long term, each with read/write badges.
  - **§5 Activity** — 24h stripe-chart (sessions per hour).
  - **§6 Kill switch** — `kill switch · 30s grace →` link.

#### Scenario: Kill switch with grace
- **WHEN** a user clicks the kill switch link
- **THEN** a confirmation modal appears showing the grace seconds and the butler name
- **AND** on confirm, `POST /api/butlers/{name}/kill {grace_seconds: 30}` is called
- **AND** `audit.append("butler.kill", target=butler_name, note=f"grace={grace_seconds}s")` is invoked
- **AND** the butler initiates shutdown after the grace window.



## dashboard-butler-management — System Prompt Versioning API


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. actual domain records, schedules, source availability, configuration inspection, prompt provenance, memory facts, and unrelated memory/contact navigation. Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: System Prompt Versioning API
The dashboard SHALL expose CRUD over a butler's system prompt with version history.

#### Scenario: Read current prompt
- **WHEN** `GET /api/butlers/{name}/prompt` is called
- **THEN** the response is `ApiResponse[PromptVersion]` with `prompt: str`, `version: int`, `updated_at`, `updated_by`.

#### Scenario: Update prompt snapshots history
- **WHEN** `PUT /api/butlers/{name}/prompt {prompt: str}` is called
- **THEN** the current row is inserted into `public.system_prompt_history` (the snapshot), then the new prompt is stored as the current version with `version = old.version + 1`
- **AND** `audit.append("butler.prompt", target=butler_name, note=f"v{new_version}")` is invoked.

#### Scenario: Prompt history list
- **WHEN** `GET /api/butlers/{name}/prompt/history?limit=20` is called
- **THEN** the response is `PaginatedResponse[PromptVersion]` ordered `version DESC`, defaulting to the most recent 20 versions.



## dashboard-butler-management — Tools & Scope API


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. actual domain records, schedules, source availability, configuration inspection, prompt provenance, memory facts, and unrelated memory/contact navigation. Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Tools & Scope API
The dashboard SHALL expose per-butler tool grants and scopes.

#### Scenario: Read tools
- **WHEN** `GET /api/butlers/{name}/tools` is called
- **THEN** the response is `ApiResponse[ButlerTool[]]` with `name`, `description`, `allowed: bool`, `scope: str | null`.

#### Scenario: Update a tool grant
- **WHEN** `PUT /api/butlers/{name}/tools/{tool} {allowed: bool, scope?: str}` is called
- **THEN** the grant is updated atomically
- **AND** `audit.append("butler.tool", target=f"{name}.{tool}", note=f"allowed={allowed}")` is invoked.



## dashboard-butler-management — Memory Access Tiles API


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. actual domain records, schedules, source availability, configuration inspection, prompt provenance, memory facts, and unrelated memory/contact navigation. Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Memory Access Tiles API
The dashboard SHALL expose per-butler memory tier access.

#### Scenario: Read memory access
- **WHEN** `GET /api/butlers/{name}/memory-access` is called
- **THEN** the response is `ApiResponse[MemoryAccess]` with `read: ("short"|"mid"|"long")[]`, `write: ("short"|"mid"|"long")[]`, `namespace: str`, `embedding_model: str`, `drops_7d: int`.



## dashboard-butler-management — Butler detail header schedule facts are truthful


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. actual domain records, schedules, source availability, configuration inspection, prompt provenance, memory facts, and unrelated memory/contact navigation. Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Butler detail header schedule facts are truthful

`ButlerDetailHeader` SHALL derive schedule facts only from enabled schedule
rows with a parseable finite `next_run_at` instant. The header SHALL use the
existing read-only schedule query and SHALL NOT write schedules, alter
scheduler calculations, or derive status-board activity. A scheduled instant
at or before the current wall clock is overdue; an instant strictly after it
is future-next. The header SHALL recompute that classification on schedule
polling and at least once per minute while mounted.

#### Scenario: Earliest future schedule is shown as next

- **WHEN** one or more enabled schedules have parseable `next_run_at` values
  strictly after the current wall clock
- **THEN** the header SHALL render the earliest such timestamp as its `next`
  fact using the shared `<Time>` primitive
- **AND** no later future schedule SHALL replace that fact

#### Scenario: Stale schedule is shown as an actionable overdue fact

- **WHEN** one or more enabled schedules have parseable `next_run_at` values
  at or before the current wall clock
- **THEN** the header SHALL render the oldest such timestamp as a visibly
  named `overdue` fact with the schedule name and a deterministic relative age
- **AND** the fact SHALL use the established amber foreground token and an
  accessible link name that does not rely on color alone
- **AND** the fact link SHALL target
  `/butlers/:name?tab=system&section=schedules`
- **AND** the stale timestamp SHALL NOT render as a literal `next` fact

#### Scenario: Overdue and future facts coexist

- **WHEN** enabled schedules include both overdue and future parseable
  `next_run_at` values
- **THEN** the header SHALL keep the most-overdue named fact visible
- **AND** the earliest independently truthful future-next fact SHALL remain
  visible

#### Scenario: Unusable timestamps do not fabricate certainty

- **WHEN** a schedule is disabled, has a null timestamp, has a malformed
  timestamp, or has an unparsable timestamp
- **THEN** that row SHALL contribute neither an overdue nor a future-next fact
- **AND** the header SHALL render no fabricated schedule age or future time for
  that row

#### Scenario: Equal schedule instants select a stable named fact

- **WHEN** multiple enabled parseable schedules tie for the selected overdue
  or future timestamp
- **THEN** the header SHALL select the fact deterministically by schedule name
  and then schedule id



## dashboard-butler-management — Truthful Status-Board Summary and Error Composition


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. actual domain records, schedules, source availability, configuration inspection, prompt provenance, memory facts, and unrelated memory/contact navigation. Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Truthful Status-Board Summary and Error Composition

The `/butlers` status board SHALL present fleet health from the canonical
server-derived `BoardRow.activity` vocabulary. A healthy count SHALL exclude
every row whose activity is `offline`, `quarantined`, `overdue`, or `unknown`.
The `unknown` aggregate SHALL be derived from canonical row activity, not from
registry `eligibility = unavailable`, which remains a separate availability
diagnostic.

The Page shell SHALL omit status-board header and footer slots only when its
initial board request has failed and no cached rows exist. A normal empty
response and initial loading continue to use the shell’s existing behavior.

#### Scenario: Fleet health excludes every non-healthy activity

- **WHEN** board rows contain one or more `offline`, `quarantined`, `overdue`,
  or `unknown` canonical activity verdicts
- **THEN** the header’s healthy/total pill subtracts all four counts from the
  registered total
- **AND** registry availability alone SHALL NOT change the `unknown` count or
  make a row appear unhealthy without its canonical activity verdict

#### Scenario: Initial failure has no misleading board chrome

- **WHEN** the initial board request fails and no cached rows are available
- **THEN** the Page error region renders the error and retry control
- **AND** the status-board header and footer SHALL NOT render around that error

#### Scenario: Cached refresh failure keeps contextual chrome

- **WHEN** a board refresh fails after one or more cached rows were loaded
- **THEN** the cached rows, status-board header, and footer remain visible
- **AND** the page renders its stale-data warning instead of replacing the
  board with a full-page error



## dashboard-butler-management — Canonical Status-Board Cadence Labels


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. actual domain records, schedules, source availability, configuration inspection, prompt provenance, memory facts, and unrelated memory/contact navigation. Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Canonical Status-Board Cadence Labels

The board’s human-facing cadence label SHALL describe only a canonical
interval: exactly one hour is `hourly`, exactly one day is `daily`, and
exactly seven days is `weekly`. A positive interval that is not one of those
canonical values, including two hours, SHALL be labeled `custom`. A butler with
no enabled schedule SHALL retain a null cadence label. The raw
`cadence_seconds` and cadence-overdue calculation remain authoritative and
unchanged.

#### Scenario: Canonical cadence interval has its named label

- **WHEN** a butler’s shortest enabled cron interval is exactly one hour, one
  day, or seven days
- **THEN** its board row exposes `hourly`, `daily`, or `weekly` respectively

#### Scenario: Noncanonical cadence avoids an inaccurate named label

- **WHEN** a butler’s shortest enabled cron interval is two hours or any other
  positive noncanonical duration
- **THEN** its board row exposes `cadence_label = custom`
- **AND** it SHALL NOT label that duration `hourly`, `daily`, or `weekly`



## dashboard-butler-management — Butler Detail Tab Set


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. actual domain records, schedules, source availability, configuration inspection, prompt provenance, memory facts, and unrelated memory/contact navigation. Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Butler Detail Tab Set

The `/butlers/:name` page SHALL render one tab vocabulary for every butler: six
base tabs (Overview, Activity, Approvals, Spend, Memory, System) plus the
butler's domain tabs from the Butler Domain Tabs registry. The tab rail shows
Overview, Activity, Approvals, Spend, and Memory first, then the butler's domain
tabs, and System last. No mode toggle exists; every tab is reachable for every
butler that carries it.

The Activity tab groups three sub-sections: Analytics (default, see Activity
tab), Sessions (see Sessions Tab), and Logs (see Logs tab). The System tab
groups seven sub-sections: Config (default, see Config Tab), Skills (see Skills
Section), Schedules (see Schedules Tab (CRUD)), MCP (see MCP Debug Tab), State
(see State Tab (CRUD)), Models, and Manage.

#### Scenario: URL-driven tab routing

- **WHEN** a user navigates to `/butlers/:name?tab=<value>`
- **THEN** the active tab SHALL be the `tab` value when it is a base tab or one
  of that butler's domain tabs
- **AND** when `tab` is absent or not valid for that butler, the active tab
  SHALL be Overview
- **AND** selecting Overview SHALL remove the `tab` parameter from the URL
- **AND** tab changes SHALL replace the current history entry rather than push
  a new one

#### Scenario: Base tabs for every butler

- **WHEN** any butler detail page loads
- **THEN** the tab rail SHALL show Overview, Activity, Approvals, Spend, Memory,
  and System
- **AND** no mode toggle or mode-gated tab SHALL be rendered

#### Scenario: Domain tabs precede System

- **WHEN** a butler with domain tabs is viewed (for example `switchboard`)
- **THEN** its domain tabs (Routing Log, Registry) SHALL appear after Memory and
  before System
- **AND** System SHALL remain the last tab

#### Scenario: Section sub-navigation is URL-driven

- **WHEN** the Activity or System tab is active
- **THEN** the active sub-section SHALL be carried in the `section` query
  parameter
- **AND** an absent or invalid `section` SHALL select the default sub-section
  (Analytics for Activity, Config for System)
- **AND** selecting the default sub-section SHALL remove `section` from the URL
- **AND** selecting Analytics SHALL also clear any `since` and `until` filters

#### Scenario: Deep link into a sub-section

- **WHEN** a user navigates to `/butlers/:name?tab=system&section=schedules`
- **THEN** the System tab SHALL open with the Schedules sub-section active

#### Scenario: Lazy-loaded tab bodies

- **WHEN** a tab or sub-section other than Overview, Activity analytics, or
  System config is selected for the first time
- **THEN** its body SHALL load on demand with a centered "Loading {label}..."
  fallback while it loads

#### Scenario: Keyboard tab navigation

- **WHEN** the butler detail page is focused
- **THEN** the digit keys 1 through 9 SHALL switch to the first nine tabs in
  rail order
- **AND** `[` and `]` SHALL switch to the previous and next tab, wrapping at the
  ends

#### Scenario: Reload from the command palette

- **WHEN** the butler detail page is open
- **THEN** the command palette SHALL offer a "Reload {butler}" command that
  refetches the butler record



## dashboard-butler-management — Butler Domain Tabs


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. actual domain records, schedules, source availability, configuration inspection, prompt provenance, memory facts, and unrelated memory/contact navigation. Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Butler Domain Tabs

A butler's detail page SHALL append the domain tabs registered for that butler
in the table below, and no others. Domain tab presence is decided by the butler
name in the frontend tab registry, never by `butler.toml` fields or runtime API
responses. Labels are sentence-case with no punctuation.

| Butler | Domain tabs (tab key: label) |
|---|---|
| chronicler | `timelines`: Timelines |
| education | `reviews`: Reviews |
| finance | `finances`: Finances |
| general | `collections`: Collections; `entities`: Entities |
| health | `health`: Measurements |
| home | `devices`: Devices |
| lifestyle | `taste`: Taste |
| qa | `investigations`: Investigations |
| relationship | `contacts`: Contacts |
| switchboard | `routing-log`: Routing Log; `registry`: Registry |
| travel | `trips`: Trips |

Butlers absent from the table carry no domain tab. Domain tab bodies use the
shared panel vocabulary specified in dashboard-design-language.

#### Scenario: Each butler renders its registered domain tab labels

- **WHEN** a butler listed in the registry is viewed
- **THEN** the tab rail SHALL show exactly that butler's registered domain tab
  labels, in registry order, between Memory and System
- **AND** no butler SHALL render a domain tab registered to another butler

#### Scenario: Butler without a domain tab

- **WHEN** a butler absent from the registry is viewed
- **THEN** the tab rail SHALL show only the six base tabs

#### Scenario: Domain tab deep link

- **WHEN** a user navigates to `/butlers/relationship?tab=contacts`
- **THEN** the Contacts tab SHALL be active
- **AND** navigating to `/butlers/finance?tab=contacts` SHALL fall back to
  Overview

#### Scenario: Domain tab is lazy-loaded

- **WHEN** a domain tab is selected for the first time
- **THEN** its body SHALL load on demand with a "Loading {label}..." fallback
  naming that tab



## dashboard-butler-management — Butler Detail Command Bar


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. actual domain records, schedules, source availability, configuration inspection, prompt provenance, memory facts, and unrelated memory/contact navigation. Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Butler Detail Command Bar

The butler detail header SHALL provide a prompt-first command bar that starts a
session for the butler: a prompt input, a complexity selector listing the
backend complexity tiers (default workhorse), and a Run button.

#### Scenario: Empty prompt fires the scheduled tick

- **WHEN** the operator presses Run (or Enter) with an empty prompt
- **THEN** the butler SHALL be triggered with its default scheduled-tick prompt
  at the selected complexity
- **AND** a "Force run triggered" confirmation toast SHALL appear

#### Scenario: Custom prompt starts a session

- **WHEN** the operator enters a prompt and presses Run
- **THEN** the butler SHALL be triggered with that prompt at the selected
  complexity
- **AND** a "Prompt sent" confirmation toast SHALL appear

#### Scenario: Run navigates to the new session

- **WHEN** a trigger succeeds and returns a session id
- **THEN** the page SHALL navigate to `/sessions/{session_id}`

#### Scenario: Run failure and in-flight state

- **WHEN** a trigger is in flight
- **THEN** the prompt input, complexity selector, and Run button SHALL be
  disabled and the button SHALL read "Running…"
- **AND** when the trigger fails, a "Failed to run butler" error toast SHALL
  appear



## dashboard-butler-management — Skills Section


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. actual domain records, schedules, source availability, configuration inspection, prompt provenance, memory facts, and unrelated memory/contact navigation. Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Skills Section

The System tab's Skills sub-section SHALL show all skills available to a butler
with drill-down into each skill's SKILL.md.

#### Scenario: Skill card grid

- **WHEN** skills are loaded
- **THEN** each skill SHALL render as a card in a responsive grid showing the
  skill name, a "skill" badge, and the first non-heading, non-empty line of the
  SKILL.md content as a description, truncated to 120 characters

#### Scenario: Skill detail dialog

- **WHEN** the operator clicks "View" on a skill card
- **THEN** a dialog SHALL open showing the skill name as title and the full
  SKILL.md content in a scrollable monospace block

#### Scenario: Use a skill from the command bar

- **WHEN** the operator clicks "Use skill" on a skill card
- **THEN** the header command bar prompt SHALL be prefilled with
  `Use the <skill> skill to ` and focused with the caret at the end
- **AND** the active tab and System sub-section SHALL NOT change
- **AND** no session SHALL start until the operator presses Run



## dashboard-butler-management — Butler Memory Tab Surface


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. actual domain records, schedules, source availability, configuration inspection, prompt provenance, memory facts, and unrelated memory/contact navigation. Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Butler Memory Tab Surface

The butler detail Memory tab SHALL surface the butler's memory state as a KPI
quartet over memory counts and a recent-writes feed, both scoped to the
current butler. The cross-butler memory browser lives on `/memory`, not in this
tab.

#### Scenario: Memory KPI quartet with "+N today" sub-lines

- **WHEN** the Memory tab loads
- **THEN** four KPI cells SHALL render: Episodes, Facts, Entities, Rules
- **AND** each cell's sub-line SHALL show "+N today", where N is the count added
  in the last 24 hours
- **AND** when the stats request fails, the quartet SHALL show "Could not load
  memory stats."

#### Scenario: Recent-writes feed

- **WHEN** the recent-writes panel renders
- **THEN** it SHALL list the butler's ten most recent episodes, newest first,
  each with a relative timestamp, the butler name, and a one-line content
  preview
- **AND** the panel SHALL be a fixed-height scroll region so no entry is cut off
  without scroll access
- **AND** when the request fails, it SHALL show "Could not load recent writes."

#### Scenario: Memory tab empty state

- **WHEN** no episodes exist for the butler
- **THEN** the recent-writes panel SHALL show "No memory writes recorded yet."
- **AND** the KPI cells SHALL render zero counts with "+0 today" sub-lines



## dashboard-butler-management — Non-Butler Page Tab Structures


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. actual domain records, schedules, source availability, configuration inspection, prompt provenance, memory facts, and unrelated memory/contact navigation. Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Non-Butler Page Tab Structures

Tabbed structures outside the butler detail view SHALL behave as specified here.

#### Scenario: Memory browser tabs

- **WHEN** the `/memory` page's browser renders
- **THEN** it SHALL offer three registers: Facts, Rules, Episodes

#### Scenario: Contact detail redirect

- **WHEN** `/contacts/:contactId` is visited
- **THEN** the route SHALL replace-navigate to `/entities/index?has=contact`
- **AND** it SHALL NOT render the retired contact-detail page or its former tabs



## dashboard-butler-management — Composed prompt preview and roster drift truth


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. actual domain records, schedules, source availability, configuration inspection, prompt provenance, memory facts, and unrelated memory/contact navigation. Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Composed prompt preview and roster drift truth

The existing butler Configuration prompt section SHALL show the currently composed prompt preview
rather than treating an absent database prompt row as absence of a system prompt. It SHALL compare
current roster-source digests with the latest executed prompt receipt and display exactly one of
`matches_git`, `drifted`, or `unknown`, with bounded roster-relative changed source names and no
claim about dynamic-layer equality.

#### Scenario: Composed prompt exists without a database row
- **WHEN** a known roster butler has no database prompt-history row but its roster composition is available
- **THEN** the Configuration section renders the composed prompt
- **AND** it does not render `No system prompt configured.`

#### Scenario: Roster source changes after execution
- **WHEN** a roster file in a synthetic fixture tree changes after the newest session receipt
- **THEN** the drift projection is `drifted`
- **AND** it names the changed roster-relative source and the comparison time without exposing other prompt content

#### Scenario: No executed receipt exists
- **WHEN** no session prompt receipt exists for the butler
- **THEN** the drift projection is `unknown`, never a green match

#### Scenario: Unavailable or corrupt receipt remains explicit
- **WHEN** the effective-prompt query fails or returns a corrupt receipt without verified prompt bytes
- **THEN** the Configuration section renders an explicit unavailable or corrupt receipt state
- **AND** it does not substitute or label the mutable authoring prompt as composed runtime instructions
- **AND** the separate prompt edit control remains available



## dashboard-butler-management — Prompt preview does not expand authoring authority


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. actual domain records, schedules, source availability, configuration inspection, prompt provenance, memory facts, and unrelated memory/contact navigation. Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Prompt preview does not expand authoring authority

The composed preview and drift projection SHALL be read-only additions. They SHALL NOT add a new
prompt authoring surface, change existing prompt PUT semantics, reinterpret a legacy database row
as an owner overlay, or modify roster/manifesto content.

#### Scenario: Existing edit control remains bounded
- **WHEN** the composed preview is rendered
- **THEN** existing prompt edit behavior remains separate from receipt and drift metadata
- **AND** neither preview nor drift performs a prompt, roster, or manifesto write

## Source References

- `about/heart-and-soul/design-language.md`: token, `<Page>`, `<Time>`, voice,
  and type-system doctrine the detail page follows.
- `frontend/src/pages/ButlerDetailPage.tsx` and
  `frontend/src/pages/butler-detail-tabs.ts`: detail page shell, tab rail, and
  domain tab registry.
- `frontend/src/components/butler-detail/`: header, command bar, Activity and
  System sections, and every tab body.
- `src/butlers/api/routers/butlers.py`, `butler_management.py`,
  `butler_logs.py`, and `sessions.py` (under `src/butlers/api/routers/`):
  butler record, prompt and tool management, log, and session analytics
  endpoints.
- Reuses `audit.append()` from dashboard-audit-log on every dispatch mutation.


## dashboard-model-settings — Butler Model Override API


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. global catalog/configuration truth and historical model, tier, override and failure evidence. Unit trigger complexity and per-unit runtime override controls cannot configure an invocation after D3/D7. Future goal detail displays actual selected model/tier/role from the invocation receipt. Global settings survive independently; removing legacy override APIs/rows is a separate exact model-owner amendment, not a dashboard permission. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Butler Model Override API
The dashboard SHALL expose REST endpoints for managing per-butler model overrides.

#### Scenario: List overrides for a butler
- **WHEN** `GET /api/butlers/{name}/model-overrides` is called
- **THEN** all overrides for the specified butler are returned, each joined with the referenced catalog entry's alias for display

#### Scenario: Upsert overrides batch
- **WHEN** `PUT /api/butlers/{name}/model-overrides` is called with an array of override objects
- **THEN** each override is upserted (insert or update on conflict of `butler_name + catalog_entry_id`)
- **AND** the full set of overrides for the butler is returned after the operation

#### Scenario: Delete specific override
- **WHEN** `DELETE /api/butlers/{name}/model-overrides/{id}` is called
- **THEN** the override is removed and the butler reverts to global defaults for that catalog entry



## dashboard-model-settings — Per-Butler Model Override UI


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. global catalog/configuration truth and historical model, tier, override and failure evidence. Unit trigger complexity and per-unit runtime override controls cannot configure an invocation after D3/D7. Future goal detail displays actual selected model/tier/role from the invocation receipt. Global settings survive independently; removing legacy override APIs/rows is a separate exact model-owner amendment, not a dashboard permission. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Per-Butler Model Override UI
Each butler's detail page SHALL include model override configuration in a section accessible from the config or a dedicated tab.

#### Scenario: Override table on butler page
- **WHEN** the operator views a butler's model configuration
- **THEN** a table shows the effective model for each complexity tier: Tier, Effective Model (alias), Source (Global / Override badge), Priority, and an Override action button

#### Scenario: Add override for a tier
- **WHEN** the operator clicks "Override" for a complexity tier
- **THEN** a dialog shows available models for that tier (from global catalog) with options to: select a different model (change priority), disable a model for this butler, or remap a model from another tier

#### Scenario: Clear override
- **WHEN** the operator clicks "Reset to Global" on an override row
- **THEN** the butler-specific override is deleted and the effective model reverts to the global default



## dashboard-model-settings — Complexity Selection in Trigger UI


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. global catalog/configuration truth and historical model, tier, override and failure evidence. Unit trigger complexity and per-unit runtime override controls cannot configure an invocation after D3/D7. Future goal detail displays actual selected model/tier/role from the invocation receipt. Global settings survive independently; removing legacy override APIs/rows is a separate exact model-owner amendment, not a dashboard permission. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Complexity Selection in Trigger UI
The manual trigger UI on each butler's detail page SHALL include a complexity selector.

#### Scenario: Complexity dropdown in trigger tab
- **WHEN** the operator uses the trigger tab to manually spawn a session
- **THEN** a complexity dropdown is shown with the six canonical tier options: Reasoning, Workhorse, Cheap, Specialty, Local, Legacy
- **AND** all six canonical tiers are user-selectable for session triggers
- **AND** the selected complexity is passed to the trigger API

#### Scenario: Resolved model preview
- **WHEN** the operator selects a complexity level
- **THEN** the UI shows which model will be used (e.g. "Will use: claude-sonnet (workhorse tier)") based on the current catalog and overrides



## dashboard-permissions — Permissions Page


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. owner-authenticated audit, bounded readable evidence, reason validation, data export/delete safeguards, webhook state and historical seed migration. Per-butler permissions are not the future D7 broker grant authority. The UI retires that matrix at M6 while retaining read-only historical decisions, the last-15 privileged-event audit, secret-like-reason rejection, separate export/delete and webhook contracts. Exact seed migration and operator rows remain untouched; no new grant editor is proposed. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Permissions Page
The dashboard SHALL have a page at `/settings/permissions` rendered in the Dispatch design language containing the full permissions matrix, an audit reel, data operations (export; wipe disabled), and a webhook registry.

#### Scenario: Permissions page layout
- **WHEN** a user navigates to `/settings/permissions`
- **THEN** the page renders, in vertical order:
  - **Page header**: title "Permissions & data", mono eyebrow "system · permissions".
  - **Matrix section**: Permissions × Butlers grid. Rows are the runtime-enforced permissions — exactly `calendar.write`, `cross_butler`, `email.send`, `notify`, `spawn` (the set enforced by `src/butlers/core/permissions.py`; no decorative permission rows that no code reads). Columns are active butlers. Cells render as `on`/`off`/`inherited`; inherited cells render dim, explicit cells render foreground.
  - **Audit reel**: last 15 **privileged-action** entries from `GET /api/audit-log?limit=15&kind=privileged`. The reel filters out high-frequency operational noise (e.g. `*_heartbeat`, `GET /api/switchboard/heartbeat`) and surfaces only mutation/security actions (`permission.set`, `data.*`, `webhook.*`, and other non-heartbeat audit rows). Mono timestamps, sans actor, serif description. Link "Full audit log →" navigates to `/audit-log`.
  - **Data ops sub-grid**: export (scope picker → signed URL). The **wipe** control is disabled (not rendered, or rendered disabled with a "temporarily disabled" note); see the Data Operations requirement.
  - **Webhooks table**: list with add/edit/test/delete actions.

#### Scenario: Matrix cell flip requires reason
- **WHEN** a user flips an explicit or inherited matrix cell from off to on or on to off
- **THEN** the native cell button remains keyboard-operable and opens a modal that prompts for a `reason` text field
- **AND** inherited cells remain visibly dim before mutation rather than being disabled, while explicit cells render foreground
- **AND** the modal's submit button is disabled while `reason.trim()` is empty
- **AND** on submit, `PUT /api/permissions/{butler}/{perm}` is called with `{granted, reason}`.

#### Scenario: First inherited mutation becomes an explicit foreground cell
- **WHEN** an operator submits a valid first grant or revoke for an inherited cell
- **THEN** the matrix optimistically renders the requested `granted` value as explicit foreground state
- **AND** a failed write restores the previous inherited dim state
- **AND** a successful write retains the explicit state without requiring a new permissions model.

#### Scenario: Audit reel filters operational noise
- **WHEN** the audit reel loads its last-15 window
- **THEN** it requests a privileged-action-only view (e.g. `GET /api/audit-log?limit=15&kind=privileged`) so that high-frequency operational rows — butler/switchboard heartbeats and routine GET traffic — are excluded
- **AND** the rows shown are mutation/security actions (`permission.set`, `data.export`, `webhook.create|update|delete|test`, and similar), so a reader of a security surface sees security-relevant activity rather than heartbeat spam
- **AND** when no privileged actions exist yet, the reel shows its empty state rather than padding with noise.

#### Scenario: Audit reel distinguishes no history from an unavailable source
- **WHEN** the privileged audit request succeeds with zero entries
- **THEN** the reel SHALL show its calm no-history state and SHALL NOT render a degraded or retry state
- **WHEN** the privileged audit request fails and no cached response is available
- **THEN** the reel SHALL name the source as unavailable or degraded and provide a retry action that re-queries the privileged audit window
- **AND** the reel MUST NOT render its no-history state, or imply that the failed request proves an empty audit history.

#### Scenario: Audit reel retains cached rows under a degraded refresh
- **WHEN** a previously successful privileged audit response is cached and a subsequent refresh fails
- **THEN** the reel SHALL retain the cached privileged-action rows
- **AND** it SHALL visibly label the source as unavailable or degraded and provide a retry action
- **AND** it MUST NOT render its no-history state while the query is errored.



## dashboard-permissions — Permissions Matrix API


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. owner-authenticated audit, bounded readable evidence, reason validation, data export/delete safeguards, webhook state and historical seed migration. Per-butler permissions are not the future D7 broker grant authority. The UI retires that matrix at M6 while retaining read-only historical decisions, the last-15 privileged-event audit, secret-like-reason rejection, separate export/delete and webhook contracts. Exact seed migration and operator rows remain untouched; no new grant editor is proposed. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Permissions Matrix API
The dashboard SHALL expose CRUD over the permissions matrix.

#### Scenario: Read full matrix
- **WHEN** `GET /api/permissions` is called
- **THEN** the response is `ApiResponse[PermissionsMatrix]` containing `butlers: string[]`, `permissions: string[]`, and `cells: {butler: {perm: PermissionCell}}` where `PermissionCell = {granted: bool, reason: str | null, updated_at: timestamp | null, inherited: bool}`
- **AND** `butlers` is the full set of active butlers and `permissions` is the full enforced set (`calendar.write`, `cross_butler`, `email.send`, `notify`, `spawn`) — the matrix is dense (every active-butler × enforced-permission pair has a cell), not built only from rows that happen to exist in `public.permissions`.

#### Scenario: Inherited vs explicit cells
- **WHEN** a butler × permission pair has **no explicit row** in `public.permissions`
- **THEN** that cell is returned with `inherited: true` and `granted` set to the system default for that permission, so the UI can render it dim and allow the owner to create an explicit override
- **AND** a pair that **does** have a row is returned with `inherited: false` (explicit), rendered foreground.

#### Scenario: Set permission requires reason
- **WHEN** `PUT /api/permissions/{butler}/{perm}` is called
- **THEN** the request body is `{granted: bool, reason: str}` and `reason` MUST be a non-empty string after trimming whitespace
- **AND** if `reason` is empty or missing, the response is `422 Unprocessable Entity` with body `{detail: {error: "reason_required"}}` (FastAPI wraps the `HTTPException.detail` payload; the frontend reads `body.detail.error`)
- **AND** on success, `audit.append("permission.set", target=f"{butler}.{perm}", note=reason)` is invoked
- **AND** the response includes the updated cell.

#### Scenario: Mutation rejects values outside the live matrix vocabulary
- **WHEN** `PUT /api/permissions/{butler}/{perm}` names a permission outside `ENFORCED_PERMISSIONS`
- **THEN** the response is `422 Unprocessable Entity` with body `{detail: {error: "permission_not_enforced"}}`
- **AND** no permissions row, `permission.set` audit row, or webhook dispatch is written.
- **WHEN** the permission is enforced but `butler` has no row in `butler_registry`
- **THEN** the response is `422 Unprocessable Entity` with body `{detail: {error: "butler_not_registered"}}`
- **AND** no permissions row, `permission.set` audit row, or webhook dispatch is written.

#### Scenario: Reason field rejects credential patterns
- **WHEN** `PUT /api/permissions/{butler}/{perm}` is called with a `reason` that matches the case-insensitive pattern `(password|token|secret|api[_-]?key|credential|private[_-]?key)`
- **THEN** the response is `422 Unprocessable Entity` with body `{detail: {error: "reason_contains_credential"}}` (FastAPI wraps the `HTTPException.detail` payload; the frontend reads `body.detail.error`)
- **AND** no state change occurs; no audit row is written.
- **AND** the check is implemented as `validate_no_secrets(text)` in `src/butlers/api/security.py` and reused by any future endpoint that takes free-text reason input.

#### Scenario: Inherited cells become explicit on mutation
- **WHEN** an inherited cell is flipped
- **THEN** the resulting row in `public.permissions` is explicit (not inherited) and the matrix re-fetch shows the cell as foreground.



## dashboard-permissions — Inherited defaults have no legacy persisted seed


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. owner-authenticated audit, bounded readable evidence, reason validation, data export/delete safeguards, webhook state and historical seed migration. Per-butler permissions are not the future D7 broker grant authority. The UI retires that matrix at M6 while retaining read-only historical decisions, the last-15 privileged-event audit, secret-like-reason rejection, separate export/delete and webhook contracts. Exact seed migration and operator rows remain untouched; no new grant editor is proposed. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Inherited defaults have no legacy persisted seed
The permissions matrix SHALL represent a system default as an inherited cell
with no persisted operator row. A forward core migration MUST remove only
legacy `public.permissions` rows whose `reason` is exactly
`seeded default (core_121)`, and MUST preserve every row with any other reason
or value. The migration MUST safely no-op when the optional permissions table
is absent, MUST be idempotent, and its downgrade MUST NOT recreate default
rows.

#### Scenario: Exact legacy seed becomes inherited
- **WHEN** a migrated database contains a `public.permissions` row whose
  `reason` is exactly `seeded default (core_121)`
- **THEN** the forward migration removes that row
- **AND** `GET /api/permissions` serializes the corresponding active
  butler-permission cell with `inherited: true`, the system default `granted`
  value, and no persisted `reason` or `updated_at` value.

#### Scenario: Explicit operator choices survive seed retirement
- **WHEN** a migrated database contains explicit grant or revoke rows with
  reasons other than `seeded default (core_121)`
- **THEN** the forward migration leaves their `granted`, `reason`, and
  `updated_at` values unchanged
- **AND** `GET /api/permissions` serializes those cells as `inherited: false`.

#### Scenario: Partial and repeated migration runs are safe
- **WHEN** the forward migration runs against a partial core-only schema that
  lacks `public.permissions`, or runs again after legacy seeds were removed
- **THEN** it completes without changing unrelated data
- **AND** downgrading does not insert or recreate permission rows.

## Source References
- PLAN.md §5 `/settings/permissions` API surface and §6 Phase 4 implementation order.
- Visual reference: the `DataExpanded` redesign prototype (graduated; now shipped in `frontend/`).
- Reuses `audit.append()` from dashboard-audit-log; every mutation in this capability is audited.
- Doctrine: `about/heart-and-soul/security.md` — "no privileged mutation without a reason" reflected in the matrix endpoint's mandatory `reason` field.


## dashboard-spend-dashboard — Spend API


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. period/filter currency and cost accounting, degraded coverage, fleet-halt predicate and safe deep links. Goal/invocation attribution is additive stored lineage; null historical unit/session provenance stays historical. Fleet-halt visibility remains an actual control-plane fact, not a region stopped-state inference. Budget exhaustion and settled spend are different receipts. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Spend API
The dashboard SHALL expose the spend endpoints.

#### Scenario: Spend totals
- **WHEN** `GET /api/spend?period=today|7d|30d` is called (or a custom range via `from`/`to` ISO date params)
- **THEN** the response is `ApiResponse[SpendSummary]` where `SpendSummary = {period, total_cost_usd, total_sessions, total_input_tokens, total_output_tokens, by_butler, by_model, unpriced_models, divergences, historical_attribution_note}`. There are no `total_usd`, `period_start`, or `period_end` fields.
- **AND** every dollar amount, token actual, `by_butler` value, and `by_model` value is grouped from `public.token_usage_ledger` joined to `public.model_catalog` by `catalog_entry_id`, so it describes the model that actually consumed the tokens rather than `sessions.model`.
- **AND** the router MUST NOT substitute per-butler session fan-out pricing when that ledger query is unavailable; it returns degraded source evidence instead of a plausible alternate dollar total.
- **AND** implicit calendar defaults derive `today` from UTC, so preset summaries, the daily default range, MTD breakdown, and forecast elapsed-day calculation align with the UTC month used by the monthly-ceiling ledger helper.

#### Scenario: Compatibility source errors are unavailable evidence in every summary and daily consumer
- **WHEN** a compatibility-success (`200`) Spend summary response has `source_error: true`, or a daily Spend response has `meta.source_error: true`
- **THEN** its compatibility totals, maps, and empty daily series MUST be treated as unavailable evidence, never as a genuine `$0`, empty-state, "all systems ok", mover, per-session, model-breakdown, or trend result.
- **AND** Dashboard/CostWidget, Sidebar, Butler spend/detail surfaces, and SpendPage/CostStripeChart render a source-degraded/unavailable state that identifies the unavailable evidence while preserving an explicitly known zero when `source_error` is absent or false.

#### Scenario: Spend breakdown
- **WHEN** `GET /api/spend/breakdown?by=butler|model|purpose` is called
- **THEN** the response is `ApiResponse[{by: str, breakdown: {key: cost_usd}, unpriced_models: [], billing_classes: {model: class}, source_error: bool, divergences: [], historical_attribution_note: str | null}]` for the current month (MTD), with no guaranteed map order.
- **AND** `butler`, `model`, and `purpose` values are grouped directly from `public.token_usage_ledger` and priced through the same executed-model pricing calculation as the monthly ceiling, not from requested session models or an MCP fan-out.
- **AND** `GET /api/spend/breakdown?by=feature` retains the by-schedule behavior defined below; it is metadata evidence outside this aggregate ledger-attribution change.

#### Scenario: Unpriced models remain visibly unpriced
- **WHEN** a ledger group uses a model absent from the pricing configuration
- **THEN** its numerical cost is omitted from the priced subtotal and it is included in `unpriced_models` as `{model, calls, input_tokens, output_tokens, cached_input_tokens, cache_creation_tokens}`.
- **AND** the API MUST NOT encode absent pricing as `0`, and the frontend renders the model as `—/unpriced` rather than a zero-valued bar or `$0.00` amount.
- **AND** an explicitly classified `subscription` or `local` model with zero marginal rates remains a known numeric zero and is identified by `billing_classes`, not placed in `unpriced_models`.
- **AND** when a relevant summary, daily, forecast, or comparison envelope has non-empty `unpriced_models`, Sidebar, Butler Spend/Overview, movers, and verdict consumers surface incomplete coverage and suppress partial dollar totals and calm pace/mover language; a numeric zero remains valid only when that envelope is empty.

#### Scenario: Spend breakdown by purpose
- **WHEN** `GET /api/spend/breakdown?by=purpose` is called
- **THEN** `purpose` keys are the dispatch `trigger_source` values (`route`/`schedule`/`classification`/`healing`/`qa`/`extraction`/`external`/`retry`/`tick`) plus `discretion` (connector discretion screening, which has no `trigger_source` equivalent); ledger rows with a `NULL` purpose (pre-migration or unattributed) are grouped under `"unknown"`.
- **AND** `source_error: true` when the DB-backed path is unavailable or the ledger query fails (no session or MCP fallback exists for this dimension) — the frontend renders a `SourceDegradedNote` instead of reading an empty breakdown as "no purpose-tagged spend this month".

#### Scenario: Spend forecast (naive estimator v1)
- **WHEN** `GET /api/spend/forecast` is called
- **THEN** the response is `{days: {date, cost_usd, projected}[], projected_eom_usd: float, days_in_month: int, days_elapsed: int, mtd_usd: float, ceiling_usd: float | null, projection_confidence: "low" | "normal", ceiling_source_error: bool, unpriced_models: [], ceiling_blind_to_unpriced_models: int, divergences: [], historical_attribution_note: str | null}` (the field is `days` not `daily`, and per-day cost is `cost_usd` not `usd`).
- **AND** `mtd_usd`, every actual `days[*].cost_usd`, and `projected_eom_usd` are priced from `public.token_usage_ledger` joined to the executed catalog entry; actual daily costs MUST NOT be reconstructed from per-butler session rows.
- **AND** `mtd_usd` and `ceiling_usd` share the `butlers.core.model_routing.price_mtd_from_ledger` calculation used by `check_monthly_ceiling`, so the priced portion can never diverge from the number that halts the fleet.
- **AND** `projected_eom_usd = mtd_usd / max(days_elapsed, 1) × days_in_month`, using that ledger-priced `mtd_usd`.
- **AND** `ceiling_blind_to_unpriced_models` equals the number of distinct unpriced model IDs in current-month ledger usage, and the frontend renders `"blind to N unpriced models"` whenever it is nonzero.
- **AND** `ceiling_source_error: true` when the ledger/ceiling query fails or no DB pool is wired — `mtd_usd`, `projected_eom_usd`, and `ceiling_usd` are then `0`/`0`/`null`, not a genuine "$0 MTD" reading, and the frontend renders a `SourceDegradedNote` in place of the KPI strip and suppresses the projected (dashed) chart segment instead of reading the zeros as truthful.
- **AND** `projection_confidence = "low"` when `days_elapsed < 3`, else `"normal"`. This signals to the Console aggregator NOT to fire a "spend near ceiling" attention item from a low-confidence projection.
- **AND** a code-level TODO marks the location of the smarter estimator for a future change. This applies to the month-end spend estimator only; the per-schedule cadence estimator it used to also cover is now specified by "Requirement: Schedule Cadence Forecast".

#### Scenario: Sessions-versus-ledger divergence is surfaced
- **WHEN** a spend response covers a date and butler for which session token totals and ledger token totals differ by more than five percent
- **THEN** the response includes a divergence record with the date, butler, both token totals, and relative difference; the frontend renders a `SourceDegradedNote` rather than presenting the aggregates as reconciled.
- **AND** session reads used for this detector are diagnostic only and MUST NOT supply a dollar amount or model price.
- **AND** a ledger group explicitly proven to contain no task session (`BOOL_OR(token_usage_ledger.session_id IS NOT NULL) = false`) is a non-roster spend source and is excluded from roster-session coverage checks; this includes connector attribution identities such as WhatsApp `wa:*@lid` and declared synthetic runtime identities.
- **AND** an unconfigured ledger identity with a task session, or without explicit sessionless proof, remains an unknown roster source and sets the comparison's `source_error` instead of being silently exempted.
- **AND** if the detector cannot obtain enough session evidence to compare a roster source, the response identifies that degraded comparison rather than reporting an empty divergence list as a successful reconciliation.
- **AND** the degraded response exposes only the generic comparison state and MUST NOT expose an excluded connector or synthetic identity in browser-facing error detail.

#### Scenario: Historical requested-model attribution is labeled
- **WHEN** a requested response window begins before `2026-07-10`
- **THEN** its `historical_attribution_note` states that legacy session model labels in that portion of the range can be requested models while all ledger-derived dollar values use the executed model.

#### Scenario: Spend by-schedule aggregation
- **WHEN** `GET /api/spend/by-schedule` (or `?by=feature` on `/api/spend/breakdown`) is called and a schedule ran under 2+ models within the queried window
- **THEN** the response contains exactly one `ScheduleCost` entry per `(butler, schedule_name)` — the per-butler fan-out prices each `(schedule, model)` fragment individually (pricing is model-specific) but merges same-named fragments into a single bucket (summed `total_runs` and `total_cost_usd`; `projected_monthly_runs` taken once, since it derives only from the cron) before returning.
- **AND** `avg_cost_per_run` and `projected_monthly_usd` are computed from the merged totals, not from any individual model fragment — a multi-model schedule must never under-rank its true burn or produce duplicate `(butler, schedule_name)` rows (the frontend keys table rows on `${butler}-${schedule_name}`).
- **AND** `ScheduleCost` carries two groups of fields that are not interchangeable: `total_runs`, `total_cost_usd` and `avg_cost_per_run` are MEASURED over the queried range, while `projected_monthly_runs` and `projected_monthly_usd` are a FORECAST, specified by "Requirement: Schedule Cadence Forecast", computed on the basis stated once in `meta.forecast_basis`.
- **AND** the payload key is `projected_monthly_runs`, replacing `runs_per_day`. This is a contract change on the `schedule_costs` core MCP tool as well as on the HTTP response, since butler LLM sessions read that tool's output keys. The value is computed on read from a live query and never persisted, so no migration, dual-read, or backfill is involved.

#### Scenario: DB-first evidence for top-sessions and by-schedule
- **WHEN** `GET /api/spend/top-sessions` or `GET /api/spend/by-schedule` (or `?by=feature` on `/api/spend/breakdown`) is called with a DB pool wired
- **THEN** each butler's evidence is read DB-first via the `butlers.core.sessions.top_sessions` / `schedule_costs` pool helpers, with the corresponding MCP tool as a per-butler fallback used only when that butler's DB pool is absent or its query fails.
- **AND** because the DB read does not depend on MCP tool registration, the STAFFER butlers (`switchboard`/`messenger`/`qa`) that structurally lack these tools now contribute real evidence instead of a permanent empty result.
- **AND** a butler is added to `unavailable_butlers` only when BOTH the DB read and the MCP fallback fail; a butler whose sessions/scheduled_tasks tables do not exist yet (schema not provisioned) yields no DB rows and is classified via the fallback, never marked degraded on the DB miss alone.
- **AND** the DB and MCP paths share one pricing/merge builder, so a butler served by either path produces the identical `TopSession` / `ScheduleCost` shape (the by-schedule per-`(butler, schedule_name)` merge above applies to both).

#### Scenario: MCP tool-absence is not a degraded source
- **WHEN** the per-butler fallback behind `/api/spend/top-sessions` or `/api/spend/by-schedule` calls a butler that has never registered the requested tool (`top_sessions` / `schedule_costs` are registered only for non-STAFFER butlers — see `core_tools/_sessions.py`, `core_tools/_scheduling.py`)
- **THEN** the call fails with `fastmcp.exceptions.ToolError` whose message starts with `"Unknown tool:"`, and the router classifies this as legitimately absent — the butler contributes a truthful zero/empty result and is NOT added to `unavailable_butlers`.
- **AND** any other failure on the same call (unreachable butler, timeout, malformed JSON, or a `ToolError` raised by the tool's own body for a different reason) is still tracked and the butler IS added to `unavailable_butlers`, so a genuine failure is never mistaken for "this butler doesn't have that feature".

#### Scenario: Spend rule condition dimensions
- **WHEN** a rule `condition` is created or updated
- **THEN** the supported dimensions are `butler` (identity name), `complexity`/`tier` (alias pair — canonical complexity tier), and `trigger`/`purpose` (alias pair — the dispatch `trigger_source`, matching the same vocabulary `/spend/breakdown?by=purpose` and `token_usage_ledger.purpose` use for this dimension).
- **AND** each dimension accepts a scalar (exact match) or a list (membership match); all supplied dimensions are ANDed; an unknown key is rejected at create/update time (`422`), and at dispatch-evaluation time causes the rule to fail-closed (never match).
- **AND** `trigger`/`purpose` cannot match when the dispatch has no trigger-source context (fail-closed, not catch-all).
- **AND** a condition MUST NOT set both `trigger` and `purpose` (they alias the same underlying value and could never legitimately hold two different values at once) — rejected at create/update time (`422`).

#### Scenario: Spend rules CRUD
- **WHEN** `GET /api/spend/rules` is called
- **THEN** rules are returned ordered by `position ASC` (top-to-bottom evaluation order).
- **WHEN** `POST /api/spend/rules` is called with `{condition, action, position?}`
- **THEN** the rule is inserted at `position` (default: end), and existing rules at `position >= p` are shifted down by one inside the insert transaction.
- **AND** the call invokes `audit.append("spend.rule")`.
- **WHEN** `PUT /api/spend/rules/{id}` is called
- **THEN** the rule fields are updated atomically; if `position` changed, other rules' positions are shifted to maintain the order.
- **WHEN** `DELETE /api/spend/rules/{id}` is called
- **THEN** the rule is removed and remaining rules' positions are compacted (no gaps).
- **AND** the insert-shift and the delete-compaction are exact inverses, which is what makes the restore in "Requirement: Routing Rule Deletion Safety" possible without a new endpoint, a tombstone table, or a migration.

#### Scenario: Monthly ceiling
- **WHEN** `PUT /api/spend/ceiling {monthly_usd}` is called
- **THEN** the singleton ceiling row is updated.
- **AND** the call invokes `audit.append("spend.ceiling")`.



## dashboard-spend-dashboard — Fleet-Halt Visibility


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. period/filter currency and cost accounting, degraded coverage, fleet-halt predicate and safe deep links. Goal/invocation attribution is additive stored lineage; null historical unit/session provenance stays historical. Fleet-halt visibility remains an actual control-plane fact, not a region stopped-state inference. Budget exhaustion and settled spend are different receipts. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Fleet-Halt Visibility
The dashboard SHALL surface the monthly spend ceiling's enforcement action —
dispatches being denied fleet-wide — as a loud, explicit state on the Spend page,
not silence. The ceiling is enforced by `check_monthly_ceiling` / the spawner
(`spawner.py:1179-1202`), which writes an `outcome='quota_skip'` row to
`public.model_dispatch_attempts` with `failure_reason` starting `"Monthly spend
ceiling reached"` for every denied dispatch (see model-failover spec, Failover
Attempt Provenance).

#### Scenario: A red fleet-halt banner renders while the ceiling is breached
- **WHEN** `GET /api/dispatch/attempts?outcome=quota_skip&reason_prefix=Monthly+spend+ceiling+reached`
  (scoped to the current calendar month) returns one or more rows
- **THEN** the Spend page renders a red state reading "Monthly ceiling reached —
  N dispatches denied since `<timestamp>`", where N is the total matching count
  for the current month and `<timestamp>` is the earliest matching row's `ts`
- **AND** the banner additionally shows a denied-today count (rows since the
  start of the current owner-tz day)
- **AND** the banner does not render when no such rows exist for the current month

#### Scenario: An attempts drawer lists recent denials with session doors
- **WHEN** the fleet-halt banner is active
- **THEN** an expandable drawer lists the most recent denied attempts (butler,
  timestamp, failure reason)
- **AND** each row whose `session_id` is non-null links to that session's detail
  page (`/sessions/:id`), mirroring the session-door pattern the Top Sessions
  table already uses
- **AND** rows with no `session_id` (pre-session ceiling denials) render without
  a session door instead of a dead or broken link

#### Scenario: The owner is notified exactly once per breach window
- **WHEN** the monthly ceiling transitions from not-breached to breached — the
  first `quota_skip` dispatch denial with `failure_reason` prefix `"Monthly
  spend ceiling reached"` in the current calendar month, detected inline in the
  spawner's ceiling-deny branch (`spawner.py`, `maybe_push_fleet_halt_attention`
  in `butlers.core.fleet_halt_attention`)
- **THEN** exactly one `public.attention_ledger` row is written with
  `source="notify"`, `outcome="delivered"`, `priority_label="high"` (the
  same lever `notify()` itself uses to always bypass quiet-hours/context-bus
  suppression — a fleet halt is expressed via the ledger's own severity dial,
  not a bespoke bypass), a `dedup_key` identifying the calendar-month halt
  window (e.g. `ceiling_halt:2026-07`), and `metadata` carrying the current
  denied-dispatch count for the month plus a door URL into the Spend page's
  attempts drawer (`/spend?openDrawer=fleet-halt`, which auto-expands the
  drawer from Scenario "An attempts drawer lists recent denials with session
  doors" above)
- **AND** the owner is paged through the same notify-boundary gating/dispatch
  primitives `notify()` uses (quiet hours via `public.approvals_policy`,
  context-bus dnd/sleeping, Switchboard `deliver()`)
- **AND** every subsequent ceiling denial in the same calendar month writes
  NEITHER another `attention_ledger` row NOR another page — debounced by a
  durable per-window marker in `public.audit_log`
  (`action="ceiling_halt_notified"`, `note=<the window>`), mirroring the same
  debounce mechanism `butlers.jobs.secrets_lifecycle` already uses for its own
  once-per-state-transition owner pushes
- **AND** the entire push is best-effort and failure-isolated: any failure
  (ledger write, debounce lookup, delivery) is logged and swallowed, and never
  blocks or delays the spawner's deny decision

#### Scenario: Degraded attempts source never renders as "no denials"
- **WHEN** `GET /api/dispatch/attempts` fails (network error, non-2xx)
- **THEN** the Spend page SHALL render a degraded-source note for the fleet-halt
  state (per the fleet degraded-source convention) instead of silently omitting
  the banner, which would read as a false "the fleet is not halted"



## dashboard-approvals — Approvals action list API


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Approvals action list API

The dashboard API SHALL expose `GET /api/approvals/actions` which returns a paginated list of approval actions.

The endpoint SHALL accept the following query parameters:
- `offset` (integer, optional, default 0) -- pagination offset
- `limit` (integer, optional, default 50) -- maximum number of actions to return
- `status` (string, optional) -- filter by action status: `pending`, `approved`, `rejected`, `expired`, `executed`, or `abandoned`
- `tool_name` (string, optional) -- filter by the tool that requested the action
- `since` (ISO 8601 timestamp, optional) -- include only actions created on or after this timestamp
- `until` (ISO 8601 timestamp, optional) -- include only actions created on or before this timestamp

The response MUST be a `PaginatedResponse<ApprovalAction>` where each `ApprovalAction` object contains:
- `id` -- string UUID identifying the action
- `tool_name` -- string name of the tool requesting approval
- `butler` -- string name of the butler that owns the action
- `status` -- one of `"pending"`, `"approved"`, `"rejected"`, `"expired"`, `"executed"`, `"abandoned"`
- `description` -- string human-readable description of the action
- `why` -- string | null serif paragraph explaining why human input is needed
- `evidence` -- string[] | null array of mono evidence lines
- `constraints` -- object mapping constraint names to their values (tool-specific structure)
- `created_at` -- ISO 8601 timestamp when the action was created
- `expires_at` -- ISO 8601 timestamp when the action will expire if not decided
- `decided_at` -- ISO 8601 timestamp when the action was approved/rejected (null if pending)
- `decided_by` -- string identifier of who decided (user, rule ID, auto-expired) (null if pending)
- `rule_id` -- string UUID of the rule that auto-approved (null if manual or not approved)
- `execution_count` -- integer count of times this action has been executed (0 if not executed)
- `target_contact` -- object (nullable) containing resolved target contact info: `id` (UUID), `name` (string), `roles` (string array). Null if the action does not target a specific contact.

#### Scenario: List with new fields populated

- **WHEN** `GET /api/approvals/actions` is called and the underlying `pending_actions` rows have `why` and `evidence` populated
- **THEN** the response includes the `why` paragraph and `evidence` array on each `ApprovalAction` row.

#### Scenario: List with legacy rows

- **WHEN** `GET /api/approvals/actions` is called and the underlying `pending_actions` rows have `why = NULL` and `evidence = []` (pre-migration data)
- **THEN** the response includes `why: null` and `evidence: []` on those rows; the rest of the row is unchanged.

#### Scenario: Fetch pending approval actions

- **WHEN** `GET /api/approvals/actions?status=pending` is called
- **THEN** the API MUST return all actions with `status = "pending"` sorted by `created_at` descending
- **AND** each action MUST include all required fields including `constraints` and `target_contact`
- **AND** the response status MUST be 200

#### Scenario: Target contact populated for notify actions

- **WHEN** a pending `notify` action has `contact_id='abc-123'` in its constraints
- **THEN** the `target_contact` field MUST include the contact's `id`, `name`, and `roles`

#### Scenario: Target contact null for non-contact actions

- **WHEN** a pending action has no contact_id in its constraints
- **THEN** the `target_contact` field MUST be `null`

#### Scenario: Filter actions by tool name

- **WHEN** `GET /api/approvals/actions?tool_name=notify` is called
- **THEN** the API MUST return only actions where `tool_name = "notify"`
- **AND** the results MUST include all statuses unless further filtered

#### Scenario: Filter actions by date range

- **WHEN** `GET /api/approvals/actions?since=2026-02-10T00:00:00Z&until=2026-02-17T23:59:59Z` is called
- **THEN** the API MUST return only actions with `created_at` between the specified timestamps (inclusive)

#### Scenario: Pagination with default limit

- **WHEN** `GET /api/approvals/actions` is called
- **THEN** the API MUST return at most 50 actions (default limit)
- **AND** the response MUST include a `meta` object with `total`, `offset`, and `limit` fields

#### Scenario: Pagination with custom offset

- **WHEN** `GET /api/approvals/actions?offset=50&limit=25` is called
- **THEN** the API MUST return actions 50-74 (25 actions starting at offset 50)

#### Scenario: No pending actions

- **WHEN** `GET /api/approvals/actions?status=pending` is called and no pending actions exist
- **THEN** the API MUST return an empty array
- **AND** the response status MUST be 200

---



## dashboard-approvals — Approvals Page in Dispatch Language


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Approvals Page in Dispatch Language

The dashboard SHALL render `/approvals` in the Dispatch design language as a replacement (not a duplicate) for the legacy approvals page. `/approvals` is the One Trust Console: the single surface for the pending decision queue, the decision dossier, and the standing autonomy ledger.

#### Scenario: Approvals page layout

- **WHEN** a user navigates to `/approvals`
- **THEN** the page renders, in vertical order:
  - **Page header**: title "Approvals", mono eyebrow "system · approvals", clock.
  - **Three-pane body**: left rail of pending approvals (rule-separated rows, ranked per the Queue ranking scenario below), center pane dossier of the selected approval, right pane Autonomy panel (always visible — see the Autonomy Panel requirement below).
  - **Dossier body**: `title` headline (sans 500, 22px), `why` serif paragraph (`max-width: 50ch`), `evidence` mono lines (rule-separated), `proposed_action` summary, primary `Approve` commit button, secondary `Deny` and `Defer` pill buttons.
  - **Policy section** below the body: quiet-hours editor (`start_hour`, `end_hour`, `timezone`).
  - **History section** at the bottom: last 30 decided approvals from `GET /api/approvals/history`; each row links to `/approvals/{id}` and opens the same dossier read-only.
- **AND** the page contains no Kanban-style columns, no charts, no cards.

#### Scenario: Legacy page deleted in same PR

- **WHEN** the new `ApprovalsPage` lands
- **THEN** the legacy approvals component (the prior `ApprovalsPage` content) is REMOVED in the same PR
- **AND** no parallel `/approvals/legacy` route exists.

#### Scenario: Target contact shown in approval dossier

- **WHEN** a pending `notify` action targets contact "Chloe" with `roles = []`
- **THEN** the dossier MUST display "Chloe" as the target contact
- **AND** the role badges MUST be empty (non-owner)

#### Scenario: Owner-targeted action shows owner badge

- **WHEN** a pending action targets the owner contact with `roles = ['owner']`
- **THEN** the dossier MUST display the owner's name with an "owner" role badge

#### Scenario: Expiring action countdown

- **WHEN** an action's expiration time is within 1 hour
- **THEN** the expiration countdown MUST display in a warning color (e.g., red or orange)

#### Scenario: Empty state when no actions

- **WHEN** a user navigates to `/approvals` with no pending or recent approvals
- **THEN** the page MUST display an empty state message (e.g., "No pending approvals")



## dashboard-approvals — Every Approval Has a URL


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Every Approval Has a URL

The dashboard SHALL expose `/approvals/:id` as a first-class route rendering the same `ApprovalsPage`, with the URL selecting the dossier.

#### Scenario: Deep link selects the named approval

- **WHEN** a user navigates directly to `/approvals/{id}`
- **THEN** the dossier pane fetches and displays that approval's detail via `GET /api/approvals/{id}`, regardless of arrival order in the pending queue
- **AND** this holds for both pending and already-decided approvals (a decided approval renders its read-only dossier).

#### Scenario: Selecting a rail item updates the URL

- **WHEN** a user clicks a pending approval in the left rail
- **THEN** the browser URL becomes `/approvals/{id}` for that approval.

#### Scenario: History rows deep-link into the dossier

- **WHEN** a user clicks a row in the History section
- **THEN** the browser navigates to `/approvals/{id}` for that decided approval and its (read-only) dossier renders.

#### Scenario: Deciding the explicitly-selected item advances the URL

- **WHEN** the approval currently named in the URL is approved, denied, or deferred
- **THEN** the URL advances to the next-ranked pending approval's `/approvals/{id}`, or to `/approvals` if none remain.



## dashboard-approvals — Queue Ranked by Expiry and Blast Radius


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Queue Ranked by Expiry and Blast Radius

The pending rail SHALL be ordered by decision urgency, not arrival order.

#### Scenario: Expiring items rank ahead of items with no expiry

- **WHEN** the pending queue contains an approval expiring within the hour and an approval with no `expires_at` that arrived earlier
- **THEN** the expiring approval renders first in the rail.

#### Scenario: Blast radius breaks ties among similarly-urgent items

- **WHEN** two pending approvals have comparable expiry urgency
- **THEN** the approval with the higher blast-radius tool (e.g. an outbound `notify`/`send_*` call) ranks ahead of a lower-radius internal data write (e.g. an `assert`/`store` call).



## dashboard-approvals — Keyboard-Driven Triage with Undo


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Keyboard-Driven Triage with Undo

`/approvals` SHALL support fully keyboard-driven triage of the pending rail: `j`/`k` move roving focus (and selection) between rail items, and `a`/`d`/`x` schedule approve/deny/defer on the focused item. These bare-key shortcuts are inactive while focus is inside an input, textarea, or content-editable element, while a modifier key is held, or while a pending `g`-chord (see the global command-menu shortcuts) owns the next keystroke.

Because a rapid keystroke during triage is cheap to mis-press, a decision scheduled via `a`/`d`/`x` SHALL NOT call its approve/deny/defer endpoint immediately. It SHALL instead enter a per-item pending state, held for a fixed undo window, during which the item renders distinctly (dimmed, verb-labeled, e.g. "Approving…") in the rail and an undo toast is shown; only after the window elapses without an undo does the mutation fire. Clicking the pending rail item, or the toast's Undo action, cancels the scheduled decision before it reaches the backend. Mouse clicks on the dossier's own Approve/Deny/Defer buttons are unaffected by this window and continue to fire immediately (the existing one-click optimistic design).

#### Scenario: j/k roving focus moves the selection

- **WHEN** the pending rail has focus (or the page has keyboard focus outside an editable field) and the user presses `j`
- **THEN** selection (and the URL) advances to the next-ranked pending item
- **AND** pressing `k` moves selection to the previous-ranked item.

#### Scenario: a/d/x schedule a decision instead of firing immediately

- **WHEN** the user presses `a`, `d`, or `x` while a pending approval has keyboard focus
- **THEN** the approve/deny/defer endpoint is NOT called immediately
- **AND** the rail item renders a per-item pending state (dimmed, verb-labeled) and an undo toast appears
- **AND** the real mutation fires only after the undo window elapses without the decision being undone.

#### Scenario: Undo cancels a scheduled decision before it reaches the backend

- **WHEN** the user clicks the pending rail item, or the undo toast's action, before the undo window elapses
- **THEN** the scheduled decision is cancelled and no approve/deny/defer request is sent
- **AND** the item returns to its normal (non-pending) rendering.

#### Scenario: Keyboard shortcuts do not fire while typing

- **WHEN** an input, textarea, or content-editable element has focus (e.g. the inline deny-reason field)
- **THEN** `j`, `k`, `a`, `d`, and `x` keystrokes are treated as ordinary text input, not triage shortcuts.



## dashboard-approvals — Approved-but-Undispatched Renders Amber, Never Success-Green


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Approved-but-Undispatched Renders Amber, Never Success-Green

An approval whose backend `status` is `"approved"` (approved but not yet dispatched — see the dispatched-vs-approved distinction in Approval Verbs) SHALL never render with the same success-green treatment as `"executed"`.

#### Scenario: Stalled approval reads as a distinct, actionable state

- **WHEN** an approval's status is `"approved"` (dispatch did not run)
- **THEN** the UI renders it with an amber/warning color family, labeled distinctly from a settled decision (e.g. "stalled")
- **AND** an executed action continues to render in its own distinct color, never sharing the amber "stalled" treatment.



## dashboard-approvals — Autonomy Panel


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Autonomy Panel

`/approvals` SHALL render an always-visible Autonomy panel from `GET /api/approvals/gated-tools`, replacing the standalone rules CRUD route. The endpoint returns every enabled configured gate, including an `active_rules` list that can be empty; each gate also identifies its owning butler, risk tier, and effective expiry.

#### Scenario: Standing rules panel replaces the orphaned rules route

- **WHEN** a user is on `/approvals`
- **THEN** the Autonomy panel is visible without further navigation
- **AND** no separate `/approvals/rules` route exists.

#### Scenario: Live use counts and inline revoke

- **WHEN** the Autonomy panel renders a standing rule
- **THEN** it displays the rule's current `use_count` (and `max_uses` when set)
- **AND** provides an inline revoke action (two-step confirm within the panel, not a native `window.confirm` dialog) that calls `POST /api/approvals/rules/{id}/revoke`.

#### Scenario: Configured zero-rule gate stays visible

- **WHEN** a configured gate has no active standing rules
- **THEN** the panel MUST display that tool with the status "always ask"
- **AND** it MUST NOT infer that unlisted tools are gated or that every action across the fleet requires manual approval.

#### Scenario: No configured gates

- **WHEN** no enabled approvals configuration declares a gated tool
- **THEN** the panel displays that no approval-gated tools are configured.

#### Scenario: Gate-rule source is degraded

- **WHEN** the gate baseline is returned with `meta.sources_degraded`
- **THEN** the panel MUST name the unavailable source
- **AND** MUST NOT label that source's tools "always ask" when their active-rule state is unknown.



## dashboard-approvals — Approvals Flat List API


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Approvals Flat List API

The dashboard SHALL expose `GET /api/approvals?state=waiting|decided|all|stalled`
as a flat-list view complementing the existing `GET /api/approvals/actions`
paginated list.

#### Scenario: Filter by state

- **WHEN** `GET /api/approvals?state=waiting` is called
- **THEN** the response is `ApiResponse[ApprovalSummary[]]` containing only
  actions in `pending` state, ordered `created_at DESC`.
- **WHEN** `GET /api/approvals?state=decided` is called
- **THEN** the response contains actions in `approved | rejected | expired |
  executed | abandoned` states.
- **WHEN** `GET /api/approvals?state=all` is called or `state` is omitted
- **THEN** all states are included.



## dashboard-approvals — Whole-population stalled approval radar


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Whole-population stalled approval radar

The flat `GET /api/approvals` endpoint SHALL accept `state=stalled` in
addition to its existing states. A stalled approval SHALL be derived only when
its persisted `status` is exactly `approved` and its `execution_result` is
`NULL`; stalled SHALL NOT be persisted as a new status or inferred from a time
threshold.

Every flat approvals response, regardless of requested state, offset, or
limit, SHALL include `meta.stalled_count`: the count of all currently stalled
actions across the endpoint's eligible approval-source population. The list
filter and the aggregate SHALL use the same per-pool eligibility and exact
stalled predicate.

#### Scenario: Stalled filter selects only approved actions without execution

- **WHEN** `GET /api/approvals?state=stalled` reads a population containing
  approved actions with null and non-null execution results plus other statuses
- **THEN** it returns only actions whose status is `approved` and whose
  `execution_result` is null
- **AND** it does not return an `executed`, `pending`, `rejected`, `expired`,
  `abandoned`, or approved action with a non-null execution result.

#### Scenario: History retry uses the durable eligibility predicate

- **WHEN** the bounded history response includes approved actions with null and
  non-null execution results
- **THEN** each `ApprovalSummary` includes its nullable, redacted
  `execution_result`
- **AND** the dashboard renders Retry only for actions whose status is
  `approved` and whose `execution_result` is null.

#### Scenario: Stalled metadata is independent of the page window

- **WHEN** `GET /api/approvals?state=decided&limit=30` returns a bounded
  history page while more than 30 older/newer rows exist
- **THEN** `meta.stalled_count` equals the count of every eligible stalled
  approval, not the count of rows on that page
- **AND** the same count is returned for `state=waiting`, `state=decided`,
  `state=all`, and `state=stalled` requests over the same healthy population.

#### Scenario: Degraded approval sources cannot imply an all-clear

- **WHEN** any eligible approval source cannot supply its list or stalled
  aggregate contribution
- **THEN** the flat response identifies that source in `meta.sources_degraded`
- **AND** any returned `meta.stalled_count` is treated as observed partial
  coverage rather than proof that no stalled approvals exist.

#### Scenario: Dashboard abandons an eligible stalled action

- **WHEN** `POST /api/approvals/{id}/abandon` receives a non-blank reason from
  the dashboard for an action whose status is `approved` and whose execution
  result is null
- **THEN** the response reports terminal `abandoned` status
- **AND** the action immediately leaves stalled results and has no Retry
  affordance.
- **WHEN** a callback, MCP, automatic, bulk, or scheduled path attempts the
  same operation
- **THEN** that path does not expose or invoke abandonment.



## dashboard-approvals — Trust Console verdict uses stalled radar metadata


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Trust Console verdict uses stalled radar metadata

The approvals Trust Console verdict opener SHALL derive its stalled-approval
clause from the flat response's `meta.stalled_count`, never by inspecting the
bounded decided-history rows. When the response names degraded sources, the
opener SHALL visibly name incomplete source coverage and SHALL NOT present zero
as a calm all-clear.

#### Scenario: History-window eviction cannot hide a stalled approval

- **WHEN** the decided-history response contains no stalled row because it is
  limited to its recent history window but its metadata has
  `stalled_count > 0`
- **THEN** the verdict opener reports the stalled count
- **AND** it does not conclude that no approval is stalled from the empty or
  bounded history rows

#### Scenario: Degraded stalled radar remains explicit

- **WHEN** the flat response has `meta.sources_degraded` and a zero or partial
  `meta.stalled_count`
- **THEN** the verdict opener names the unavailable source coverage
- **AND** it does not render a healthy no-stalled-approvals conclusion



## dashboard-approvals — Approval Detail API


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Approval Detail API

The dashboard SHALL expose `GET /api/approvals/{id}` returning the full dossier
for one approval.

#### Scenario: Detail response shape

- **WHEN** `GET /api/approvals/{id}` is called
- **THEN** the response is `ApiResponse[ApprovalDetail]` with fields `id`,
  `title`, `butler`, `status`, `created_at` (alias `ts`), `expires_at` (alias
  `expires`), `why` (string | null — serif paragraph), `evidence` (string[] |
  null — mono lines), `proposed_action` (object describing the tool call being
  approved), `session_id` (string | null — the originating session/trace, when
  known), `decided_by` (string | null), `decided_at` (timestamp | null),
  `denial_reason` (string | null), and `execution_result` (object | null).
- **AND** when `why` or `evidence` is null (legacy row), the UI renders a
  serif-italic empty state for the missing section.
- **AND** when `session_id` is present, the dossier header links to
  `/sessions/{session_id}` so the owner can inspect the originating
  session/trace before deciding.



## dashboard-approvals — Approval Verbs


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Approval Verbs
The dashboard SHALL expose explicit verb endpoints for approve, deny, defer,
and dashboard-only abandonment. A defer remains an authenticated pending-state
operation: it extends expiry and atomically resets the approval notification
re-presentation timer to `now + hours` through the delivery-intent
presentation-generation protocol, rather than through generic notification
retry or defer controls.

ID: REQ-dashboard-approvals-003
Source: RFC-0021,RFC-0023
Scope: v1-mandatory

#### Scenario: Defer with bounded hours and a later presentation
- **WHEN** `POST /api/approvals/{id}/defer {hours: int}` is called
- **THEN** the call is rejected with `422` unless `1 ≤ hours ≤ 168`
- **AND** on success, one action/intent/presentation transaction extends
  `expires_at`, supersedes an unstarted current presentation, and schedules
  exactly one successor generation for that successful defer at `now + hours`
- **AND** it retains the logical action key, does not enqueue a generic
  notification/deferred row, and appends
  `audit.append("approval.defer", target=action_id, note=str(hours))`

#### Scenario: Defer races a worker without an unsafe duplicate
- **WHEN** an authenticated defer and a worker handoff-start contend for the
  current presentation
- **THEN** defer-first prevents that generation's provider call, while
  handoff-start-first leaves only historical attempt evidence and schedules
  that successful defer's one successor generation
- **AND** the response/API projection reports only safe timing/state, never a
  key, callback token, recipient, or raw provider result

#### Scenario: Defer preserves other burst-digest members
- **WHEN** an authenticated actor defers a fourth cohort anchor or later
  collapsed member before its shared cohort digest begins handoff
- **THEN** the action's later direct presentation is scheduled for `now + hours`
  and that action's membership is marked ineligible for the unstarted digest
- **AND** other eligible cohort members remain deliverable through their shared
  digest without exposing a recovery key or callback material

#### Scenario: Approve with optional edits

- **WHEN** `POST /api/approvals/{id}/approve {edits?: object}` is called
- **THEN** the action is approved with any supplied `edits` applied to its arguments
- **AND** `audit.append("approval.approve", target=action_id, note=json.dumps(edits))` is invoked
- **AND** the underlying tool is executed via the shared executor (existing module-approvals behavior).

#### Scenario: Deny with reason

- **WHEN** `POST /api/approvals/{id}/deny {reason?: str}` is called
- **THEN** the action transitions to `rejected`
- **AND** `audit.append("approval.deny", target=action_id, note=reason)` is invoked.

#### Scenario: Defer with bounded hours

- **WHEN** `POST /api/approvals/{id}/defer {hours: int}` is called
- **THEN** the call is rejected with `422` unless `1 ≤ hours ≤ 168`
- **AND** on success, the action's `expires_at` is extended by `hours` and the notification re-presentation timer is reset to `now + hours`
- **AND** `audit.append("approval.defer", target=action_id, note=str(hours))` is invoked.

#### Scenario: Dashboard abandons an eligible stalled action

- **WHEN** `POST /api/approvals/{id}/abandon {reason: string}` is called by an
  authenticated dashboard actor for an action whose status is `approved` and
  execution result is null
- **THEN** the action transitions to `abandoned` with an immutable
  `action_abandoned` event carrying the actor and non-blank reason
- **AND** the response reports the terminal status
- **AND** the action is absent from stalled results and has no Retry affordance.

#### Scenario: Dashboard rejects invalid abandonment without mutation

- **WHEN** the abandon endpoint receives a blank reason or an action outside
  the exact approved/null-execution predicate
- **THEN** it returns a validation or transition error without writing an event
  or changing the action.

#### Scenario: Abandonment has no alternate invocation path

- **WHEN** an approval is surfaced through an MCP tool, Telegram callback,
  automatic workflow, scheduled cleanup, or bulk operation
- **THEN** that path does not expose or invoke abandonment.



## dashboard-approvals — Post-Approval Teaching Digest


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Post-Approval Teaching Digest

After a successful approval, `/approvals` SHALL offer a short, inline opportunity to create a standing rule for the approved action. Approval itself has no implicit rule-creation side effect.

#### Scenario: Show a redacted proposed scope after approval

- **WHEN** `POST /api/approvals/{id}/approve` succeeds
- **THEN** the dashboard fetches `GET /api/approvals/rules/suggestions/{id}`
- **AND** displays "Approved. Always allow this shape?" with the redacted proposed constraints.

#### Scenario: A standing rule requires a second confirmation

- **WHEN** the owner selects "Always allow this shape"
- **THEN** the dashboard presents a distinct "Create standing rule" confirmation
- **AND** only that confirmation calls `POST /api/approvals/rules/from-action` with the action ID.

#### Scenario: Keep asking does not mutate autonomy

- **WHEN** the owner selects "Keep asking" or dismisses the digest
- **THEN** no standing-rule mutation is sent.

#### Scenario: Proposed scope is unavailable

- **WHEN** the rule-suggestion preview cannot be fetched
- **THEN** the dashboard reports that no rule was created
- **AND** does not offer a create action based on an unknown scope.



## dashboard-approvals — Owner Attention Policy


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Owner Attention Policy

The dashboard SHALL expose the stable `GET/PUT /api/approvals/policy` endpoint
to manage the global Owner Attention Policy. The policy controls routine
owner-attention suppression; it does not configure per-butler
`delivery_preferences`.

#### Scenario: Read policy

- **WHEN** `GET /api/approvals/policy` is called
- **THEN** the response is `ApiResponse[ApprovalsPolicy]` with
  `quiet_start_hour: int` (0–23), `quiet_end_hour: int` (0–23), and
  `timezone: str` (IANA)
- **AND** its semantics are an end-exclusive
  `[quiet_start_hour, quiet_end_hour)` Owner Attention Policy interval

#### Scenario: Update complete policy

- **WHEN** `PUT /api/approvals/policy` is called with both hour fields in
  0–23 and a recognized IANA timezone
- **THEN** the singleton row is updated and `audit.append("approvals.policy")`
  is invoked

#### Scenario: Reject incomplete or invalid policy

- **WHEN** `PUT /api/approvals/policy` supplies only one quiet hour or an
  unrecognized IANA timezone
- **THEN** validation rejects the request without mutating the singleton row

#### Scenario: Quiet hours defer a routine owner-default notification

- **WHEN** the notification dispatcher handles a routine implicit-owner `send`
  or `insight` call with priority other than `high`
- **AND** the current local time is within the end-exclusive policy interval
- **THEN** it parks the full envelope in the originating schema's
  `deferred_notifications` table for the exact configured quiet end
- **AND** it returns the established `deferred` result rather than silently
  dropping the page
- **AND** high-priority and explicit-target notifications retain their existing
  immediate behavior

#### Scenario: Approval-request pushes retain their dedicated behavior

- **WHEN** an approval gate emits an `approval_request` push during Owner
  Attention Policy quiet hours
- **THEN** its existing decision-loop deferral behavior uses the exact policy
  end while its pending-action expiry semantics remain unchanged
- **AND** it is not reclassified as a routine `send` or `insight` hold



## dashboard-approvals — Approvals Live Stream


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Approvals Live Stream

The dashboard SHALL fan approval lifecycle events onto the unified fleet event bus (`WS /api/events/stream`). There is no dedicated approvals stream route.

#### Scenario: Stream event shape

- **WHEN** an approval transitions state
- **THEN** an event `{type: "approval", data: {kind: "created"|"approved"|"rejected"|"deferred"|"executed"|"expired"|"abandoned", approval_id, ...}}` is broadcast on `WS /api/events/stream`.



## dashboard-approvals — Promotion Suggestions API Endpoint


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Promotion Suggestions API Endpoint

The dashboard API SHALL expose `GET /api/approvals/suggestions` which returns a list of autonomy promotion and demotion suggestions.

The endpoint SHALL accept the following query parameters:
- `status` (string, optional, default `pending`) -- filter by suggestion status: `pending`, `confirmed`, `dismissed`, `superseded`, or `all`
- `suggestion_type` (string, optional) -- filter by type: `promotion` or `demotion`
- `limit` (integer, optional, default 20) -- maximum number of suggestions to return
- `offset` (integer, optional, default 0) -- pagination offset

The response MUST be a `PaginatedResponse<AutonomySuggestion>` where each `AutonomySuggestion` object contains:
- `id` -- string UUID identifying the suggestion
- `action_id` -- optional string UUID of the originating approval action, when retained
- `suggestion_type` -- `"promotion"` or `"demotion"`
- `pattern_fingerprint` -- string hash identifying the action pattern
- `tool_name` -- string name of the tool
- `representative_args` -- object with the exact tool arguments this suggestion covers
- `scope_description` -- string human-readable description of what the proposed rule would auto-approve
- `status` -- one of `"pending"`, `"confirmed"`, `"dismissed"`, `"superseded"`
- `approval_count_at_creation` -- integer number of approvals that triggered this suggestion
- `created_at` -- ISO 8601 timestamp
- `decided_at` -- ISO 8601 timestamp (null if pending)
- `decided_by` -- string identifier (null if pending)
- `resulting_rule_id` -- string UUID of rule created on confirmation (null otherwise)
- `velocity` -- object (nullable) containing `avg_seconds`, `sample_count`, `fast_approval` from the velocity tracker

#### Scenario: Fetch pending promotion suggestions

- **WHEN** `GET /api/approvals/suggestions?status=pending&suggestion_type=promotion` is called
- **THEN** the API MUST return all pending promotion suggestions sorted by `created_at DESC`
- **AND** each suggestion MUST include `scope_description` and `velocity` data
- **AND** the response status MUST be 200

#### Scenario: Fetch pending demotion suggestions

- **WHEN** `GET /api/approvals/suggestions?status=pending&suggestion_type=demotion` is called
- **THEN** the API MUST return all pending demotion suggestions
- **AND** each suggestion MUST include the error details from the failed execution in metadata

#### Scenario: No pending suggestions

- **WHEN** `GET /api/approvals/suggestions?status=pending` is called and none exist
- **THEN** the API MUST return an empty array with response status 200



## dashboard-approvals — Suggestion Confirmation API Endpoint


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Suggestion Confirmation API Endpoint

The dashboard API SHALL expose `POST /api/approvals/suggestions/{suggestionId}/confirm` to confirm a promotion or demotion suggestion.

#### Scenario: Confirm a promotion suggestion via API

- **WHEN** `POST /api/approvals/suggestions/{suggestionId}/confirm` is called with authenticated user context
- **THEN** the API MUST invoke `confirm_promotion_suggestion` on the approvals module
- **AND** the response MUST include the created `rule_id` on success
- **AND** the response status MUST be 200

#### Scenario: Confirm without authentication

- **WHEN** `POST /api/approvals/suggestions/{suggestionId}/confirm` is called without authentication
- **THEN** the response status MUST be 401



## dashboard-approvals — Suggestion Dismissal API Endpoint


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Suggestion Dismissal API Endpoint

The dashboard API SHALL expose `POST /api/approvals/suggestions/{suggestionId}/dismiss` to dismiss a promotion or demotion suggestion. The request body MAY include an optional `reason` string.

#### Scenario: Dismiss a suggestion via API

- **WHEN** `POST /api/approvals/suggestions/{suggestionId}/dismiss` is called with `{"reason": "Not needed"}` and authenticated user context
- **THEN** the API MUST invoke `dismiss_promotion_suggestion` on the approvals module
- **AND** the response status MUST be 200

#### Scenario: Dismiss without reason

- **WHEN** `POST /api/approvals/suggestions/{suggestionId}/dismiss` is called with no body
- **THEN** the dismissal MUST proceed with `reason` as `null`



## dashboard-approvals — Autonomy Suggestions Dashboard Section


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Autonomy Suggestions Dashboard Section

The approvals dashboard page at `/approvals` SHALL include an "Autonomy Suggestions" section displayed above the two-pane approvals body (the pending-approvals rail and dossier) when pending suggestions exist. The prior "actions table" layout referenced here has graduated to the Dispatch two-pane dossier, so the section sits between the page header and that body.

#### Scenario: Promotion suggestion card displayed

- **WHEN** pending promotion suggestions exist
- **THEN** the dashboard MUST display a card for each suggestion containing:
  - The tool name
  - The human-readable scope description (e.g., "Auto-approve send_telegram when chat_id = 'mom_123' AND text = 'Good morning'")
  - The number of times this exact action was manually approved
  - Approval velocity indicator (fast/normal)
  - "Confirm rule" and "Dismiss" action buttons
- **AND** the card MUST visually emphasize that the rule scope is exact-match only

#### Scenario: Demotion suggestion card displayed

- **WHEN** pending demotion suggestions exist
- **THEN** the dashboard MUST display a warning card for each demotion containing:
  - The tool name and rule description
  - The execution error summary
  - "Revoke rule" and "Keep rule" action buttons
- **AND** the card MUST use a warning/alert visual style

#### Scenario: Suggestion links back to its source approval

- **WHEN** an autonomy suggestion includes `action_id`
- **THEN** its card MUST offer a link to `/approvals/{action_id}` so the owner can review the originating dossier.

#### Scenario: No pending suggestions hides section

- **WHEN** no pending suggestions exist
- **THEN** the autonomy suggestions section MUST NOT be rendered

#### Scenario: Confirm suggestion from card

- **WHEN** a user clicks "Confirm rule" on a promotion suggestion card
- **THEN** the dashboard MUST call `POST /api/approvals/suggestions/{id}/confirm`
- **AND** the card MUST be removed from the suggestions section on success
- **AND** a success toast MUST indicate the new standing rule was created

#### Scenario: Dismiss suggestion from card

- **WHEN** a user clicks "Dismiss" on a promotion suggestion card
- **THEN** the dashboard MAY show an optional reason input
- **AND** MUST call `POST /api/approvals/suggestions/{id}/dismiss`
- **AND** the card MUST be removed from the suggestions section on success



## dashboard-approvals — Rule Promotion Suggestions API Endpoint


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Rule Promotion Suggestions API Endpoint

The dashboard API SHALL expose `GET /api/switchboard/rule-promotion-suggestions`, taking no query parameters and returning `ApiResponse[RulePromotionSurface]` with two sections, `pending` and `auto_applied`, drawn from `switchboard.rule_promotion_suggestions`. This is a distinct endpoint namespace from `GET /api/approvals/suggestions` (autonomy tool-call suggestions) — the two suggestion families track different underlying tables and are not merged into one response shape, though both render through the dashboard's approvals-surface visual language.

- `pending` holds `pending_review` promotion suggestions that need an owner decision: every `route_to:<butler>` suggestion and any suggestion that is not clearly automated. Clearly-automated suggestions whose action is `skip` or `metadata_only` auto-apply and never appear here. Each item carries `id`, `sender_key`, `source_channel`, `proposed_rule_type`, `proposed_condition`, `proposed_action`, `evidence_count`, `is_clearly_automated`, `first_evidence_at`, `last_evidence_at`, and `created_at`, ordered by `created_at ASC`.
- `auto_applied` holds suggestions confirmed by the auto-apply actor, newest decision first and capped at 50. Each item carries `id`, `sender_key`, `source_channel`, `proposed_action`, `evidence_count`, `created_rule_id`, `rule_enabled` (the minted rule's live `enabled` state), `decided_at`, and `decided_by`.

If either section's query fails, that section MUST be returned empty and its source name (`rule_promotion_pending` or `rule_promotion_auto_applied`) MUST be listed in `meta.sources_degraded`, so a read failure is never shown as a genuinely empty queue.

#### Scenario: Fetch pending rule promotion suggestions

- **WHEN** `GET /api/switchboard/rule-promotion-suggestions` is called and pending suggestions exist
- **THEN** the response status MUST be 200
- **AND** `data.pending` MUST list them oldest first, excluding clearly-automated `skip`/`metadata_only` suggestions
- **AND** `data.auto_applied` MUST list auto-applied promotions with their minted rule's `rule_enabled` state

#### Scenario: No pending suggestions

- **WHEN** `GET /api/switchboard/rule-promotion-suggestions` is called and no pending or auto-applied suggestions exist
- **THEN** the API MUST return empty `pending` and `auto_applied` arrays with response status 200
- **AND** `meta.sources_degraded` MUST NOT name either section



## dashboard-approvals — Rule Promotion Metrics Endpoint and Tile


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Rule Promotion Metrics Endpoint and Tile

The dashboard SHALL expose a rule-promotion metrics endpoint `GET /api/switchboard/rule-promotion-stats` returning aggregate counts over all time: promotion-suggestion lifecycle counts (`suggestions_pending`, `suggestions_confirmed`, `suggestions_dismissed`, `suggestions_superseded`), live promoted rules (`promoted_rules_active`, meaning `ingestion_rules` with `created_by='promotion'`, `enabled`, not soft-deleted), events those rules routed without an LLM session (`promoted_rule_matches`, meaning `routing_verdict_log` rows with `verdict_source='rule'` matched to a promoted rule), an `llm_sessions_avoided_estimate`, the demotion drift signal (`demotion_pending`, meaning pending `suggestion_kind='demotion'` suggestions), and the spot-check sample count backing the agreement scores (`promoted_rule_spot_checks`).

`llm_sessions_avoided_estimate` SHALL equal `promoted_rule_matches`: one event routed by a promoted rule is one spawned-session LLM round-trip removed. It MUST be labelled an estimate in the UI (it counts matches since promotion, not a counterfactual replay).

The endpoint SHALL compute each block with an independent sub-query and follow the degraded-envelope convention: a failed sub-query leaves its fields at 0 AND adds its source name (`suggestion_counts`, `promoted_rules`, or `verdict_metrics`) to `meta.sources_degraded`, so a failed query never renders as a truthful zero.

The approvals dashboard SHALL render a rule-promotion metrics tile from this endpoint when there is any promotion history to measure, or whenever a source is degraded. A degraded block SHALL render an inline degraded note naming the unavailable source rather than a fabricated zero.

#### Scenario: Metrics reported for promoted rules

- **WHEN** promoted rules have routed events recorded in `routing_verdict_log` with `verdict_source='rule'`
- **THEN** `GET /api/switchboard/rule-promotion-stats` returns `promoted_rule_matches` equal to that count
- **AND** `llm_sessions_avoided_estimate` equals `promoted_rule_matches`

#### Scenario: A failed sub-query is flagged, not zeroed silently

- **WHEN** the verdict-log sub-query raises
- **THEN** the response still returns 200 with the other blocks computed
- **AND** `meta.sources_degraded` contains `verdict_metrics`
- **AND** the tile renders a degraded note for the savings block instead of showing zero sessions avoided

#### Scenario: Sessions-avoided is labelled an estimate

- **WHEN** the metrics tile renders the sessions-avoided figure
- **THEN** the tile MUST label it an estimate rather than an exact measured count



## dashboard-approvals — Decision and execution outcome in approval dossier


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Decision and execution outcome in approval dossier

The dashboard SHALL render an approval's retained decision provenance and safe
terminal outcome from the approval-detail response. It SHALL not add a durable
copy of an audit-event rejection reason or broaden retry behavior.

#### Scenario: Rejection reason comes from the latest immutable event

- **WHEN** a rejected action has one or more immutable `action_rejected`
  `approval_events` with a recorded reason
- **THEN** the detail response includes the reason from the latest such event
  as `denial_reason`
- **AND** the dossier renders that recorded denial reason alongside its existing
  `decided_by` and `decided_at` provenance.

#### Scenario: Legacy or unavailable rejection event does not break detail

- **WHEN** an action has no readable `action_rejected` event, including a legacy
  row or a pool where the optional event lookup is unavailable
- **THEN** the detail response includes `denial_reason: null`
- **AND** the remaining dossier detail remains available without synthesizing a
  reason from presentation text.

#### Scenario: Execution outcome is redacted before presentation

- **WHEN** an action has a persisted `execution_result`
- **THEN** the detail response and dossier render only the result after the
  established approvals redaction contract is applied
- **AND** an execution-result `error` does not expose its raw message or any
  secret-derived text.

#### Scenario: Retry is offered only to an approved unexecuted action

- **WHEN** the dossier detail reports `status = "approved"` and
  `execution_result = null`
- **THEN** the dossier renders its Retry dispatch control.

#### Scenario: Retry is absent after an execution record or a non-approved decision

- **WHEN** the dossier detail has a non-null `execution_result` or a status
  other than `approved`
- **THEN** the dossier does not render Retry dispatch
- **AND** an executed failure is not made retryable by this dashboard surface.



## dashboard-approvals — Retry Reports Dispatch Failure Class Truthfully


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Retry Reports Dispatch Failure Class Truthfully

Approval Retry endpoints MUST distinguish failure to reach the owning butler from a
reachable executor or tool rejection. Neither failure class may be presented as
successful execution, and safe actionable detail MUST be returned for a reachable
rejection.

#### Scenario: Owning butler is unreachable

- **WHEN** Retry cannot establish a dispatch path to the owning butler
- **THEN** the API returns an unavailable response identifying that no owning butler is reachable

#### Scenario: Reachable executor rejects stored action

- **WHEN** Retry reaches the owning butler and its executor or native handler rejects the stored action
- **THEN** the API returns a failure response identifying an executor or tool rejection
- **AND** the response includes bounded safe detail suitable for operator diagnosis
- **AND** it MUST NOT claim that no butler was reachable

#### Scenario: Retry execution fails

- **WHEN** either Retry endpoint receives a dispatch failure
- **THEN** the action remains `approved` with `execution_result = null`



## dashboard-approvals — Approval readers preserve unavailable source evidence


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Approval readers preserve unavailable source evidence

The `/approvals` surface SHALL distinguish a successful empty metrics,
autonomy-suggestions, or rule-promotion response from a failed or degraded
read. A family-specific approval metrics failure SHALL name the affected pool
and never imply a zero-derived all-clear. A whole suggestions or promotion
query failure SHALL render a named unavailable state with a read-only retry;
an error after cached cards or a cached tile SHALL retain that usable evidence
alongside the unavailable state.

#### Scenario: Pending metrics are partial

- **WHEN** approval metrics include non-empty
  `meta.pending_actions_sources_degraded`
- **THEN** `/approvals` names the unavailable pending-actions source(s) and
  exposes a retry of the metrics read
- **AND** it does not present the partial pending count as a complete empty or
  all-clear result
- **AND** the independently fetched queue remains visible according to its own
  source health.

#### Scenario: Rule metrics are partial without affecting action metrics

- **WHEN** approval metrics include non-empty
  `meta.approval_rules_sources_degraded` but no pending-actions degradation
- **THEN** `/approvals` names the unavailable rule source(s) and exposes a
  retry of the metrics read
- **AND** it does not classify a partial active-rule count as a real zero
- **AND** it does not label a healthy pending-actions result unavailable.

#### Scenario: A suggestions reader fails before returning data

- **WHEN** an autonomy-suggestions or rule-promotion-suggestions query fails
  before a successful response is available
- **THEN** its section renders a named unavailable state and a retry control
- **AND** it does not hide the section by treating the missing response as an
  empty suggestions list.

#### Scenario: A promotion reader retains cached evidence on refresh failure

- **WHEN** a rule-promotion suggestions or statistics query fails after cached
  cards or statistics are available
- **THEN** the cached cards or tile remain visible
- **AND** a named unavailable state and retry control are rendered beside that
  stale evidence
- **AND** an existing per-block `meta.sources_degraded` note continues to hide
  only its affected fabricated-zero block.



## dashboard-approvals — URL-backed stalled approvals lane


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: URL-backed stalled approvals lane

The Approvals Trust Console SHALL treat its `state` query parameter as the
source of truth for the rail lane. The default rail SHALL read the waiting
flat approvals state, and `/approvals?state=stalled` SHALL read the existing
flat stalled state whose rows satisfy exactly `status = approved` and
`execution_result = null`. The dashboard SHALL NOT persist `stalled` as an
approval status.

The lane control and its rows SHALL use native keyboard-operable elements,
show visible focus, and expose the active lane semantically. Selecting a
dossier from the stalled lane SHALL retain `state=stalled` in its URL.

#### Scenario: Direct stalled deep link opens the stalled rail

- **WHEN** the owner navigates directly to `/approvals?state=stalled`
- **THEN** the Trust Console requests the flat approvals endpoint with
  `state=stalled`
- **AND** the rail labels and displays only the returned stalled rows rather
  than the waiting queue.

#### Scenario: Direct stalled dossier remains reachable beyond the rail page

- **WHEN** the owner navigates to `/approvals/{id}?state=stalled` and the
  approval is stalled but falls outside the current bounded flat-result page
- **THEN** after the current stalled flat result settles, the Trust Console
  verifies that id through a dedicated forced-fresh detail query that does not
  reuse the ordinary dossier cache
- **AND** it displays the dossier only when that current verifier response has
  the exact requested id, `status = approved`, and an explicitly null
  `execution_result`
- **AND** it suppresses the dossier while verification is pending or fails,
  and when the response is pending, has a non-null or missing execution
  result, or names another id.

#### Scenario: Empty stalled lane remains truthful

- **WHEN** the settled stalled flat response has no rows and no degraded
  sources
- **THEN** the Trust Console reports that there are no stalled approvals
- **AND** it does not claim that no approvals are waiting or prompt the owner
  to select a pending approval.

#### Scenario: Stalled radar has a truthful drill-down destination

- **WHEN** the flat response reports one or more stalled actions in
  `meta.stalled_count`
- **THEN** the stalled verdict clause is a keyboard-operable link to
  `/approvals?state=stalled`
- **AND** it does not fabricate a link to a particular approval id from the
  aggregate count.

#### Scenario: Stalled lane does not expose pending-decision shortcuts

- **WHEN** the owner is viewing the stalled lane
- **THEN** its rail remains navigable by the existing keyboard movement
  controls
- **AND** it does not register approval, denial, or defer keyboard verbs for
  the selected stalled row.



## dashboard-approvals — Safe retry refreshes confirmed approval state


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Safe retry refreshes confirmed approval state

The dashboard SHALL reuse the existing Retry dispatch action only when an
approval has `status = approved` and an explicitly null `execution_result`.
It SHALL NOT render Retry for an executed failure, any non-approved status, or
an unknown/missing execution result. The Retry control SHALL remain pending
without locally removing the row while its server request is in flight.

After a successful server response, the dashboard SHALL invalidate all flat
approval query variants, the affected approval dossier, every isolated Stalled
direct-link verifier generation for that id, approval history, and approval
metrics so the count, lane, history, and dossier reconcile from
server-authoritative data. It SHALL not optimistically remove the row or
invalidate those views when the retry request fails.

#### Scenario: Retry stays bounded to an approved action without a result

- **WHEN** a stalled row has `status = approved` and
  `execution_result = null`
- **THEN** the dashboard renders the existing Retry dispatch control
- **AND** when the same row has a non-null or missing execution result, or a
  different status, the dashboard renders no Retry control.

#### Scenario: Confirmed retry reconciles every approval read

- **WHEN** the owner activates Retry dispatch and the server returns a
  successful response
- **THEN** the UI reports whether dispatch ran from that response
- **AND** it invalidates the waiting and stalled flat views, history, the
  affected dossier, any isolated Stalled direct-link verifier for that id, and
  metrics after that completion.

#### Scenario: Failed retry leaves the server-authoritative row visible

- **WHEN** the Retry dispatch request fails
- **THEN** the dashboard reports the returned error without claiming a
  dispatch cause
- **AND** it does not optimistically remove the row or invalidate the
  approval read caches.



## dashboard-approvals — Unroutable command recovery


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Unroutable command recovery

The Approvals command surface SHALL expose replay-eligible dashboard dead letters whose routing attempt reached zero targets. This is an owner-visible recovery queue, not a claim that a target accepted the original message.

#### Scenario: Unroutable question appears as a command row
- **WHEN** `GET /api/approvals/unroutable` finds a replay-eligible dashboard dead letter with typed unroutable routing evidence
- **THEN** `/approvals` SHALL render a compact `Unroutable: <question>` row
- **AND** the row SHALL offer a Retry verb without rendering the prior outcome as routed

#### Scenario: First retry queues one replay
- **WHEN** the owner invokes `POST /api/approvals/unroutable/{id}/retry` for an eligible dead letter
- **THEN** exactly one replay request SHALL be durably queued with lineage to the original dead letter
- **AND** the response SHALL identify the queued replay without claiming that routing has already succeeded

#### Scenario: Repeated or concurrent retry is idempotent
- **WHEN** the retry verb is invoked again for the same dead letter while or after its replay is queued
- **THEN** it SHALL return HTTP 409 Conflict
- **AND** it SHALL not enqueue a second replay request

#### Scenario: Unroutable recovery read is unavailable
- **WHEN** the unroutable-command read fails
- **THEN** the command surface SHALL show a named unavailable state
- **AND** it SHALL not substitute an empty all-clear



## dashboard-approvals — Rule Promotion Suggestion Confirm, Dismiss, and Rule-Enabled Endpoints


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Rule Promotion Suggestion Confirm, Dismiss, and Rule-Enabled Endpoints

The dashboard API SHALL expose three per-suggestion owner actions under `/api/switchboard/rule-promotion-suggestions/{id}`; there is no bulk-confirm endpoint:

- `POST .../{id}/confirm` mints the `ingestion_rules` row for a `pending_review` suggestion and returns `ApiResponse[IngestionRule]`. It returns 404 when the suggestion does not exist, 409 when it is not `pending_review`, and 422 for an invalid id or a `route_to` target that is not a registered butler. Each confirm is audited as `rule_promotion_confirm`.
- `POST .../{id}/dismiss` accepts an optional body `{reason, cooldown_days}` (`cooldown_days` defaults to 30 and MUST be non-negative), transitions the suggestion to `dismissed`, and records the reason, `cooldown_until`, and `decided_by = 'owner'`. It returns 404 when the suggestion does not exist and 409 when it is not `pending_review`.
- `POST .../{id}/rule-enabled` accepts `{enabled}` and toggles the rule minted from that suggestion, returning `{rule_id, enabled}`. It returns 404 when the suggestion or its rule does not exist and 409 when the suggestion has no minted rule. This is the reversible control for auto-applied promotions.

#### Scenario: Confirm a single suggestion

- **WHEN** `POST /api/switchboard/rule-promotion-suggestions/{id}/confirm` is called on a `pending_review` suggestion
- **THEN** the response status MUST be 200 and the body MUST carry the created ingestion rule
- **AND** a second confirm of the same suggestion MUST return 409

#### Scenario: Dismiss with reason

- **WHEN** `POST /api/switchboard/rule-promotion-suggestions/{id}/dismiss` is called with `{"reason": "Sender's routing target changed"}`
- **THEN** the suggestion MUST transition to `dismissed` with the reason and a cooldown recorded

#### Scenario: Disable an auto-applied rule

- **WHEN** `POST /api/switchboard/rule-promotion-suggestions/{id}/rule-enabled` is called with `{"enabled": false}` for an auto-applied suggestion
- **THEN** the minted rule MUST be disabled and the response MUST report `{rule_id, enabled: false}`
- **AND** calling it with `{"enabled": true}` MUST re-enable the same rule



## dashboard-approvals — Rule Promotion Approvals Banner


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Rule Promotion Approvals Banner

The approvals dashboard page SHALL render a rule-promotion banner, visually consistent with the Autonomy Suggestions section, fed by `GET /api/switchboard/rule-promotion-suggestions`.

- Each `pending` suggestion MUST render as its own card showing the sender and proposed action, the evidence count, and "Confirm rule" and "Dismiss" actions.
- Each `auto_applied` suggestion MUST render informationally (it needs no confirmation) with its evidence count marked as clearly automated and a reversible "Disable rule" / "Re-enable rule" control backed by the rule-enabled endpoint.
- When the read fails, the banner MUST show a degraded-source note rather than hiding itself.

#### Scenario: Pending suggestion renders as its own card

- **WHEN** a pending `route_to` suggestion exists
- **THEN** the banner MUST display it as its own card, not grouped with other suggestions

#### Scenario: Confirming from the dashboard calls the API

- **WHEN** the owner clicks "Confirm rule" on a pending card
- **THEN** the dashboard MUST call `POST /api/switchboard/rule-promotion-suggestions/{id}/confirm`
- **AND** on success the card MUST leave the banner and a "Routing rule created" toast MUST appear

#### Scenario: Auto-applied promotions are reversible

- **WHEN** an auto-applied promotion is listed
- **THEN** it MUST render without a confirm action
- **AND** clicking "Disable rule" MUST call the rule-enabled endpoint and the control MUST then offer "Re-enable rule"

#### Scenario: Nothing to show hides the banner

- **WHEN** there are no pending items, no auto-applied items, and no read error
- **THEN** the rule-promotion banner MUST NOT be rendered



## dashboard-approvals — Truthful approval-delivery recovery projection


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Truthful approval-delivery recovery projection
The approvals API and dashboard SHALL project a pending action's durable delivery state, mode, safe reason, attempt count, next eligible time, and derived stuck/ambiguous status without exposing recipient identity, callback material, action key, raw provider response, raw error, or a false claim that uncertain delivery was never attempted.

ID: REQ-dashboard-approvals-001
Source: RFC-0023
Scope: v1-mandatory

#### Scenario: Retry and quiet-hours state render honestly
- **WHEN** a pending action has a `ready`, `retry_wait`, or quiet-hours-delayed current delivery presentation
- **THEN** list and detail responses identify its delivery state and safe timing/reason truth
- **AND** the dashboard distinguishes waiting/retrying from confirmed notification delivery

#### Scenario: Ambiguous state is visible without unsafe detail
- **WHEN** an approval delivery presentation is `ambiguous` or derived stuck
- **THEN** the action detail visibly marks notification recovery as uncertain or needing attention
- **AND** neither the API nor UI includes the provider error text, recipient, callback token, secret, or provider payload

#### Scenario: Legacy emission data remains explicitly legacy
- **WHEN** an action predates new intent admission and only has an `approval_push_emissions` outcome
- **THEN** the API identifies that outcome as legacy evidence rather than authoritative recovery state
- **AND** the UI does not claim “never attempted” solely because that legacy outcome is null or failed



## dashboard-approvals — Safe delivery-recovery observability


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Safe delivery-recovery observability
The approvals service SHALL expose aggregate delivery-recovery counts, oldest-due age, expired-lease count, ambiguous count, and safe state/reason dimensions for operator observability without adding a dashboard action that approves, executes, replays, or mutates a parked domain action.

ID: REQ-dashboard-approvals-002
Source: RFC-0023
Scope: v1-mandatory

#### Scenario: Stuck backlog is measurable without sensitive labels
- **WHEN** a schema contains overdue retry work, expired claims, or ambiguous presentations
- **THEN** its metrics/read-model aggregation reports the safe count and age dimensions needed to detect the condition
- **AND** labels and responses exclude tool arguments, message content, recipient, callback material, and raw transport data

#### Scenario: Observability does not create a domain-action bypass
- **WHEN** an operator views delivery-recovery detail or a stuck-presentation aggregate
- **THEN** the surface provides no control that approves, rejects, expires, executes, or replays the parked domain action
- **AND** normal authenticated approval decision endpoints remain the only domain-action path



## dashboard-approvals — Prepared approval presentation is deliberate digest truth


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. exact-call owner authorization, delivery versus decision truth, pending/stalled coverage, expiry, defer, abandonment, retry predicates, prepared origin and promotion-family separation. D19 adds goal/invocation/exposure/cause and cancelled/unknown refusal, preserving all old approval clauses. Direct authored recipient/content Submit has its own server approval and is excluded from promotion. Untrusted prepared effects stay parked. Routing promotion cannot change deterministic admission policy; retiring that routing UI requires its owner-controlled collision amendment. No old execution or recipient authority is inferred from the new labels. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Prepared approval presentation is deliberate digest truth

The approval rail and selected dossier SHALL present an explicit server-observed `origin="prepared"` as a calm, accessible `Prepared` origin label while retaining the action's actual decision and execution status. Intended absence of a prepared-action push SHALL NOT render a failed-push warning. An ordinary confirmed failed push and independently observed failure or degraded evidence SHALL retain their existing truthful presentation. Unknown origin SHALL remain distinguishable from Prepared and SHALL NOT be inferred from absence or treated as an all-clear. This classification SHALL add no prepare, approve, execute, send, retry or provider authority.

ID: REQ-dashboard-approvals-004
Source: prepared-approval-origin-presentation/design.md; dashboard-design-language Kind Tags and Page Primitive; owner-released run13 S3 (bu-3b6goa)
Scope: v1-mandatory

#### Scenario: Prepared draft is calm in the rail and dossier

- **WHEN** real list and detail responses identify a pending digest-only action with explicit prepared origin and no actual failed push
- **THEN** both the mounted rail item and its selected dossier expose `Prepared` through neutral Dispatch category treatment and accessible text
- **AND** intended non-send does not create a failed-push warning, delivery-success claim or green executed state.

#### Scenario: Ordinary failed delivery remains a real failure

- **WHEN** an ordinary pending action has an explicit confirmed failed push, including a row whose origin classification is unknown
- **THEN** the rail and dossier retain the existing failure warning and safe failure context
- **AND** the Prepared classification rule neither silences real failure nor weakens existing action permissions.

#### Scenario: Unknown and malformed origin remain honest

- **WHEN** a list or detail response omits origin or carries null, malformed or unrecognized origin and lacks confirmed delivery success
- **THEN** the consumer distinguishes unknown classification from Prepared without inventing a prepared badge, a failed push or an all-clear
- **AND** actual decision status and independent delivery evidence remain visible under their existing contracts.

#### Scenario: Loading and degraded reads do not fabricate Prepared

- **WHEN** the list or dossier is loading, fails before data, or fails during refresh with cached data
- **THEN** existing loading, named error, partial-coverage and cached-evidence states remain explicit instead of fabricating a Prepared row or clean empty result
- **AND** any retained cached Prepared evidence is accompanied by its existing unavailable or stale indication and does not certify current delivery health.

#### Scenario: Origin label preserves selection status and decision boundaries

- **WHEN** an explicit prepared action is selected, changes decision or execution status, or the owner selects a different action
- **THEN** the origin label stays bound to that action and remains separate from its actual pending, approved, executed, rejected, expired or abandoned status
- **AND** existing keyboard selection, focus, dossier URL, expiry and server-authorized action controls remain intact
- **AND** no origin label triggers a prepare, approve, execute, send, retry or provider request.

## Source References

- PLAN.md §5 `/approvals` API surface and §6 Phase 6 implementation order.
- Visual reference: the `ApprovalsPage` redesign prototype (graduated; now shipped in `frontend/`).
- Reuses `audit.append()` from dashboard-audit-log on every mutation.


## dashboard-overview — Home Page Information Hierarchy


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. truthful whole-population triage, unavailable sources, action eligibility, caching, period/currency and safe drill-down. Completed goals today is the proposed successor hero, counted only from D11 completion and actual owner-reply delivery. Active goals, due obligations and decisions replace unit/session editorial hierarchy. Unknown/partial/failed/stopped/budget-exhausted receipts remain supporting facts; old runtime facts move to System only where their actual source exists. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Home Page Information Hierarchy

The home page at `/` SHALL render the editorial triage cockpit for the owner.
It SHALL use the editorial archetype defined by
`about/heart-and-soul/design-language.md` and
`about/lay-and-land/frontend.md`: a two-column page where the left column is the
system speaking and naming attention, and the right column is a quiet operational
index.

The page SHALL render these surfaces:

1. **Briefing**: the Voice surface rendered from `GET /api/dashboard/briefing`.
2. **Needs attention**: a rule-separated attention list derived from
   `GET /api/issues`.
3. **Runtime KPI strip**: four promoted operational KPIs derived from
   `GET /api/butlers` and `GET /api/approvals/metrics`.
4. **Operations**: right-column butler scan list derived from `GET /api/butlers`
   and `GET /api/spend/summary?period=today`.
5. **Now**: right-column immediate operational items derived from existing
   approval, QA, notification, and activity endpoints.

The session stripe chart SHALL NOT be the primary region of the Overview page.
No chart-first or card-grid requirement SHALL outrank the briefing, attention
list, or KPI strip on `/`.

#### Scenario: Editorial cockpit renders instead of chart-first hierarchy

- **WHEN** a user navigates to `/`
- **THEN** `DashboardPage` renders inside `<Page archetype="editorial" title="Overview">`
- **AND** the page presents the briefing, `Needs attention`, runtime KPI strip,
  `Operations`, and `Now` surfaces
- **AND** no session stripe chart is required as the first or dominant region
- **AND** the Overview does not require the old five-region chart-first order
  (session chart, recent moments, secondary cards, QA widget, supporting strip)

#### Scenario: Existing endpoint data sources are sufficient

- **WHEN** the Overview composes its cockpit surfaces
- **THEN** it uses the existing endpoint families named in this requirement
- **AND** it SHALL NOT introduce a new Overview aggregation endpoint unless a
  separate OpenSpec change justifies why the existing dashboard sources cannot
  supply the required state



## dashboard-overview — Briefing Voice Surface


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. truthful whole-population triage, unavailable sources, action eligibility, caching, period/currency and safe drill-down. Completed goals today is the proposed successor hero, counted only from D11 completion and actual owner-reply delivery. Active goals, due obligations and decisions replace unit/session editorial hierarchy. Unknown/partial/failed/stopped/budget-exhausted receipts remain supporting facts; old runtime facts move to System only where their actual source exists. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Briefing Voice Surface

The home page SHALL render the existing dashboard briefing as the first
left-column surface. The briefing wire contract is owned by
`dashboard-briefing`; `dashboard-overview` owns only the page composition that
consumes it.

#### Scenario: Briefing uses the six-field briefing response

- **WHEN** the briefing query succeeds
- **THEN** the page renders `greet`, `headline`, `elaboration`, `source`,
  and `generated_at` from `GET /api/dashboard/briefing` (the `state_class`
  field is a server-side classifier input and is not rendered by the page)
- **AND** the page does not require or render additional machine provenance
  fields from that endpoint

#### Scenario: Briefing handles loading and fallback states

- **WHEN** the briefing query is fetching
- **THEN** the status pill names the in-flight state
- **AND** the Voice paragraph area remains stable

- **WHEN** the endpoint returns `source = "fallback"`
- **THEN** the page renders the fallback paragraph without treating fallback as
  an error state



## dashboard-overview — Needs Attention List


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. truthful whole-population triage, unavailable sources, action eligibility, caching, period/currency and safe drill-down. Completed goals today is the proposed successor hero, counted only from D11 completion and actual owner-reply delivery. Active goals, due obligations and decisions replace unit/session editorial hierarchy. Unknown/partial/failed/stopped/budget-exhausted receipts remain supporting facts; old runtime facts move to System only where their actual source exists. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Needs Attention List

The home page SHALL render a `Needs attention` list composed from current state from
`GET /api/issues`, the canonical butler liveness verdict, pending approvals, bounded
notification delivery pressure, and active QA staffer state. A row SHALL represent either
live state or a time-bounded recent failure; older issue and notification records remain
available as history and SHALL NOT make the list or briefing imply that the system is
currently unhealthy. The list is a rule-separated attention surface, not a card grid or
table.

#### Scenario: Unknown latest QA patrol status surfaces as attention

- **WHEN** `GET /api/qa/summary` reports `staffer_status = "unknown_patrol_status"`
  for a latest completed patrol and its circuit breaker is not tripped
- **THEN** the attention list renders a high-severity `QA patrol status unknown` row
  linking to `/qa`
- **AND** the row explains that the latest patrol reported an unrecognized status
  without rendering the raw stored value as UI copy
- **AND** the same condition appears in the Overview's `Now` list and SHALL NOT be
  omitted as healthy, calm, or no QA attention
- **AND** a tripped breaker continues to take precedence over this row

#### Scenario: Attention rows are derived from current issues

- **WHEN** `GET /api/issues` returns one or more `Issue` objects whose parseable
  `last_seen_at` falls in the closed interval `[now - 12 hours, now]`
- **THEN** each current row shows severity mark, issue description, butler/source detail,
  optional error context, and a link when `link` is present
- **AND** within a severity tier, older unresolved current issues sort before newer issues
  when `first_seen_at` exists
- **WHEN** an issue has `last_seen_at` older than 12 hours or no parseable `last_seen_at`
- **THEN** it SHALL NOT render as a current attention row
- **AND** it remains eligible for the older-history rollup

#### Scenario: Attention rows are severity-first and stable across kinds

- **WHEN** the attention list composes rows from more than one source kind
  (issue, runtime/liveness, approval, notification, qa)
- **THEN** the full list is ordered by severity first — critical, then
  high/error, then medium/warning/warn, then low, then all other
  severities — across ALL kinds, not grouped by kind first
- **AND** a higher-severity row from one kind (e.g. a tripped QA circuit
  breaker) SHALL rank above a lower-severity row from another kind (e.g. a
  medium-severity issue), even if that other kind is normally rendered
  earlier
- **AND** rows tied on severity keep a stable, deterministic relative order
  (their kind's own internal ordering, e.g. issues by recency, approvals by
  soonest-expiry) so the list does not reshuffle between otherwise-identical
  renders
- **AND** the trailing "N more/older issue groups" rollup row and any
  explicitly-included old-issue rows remain appended after the severity-sorted
  set, since they summarize/de-prioritize rather than represent a current
  signal

#### Scenario: A tripped QA circuit breaker surfaces as an attention row

- **WHEN** `GET /api/qa/summary`'s `circuit_breaker.tripped` is `true`
- **THEN** the attention list renders a critical-severity row naming the
  circuit breaker as tripped and the `consecutive_failures` count, linking to
  `/qa`
- **AND** this row takes precedence over a same-summary recent patrol-error
  row (a tripped breaker means the QA staffer has stopped dispatching
  entirely, a more severe state than one failed patrol run)

#### Scenario: A recent QA patrol error surfaces as an attention row

- **WHEN** `GET /api/qa/summary` returns a `last_patrol` whose `status` is
  `error` and whose `started_at` is in the closed interval `[now - 24 hours,
  now]`, and its circuit breaker is not tripped
- **THEN** the attention list renders a high-severity "QA patrol failed" row
  linking to `/qa`
- **AND** a null `error_detail` still renders the failure row with generic
  failure context
- **AND** any non-`error` status, including one with non-null `error_detail`,
  does not render a patrol-failure row

#### Scenario: Active QA investigations surface as attention

- **WHEN** no QA breaker or recent patrol error has higher precedence and
  `GET /api/qa/summary` reports `kpis.active_cases_now` greater than zero
- **THEN** the attention list renders a medium-severity row naming the active QA
  investigation count and linking to `/qa`
- **WHEN** only `stats_24h.dispatched_investigations` or `stats_24h.novel_findings` is
  greater than zero
- **THEN** the list does not render a QA attention row solely for that completed activity

#### Scenario: Notification pressure is time-bounded

- **WHEN** the Overview requests `GET /api/notifications/stats` with a closed
  minute-aligned interval captured once for the render (`until` is the current
  minute boundary and `since = until - 24 hours`), and the bounded response has
  `failed` greater than zero
- **THEN** the list renders a medium-severity notification row naming the failed count in
  the last 24 hours
- **AND** its link preserves the `terminal_failed` status filter and both boundaries of
  that same closed interval, so it resolves the exact terminal-failure set counted by
  `NotificationStats.failed` rather than including attempts later superseded by a retry
- **AND** the Notifications destination renders that same minute-aligned boundary in its
  visible local date-time filter rather than silently applying an undisclosed filter
- **WHEN** the bounded response has `failed = 0` while all-time failures exist
- **THEN** the list renders no normal notification-pressure row

#### Scenario: An unreachable notifications source surfaces as a degraded row

- **WHEN** `GET /api/notifications/stats` returns `source_available: false`
- **THEN** the attention list renders a high-severity, source-error row
  naming the notifications feed as unavailable, instead of silently showing
  no notification-pressure row (the underlying `failed` count is a
  fabricated zero in this case, not a genuine "no failures" result)
- **AND** this row does not also render alongside a normal "N failed
  notifications" row for the same fetch

#### Scenario: An active fleet-halt (monthly spend ceiling) surfaces as a critical row

- **WHEN** the fleet-halt status derived from `GET /api/dispatch/attempts`
  (see dashboard-spend-dashboard spec, Fleet-Halt Visibility) is active — i.e.
  the monthly spend ceiling has denied one or more dispatches this month
- **THEN** the attention list renders a critical-severity row reading "Monthly
  ceiling reached — dispatches denied", naming the denied-today count and the
  since-timestamp, linking to `/spend`
- **AND** this row ranks by the same severity-first ordering as every other
  attention row (critical sorts above high/medium/low)
- **AND** when the fleet-halt data source itself fails to load, the attention
  list renders a high-severity source-error row instead of silently omitting
  the fleet-halt signal (never reads a failed fetch as "the fleet is fine")

#### Scenario: An unreachable butler board source surfaces as a degraded row

- **WHEN** `GET /api/butlers/board` fails to load (`butlersError` is `true`)
- **THEN** the attention list renders a high-severity, source-error row
  naming butler status as unavailable, linking to `/butlers`
- **AND** this holds even when no other attention source has a signal, so
  the list cannot silently render `Nothing waiting.` while the SAME board
  fetch drives the dashboard briefing headline's `"degraded"` state_class
  (`dashboard-briefing` spec's Degraded class scenario)

#### Scenario: Historical issues are summarized

- **WHEN** an unresolved issue's `last_seen_at` is older than 12 hours or is not
  parseable
- **THEN** the row is represented only by older-history detail or an aggregate rollup
- **AND** its age is calculated from `last_seen_at` relative to the owner's configured
  timezone
- **AND** repeated old issues with the same `type` and `description` MAY collapse
  into one summarized row when `occurrences` or `butlers` indicates multiplicity
- **AND** the summary MUST name the affected butlers with human-readable names,
  not raw machine identifiers

#### Scenario: Attention list handles empty, loading, and error states

- **WHEN** issues are loading
- **THEN** the list renders stable loading rows or an equivalent skeleton

- **WHEN** all loaded sources report no current attention rows
- **THEN** the list renders the serif Voice empty state `Nothing waiting.`
- **AND** it does not render an empty table, blank card, or celebratory graphic

- **WHEN** `GET /api/issues` fails
- **THEN** the list renders a local error row for the attention surface
- **AND** the rest of the Overview remains visible



## dashboard-overview — Inline Approve/Deny/Defer on Attention Rows


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. truthful whole-population triage, unavailable sources, action eligibility, caching, period/currency and safe drill-down. Completed goals today is the proposed successor hero, counted only from D11 completion and actual owner-reply delivery. Active goals, due obligations and decisions replace unit/session editorial hierarchy. Unknown/partial/failed/stopped/budget-exhausted receipts remain supporting facts; old runtime facts move to System only where their actual source exists. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Inline Approve/Deny/Defer on Attention Rows

When individual pending approvals are available (not just the aggregate pending count), the `Needs attention` list SHALL render one actionable row per approval — up to a small cap, with any remainder collapsed into a single "N more pending approvals" row linking to `/approvals` — instead of only the aggregate count row. Each actionable row SHALL expose verb-labeled Approve/Deny/Defer buttons that call the same approve/deny/defer endpoints `/approvals` uses, letting the owner decide without leaving the Overview page. A row's own pending state (e.g. "Approving…") SHALL be scoped to that row's button only, never borrowed by a sibling row's in-flight decision.

When the individual pending-approvals detail fetch is unavailable or empty while the aggregate metrics still report a nonzero pending count, the list SHALL fall back to the existing aggregate "N pending approvals" link row rather than silently dropping the signal.

#### Scenario: Actionable rows render inline decision buttons

- **WHEN** one or more individual pending approvals are available
- **THEN** the attention list renders one row per approval (capped, with a "N more" row for the remainder) each showing Approve, Deny, and Defer buttons
- **AND** clicking a button calls the corresponding approve/deny/defer endpoint with that approval's id and optimistically removes the row on success.

#### Scenario: Falls back to the aggregate count when the detail list is unavailable

- **WHEN** the individual pending-approvals fetch errors or returns no rows while the aggregate metrics report `total_pending > 0`
- **THEN** the attention list renders the existing aggregate "N pending approvals" row linking to `/approvals`, with no inline decision buttons.



## dashboard-overview — Operations Index


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. truthful whole-population triage, unavailable sources, action eligibility, caching, period/currency and safe drill-down. Completed goals today is the proposed successor hero, counted only from D11 completion and actual owner-reply delivery. Active goals, due obligations and decisions replace unit/session editorial hierarchy. Unknown/partial/failed/stopped/budget-exhausted receipts remain supporting facts; old runtime facts move to System only where their actual source exists. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Operations Index

The home page SHALL render a right-column `Operations` section summarizing the
active domain butlers. The section is a scan list, not a chart.

#### Scenario: Operations rows join butler and spend summaries

- **WHEN** `GET /api/butlers` returns butler rows
- **THEN** `Operations` renders only rows whose `type` is `"butler"`
- **AND** each row shows the butler identity, session count from `sessions_24h`,
  and today's spend from `GET /api/spend/summary?period=today` `by_butler`
- **AND** missing spend data renders as an explicit zero or unavailable value,
  not by hiding the row

#### Scenario: Operations handles empty, loading, and error states

- **WHEN** butlers are loading
- **THEN** `Operations` renders stable loading rows or an equivalent skeleton

- **WHEN** no domain butlers are active
- **THEN** `Operations` renders `No butlers active.`

- **WHEN** the butlers query fails
- **THEN** `Operations` renders a local error state
- **AND** the rest of the Overview remains visible



## dashboard-overview — Now List


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. truthful whole-population triage, unavailable sources, action eligibility, caching, period/currency and safe drill-down. Completed goals today is the proposed successor hero, counted only from D11 completion and actual owner-reply delivery. Active goals, due obligations and decisions replace unit/session editorial hierarchy. Unknown/partial/failed/stopped/budget-exhausted receipts remain supporting facts; old runtime facts move to System only where their actual source exists. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Now List

The home page SHALL render a right-column `Now` section for immediate operational items.
In the first implementation this section is sourced from existing endpoints and does not
require a new endpoint. `Now` MAY show time-bounded completed activity, but that activity
SHALL NOT be treated as an active `Needs attention` signal or a briefing-classification
input.

The acceptable first-source set is:

- `GET /api/approvals/metrics` for pending approval count;
- `GET /api/qa/summary` for active QA cases and time-bounded patrol/finding/dispatch
  activity;
- `GET /api/qa/investigations` when the row needs active investigation or PR
  detail beyond the summary counts;
- `GET /api/notifications/stats` with the closed 24-hour `since` and `until` boundaries
  for failed notification pressure;
- `GET /api/timeline` for recent activity, or `GET /api/sessions` when the
  implementation only needs recent completed sessions.

#### Scenario: Pending approvals appear in Now

- **WHEN** `GET /api/approvals/metrics` returns `total_pending` greater than zero
- **THEN** `Now` renders one immediate item naming the pending approval count
- **AND** the item is labelled as an approval item

#### Scenario: QA state and activity appear in Now

- **WHEN** `GET /api/qa/summary` reports an active QA case, a recent patrol failure, or
  another current QA alert
- **THEN** `Now` renders an immediate item naming the QA state in human-readable terms
- **AND** if active investigation or PR detail is needed, the page MAY read
  `GET /api/qa/investigations` instead of introducing a new endpoint
- **WHEN** only `stats_24h.dispatched_investigations` or `stats_24h.novel_findings` is
  greater than zero
- **THEN** `Now` MAY render a compact activity item that names the count and its
  `last 24 hours` boundary
- **AND** it SHALL NOT label that completed activity as active follow-up work or failure

#### Scenario: Failed notification pressure appears in Now

- **WHEN** `GET /api/notifications/stats?since=<now-24h>&until=<now>` returns `failed`
  greater than zero
- **THEN** `Now` renders an immediate item naming the failed notification count in the
  last 24 hours
- **AND** the item is labelled as a notification item and links to the same
  `terminal_failed` window predicate

#### Scenario: Recent activity appears in Now

- **WHEN** `GET /api/timeline` returns recent activity, or `GET /api/sessions`
  returns recent completed sessions
- **THEN** `Now` MAY render a compact recent activity item
- **AND** the row links to the appropriate timeline or sessions surface when a
  link is available

#### Scenario: Now handles empty, loading, and error states

- **WHEN** one or more `Now` sources are loading
- **THEN** `Now` renders stable loading rows or an equivalent skeleton

- **WHEN** every loaded `Now` source reports no actionable state
- **THEN** `Now` renders `Nothing scheduled.`

- **WHEN** a `Now` source fails
- **THEN** `Now` renders a local error state for that source
- **AND** the rest of the Overview remains visible



## dashboard-overview — Page Archetype Compliance


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. truthful whole-population triage, unavailable sources, action eligibility, caching, period/currency and safe drill-down. Completed goals today is the proposed successor hero, counted only from D11 completion and actual owner-reply delivery. Active goals, due obligations and decisions replace unit/session editorial hierarchy. Unknown/partial/failed/stopped/budget-exhausted receipts remain supporting facts; old runtime facts move to System only where their actual source exists. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Page Archetype Compliance

The home page SHALL adopt the Editorial archetype and render through the shared `<Page>`
primitive (`components/ui/page.tsx`). Layout, spacing, and conformance rules are owned by
`dashboard-design-language` (Requirement: Page Shell and Layout; Requirement: Page Conformance);
this requirement binds only the Overview's use of that archetype.

#### Scenario: Page renders inside the standard shell

- **WHEN** a user navigates to `/`
- **THEN** the home page SHALL render inside the standard dashboard shell
  (sidebar, header bar, error boundary) as defined by `dashboard-shell`
- **AND** the page content SHALL not reimplement chrome that belongs to the shell

#### Scenario: Page uses the shared Page primitive

- **WHEN** `DashboardPage` renders
- **THEN** it SHALL use `<Page archetype="editorial" title="Overview">` as its
  outermost container
- **AND** the cockpit surfaces SHALL be direct children of `<Page>` rather than an
  extra layout wrapper



## dashboard-overview — Internal maintenance rollup in Dashboard Now


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. truthful whole-population triage, unavailable sources, action eligibility, caching, period/currency and safe drill-down. Completed goals today is the proposed successor hero, counted only from D11 completion and actual owner-reply delivery. Active goals, due obligations and decisions replace unit/session editorial hierarchy. Unknown/partial/failed/stopped/budget-exhausted receipts remain supporting facts; old runtime facts move to System only where their actual source exists. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Internal maintenance rollup in Dashboard Now

Dashboard Now SHALL remain owner-focused by default and SHALL not use
maintenance runs whose `data.success` is exactly `true` as ordinary recent
activity. A `false` value is a failure, while `null`, missing, and nonboolean
values are running or unknown and SHALL remain ordinary activity while the
lens is disabled. It SHALL provide the same accessible, URL-backed Internal
lens as the Timeline. When enabled, Dashboard Now SHALL render compact
per-butler maintenance rollups from the Timeline event machine class and link
them to the Timeline with its Internal lens enabled. Failed maintenance
sessions SHALL remain visible as error activity while the lens is disabled.
Dashboard Now SHALL render a confirmed failure with a textual `failed` marker
and destructive error treatment rather than leaving its status only in
non-rendered row detail. An Internal rollup with failed runs SHALL visibly
include its exact failed-run count.

#### Scenario: Dashboard Now defaults to owner activity

- **WHEN** Dashboard Now receives successful maintenance Timeline events and
  the URL does not include `internal=1`
- **THEN** it does not render those events as ordinary activity rows
- **AND** it continues to render owner activity and error rows

#### Scenario: Dashboard Now keeps running and unknown maintenance visible

- **WHEN** Dashboard Now receives a maintenance Timeline event whose
  `data.success` is `null`, absent, or nonboolean and the URL does not include
  `internal=1`
- **THEN** it renders that event as ordinary recent activity

#### Scenario: Dashboard Now renders failed maintenance as a visible failure

- **WHEN** Dashboard Now receives a maintenance Timeline event whose
  `data.success` is exactly `false` and the URL does not include `internal=1`
- **THEN** it renders the event with a textual `failed` marker and destructive
  error treatment, rather than only an ordinary `activity` marker
- **AND** it continues to treat `null`, missing, and nonboolean success values
  as visible running or unknown activity rather than failures

#### Scenario: Internal Dashboard Now lens groups maintenance by butler

- **WHEN** the operator enables the Internal lens and multiple loaded
  maintenance events belong to the same butler
- **THEN** Dashboard Now renders one maintenance rollup with the exact loaded
  event count for that butler
- **AND** when the rollup contains failed runs, it visibly includes the exact
  failed-run count and failure treatment
- **AND** its link opens the Timeline with `internal=1`



## dashboard-overview — Overview does not derive approval all-clear signals from partial metrics


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. truthful whole-population triage, unavailable sources, action eligibility, caching, period/currency and safe drill-down. Completed goals today is the proposed successor hero, counted only from D11 completion and actual owner-reply delivery. Active goals, due obligations and decisions replace unit/session editorial hierarchy. Unknown/partial/failed/stopped/budget-exhausted receipts remain supporting facts; old runtime facts move to System only where their actual source exists. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Overview does not derive approval all-clear signals from partial metrics

The dashboard overview model SHALL derive aggregate pending-approval attention
and Now signals only from a complete pending-actions metric family. When
`meta.pending_actions_sources_degraded` is non-empty, it SHALL surface a named
approval-source-unavailable signal instead of a zero or empty-derived
all-clear. Independently successful individual approval rows remain usable.

#### Scenario: Partial aggregate with no individual rows

- **WHEN** approval metrics return `total_pending = 0` with non-empty
  `meta.pending_actions_sources_degraded` and no individual pending rows are
  available
- **THEN** the overview does not render a `0 pending approvals` result or
  conclude that nothing needs attention from that aggregate
- **AND** it renders a named unavailable approval source signal.

#### Scenario: Healthy individual rows remain actionable

- **WHEN** the pending-actions metrics aggregate is partial but the individual
  pending-approvals query returns healthy rows
- **THEN** the overview continues to render those individual rows and their
  existing action affordances
- **AND** it keeps the named aggregate-unavailable signal visible.

#### Scenario: A complete zero remains calm

- **WHEN** the approval metrics response has an empty or absent
  `pending_actions_sources_degraded` list and `total_pending = 0`
- **THEN** the overview renders no approval-unavailable signal
- **AND** it preserves its normal calm zero behavior.



## dashboard-overview — Runtime KPI Strip Cells and Doors


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. truthful whole-population triage, unavailable sources, action eligibility, caching, period/currency and safe drill-down. Completed goals today is the proposed successor hero, counted only from D11 completion and actual owner-reply delivery. Active goals, due obligations and decisions replace unit/session editorial hierarchy. Unknown/partial/failed/stopped/budget-exhausted receipts remain supporting facts; old runtime facts move to System only where their actual source exists. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Runtime KPI Strip Cells and Doors

The home page SHALL render a promoted four-cell runtime KPI strip. "Promoted"
means the KPIs are part of the primary information hierarchy; it does not mean
they use heavier card chrome. The strip SHALL remain hairline-divided,
tabular-numeric, and visually calm.

#### Scenario: KPI cells have defined meanings

- **WHEN** the runtime KPI strip renders
- **THEN** it includes exactly these four cells:
  - `Total butlers`: count of `GET /api/butlers` rows where `type` is `"butler"`
  - `Healthy`: count of butler rows whose `status` is `"ok"`, `"online"`, or `"healthy"`
  - `Sessions · 24h`: sum of `sessions_24h` across butler rows
  - `Pending approvals`: `total_pending` from `GET /api/approvals/metrics`
- **AND** every numeric value uses tabular numerals

#### Scenario: KPI strip handles loading and partial failure

- **WHEN** either KPI source is still loading
- **THEN** cells depending on unavailable data render an unavailable/loading
  value without shifting layout

- **WHEN** one KPI source fails
- **THEN** cells backed by the failed source render an unavailable/error value
- **AND** cells backed by the still-available source MAY continue rendering

#### Scenario: KPI cells are doors to supported destinations only

- **WHEN** a KPI cell's backing value is available (including a genuine zero)
- **THEN** the whole cell is a navigable door: `Total butlers` routes to
  `/butlers`; `Healthy` routes to the SAME unfiltered `/butlers` board (no
  `healthy`-only filter exists anywhere in the product) with an accessible
  name that says so explicitly; `Sessions · 24h` routes to
  `/sessions?since=<captured-since>&until=<captured-until>` using one 24-hour
  window captured once per render, not a fresh instant recomputed between
  render and click; `Pending approvals` routes to `/approvals`

#### Scenario: Unavailable KPI cells never carry a door

- **WHEN** a KPI cell renders its unavailable value (`—`, from loading, error,
  or a degraded source)
- **THEN** that cell has no href and is not a link, div[role=link], button, or
  any other interactive control
- **AND** a genuine zero value is unaffected by this rule and keeps its door

#### Scenario: Degraded Sessions aggregate leaves the other KPI doors available

- **WHEN** `GET /api/butlers/board` succeeds but reports
  `aggregates.sessions_source_error = true`
- **THEN** `Sessions · 24h` renders `—` with unavailable semantics and no
  Sessions door, because its aggregate is only a partial sum
- **AND** the available `Total butlers`, `Healthy`, and `Pending approvals`
  values retain their normal values and supported doors



## dashboard-overview — Spend widget for dashboard overview


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. truthful whole-population triage, unavailable sources, action eligibility, caching, period/currency and safe drill-down. Completed goals today is the proposed successor hero, counted only from D11 completion and actual owner-reply delivery. Active goals, due obligations and decisions replace unit/session editorial hierarchy. Unknown/partial/failed/stopped/budget-exhausted receipts remain supporting facts; old runtime facts move to System only where their actual source exists. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Spend widget for dashboard overview

The Overview MUST render a full-width cost band below the editorial grid: a `CostWidget` in a
half-width column followed by the `TopSessionsTable`. The widget MUST display:
- Title "Cost Today" with a "View all" link to `/spend`.
- Total cost for the day formatted as currency when its direct summary query succeeds with priced data.
- Top butler name and cost (e.g., "Top: health ($3.50)") when its direct summary query succeeds with a top butler.
- A sparkline showing the real trailing 7-day daily spend series.

The widget MUST distinguish a direct Overview summary-query failure from a successful
compatibility envelope with `source_error` and from a successful zero-cost summary.

#### Scenario: Widget with no data

- **WHEN** `totalCostUsd` is 0 and `topButler` is null
- **AND** the direct summary query succeeded without `source_error`
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

#### Scenario: Cost band renders below the cockpit

- **WHEN** a user navigates to `/`
- **THEN** the cost band renders below the editorial grid, with `CostWidget` above
  `TopSessionsTable`
- **AND** the widget draws from the direct `useSpendSummary("today")` query and the table from
  the direct `useTopSessions()` query



## dashboard-overview — Top sessions table


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. truthful whole-population triage, unavailable sources, action eligibility, caching, period/currency and safe drill-down. Completed goals today is the proposed successor hero, counted only from D11 completion and actual owner-reply delivery. Active goals, due obligations and decisions replace unit/session editorial hierarchy. Unknown/partial/failed/stopped/budget-exhausted receipts remain supporting facts; old runtime facts move to System only where their actual source exists. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Top sessions table

The Overview cost band MUST provide a `TopSessionsTable` displaying the most expensive LLM sessions. The table MUST display columns: rank number (#), Butler (secondary badge), Model (muted text), Tokens (input/output formatted as abbreviated counts separated by "/"), Cost (right-aligned, bold, tabular-nums), Time (right-aligned, formatted as "MMM d, HH:mm").

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

## Source References

- `about/heart-and-soul/design-language.md` §Editorial archetype: the Overview
  uses the Voice surface, status pill, attention list, KPI strip, and
  right-column index.
- `about/lay-and-land/frontend.md` §Editorial archetype layout: the Overview
  frame is `<Page archetype="editorial">` with left-column narrative and
  right-column scan lists.
- `openspec/specs/dashboard-briefing/spec.md`: the briefing response remains the
  six-field API contract consumed by the Overview page.
- Live code: `frontend/src/pages/DashboardPage.tsx` (page composition),
  `frontend/src/components/costs/CostWidget.tsx` and
  `frontend/src/components/costs/TopSessionsTable.tsx` (cost band),
  `frontend/src/components/ui/page.tsx` (`<Page>` primitive).
- Current endpoint sources: `GET /api/dashboard/briefing`, `GET /api/issues`,
  `GET /api/butlers`, `GET /api/spend/summary?period=today`,
  `GET /api/approvals/metrics`, `GET /api/qa/summary`,
  `GET /api/qa/investigations`, `GET /api/notifications/stats`,
  `GET /api/timeline`, and `GET /api/sessions`.


## dashboard-briefing — LLM Elaboration


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. five independent attention sources, exact state classifier, owner timezone greeting, six-field response, voice lint, generation-fenced five-minute cache and honest fallback. The elaboration producer is an admitted standing-obligation goal, not an owner question or new D18 function. The complete composer/cache/receipt/fallback contract is in dashboard-goal-briefing. Synthetic unit runtime ownership retires only when the briefing owner integrates that goal producer. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: LLM Elaboration

The endpoint SHALL call the local catalog-backed runtime adapter path with a pinned prompt to produce a one-to-three sentence elaboration paragraph. The prompt MUST encode the dashboard voice rules. The runtime call MUST use the synthetic butler identity `__dashboard_briefing__`, resolve runtime/model/args/timeout from `public.model_catalog` at the `trivial` complexity tier, and run without MCP tools.

#### Scenario: Prompt receives bounded dashboard context

- **WHEN** the endpoint composes an LLM elaboration
- **THEN** the user prompt includes a bounded internal state snapshot with owner-local time, attention totals, top attention item descriptions, relevant butler names, timestamps, and unhealthy butler summaries
- **AND** the snapshot includes enough source context for the paragraph to name the most important current ecosystem fact
- **AND** the public response still contains exactly the six required Briefing fields, with no additional metadata or context field

#### Scenario: LLM happy path

- **WHEN** the local runtime call returns within its configured timeout
- **AND** the response passes the post-generation voice lint
- **THEN** `elaboration` is set to the LLM response
- **AND** `source` is `"llm"`

#### Scenario: LLM timeout

- **WHEN** the local runtime call exceeds its configured timeout
- **THEN** the endpoint cancels the call
- **AND** `elaboration` is set to the templated fallback for the computed `state_class`
- **AND** `source` is `"fallback"`

#### Scenario: LLM error or empty response

- **WHEN** the LLM call raises an exception or returns an empty body
- **THEN** `elaboration` is set to the templated fallback
- **AND** `source` is `"fallback"`



## dashboard-education-api — Curriculum request submission endpoint


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. validated receipt-before-work submission, one-open partial-index guard, status reads, full lifecycle, notification evidence, paired CHECK constraints and abandonment sweep. The future producer is an education obligation goal, with stable request and actual invocation correlation; it cannot fabricate legacy session IDs. The complete goal receipt contract retains every delivered/no_record/unproven and failure case. Current trigger/session producer remains until the education owner allocates its exact migration. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Curriculum request submission endpoint

The system SHALL expose `POST /api/education/curriculum-requests` accepting a JSON body `{"topic": "<topic>", "goal": "<optional_goal>"}`.

The `topic` field SHALL be required and non-empty (max 200 characters). The `goal` field SHALL be optional (max 500 characters).

Before any detached work begins, the endpoint SHALL insert an immutable receipt row into `education.curriculum_requests` with status `accepted`, the submitted topic and goal, and a generated `id`. That `id` is the request's permanent receipt.

The endpoint SHALL NOT persist a `pending_curriculum_request` KV key. The one-pending-at-a-time guard SHALL be the partial unique index `uq_curriculum_requests_one_open`, which permits at most one receipt in a non-terminal status (`accepted`, `running`). When the insert is refused by that index, the endpoint SHALL return 409 Conflict.

When the receipt store cannot be read or written because the education migration chain is absent, the endpoint SHALL return 503 rather than accepting a request it cannot evidence.

On success, the endpoint SHALL return 202 Accepted with body `{"status": "accepted", "topic": "<topic>", "request_id": "<uuid>"}`. `202` SHALL mean *accepted and recorded* only; it SHALL NOT be treated by any caller as evidence that a curriculum was created or that the owner was contacted.

#### Scenario: Submit a new curriculum request

- **WHEN** a POST request is made to `/api/education/curriculum-requests` with body `{"topic": "Python", "goal": "Learn web development with Flask"}`
- **AND** no non-terminal receipt exists
- **THEN** the response status SHALL be 202
- **AND** the response body SHALL contain `{"status": "accepted", "topic": "Python"}` and a `request_id`
- **AND** a receipt row SHALL exist in `education.curriculum_requests` with status `accepted`, the topic and goal, and no outcome evidence

#### Scenario: Receipt precedes detached work

- **WHEN** a curriculum request is accepted
- **THEN** the receipt row SHALL be persisted before the detached curriculum task is created

#### Scenario: Submit request without goal

- **WHEN** a POST request is made with body `{"topic": "Linear Algebra"}`
- **AND** no non-terminal receipt exists
- **THEN** the response status SHALL be 202
- **AND** the receipt row SHALL have `goal` set to null

#### Scenario: Duplicate request while one is in flight

- **WHEN** a POST request is made to `/api/education/curriculum-requests`
- **AND** a receipt already exists in status `accepted` or `running`
- **THEN** the response status SHALL be 409
- **AND** the response body SHALL indicate a curriculum request is already pending

#### Scenario: Receipt store unavailable

- **WHEN** a POST request is made and `education.curriculum_requests` does not exist
- **THEN** the response status SHALL be 503

#### Scenario: Empty topic

- **WHEN** a POST request is made with body `{"topic": ""}`
- **THEN** the response status SHALL be 422

#### Scenario: Topic exceeds length limit

- **WHEN** a POST request is made with a `topic` longer than 200 characters
- **THEN** the response status SHALL be 422

---



## dashboard-education-api — Curriculum request receipt lifecycle


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. validated receipt-before-work submission, one-open partial-index guard, status reads, full lifecycle, notification evidence, paired CHECK constraints and abandonment sweep. The future producer is an education obligation goal, with stable request and actual invocation correlation; it cannot fabricate legacy session IDs. The complete goal receipt contract retains every delivered/no_record/unproven and failure case. Current trigger/session producer remains until the education owner allocates its exact migration. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Curriculum request receipt lifecycle

Each accepted curriculum request SHALL settle to a terminal outcome on its receipt row, so that a failure of the detached work is visible to the owner rather than only to the log.

The receipt SHALL carry, in addition to `id`, `topic` and `goal`: `status` (one of `accepted`, `running`, `completed`, `failed`), `session_id`, `mind_map_id`, `calibration_ready_at`, `calibration_notice_outcome`, `calibration_notice_accepted_at`, `failure_reason`, `requested_at`, `triggered_at`, `settled_at`, and `updated_at`.

The detached task SHALL stamp `status = running` and `triggered_at` before handing the request to the butler's `trigger` MCP tool, and SHALL await that tool to completion.

The task SHALL settle `status = completed` only when the triggered session reported success **and** a mind map created at or after `triggered_at` is found. `session_id` SHALL be recorded from the trigger result, `mind_map_id` from that correlation, and `calibration_ready_at` SHALL be set only when the correlated mind map's teaching flow has reached `diagnosing` or a later state.

`calibration_ready_at` attests that calibration **began**. It SHALL NOT be read, rendered, or documented as evidence that the owner was contacted.

Whether the owner was contacted SHALL be recorded separately, from the notification path and never from teaching-flow state. On the completed path the task SHALL consult `public.attention_ledger` for the `source="notify"` dispatch recorded against the session it triggered, and SHALL settle `calibration_notice_outcome` to one of:

- the ledger's own word for the dispatch (`delivered`, `coalesced`, `deferred`, `suppressed`, `failed`);
- `no_record` when the ledger was read and held no notify row for that session;
- `unproven` when the ledger could not be read, or there was no session id to read it with.

`calibration_notice_accepted_at` SHALL be set from the ledger row's own `occurred_at`, and SHALL be set for `outcome = delivered` alone. It means a delivery channel accepted the message; it does not mean the owner read it, and no surface SHALL describe it as though it did.

An absent ledger row SHALL NOT be settled as a failed notice. Ledger recording is best-effort and never raises, so `no_record` records that the evidence is missing, which is a weaker claim than the notice having failed and SHALL stay distinguishable from it.

A settle that has no notice evidence to record SHALL leave both columns as it found them, so a later or duplicate settle cannot blank evidence an earlier one recorded. The two columns SHALL be written as a single unit, because they are decided from one piece of evidence and the database binds them.

The database SHALL enforce that `calibration_notice_outcome` is one of the values above, and that `calibration_notice_accepted_at` is present exactly when the outcome is `delivered` — absent for every other outcome, and absent when the outcome is null.

The task SHALL settle `status = failed` with a stable `failure_reason` on every other exit path: `trigger_unreachable` when the butler could not be reached, `session_error` when the session reported its own failure, and `no_curriculum_created` when a session exited cleanly without producing a curriculum. A clean session exit SHALL NOT by itself settle `completed`.

Settlement SHALL be idempotent: the first terminal write SHALL win, and a later or duplicate settle SHALL be a no-op rather than a contradicting outcome.

A receipt that remains non-terminal for longer than the abandonment timeout SHALL be settled to `failed` with `failure_reason = "timed_out"`, releasing the pending guard. This sweep SHALL run on every submit and every status read, so that an API restart that kills an in-flight task cannot strand the guard.

The database SHALL enforce that a terminal status carries `settled_at`, that a non-terminal status does not, and that `failed` carries a `failure_reason`.

#### Scenario: Successful curriculum settles with evidence

- **WHEN** the triggered session reports success and a mind map was created after `triggered_at`
- **THEN** the receipt SHALL settle `status = completed` with `session_id`, `mind_map_id`, and `settled_at` recorded

#### Scenario: Trigger cannot reach the butler

- **WHEN** the MCP client or `trigger` call raises
- **THEN** the receipt SHALL settle `status = failed` with `failure_reason = "trigger_unreachable"`

#### Scenario: Session reports its own failure

- **WHEN** the `trigger` result reports `success: false`
- **THEN** the receipt SHALL settle `status = failed` with `failure_reason = "session_error"`
- **AND** the receipt SHALL retain the `session_id` of the failed session

#### Scenario: Session exits without creating a curriculum

- **WHEN** the triggered session reports success but no mind map was created at or after `triggered_at`
- **THEN** the receipt SHALL settle `status = failed` with `failure_reason = "no_curriculum_created"`

#### Scenario: Settlement is idempotent

- **WHEN** a receipt already holds a terminal status
- **AND** a second settle is attempted with a different status
- **THEN** the receipt SHALL retain its original terminal status and evidence

#### Scenario: A failed notice never becomes a delivery claim

- **WHEN** the teaching flow has reached `diagnosing`, so `calibration_ready_at` is set
- **AND** the ledger recorded `outcome = "failed"` for the triggered session's notify
- **THEN** the receipt SHALL settle `calibration_notice_outcome = "failed"` with `calibration_notice_accepted_at` unset
- **AND** `calibration_ready_at` SHALL remain set, because both statements are true and neither implies the other

#### Scenario: A missing ledger row is absence of evidence, not evidence of failure

- **WHEN** the ledger holds no notify row for the triggered session
- **THEN** the receipt SHALL settle `calibration_notice_outcome = "no_record"` with `calibration_notice_accepted_at` unset

#### Scenario: An unreadable ledger is reported as unproven

- **WHEN** the ledger read raises, or the trigger result carried no session id to read it with
- **THEN** the receipt SHALL settle `calibration_notice_outcome = "unproven"` with `calibration_notice_accepted_at` unset
- **AND** the request SHALL still settle its own status normally

#### Scenario: Channel acceptance is recorded with the ledger's own moment

- **WHEN** the ledger recorded `outcome = "delivered"` for the triggered session's notify
- **THEN** the receipt SHALL settle `calibration_notice_outcome = "delivered"` and `calibration_notice_accepted_at` to that ledger row's `occurred_at`

#### Scenario: The database refuses an unbacked acceptance moment

- **WHEN** a receipt row is written with `calibration_notice_accepted_at` set and `calibration_notice_outcome` anything other than `delivered`, null included
- **THEN** the write SHALL be rejected by a CHECK constraint

#### Scenario: Abandoned receipt is swept

- **WHEN** a receipt has been non-terminal for longer than the abandonment timeout
- **AND** a curriculum request is submitted or a receipt status is read
- **THEN** the abandoned receipt SHALL settle `status = failed` with `failure_reason = "timed_out"`
- **AND** a new curriculum request SHALL be accepted



## dashboard-education-api — Curriculum request status read


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. validated receipt-before-work submission, one-open partial-index guard, status reads, full lifecycle, notification evidence, paired CHECK constraints and abandonment sweep. The future producer is an education obligation goal, with stable request and actual invocation correlation; it cannot fabricate legacy session IDs. The complete goal receipt contract retains every delivered/no_record/unproven and failure case. Current trigger/session producer remains until the education owner allocates its exact migration. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Curriculum request status read

The system SHALL expose `GET /api/education/curriculum-requests/{request_id}` and `GET /api/education/curriculum-requests/latest`, both returning `{"receipts_available": <bool>, "receipt": <receipt|null>}`.

`receipts_available: false` SHALL mean the receipt store could not be read (for example, the education migration chain is absent). Callers SHALL render that as status unavailable, never as "no request in flight".

`receipts_available: true` with `receipt: null` SHALL mean the store is readable and no matching request exists.

`GET /curriculum-requests/{request_id}` SHALL return 404 for an unknown request ID and 422 for a malformed one. Both reads SHALL be read-only with respect to request outcomes; the only write they perform is the abandonment sweep.

#### Scenario: Read a terminal receipt

- **WHEN** a GET request is made to `/api/education/curriculum-requests/{request_id}` for a completed request
- **THEN** the response status SHALL be 200
- **AND** the body SHALL contain `receipts_available: true` and a receipt with `status: "completed"`, `session_id`, `mind_map_id`, and `settled_at`

#### Scenario: Read a failed receipt

- **WHEN** a GET request is made for a failed request
- **THEN** the receipt SHALL carry the terminal `failure_reason`

#### Scenario: Unknown request ID

- **WHEN** a GET request is made for a request ID that does not exist
- **THEN** the response status SHALL be 404

#### Scenario: Malformed request ID

- **WHEN** a GET request is made with a path segment that is not a UUID
- **THEN** the response status SHALL be 422

#### Scenario: Receipt store unavailable

- **WHEN** `education.curriculum_requests` does not exist
- **THEN** the response status SHALL be 200
- **AND** the body SHALL contain `receipts_available: false` and `receipt: null`

#### Scenario: No request has ever been made

- **WHEN** a GET request is made to `/api/education/curriculum-requests/latest`
- **AND** the store is readable and empty
- **THEN** the body SHALL contain `receipts_available: true` and `receipt: null`



## dashboard-education-ui — Curriculum request outcome receipt


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. separate curriculum existence, calibration beginning and channel acceptance, full request panel states, retry, polling, recency and review data. These panels live in the education Region. Actual map/request doors and historical session evidence remain. New invocation/goal doors require stored linkage. No unit Trigger control remains in the future UI, and no completed or notified claim comes from an accepted request. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Curriculum request outcome receipt

After a curriculum request is accepted, the Education page SHALL render a receipt panel for the tracked request, fed by `GET /curriculum-requests/{request_id}` (falling back to `GET /curriculum-requests/latest` when no request has been submitted in this session).

The panel SHALL render four distinct states and SHALL NOT collapse any of them into another:
- **accepted / running** — the request was accepted and work is in flight. The panel SHALL say so and SHALL NOT claim the curriculum exists or that the owner has been contacted.
- **completed** — the receipt carries terminal evidence. The panel SHALL name the curriculum topic and SHALL distinguish `calibration_ready_at` being set (the teaching flow has started calibrating) from it being unset, rather than implying calibration in both cases. The panel SHALL state what became of the calibration notice as a line separate from calibration, so that a reader cannot take calibration having started as evidence of having been messaged.
- **failed** — the panel SHALL render the terminal `failure_reason` in owner-readable language and SHALL offer a retry.
- **unavailable** — when `receipts_available` is `false`, the panel SHALL say the status could not be read, and SHALL NOT render an all-clear or an empty "no request" state.

The panel SHALL claim that a message went out only from `calibration_notice_outcome`, and only for the value `delivered`. That claim SHALL be worded as the delivery channel having accepted the message, because that is the strongest thing the notification path attests; the panel SHALL NOT state or imply that the owner received or read anything.

For every other outcome — a recorded `failed`, `suppressed`, `deferred` or `coalesced` dispatch, a `no_record` absence, an `unproven` read, a null outcome, or an outcome the frontend does not recognise — the panel SHALL say that contact is not confirmed, and SHALL direct the owner to this panel rather than to their messages. An unrecognised outcome SHALL degrade to "not confirmed" and never to an implied yes, so a new backend outcome cannot silently become a delivery claim in the UI.

The panel SHALL provide doors to the evidence it names: a link to the session (`/sessions/{session_id}`) whenever `session_id` is present, including on the failure path, and a control that opens the correlated curriculum whenever `mind_map_id` is present.

While the receipt is non-terminal the query SHALL poll; once terminal it SHALL stop polling.

In fallback mode (no request submitted in this session, so the panel reads the latest request), a receipt that settled longer than the recency window ago SHALL NOT be rendered — otherwise a curriculum created weeks ago would keep a permanent card on the page. A request tracked by `request_id` from this session's 202 SHALL always be rendered, however long ago it settled.

The panel SHALL be announced to assistive technology as a live status region, and every door SHALL be a keyboard-reachable control with an accessible name.

#### Scenario: Accepted request does not claim completion

- **WHEN** the tracked receipt has status `accepted`
- **THEN** the panel SHALL describe the request as accepted and in progress
- **AND** SHALL NOT state that a curriculum was created or that the butler has messaged the owner

#### Scenario: Completed request opens its doors

- **WHEN** the tracked receipt has status `completed` with a `mind_map_id` and a `session_id`
- **THEN** the panel SHALL offer a control that selects that curriculum
- **AND** SHALL offer a link to `/sessions/{session_id}`

#### Scenario: Calibration started does not become "the butler messaged you"

- **WHEN** the tracked receipt has status `completed` with `calibration_ready_at` set
- **AND** `calibration_notice_outcome` is `failed`, `suppressed`, `no_record`, `unproven`, null, or a value the frontend does not recognise
- **THEN** the panel SHALL state that contact is not confirmed and point the owner at this panel
- **AND** SHALL NOT state that a message reached the owner
- **AND** SHALL still state that calibration has started

#### Scenario: Channel acceptance is stated as channel acceptance

- **WHEN** the tracked receipt carries `calibration_notice_outcome: "delivered"`
- **THEN** the panel SHALL say the owner's messaging channel accepted the butler's starting message
- **AND** SHALL NOT describe it as read, seen, or received by the owner

#### Scenario: Failed request is legible and retryable

- **WHEN** the tracked receipt has status `failed` with `failure_reason: "trigger_unreachable"`
- **THEN** the panel SHALL explain that the butler could not be reached
- **AND** SHALL offer a retry control
- **AND** SHALL still link the session when `session_id` is present

#### Scenario: Unavailable status is not an all-clear

- **WHEN** the status read returns `receipts_available: false`
- **THEN** the panel SHALL state that the request status could not be read
- **AND** SHALL NOT render a success, a failure, or an empty state

#### Scenario: Polling stops at a terminal state

- **WHEN** the tracked receipt reaches `completed` or `failed`
- **THEN** the receipt query SHALL stop refetching

#### Scenario: A long-settled fallback receipt is not parked on the page

- **WHEN** no request has been submitted in this session
- **AND** the latest receipt settled longer than the recency window ago
- **THEN** the panel SHALL render nothing

#### Scenario: A request tracked in this session stays visible

- **WHEN** a request submitted in this session settled longer than the recency window ago
- **THEN** the panel SHALL still render its terminal outcome



## dashboard-education-ui — Education butler-detail Reviews tab


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. separate curriculum existence, calibration beginning and channel acceptance, full request panel states, retry, polling, recency and review data. These panels live in the education Region. Actual map/request doors and historical session evidence remain. New invocation/goal doors require stored linkage. No unit Trigger control remains in the future UI, and no completed or notified claim comes from an accepted request. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Education butler-detail Reviews tab

In addition to the standalone `/education` page, the butler detail page for the education butler SHALL provide a "Reviews" tab (`ButlerEducationReviewsTab`) that aggregates learning state across all active mind maps into a single multi-panel dashboard.

The tab SHALL display:
1. A KPI summary row: total review cards, mastered count, overdue count, and average mastery score.
2. A mind maps progress panel: per-map mastery percentage with a progress bar and mastered/total node count.
3. A pending reviews timeline grouped by Overdue / Today / This Week / Later, with colored left borders per group.
4. A frontier nodes panel: the next concepts to learn, each with a mastery badge.
5. A 7-day retention trend chart with the latest mastery percentage as a headline value and a hover tooltip.

Polling intervals SHALL scale with the number of active mind maps so that total request volume stays bounded as the map count grows (base interval 15 seconds for reviews, 30 seconds for mastery and frontier data).

#### Scenario: Reviews tab aggregates across maps

- **WHEN** the education butler detail page Reviews tab loads with multiple active mind maps
- **THEN** the KPI row, mind maps progress panel, pending reviews timeline, frontier panel, and retention trend chart SHALL each render with data aggregated across those maps
- **AND** the pending reviews SHALL be grouped into Overdue, Today, This Week, and Later sections


## dashboard-ingestion-dispatch-console — Timeline Ledger


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. timeline status taxonomy, exact request identity, channel/range/search/filter/cursor and unavailable evidence. Region and goal trace doors are additive only for recorded relationships. Historical unit targets remain evidence; routing log console controls retire without inventing safe replay or a new receiver claim. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Timeline Ledger

The `/ingestion` Timeline SHALL render external events as a ledger stream.

It SHALL include:

- header band with eyebrow, live freshness/status pill, range-aware headline,
  one-sentence serif summary, and event/session/cost KPIs;
- sticky toolbar with range picker, search, saved views (with a
  filters-diverged indicator and a re-apply/update path), status filter chips
  (the badge vocabulary exactly), and an "add channel" control alongside any
  active channel chips;
- bulk-action bar when rows are selected, including a select-all-visible
  action (capped at the bulk replay batch limit) and, on a replay-unsafe
  (409) rejection, a one-click action to deselect exactly the ineligible
  events;
- hour-group headers with an honest event/error/replay count sourced from the
  histogram endpoint (correct even when only some pages of that hour have
  loaded) and a status-stacked, clickable, keyboard-operable per-minute
  activity strip;
- ledger rows with time (leftmost column, mono `HH:mm:ss` via the shared Time
  primitive), a click-to-filter channel glyph, sender summary with an inline
  filter/error reason, quiet dot-and-word status, a per-butler dispatch-ticks
  cell, cost, and an expand control; a demoted selection checkbox (hidden by
  default, revealed on hover/focus or once selection mode is active); token
  totals live in the expanded drawer, not the row;
- in-place expanded drawer with step ledger, raw payload, replay history,
  request metadata, session index, and copy/open actions;
- footer rollup band for the active filter window.

#### Scenario: Every ledger row can expand into full request detail

- **WHEN** the owner clicks or keyboard-activates (focus + Enter) any event
  row, regardless of its status — including `filtered` and `error` rows
- **THEN** an in-place drawer opens below that row
- **AND** the drawer includes a step-ledger tab for every session associated
  with the event
- **AND** each session block exposes status, session id, model, duration, cost,
  token totals, and step rows
- **AND** the drawer includes raw-payload and replay-history tabs
- **AND** the right rail exposes request metadata and a session index
- **AND** for `filtered` or `error` rows with no sessions, the drawer states
  the honest reason (skip-triage rule, filter reason, or dispatch failure)
  instead of a bare "no sessions" message

#### Scenario: Row status never renders as a filled pill

- **WHEN** the owner views the ledger
- **THEN** each row's status renders as a small dot plus a mono status word
  (state color as foreground/border only)
- **AND** no row renders a background-filled status badge
- **AND** the status word matches the badge vocabulary exactly: `ingested`,
  `skipped`, `filtered`, `error`, `failed`, `replay pending`,
  `replay complete`, `replay failed`
- **AND** `filtered` rows are visually de-emphasized (reduced opacity) rather
  than distinguished by a gray pill
- **AND** `filtered`/`error`/`failed` rows show their
  `filter_reason`/`error_detail` inline next to the sender, truncated with a
  title tooltip, instead of only on hover of the status control
- **AND** `failed` (a routing failure recorded after the event was already
  ingested — see `ingestion_event_mark_failed`) renders with the same
  destructive-red treatment as `error` and is replayable straight back to
  `ingested`, alongside `filtered`/`error`/`replay_failed`

#### Scenario: Ledger row shows a dispatch-ticks summary without opening the drawer

- **WHEN** the owner views a ledger row for an event with one or more butler
  sessions
- **THEN** the row's dispatch column renders one tick per session (from the
  list-provided, API-capped session summary), each tick's width proportional
  to that session's duration, with a minimum width and a total width bounded
  to the column
- **AND** a failed session's tick renders in the destructive color; other
  ticks render as a neutral foreground color (butler hue is not used on the
  tick fill, consistent with "butler hues only on letter marks"; the butler
  name appears in the tick's hover/focus tooltip instead)
- **AND** a trailing mono session count appears once more than one session
  fired
- **AND** the dispatch column as a whole is keyboard-focusable and activating
  it (click or Enter) opens the row's drawer at the sessions tab, without
  toggling any other row control
- **AND** an event with no sessions renders a muted em-dash instead of an
  interactive cell
- **AND** rendering the cell issues no additional network request beyond the
  events list response

#### Scenario: Raw payload access is audited

- **WHEN** the owner opens or downloads an event raw payload
- **THEN** the backend records an audit entry for that payload access
- **AND** the UI shows loading, unavailable, and permission/error states
  without exposing stale or partial PII as successful content

#### Scenario: Hour strip renders status-stacked activity and reads honestly

- **WHEN** the owner views an hour-group header
- **THEN** the header's event/error/replay counts come from
  `GET /api/ingestion/events/histogram` for that hour and its active
  filters, and are correct even when only some pages of that hour have
  loaded into the ledger
- **AND** the event and error counts include `failed` and `replay_failed`
  events (terminal failures recorded after ingestion or replay) — each counts
  as both an event and an error, the same as `error`, so it never silently
  vanishes from the honest hourly total
- **AND** the per-minute strip renders each minute as a status-stacked bar:
  ingested at a low foreground alpha, filtered/skipped at a lower foreground
  alpha, error/failed/replay failed together in the destructive color, replay
  pending in neutral, and replay complete in green
- **AND** a minute where every event errored, failed, or replay-failed renders
  as solid destructive color
- **AND** the strip exposes an `aria-label` summarizing the hour's activity
  instead of being hidden from assistive technology

#### Scenario: Hour strip minutes are keyboard-operable and route to the ledger or a scoped view

- **WHEN** the owner activates a minute in the strip (click or keyboard)
- **THEN** if a loaded ledger row falls within that minute, the ledger
  scrolls that row into view
- **AND** otherwise the ledger's window narrows to that exact minute,
  reflected in the URL like every other filter
- **AND** every minute is reachable via keyboard focus with a visible focus
  state
- **AND** hovering or focusing a minute shows its time and per-status counts

#### Scenario: Timeline URL opens an event drawer

- **WHEN** the owner loads `/ingestion?event=<event-id>`
- **THEN** the matching ledger row scrolls into view when present
- **AND** that row opens its drawer
- **AND** closing the drawer removes the `event` query parameter



## qa-dashboard — QA Overview Page


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. case diagnosis, watch/patrol evidence, breaker tri-state, source failures, safe case/PR doors and authentic control receipts. QA case/dossier remains a System/domain workspace. Session doors become stored invocation/goal doors when the QA executor actually supplies them. D19 grants no QA executor category, retry, circuit-breaker reset or provider route. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: QA Overview Page
The dashboard SHALL have a top-level QA page at route `/qa` that presents the QA staffer's work as a per-case dossier in the Dispatch design language. The page narrates what the staff caught and what it fixed; it is not a pipeline dashboard.

#### Scenario: Overview page layout
- **WHEN** a user navigates to `/qa`
- **THEN** the page renders, in vertical order:
  - **Sticky top bar**: severity filter (all / high / medium / low) and theme toggle, shared with `/overview` and `/butlers`
  - **Page header**: title "What the staff caught and fixed", clock (mono, `HH:MM` 24h, tabular nums), and a mono eyebrow "QA Staffer · dossier" plus "port :<N> · model <M> · patrol every <P>m" caption, where `P` is `[modules.qa].patrol_interval_minutes`
  - **KPI strip**: five hairline-divided cells in this exact order:
    - `prs landed · 24h`
    - `mttr · 24h`
    - `self-resolved · 7d`
    - `active cases · now`
    - `failed · 24h`
  - **Two-pane body** (320 px case-list rail on the left + dossier body on the right):
    - **Case list rail**: rule-separated rows; each row shows severity glyph (square, high/medium/low), short case id (e.g. `#218`), butler name, headline (one line), `detected` + `age` mono sub-line, a PR-state dot on the right, and a compact session-trace door when linked session evidence exists
    - **Dossier body** for the selected case (see Case Dossier Layout requirement)
- **AND** the page uses Inter Tight (sans), JetBrains Mono (mono), Source Serif 4 (serif), and the OKLCH palette tokens already shipped in `frontend/src/index.css` — no new tokens are introduced
- **AND** the page contains no card chrome, no drop shadows, no gradients, and no recharts components
- **AND** when no case is selected via `?case=`, the dossier body defaults to the most recent case in the rail (the rail's first row); when the rail is empty, the dossier body is hidden per the Empty case list scenario

#### Scenario: Empty case list
- **WHEN** the QA staffer has not produced any cases in the last 7 days
- **THEN** the case list rail renders a single serif-italic line "Nothing in the dossier." and the dossier body is hidden
- **AND** the KPI strip continues to render with zero values

#### Scenario: Case-list session trace door
- **WHEN** a case-list row has a non-null `healing_session_id` and/or a non-empty `session_ids[]`
- **THEN** it renders a compact, labeled navigation door to `/sessions/:id`, targeting the investigation session when present and otherwise the first failing session
- **AND** the door visibly states the number of distinct linked session traces and remains a sibling control to the row-selection button, so both controls have independent keyboard and screen-reader semantics
- **AND** when `healing_session_id` is null and `session_ids` is empty, no session-trace door is rendered

#### Scenario: Patrol pulse strip degraded source
- **WHEN** the overview page's recent-patrols pulse strip queries `GET /api/qa/patrols` and that query errors (network failure or non-2xx)
- **THEN** the strip renders a single-line `role="alert"` degraded note naming the patrols source inline with an em-dash qualifier (e.g. "Recent patrols: patrol source unreachable — recent patrols unavailable"), never vanishing into the same nothing as a genuinely clear stream
- **AND** a reachable-but-empty patrols source (no error, zero patrols) legitimately hides the strip, as does the still-loading state (no fabricated strip before the first response)

#### Scenario: KPI strip values
- **WHEN** the KPI strip renders
- **THEN** each cell shows:
  - A mono uppercase eyebrow label (`prs landed · 24h`, `mttr · 24h`, `self-resolved · 7d`, `active cases · now`, `failed · 24h`)
  - A large sans-500 tabular-nums numeric value
  - A mono sub-label describing context (`+2 vs prior 24h`, `−12m vs 7d`, `+4pp vs prior week`, `N awaiting CI · M escalated`, where N = `active_breakdown.awaiting_ci` and M = `active_breakdown.escalated_open_cases`)
- **AND** when MTTR is computed over an empty sample (no `pr_merged` closures in the window), the cell shows `—` and the sub-label reads `no repairs merged in 24h`
- **AND** the `failed · 24h` cell renders `kpis.failed_24h` with a destructive (red) tint on the numeric value only when the count is greater than zero — a zero-crash 24h stays neutral, never manufacturing alarm

#### Scenario: Force-patrol control surfaces the honest outcome
- **WHEN** a user activates the "Force patrol" control in the sticky top bar and confirms the prompt
- **THEN** the dashboard calls `POST /api/qa/force-patrol` and branches the toast on the response's `triggered` flag, not on the HTTP 202 accept alone (the endpoint accepts the request even when no patrol runs):
  - `triggered: true` → a success toast carrying the response `message`
  - `triggered: false` or absent → a warning toast carrying the response `message`, which names why no patrol ran (e.g. the QA daemon is unreachable, or a cycle is already in progress) — never a success toast
- **AND** a transport error (non-2xx / network failure) renders an error toast naming the failure

#### Scenario: Circuit-breaker control renders an honest tri-state
- **WHEN** the sticky top bar renders the QA circuit-breaker control, deriving its state from the `/api/qa/summary` query that feeds it
- **THEN** the control renders exactly one of three states, never defaulting a dead or in-flight feed to the calm "closed":
  - **closed** — the summary loaded and `circuit_breaker.tripped` is `false`: a muted, disabled "Circuit breaker closed" control (aria-label `QA circuit breaker closed`)
  - **tripped** — the summary loaded and `circuit_breaker.tripped` is `true`: an enabled destructive "Reset breaker" control (aria-label `Reset QA circuit breaker`)
  - **unknown** — the summary query is loading or errored: an amber, disabled "Circuit breaker unknown" control (aria-label `QA circuit breaker state unknown`); reset is withheld because trippedness is not proven
- **AND** the same tri-state (closed / tripped / **unknown** on `isError`) governs the butler-detail QA tab's `CircuitBreakerChip`, which derives from `GET /api/qa/circuit-breaker`

#### Scenario: Reset confirm shows the failing attempts as evidence
- **WHEN** a user activates the "Reset breaker" control while the breaker is tripped
- **THEN** the dashboard opens a confirm dialog (not a bare browser prompt) that lists the failing attempts from `GET /api/qa/circuit-breaker` (`recent_attempts` — id, status, relative close time), the evidence the breaker tripped on, before any reset is issued
- **AND** confirming issues `POST /api/qa/circuit-breaker/reset` and reports the outcome via toast; cancelling (or dismissing) issues no request
- **AND** when the circuit-breaker source is unreachable, the dialog names the missing evidence inline rather than presenting an empty confirm as calm

#### Scenario: Verdict opener names overdue patrols, pre-trip failure streaks, and watcher-death
- **WHEN** the page opener (`QaVerdictOpener`, composed from `GET /api/qa/summary` via the shared `DispatchVerdict` primitive) renders
- **THEN**, in addition to the existing breaker-tripped / last-patrol-failed / no-patrol-history / credential-presence clauses, it evaluates three more, all independent of each other and of the breaker-tripped state:
  - **Pre-trip failure streak** — when `circuit_breaker.consecutive_failures > 0` AND the breaker is not yet tripped, a clause names the streak and the trip threshold (`"N consecutive failures — breaker opens at <threshold>"`), naming the problem before it becomes a full trip rather than only after
  - **Overdue patrol** — when `last_patrol_at` is older than `2 × patrol_interval_minutes` (both already present on the summary response), a clause reads `"patrol overdue — last patrol <relative time>"`; entirely client-computable, no backend change
  - **Runtime-credential alert** — when `runtime_credential_alert` is non-null, a clause reads `"runtime CLI credential may be unhealthy — <alert text>"` — the watcher-death signal: `staffer_status` can read `healthy` while the QA staffer's own model dispatch is dying on a revoked/expired credential, since `staffer_status` only ever inspects patrol-run status and the healing circuit breaker

#### Scenario: Reset is a command-palette verb while tripped
- **WHEN** the QA overview page is mounted and the breaker is tripped
- **THEN** a "Reset circuit breaker" command is registered in the command palette's Actions group (alongside the always-present "Force patrol" verb), opening the same evidence-bearing confirm dialog
- **AND** the command is NOT registered while the breaker is closed or unknown — trippedness must be proven before the reset verb is offered



## qa-dashboard — Case Dossier Layout


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. case diagnosis, watch/patrol evidence, breaker tri-state, source failures, safe case/PR doors and authentic control receipts. QA case/dossier remains a System/domain workspace. Session doors become stored invocation/goal doors when the QA executor actually supplies them. D19 grants no QA executor category, retry, circuit-breaker reset or provider route. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Case Dossier Layout
The QA dashboard SHALL render any single case (either as the right-pane on `/qa?case=` or as the full body of `/qa/investigations/:attemptId`) using a shared Case Dossier component composed of a header, a two-column diagnosis/PR body, and a full-width patrol journal.

#### Scenario: Dossier header
- **WHEN** the Case Dossier renders
- **THEN** the header row shows: severity glyph, mono `#<short_id>`, mono `· <butler>`, mono `· detected <HH:MM>`, and a right-aligned state track ("detect — diagnose — pr — landed" with an `escalated` variant and a `failed` variant)
- **AND** the `escalated` variant renders an amber `· escalated` suffix with `pr`/`landed` shown in amber (handed to the user, no fix); the `failed` variant renders a destructive (red) `· failed` suffix with `pr`/`landed` shown in the destructive color (the investigation crashed before ever reaching a fix) — the two terminal stops are visually distinct so a dead investigation is never mistaken for a deliberate escalation
- **AND** below the row, an H2 sans-500 22 px headline renders the case's `headline` (or `event_summary` as a fallback when `investigation_notes.headline` is null)
- **AND** the case-list rail marks a `failed`-state row with a destructive `· failed` suffix on its detected/age sub-line, alongside the existing severity glyph and PR-state dot

#### Scenario: Active dismissal display
- **WHEN** the Case Dossier renders a case whose fingerprint has an active dismissal record (`qa_dismissals` row with `dismissed_until > now()`)
- **THEN** the header renders a mono caption "dismissed until <dismissed_until>" beneath the sev/id/butler row
- **AND** a "remove dismissal" pill action is rendered alongside the existing `Retry` / `Dismiss` pills
- **AND** clicking "remove dismissal" calls `DELETE /api/qa/dismissals/{fingerprint}` and triggers a re-fetch of the case so the caption and pill disappear

#### Scenario: Terminal states offer Retry, never Dismiss
- **WHEN** the Case Dossier renders a case whose `state_track_stage` is `landed`, `escalated`, or `failed`
- **THEN** a "Retry" pill is rendered (calling `POST /api/healing/attempts/{id}/retry`, which the backend already accepts for any terminal `healing_attempts.status` including `failed`/`timeout`/`anonymization_failed`) and the "Dismiss" pill is withheld — a terminal case, whether landed, escalated, or crashed, is not a candidate for false-positive dismissal
- **AND** every other stage (`detect`, `diagnose`, `pr`) offers Dismiss (when a fingerprint is linked and no dismissal is active) and withholds Retry, since the investigation is still active

#### Scenario: Failure banner quotes the crash
- **WHEN** the Case Dossier renders a case whose `state_track_stage` is `failed`
- **THEN** the diagnosis column opens with a destructive (red) serif-italic banner and a bounded raw `error_detail` block
- **AND** a failed case with `proposal_state = "unpublished"` says that a local proposal was produced but publication failed; other failed cases use the generic crash copy
- **AND** the Diagnosis/Hypothesis/Evidence/Considered sections beneath it fall back to "No investigation notes were captured for this case." (the same fallback `pr`/`landed`/`escalated` use) rather than the in-flight "Diagnosing…" copy, since a `failed` case is terminal, not still working
- **AND** a failed case with no retained proposal renders "No PR. Investigation failed." in the destructive color (not the calm "No PR yet." reserved for in-flight, pr-less stages, and not "No PR. Escalated to user.", which asserts a human hand-off that never happened for a crash)

#### Scenario: Diagnosis column
- **WHEN** the Case Dossier renders the left column
- **THEN** it renders these sections in order, each preceded by a mono uppercase eyebrow:
  - **Diagnosis** — serif paragraph rendered from `investigation_notes.blurb_segments`. Plain strings render as-is; anchored segments render the inner text plus a mono superscript `[N]` matching the claim's number-in-order-of-appearance
  - **Hypothesis** — single mono line rendered from `investigation_notes.hypothesis`
  - **Evidence · log fragments** — mono grid rows rendered from `investigation_notes.evidence_lines[]`; each row shows ts, level, butler, msg, and the bracketed claim numbers `[N]` it supports
  - **Considered & ruled out** — rendered from `investigation_notes.counter_evidence[]`; one row per entry showing hypothesis + reason + verdict
- **AND** for `proposal_state = "unpublished"`, the diagnosis uses the root-cause `hypothesis` and does not render agent-authored `blurb_segments` that may describe proposed code in present tense
- **AND** when the user hovers a claim segment in the Diagnosis paragraph, every evidence row whose id appears in that claim's `evidence_ids[]` is visually highlighted; when the user hovers an evidence row, the claim segments referencing that row's id are highlighted (bidirectional linkage)

#### Scenario: Proposed fix column
- **WHEN** the Case Dossier renders the right column
- **THEN** it renders a PR panel (when `pr` is non-null) containing:
  - State chip (drafted/open/merged/closed) with appropriate state color from the Dispatch palette
  - Mono `pr #<number> · <state>`
  - Sans-500 14 px PR title
  - Mono caption with branch name, CI status, `+<additions> −<deletions>`
  - "Why this fix" eyebrow followed by a serif-italic 13 px line rendered from `investigation_notes.why_this_fix`
  - "Diff preview" eyebrow followed by a line-kind-aware diff renderer for `investigation_notes.diff_snapshot[]`
  - Mono footer caption with `opened <HH:MM>` and optional `· merged <HH:MM>`
- **AND** when `pr` is null, the column renders a single serif-italic line "No PR — escalated to user."
- **AND** when `proposal_state = "unpublished"`, the column instead labels the artifact `Local proposal`, explains that publication failed and no PR was created, and retains `why_this_fix` plus the diff under proposal-specific labels

#### Scenario: Session trace doors
- **WHEN** the Case Dossier renders a case whose attempt carries a `healing_session_id` and/or a non-empty `session_ids[]`
- **THEN** it renders a "Session trace" section with a real navigation door (a react-router `<Link>` to `/sessions/:id`, not a dead onClick) for the investigation session (`healing_session_id`) and one door per failing session in `session_ids[]`
- **AND** when `healing_session_id` is null AND `session_ids` is empty, the section is not rendered at all — no empty header and no broken link

#### Scenario: Patrol journal section
- **WHEN** the Case Dossier renders the full-width patrol journal section
- **THEN** the section renders a mono row for each event from `/api/qa/cases/:id/journal`, in chronological order: ts (`HH:MM`), step name in step color (flagged/opened amber, sampled/cross-checked/drafted neutral, considered/wait/tick dim, concluded/merged green, escalated amber), text, and an optional dim detail line beneath
- **AND** an eyebrow above the section reads "Patrol journal · every QA decision on this case" with a right-aligned `<count> entries · patrol every <P>m` caption
- **AND** when `proposal_state = "unpublished"`, a `concluded` event's agent-authored proposal detail is replaced with the factual projection `Proposal remained unpublished.` so proposed behavior cannot read as deployed
- **AND** when the case has no journal events yet, the section is hidden entirely



## system-overview-page — Per-Butler Heartbeat Facts


The complete existing contract remains normative at its actual current functional boundary, including every scenario below. actual process/container/heartbeat facts with source-dependent liveness and freshness. System remains the infrastructure home. A Region is not a process and its presence does not prove liveness; region detail links to actual infrastructure facts without a guessed mapping. This correction withdraws the prior proposed removal; its new dark UI is not evidence of an implemented or authorized retirement.


### Requirement: Per-Butler Heartbeat Facts

The `/api/system/butlers/heartbeat` endpoint SHALL return the last-known heartbeat
timestamp and session activity summary for each registered butler.

#### Scenario: Heartbeat endpoint returns per-butler status

- **WHEN** `GET /api/system/butlers/heartbeat` is called
- **THEN** the response body contains:
  - `butlers: ButlerHeartbeat[]` -- one entry per registered butler, ordered by
    butler name ascending
- **AND** each `ButlerHeartbeat` entry contains:
  - `name: string` -- butler name (e.g., `"general"`, `"health"`)
  - `last_heartbeat_at: string | null` -- ISO 8601 UTC timestamp of the most recent
    liveness heartbeat recorded in the switchboard registry, or `null` if the butler
    has never registered
  - `last_session_at: string | null` -- ISO 8601 UTC timestamp of the most recent
    completed session for this butler, derived from `{schema}.sessions WHERE
    completed_at IS NOT NULL ORDER BY completed_at DESC LIMIT 1`. The `IS NOT NULL`
    filter is required because active sessions have `completed_at = NULL` and
    PostgreSQL sorts NULLs last by default in DESC -- omitting the filter risks
    returning an active (incomplete) session as the "last" session.
  - `active_session_count: number` -- count of sessions where `completed_at IS NULL`
    in `{schema}.sessions` at query time. Note: the sessions table has no `status`
    column; active sessions are identified by `completed_at IS NULL` (see
    `src/butlers/core/sessions.py` `sessions_active` implementation)
  - `heartbeat_age_seconds: number | null` -- seconds since `last_heartbeat_at`,
    or `null`; the frontend uses this to classify freshness without client-side math
- **AND** the response wraps in the standard `ApiResponse<HeartbeatFacts>` envelope

#### Scenario: Heartbeat data is read from the registry, not from live MCP calls

- **WHEN** the heartbeat endpoint assembles its response
- **THEN** it reads liveness data from the switchboard's liveness registry table
  (the same source the butler list page uses)
- **AND** it does NOT issue live MCP `status` tool calls to any butler
- **AND** if a butler's liveness entry is missing from the registry (never started or
  deregistered), `last_heartbeat_at` is `null` and `heartbeat_age_seconds` is `null`

#### Scenario: Session facts are read via the dashboard API's existing DB fan-out

- **WHEN** the heartbeat endpoint reads per-butler session data
- **THEN** it uses the `DatabaseManager` fan-out pattern that the dashboard API already
  uses for cross-butler queries (not new ad-hoc SQL per butler)
- **AND** if a butler's schema is unreachable, that butler's `last_session_at` and
  `active_session_count` are `null` and 0 respectively, and the entry is still
  included in the response with an `error: "schema_unreachable"` flag

## Owner-note amendment retention boundary

Every full KEEP body and scenario clause above remains unchanged. New global Hold/Stop-all and research-read clauses coexist as future D19 obligations at their named source-owner boundaries; their complete WHEN/THEN and exact coordination map are in the paired deltas and contract-migration.md. In particular, the old regional quarantine Pause is retained before cutover, the proposed future retirement does not revive it as a global mutation, and read-only Operator remains entirely read-only. Draft4549/4548 and pending D20 source are not adopted by their appearance in this account. No count, annex, synthetic interaction or affected-authoring receipt establishes runtime delivery or final owner acceptance.
