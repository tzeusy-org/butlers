# Backend API Contract (Target State)

> **Purpose:** Define the mandatory backend endpoints and payload shapes required to support the dashboard frontend.
> **Audience:** Backend developers implementing API endpoints and frontend developers consuming them.
> **Prerequisites:** [Data Access and Refresh](data-access-and-refresh.md), [Feature Inventory](feature-inventory.md).

This document is the canonical backend contract required to support the frontend.

All endpoints and payload shapes below are mandatory. Absence or shape drift is non-compliant with frontend support.

## Global Contract

- API base path: `/api`
- Content type: `application/json`
- Error envelope: `ErrorResponse`
- No trailing-slash redirects for API routes.

## Response Envelope Rules

- Standard success envelope:
  - `ApiResponse<T>` for single resources and aggregates
  - `PaginatedResponse<T>` for offset/limit lists
- Explicit exceptions (frontend contract):
  - Timeline uses `TimelineResponse` (unwrapped).
  - Relationship domain endpoints use unwrapped typed payloads.
  - Trigger endpoint returns `TriggerResponse` (unwrapped).

## Bead Snapshot Detail Contract

- `GET /api/beads/{id}` -> `ApiResponse<BeadDetail>`.
- The endpoint reads only the existing read-only Beads JSONL export. It does
  not call `bd`, Dolt, GitHub, a database, a credential service, an external
  service, or any tracker mutation path.
- `meta.export_as_of` is the export file mtime and is present on every 200
  response. It is source freshness evidence, not a claim that a live tracker
  was queried.
- `BeadDetail` is a strict allowlist: `id`, `title`, `status`, `priority`,
  `type`, `description`, `design`, `acceptance_criteria`, `labels`,
  `created_at`, `updated_at`, `started_at`, `closed_at`, `due_at`,
  `dependencies`, and `external_ref`.
- `dependencies` contains at most 20 direct source-order summaries. Every
  summary contains only `id`, `title`, `status`, `priority`, and `type`.
- The response never contains notes, metadata, comments, identities,
  credentials, raw source records, raw dependency edges, or an arbitrary URL
  / `href` field.
- A fresh, fully readable snapshot that lacks the requested ID returns HTTP
  404 with `ErrorResponse.error.code = "BEAD_NOT_FOUND"`.
- A missing, stale, oversized, unreadable, or malformed snapshot returns HTTP
  503 with `ErrorResponse.error.code = "BEAD_SNAPSHOT_UNAVAILABLE"` and
  `ErrorResponse.error.details.export_as_of` (ISO timestamp when known,
  otherwise `null`). A 503 is never converted to a calm empty detail or a 404.

The matching dashboard page is `/beads/:id`. It renders only the typed API
allowlist, exposes the source-as-of time, and has no tracker mutation control.
Every decision and blocker drill-down builds a same-origin `/beads/:id` route
from its safe ID. `external_ref` is displayed as inert text, never an anchor
or a navigation target.

## Core System Endpoints

- `GET /api/health` -> `{ "status": "ok" | string }`
- `GET /api/butlers` -> `ApiResponse<ButlerSummary[]>`
- `GET /api/butlers/{name}` -> `ApiResponse<ButlerDetail>`
- `GET /api/butlers/{name}/config` -> `ApiResponse<ButlerConfigResponse>`
- `GET /api/butlers/{name}/skills` -> `ApiResponse<SkillInfo[]>`
- `POST /api/butlers/{name}/trigger` -> `TriggerResponse`
- `GET /api/butlers/{name}/mcp/tools` -> `ApiResponse<MCPToolInfo[]>`
- `POST /api/butlers/{name}/mcp/call` -> `ApiResponse<MCPToolCallResponse>`

## Dashboard Conversations Contract

- `GET /api/butlers/{name}/conversations` ->
  `PaginatedResponse<ConversationSummary>`; accepts `status` (`active` |
  `archived` | `all`), `offset`, and `limit`.
- `GET /api/butlers/{name}/conversations/search` ->
  `PaginatedResponse<ConversationSearchResult>`; requires non-empty `q` and
  accepts `offset` and `limit`.
- `GET /api/butlers/{name}/conversations/{conversationId}/messages` ->
  `PaginatedResponse<ConversationMessage>`; accepts `offset` and `limit`.
- `POST /api/butlers/{name}/conversations` and `POST
  /api/butlers/{name}/conversations/{conversationId}/messages` accept a
  client-created immutable `message_id` and return `text/event-stream`.
- `POST /api/butlers/{name}/conversation-turns/{messageId}/cancel` is the
  canonical durable Stop endpoint for that immutable turn. The older
  conversation-scoped route is compatibility-only.

The conversation stream emits `conversation_created` for a new conversation,
`dispatch_accepted`, `token`, `message_complete`, `error`, and `done`.
`dispatch_accepted` has `{ routed_butler: string | null }` and is a transient,
current-turn ingress receipt only. It may appear only after immutable ingress is
durably accepted and `dispatch_status` safely observes that same active turn:

- The first receipt is always `routed_butler: null`, even if that observation
  already has `target_kind: "route"`; it means Switchboard accepted the turn,
  not that the turn is permanently targetless.
- Only a later distinct safe observation may issue one non-empty named upgrade
  for the durable `route` target. The server never emits both receipts from one
  observation or a second named upgrade.
- The server does not emit it for a legacy stream without immutable message
  identity, unavailable/unsafe status, cancellation/ambiguity, a terminal
  action target, or based on classifier triage/stored conversation routing.

The UI must use a named receipt only for the current stream's accountable Butler
link; it must not infer a current target from `conversation.routed_butler`.
While pending, it presents one polite atomic status (`Sending to Switchboard.`,
the targetless acceptance text, or the named-route text) while treating typing
dots as decorative. During cancellation settlement or confirmed Stop, the
receipt status/dots and named link are suppressed so the Stop status is the
single authoritative live region. A list, search, or history query error is never an empty
result: show a retry that calls that query's `refetch`, retain cached same-thread
data, draft, and selection, and keep optimistic messages scoped to their owning
conversation.

## Sessions Contract

- `GET /api/sessions` -> `PaginatedResponse<SessionSummary>`
- `GET /api/sessions/{id}` -> `ApiResponse<SessionDetail>`
- `GET /api/sessions/aggregate` -> `ApiResponse<SessionAggregate>`
- `GET /api/butlers/{name}/sessions` -> `PaginatedResponse<SessionSummary>`

Single-session detail is served only by the global `GET /api/sessions/{id}`
fan-out. Session IDs are globally unique; legacy dashboard links that retain
`/sessions/{id}?butler={name}` remain accepted but the query is ignored and
does not select a second detail endpoint.

`GET /api/sessions/aggregate?include_trigger_breakdown=true` returns optional
`by_trigger_source` attribution. Its
`data.trigger_breakdown_degraded_sources: string[]` names pools that failed
only the optional trigger-breakdown fan-out; scalar aggregate failures remain
exclusively in `meta.sources_degraded`.

Required query filters for list endpoints:

- `offset`
- `limit`
- `butler` (cross-butler endpoint)
- `trigger_source`
- `status` (`success` | `failed`)
- `since` (ISO timestamp)
- `until` (ISO timestamp)

## Timeline Contract

- `GET /api/timeline` -> `TimelineResponse`

Required query support:

- `limit`
- repeated `butler`
- repeated `event_type`
- `before` (cursor token)
- `trace` (OpenTelemetry trace scope; matching sessions and trace-attributed
  notifications only)

## Chronicles Editorial Briefing Contract

- `GET /api/chronicler/briefing?date={YYYY-MM-DD}&tz={IANA timezone}` ->
  unwrapped `ChroniclesBriefing`.
- `date` is a local calendar day interpreted in `tz`. The dashboard always
  passes its owner-timezone selection explicitly; when omitted, the endpoint
  uses its defensive previous-UTC-date fallback. `tz` defaults to Chronicler's
  stable owner-timezone fallback.
- `state_class` is a closed union:
  - content states: `urgent` | `busy` | `mild` | `quiet`;
  - non-content states: `no_data` | `unavailable` | `degraded`.

The non-content states are an explicit response union, not a quiet-day
variant. They carry deterministic headline and voice copy, empty KPI/recent
day content, and may retain named source-error attention rows. Clients must
not display cached or stale editorial prose, KPI, recent-days rows, or the
Chronicles drilldown for a non-content state. An unknown or missing
`state_class` is malformed input and must fail closed as the deterministic
`unavailable` presentation.

Coverage and cache precedence are fixed:

1. Chronicler first resolves durable local coverage for the exact requested
   local day. Only an authoritative witness (`day_close_success`, an admitted
   day-close cache, active `activity`/`evidence` episode proof) counts;
   calendar intent, tombstones, and retained `legacy_unverified` rows do not.
   A cache witness additionally requires an active, admitted row whose
   `day_close:{date}:tz:{exact-IANA-name}` key and `date_label` agree with the
   witness and whose
   `[start_at, end_at)` exactly equals that date's owner-timezone local-day UTC
   window; a UTC-midnight window is not proof for a non-UTC owner.
2. A settled day before the authoritative floor returns `no_data`; a gap at or
   after the floor, or no floor, returns `unavailable`. A failed owned read
   returns `unavailable` or `degraded` as applicable.
3. Only a covered, available content state may read a day-close cache. Its
   deterministic date/admission check runs before freshness: an invalid or
   mismatched row is never rendered; a valid stale row is marked `stale`; a
   miss uses the templated fallback. The endpoint never initiates an LLM call.

`earliest_date` is the earliest authoritatively covered local date for the
requested timezone, or `null` when there is no durable coverage proof. It
blocks additional backward navigation, but it does not rewrite a valid
pre-floor deep link: `/chronicles?date=<pre-floor>` remains in the URL and
receives the explicit `no_data` state so a user can move forward again. Future
dates are clamped to the most-recent settled day.

`recent_days` contains only exact authoritative witness dates in the recent
window. It is archive navigation evidence, not an episode-derived rolling list;
the client must not synthesize omitted dates.

The client must render briefing editorial content only when response `date`
equals the selected URL date. A date-keyed query may retain prior placeholder
data during navigation; that transition must use a safe loading presentation,
not prior prose, KPI, recent-day rows, cache state, or drilldown content.

## Chronicler Day-Close Cache and Refresh Contract

- `GET /api/chronicler/aggregate/day-close` requires both
  `date=YYYY-MM-DD` and an exact, non-empty IANA `tz` query parameter. The
  typed dashboard client and its query key carry both values.
- Day-close cache identity is
  `day_close:{YYYY-MM-DD}:tz:{exact-IANA-name}`. The exact accepted timezone
  string is part of the key; it is not replaced by a default or a normalized
  alias.
- Writer containment serializes the actual `(date, tz)` tuple through a
  collision-safe Chronicler-local transaction lock, never a fixed-width hash
  that could make different tuples contend.
- A missing `tz` returns `400` with `error.code = "missing_parameter"`; an
  empty or unresolvable value returns `400` with
  `error.code = "invalid_timezone"`. These failures occur before cache,
  rate-limit, or dispatch work.
- Preserved legacy `day_close:{date}` rows are never queried as a compatibility
  fallback. They remain untouched audit history and appear as normal cache
  misses until a tuple-keyed row is written.

- `POST /api/chronicler/aggregate/day-close/refresh` accepts
  `{date: YYYY-MM-DD, tz: exact IANA timezone}` and reuses the scheduled
  `chronicler_day_close` path; the dashboard has no separate LLM route.
- The target must be a settled historical local day: `date` is strictly before
  the server's current date in the supplied `tz`. Today and future targets
  return `400` with `error.code = "day_close_not_settled"` before any
  rate-limit lookup or dispatch. A valid historical target continues to the
  normal tuple-keyed rate-limit and dispatch path.
- When a selected briefing reports its day-close prose as stale, the Chronicles
  page's **Regenerate** action POSTs that exact selected `{date, tz}` tuple and
  re-fetches the same tuple only after a successful response. Failure leaves
  the stale indication visible rather than replacing it with unproven prose.
- A prose-producing or contained-invalid success returns
  `{cache_key, cache_built_at, invalid, invalid_reason}`. `invalid_reason` is
  `null`, `inadmissible_prose`, or `date_mismatch`.
- A canonical executed bundle that is bound to the requested `date` and `tz`
  and contains empty `episodes` and `events` returns the distinct successful
  response `{cache_key, quiet: true}`. It writes no cache row and never
  returns a prior row's `cache_built_at`.
- Blank prose is quiet only for that validated empty bundle. A missing,
  malformed, mismatched, duplicate-execution, or non-empty bundle with blank
  prose returns `502` with `error.code = "cache_write_failed"`; it must not
  reuse an old cache row as the result of the refresh.

## Notifications Contract

- `GET /api/notifications` -> `PaginatedResponse<NotificationSummary>`
- `GET /api/notifications/stats` -> `ApiResponse<NotificationStats>`
- `GET /api/butlers/{name}/notifications` -> `PaginatedResponse<NotificationSummary>`
- `PATCH /api/notifications/{id}/read` -> `ApiResponse<NotificationSummary>`

Required query support:

- `offset`
- `limit`
- `butler` (cross-butler endpoint)
- `channel`
- `status`, including computed `retried` and `terminal_failed`; `terminal_failed`
  means a failed attempt with no later matching sent retry and matches the
  `NotificationStats.failed` count
- `since`
- `until`

`NotificationSummary.metadata` normalization:

- The global list, butler-scoped list, and mark-read response always emit
  `metadata` as either an object or `null` through the same one-layer normalizer.
- A mapping is returned as a shallow object copy, and `null` remains `null`.
- A legacy JSONB string whose one JSON parse yields an object is returned as
  that object.
- A malformed string, a string whose one parse cannot complete because of a
  JSON decoder safety limit, or a string whose one parse yields an array,
  string, number, boolean, or `null`, is returned as
  `{"_raw": <original outer string>}`.
- An actual non-string JSONB array, number, or boolean remains `null`; it is
  not wrapped in `_raw`.
- The normalizer never recursively decodes a parsed string or infers missing
  provenance.

## Issues Contract

- `GET /api/issues` -> `ApiResponse<Issue[]>`

`Issue` payload requirements:

- Grouped by normalized error message across butlers.
- Includes chronology metadata:
  - `occurrences` (aggregate count)
  - `first_seen_at` (earliest observed timestamp)
  - `last_seen_at` (latest observed timestamp)
- Includes `butlers` (distinct butler names participating in the group).
- Endpoint response ordering is newest-first by `last_seen_at`.
- The audit-derived lane returns at most the newest 500 groups. If more groups
  match, `meta.truncated` is `true` and the UI must say that some audit-derived
  issues may be missing; the field is absent when the grouped result is
  complete, including exactly 500 groups.

## Spend Contract

- `GET /api/spend/summary?period={today|7d|30d|90d}` -> `ApiResponse<SpendSummary>`
- `GET /api/spend/daily` -> `ApiResponse<DailySpend[]>`
- `GET /api/spend/top-sessions?limit=...` -> `ApiResponse<TopSession[]>`

## Audit Contract

- `GET /api/audit-log` -> `PaginatedResponse<AuditEntry>`

Required query support:

- `offset`
- `limit`
- `butler`
- `operation`
- `since`
- `until`

## Search Contract

- `GET /api/search?q=...&limit=...` -> `ApiResponse<SearchResults>`

`SearchResult` entries must be frontend-navigation-ready and include:

- `id`
- `butler`
- `type`
- `title`
- `snippet`
- `url`

Grouped result keys required by frontend:

- `sessions`
- `state`
- `contacts` (optional when no matches, but key must be supported)

## Butler Schedules Contract

- `GET /api/butlers/{name}/schedules` -> `ApiResponse<Schedule[]>`
- `POST /api/butlers/{name}/schedules` -> `ApiResponse<...>`
- `PUT /api/butlers/{name}/schedules/{scheduleId}` -> `ApiResponse<...>`
- `DELETE /api/butlers/{name}/schedules/{scheduleId}` -> `ApiResponse<...>`
- `PATCH /api/butlers/{name}/schedules/{scheduleId}/toggle` -> `ApiResponse<...>`

Schedule execution semantics (dashboard-facing):

- `Schedule.source` describes schedule origin (`toml` vs `db`); it is not the execution mode.
- Runtime mode schedules execute through `spawner.trigger(..., trigger_source="schedule:<task-name>")` and typically correlate with session rows.
- Native mode schedules execute deterministic daemon jobs directly and may not create `sessions` rows.
- The dashboard MUST treat schedule status fields (`enabled`, `next_run_at`, `last_run_at`) as authoritative regardless of execution mode.
- Schedule failures for both execution modes surface through `/api/issues` as `scheduled_task_failure:<schedule-name>`.

## Calendar Workspace Contract

- `GET /api/calendar/workspace` -> `ApiResponse<CalendarWorkspaceReadResponse>`
- `GET /api/calendar/workspace/meta` -> `ApiResponse<CalendarWorkspaceMetaResponse>`
- `POST /api/calendar/workspace/sync` -> `202 Accepted` + `ApiResponse<CalendarWorkspaceSyncResponse>`
- `POST /api/calendar/workspace/user-events` -> `ApiResponse<CalendarWorkspaceMutationResponse>`
- `POST /api/calendar/workspace/butler-events` -> `ApiResponse<CalendarWorkspaceMutationResponse>`

Required query support for `GET /api/calendar/workspace`:

- `view` (`user|butler`) — required
- `start` (ISO timestamp) — required
- `end` (ISO timestamp) — required
- `timezone` (IANA timezone, optional display conversion)
- repeated `butlers` filter
- repeated `sources` (`calendar_sources.source_key`) filter

Read response requirements:

- `data.entries` is a normalized `UnifiedCalendarEntry[]` list for direct calendar rendering.
- `data.source_freshness` includes per-source sync freshness metadata (`sync_state`, `staleness_ms`, timestamps, last error).
- `data.lanes` includes butler-lane metadata (`lane_id`, `butler_name`, `title`, `source_keys`).

Meta response requirements:

- `capabilities` contains view/filter/sync capability flags.
- `connected_sources` lists source registry rows with freshness and writeability metadata.
- `writable_calendars` lists user-lane writable provider calendars.
- `lane_definitions` lists butler-lane descriptors for workspace layout.
- `default_timezone` is required.

Sync response requirements:

- Supports global refresh (`{"all": true}`) and source-targeted refresh (`source_key` or `source_id`).
- Is an acknowledgement, not provider completion: returns `202 Accepted` only after each selected CalendarModule has accepted or rejected its durable action-log command.
- Returns outer `data.request_id`, plus per-target `request_id`, `status`, `coalesced`, `detail`, and `error` fields in `data.targets`.
- Global refresh selects one enabled, core-capable canonical owner per duplicate provider `source_key`, then sends at most one owner-wide queued command (without `calendar_id`) to that owner. It never fan-outs one request per replicated schema row.
- A source-targeted request with an explicit `source_id` or `butler` preserves that physical target; a source-key-only request resolves its canonical owner.
- `full=true` is queued cursor-recovery intent. A `queued` acknowledgement must not be rendered as completed/recovered; eventual state is observed through source freshness and action/audit telemetry.

Mutation endpoint requirements:

- `POST /api/calendar/workspace/user-events` accepts `{butler_name, action, request_id?, payload}`.
- User action values: `create|update|delete`.
- A user-event update replaces linked people with a non-empty `entity_ids` array. To deliberately remove every linked person, send `entity_ids: []` with `clear_entity_ids: true`; omitted or empty `entity_ids` without that flag preserve existing links.
- User event update/delete payloads that touch recurring provider events support `recurrence_scope` values `this`, `following`, and `series`; `this` and `following` also require the occurrence `instance_start_at`, while `series` updates the whole series.
- `POST /api/calendar/workspace/butler-events` accepts `{butler_name, action, request_id?, payload}`.
- Butler action values: `create|update|delete|toggle`.
- Butler payloads must include `event_id` for `update|delete|toggle`; `toggle` also requires `enabled`.
- Both mutation endpoints return `action`, `tool_name`, `request_id`, `result`, and projection freshness metadata (`projection_version`, `staleness_ms`, `projection_freshness`).

Operational sync and telemetry guidance:

- Frontend clients should treat `projection_freshness` and `source_freshness.sync_state`/`staleness_ms` as the canonical sync health indicators for UX state.
- `request_id` is the correlation key for idempotent replay and audit/action-log tracing across API, MCP tool calls, and projection reconciliation.
- `POST /api/calendar/workspace/sync` target statuses (`queued`, `completed`, `failed`), correlation, coalescing, detail, and error are the contract surface for operator-visible dispatch telemetry; they are not proof of provider completion.

## Butler State Contract

- `GET /api/butlers/{name}/state` -> `ApiResponse<StateEntry[]>`
- `PUT /api/butlers/{name}/state/{key}` -> `ApiResponse<...>`
- `DELETE /api/butlers/{name}/state/{key}` -> `ApiResponse<...>`

## Butler MCP Debug Contract

- `GET /api/butlers/{name}/mcp/tools` -> `ApiResponse<MCPToolInfo[]>`
  - `MCPToolInfo`: `name`, `description`, `input_schema`
- `POST /api/butlers/{name}/mcp/call` -> `ApiResponse<MCPToolCallResponse>`
  - Request: `{ tool_name: string, arguments?: object }`
  - Response: `tool_name`, `arguments`, `result` (parsed when JSON), `raw_text`, `is_error`

## Relationship Domain Contract

- `GET /api/relationship/contacts` -> `ContactListResponse`
- `GET /api/relationship/contacts/{contactId}` -> `ContactDetail`
- `GET /api/relationship/contacts/{contactId}/notes` -> `Note[]`
- `GET /api/relationship/contacts/{contactId}/interactions` -> `Interaction[]`
- `GET /api/relationship/contacts/{contactId}/gifts` -> `Gift[]`
- `GET /api/relationship/contacts/{contactId}/loans` -> `Loan[]`
- `GET /api/relationship/contacts/{contactId}/feed` -> `ActivityFeedItem[]`
- `GET /api/relationship/groups` -> `GroupListResponse`
- `GET /api/relationship/groups/{groupId}` -> `Group`
- `GET /api/relationship/labels` -> `Label[]`
- `GET /api/relationship/upcoming-dates` -> `UpcomingDate[]`

## Health Domain Contract

- `GET /api/health/measurements` -> `PaginatedResponse<Measurement>`
- `GET /api/health/measurements/types` -> `MeasurementTypesResponse`
- `GET /api/health/medications` -> `PaginatedResponse<Medication>`
- `GET /api/health/medications/{medicationId}/doses` -> `Dose[]`
- `GET /api/health/conditions` -> `PaginatedResponse<HealthCondition>`
- `GET /api/health/symptoms` -> `PaginatedResponse<Symptom>`
- `GET /api/health/meals` -> `PaginatedResponse<Meal>`
- `GET /api/health/research` -> `PaginatedResponse<HealthResearch>`

`MeasurementTypesResponse` is an unwrapped `{ "types": MeasurementTypeInfo[] }`
payload derived only from active Health-pool `facts` whose predicates match
`measurement_%` and have a `valid_at`. Entries are ordered by their predicate
suffix (`type`) and include `type`, deterministic slug-derived `label`,
`sample_count`, `latest_at`, latest observed `unit` (or `null`),
`value_shape` (`scalar` | `compound` | `unknown`), `chart_eligible`, and
`kpi_eligible`.

This is an observed read vocabulary: it includes unknown/imported types but
does not alter the fixed five-type manual measurement writer allowlist.
`kpi_eligible` identifies server-authorized candidates for the fixed four
structural dashboard KPI positions. The current server marks the four core
types; a future non-core eligible type can fill only an absent core position,
not add a cell or displace an observed core type.

## Connectors Contract

- `GET /api/connectors` -> `ApiResponse<ConnectorSummary[]>`
- `GET /api/connectors/{connectorType}/{endpointIdentity}` -> `ApiResponse<ConnectorDetail>`
- `GET /api/connectors/{connectorType}/{endpointIdentity}/stats` -> `ApiResponse<ConnectorStats>`
- `GET /api/connectors/summary` -> `ApiResponse<ConnectorCrossSummary>`
- `GET /api/connectors/fanout` -> `ApiResponse<ConnectorFanout>`

Required query support:

- `/api/connectors/{connectorType}/{endpointIdentity}/stats`:
  - `period` (`24h` | `7d` | `30d`)
- `/api/connectors/summary`:
  - `period` (`24h` | `7d` | `30d`)
- `/api/connectors/fanout`:
  - `period` (`7d` | `30d`)

Response model shapes:

- `ConnectorSummary`:
  - `connector_type`: string
  - `endpoint_identity`: string
  - `liveness`: `"online"` | `"stale"` | `"offline"`
  - `state`: `"healthy"` | `"degraded"` | `"error"`
  - `error_message`: string | null
  - `version`: string | null
  - `uptime_s`: number | null
  - `last_heartbeat_at`: ISO timestamp | null
  - `first_seen_at`: ISO timestamp
  - `today`: `ConnectorDaySummary` | null

- `ConnectorDaySummary`:
  - `messages_ingested`: number
  - `messages_failed`: number
  - `uptime_pct`: number | null

- `ConnectorDetail` (extends `ConnectorSummary`):
  - `instance_id`: UUID | null
  - `registered_via`: string
  - `checkpoint`: `{ cursor: string | null, updated_at: ISO timestamp | null }` | null
  - `counters`: `{ messages_ingested, messages_failed, source_api_calls, checkpoint_saves, dedupe_accepted }` | null

- `ConnectorStats`:
  - `connector_type`: string
  - `endpoint_identity`: string
  - `period`: string
  - `summary`: `{ messages_ingested, messages_failed, error_rate_pct, uptime_pct, avg_messages_per_hour }`
  - `timeseries`: `ConnectorStatsBucket[]`

- `ConnectorStatsBucket`:
  - `bucket`: ISO timestamp
  - `messages_ingested`: number
  - `messages_failed`: number
  - `healthy_count`: number
  - `degraded_count`: number
  - `error_count`: number

- `ConnectorCrossSummary`:
  - `period`: string
  - `total_connectors`: number
  - `connectors_online`: number
  - `connectors_stale`: number
  - `connectors_offline`: number
  - `total_messages_ingested`: number
  - `total_messages_failed`: number
  - `overall_error_rate_pct`: number
  - `by_connector`: `ConnectorSummary[]` (lightweight subset)

- `ConnectorFanout`:
  - `period`: string
  - `matrix`: `ConnectorFanoutEntry[]`

- `ConnectorFanoutEntry`:
  - `connector_type`: string
  - `endpoint_identity`: string
  - `targets`: `Record<string, number>` (butler name -> message count)

## General and Switchboard Views Contract

- `GET /api/general/collections` -> `PaginatedResponse<GeneralCollection>`
- `GET /api/general/collections/{collectionId}/entities` -> `PaginatedResponse<GeneralEntity>`
- `GET /api/general/entities` -> `PaginatedResponse<GeneralEntity>`
- `GET /api/general/entities/{entityId}` -> `ApiResponse<GeneralEntity>`
- `GET /api/switchboard/routing-log` -> `PaginatedResponse<RoutingEntry>`
- `GET /api/switchboard/registry` -> `ApiResponse<RegistryEntry[]>`

## Memory Domain Contract

- `GET /api/memory/stats` -> `ApiResponse<MemoryStats>`
  - Additive `meta.graph_health` is a read-only coverage view, not a graph
    health, provenance-link, or repair verdict:
    - `coverage`: `complete | incomplete | unknown`
    - `pools`: `GraphHealthPoolCoverage[]`, where each row has
      `source_butler`, `source_schema`, `coverage: complete | unknown`,
      `reapable_expired_episodes`, `retention_eligible_episodes`, and
      `reapable_expired_ratio`.
    - Complete rows reuse the existing consolidation-aware cleanup-lag
      numerator and `expires_at IS NOT NULL` denominator. Unknown rows have
      null metrics; no completed relevant pool means fleet coverage is
      `unknown`, not zero or healthy.
    - Existing `retention_*` data and metadata fields retain their prior names
      and semantics. The stats request remains side-effect-free and does not
      expose a cleanup, repair, or other graph mutation.
- `GET /api/memory/episodes` -> `PaginatedResponse<Episode>`
- `GET /api/memory/facts` -> `PaginatedResponse<Fact>`
- `GET /api/memory/facts/{factId}` -> `ApiResponse<Fact>`
- `GET /api/memory/rules` -> `PaginatedResponse<MemoryRule>`
- `GET /api/memory/rules/{ruleId}` -> `ApiResponse<MemoryRule>`
- Facts and rules with a source episode carry
  `source_episode_status: available | expired | unresolved | null`; only
  `available` permits a live episode navigation affordance.
- `GET /api/memory/links/{memoryType}/{memoryId}?direction=incoming|outgoing|both`
  -> `ApiResponse<MemoryLink[]>`; each episode endpoint carries its matching
  `source_episode_status` or `target_episode_status` in the same vocabulary,
  while non-episode endpoints are `null`.
- `GET /api/memory/activity` -> `ApiResponse<MemoryActivity[]>`

## Approvals Domain Contract

- `GET /api/approvals` and `GET /api/approvals/history` -> `ApiResponse<ApprovalSummary[]>`; summaries carry a nullable, redacted `execution_result`, and Retry is eligible only for `status = approved` with `execution_result = null`
- `GET /api/approvals/actions` -> `PaginatedResponse<ApprovalAction>`
- `GET /api/approvals/actions/{actionId}` -> `ApiResponse<ApprovalAction>`
- `POST /api/approvals/actions/{actionId}/approve` -> `ApiResponse<ApprovalAction>`
- `POST /api/approvals/actions/{actionId}/reject` -> `ApiResponse<ApprovalAction>`
- `POST /api/approvals/{actionId}/abandon` -> `ApiResponse<ApprovalAction>`; dashboard-only, body `{ reason: string }`, valid only for `approved` actions with `execution_result = null`
- `POST /api/approvals/actions/expire-stale` -> `ApiResponse<{ expired_count: number, expired_ids: string[] }>`
- `GET /api/approvals/actions/executed` -> `PaginatedResponse<ApprovalAction>`

- `POST /api/approvals/rules` -> `ApiResponse<ApprovalRule>`
- `POST /api/approvals/rules/from-action` -> `ApiResponse<ApprovalRule>`
- `GET /api/approvals/rules` -> `PaginatedResponse<ApprovalRule>`
- `GET /api/approvals/rules/{ruleId}` -> `ApiResponse<ApprovalRule>`
- `POST /api/approvals/rules/{ruleId}/revoke` -> `ApiResponse<ApprovalRule>`
- `GET /api/approvals/rules/suggestions/{actionId}` -> `ApiResponse<RuleConstraintSuggestion>`

- `GET /api/approvals/metrics` -> `ApiResponse<ApprovalMetrics>`

Required query support:

- `/api/approvals/actions`:
  - `offset`
  - `limit`
  - `status` (`pending|approved|rejected|expired|executed|abandoned`)
  - `tool_name`
  - `since`
  - `until`
- `/api/approvals/actions/executed`:
  - `offset`
  - `limit`
  - `tool_name`
  - `rule_id`
  - `since`
  - `until`
- `/api/approvals/rules`:
  - `offset`
  - `limit`
  - `tool_name`
  - `active_only`

## OAuth Domain Contract

Endpoints for initiating the Google OAuth authorization flow and surfacing
credential connectivity state in the dashboard.

### Bootstrap Flow

- `GET /api/oauth/google/start` — begin Google OAuth authorization
  - Query params:
    - `redirect` (bool, default `true`): if `true` returns a `302` redirect to Google;
      if `false` returns `OAuthStartResponse` JSON for programmatic callers.
  - Success (redirect=true): `302` → Google authorization URL
  - Success (redirect=false): `200 OAuthStartResponse`
  - Error: `503` when server-side credentials are not configured

- `GET /api/oauth/google/callback` — handle Google callback after user authorization
  - Query params (injected by Google): `code`, `state`, `error`, `error_description`
  - Success: `302` → the page that initiated the flow, built from the CSRF
    state (`connector_detail_path` deep-link > `page_of_origin` > default
    `/secrets?focus=u:google&toast=connected`)
  - Provider error (user denied consent, etc.): `302` → originating page with
    `?oauth_error=provider_error` when page context or `OAUTH_DASHBOARD_URL`
    is available; otherwise `400 OAuthCallbackError`
  - Pre-state errors (missing code/state, invalid state) and post-state
    failures (token exchange, userinfo): `400`/`502 OAuthCallbackError` JSON
  - `OAUTH_DASHBOARD_URL`, when set, acts as the frontend **base URL**: the
    redirect becomes `{OAUTH_DASHBOARD_URL}{built_path}` (needed when the UI
    is served from a different origin/path prefix than the API). The legacy
    `?oauth_success=true` redirect param is gone — the built paths carry
    `toast=connected` / `oauth_error=<code>` instead.

### Credential Status Surface

- `GET /api/oauth/status` → `OAuthStatusResponse`

Always returns HTTP 200. Errors and non-connected states are encoded in
the payload, not in the HTTP status code. This makes the endpoint safe to
poll from the dashboard without special error handling.

#### `OAuthStatusResponse`

```typescript
interface OAuthStatusResponse {
  google: OAuthCredentialStatus;
}
```

#### `OAuthCredentialStatus`

```typescript
interface OAuthCredentialStatus {
  provider: string;                  // "google"
  state: OAuthCredentialState;       // machine-readable state enum
  connected: boolean;                // true iff state === "connected"
  scopes_granted: string[] | null;   // OAuth scopes present on the credential
  remediation: string | null;        // actionable guidance when connected=false
  detail: string | null;             // technical detail for operator debugging
}
```

#### `OAuthCredentialState` enum

| Value | Meaning | Frontend UX |
|-------|---------|-------------|
| `connected` | Credentials present, validated, scopes sufficient | Show green badge |
| `not_configured` | No client credentials or no refresh token | Show "Connect Google" button |
| `expired` | Refresh token revoked or expired | Show "Reconnect Google" button |
| `missing_scope` | Token valid but lacks required permissions | Show "Re-authorize Google" button |
| `redirect_uri_mismatch` | Client credentials or redirect URI invalid | Show "Check Configuration" alert |
| `unapproved_tester` | App in testing mode, account not added as tester | Show tester setup guidance |
| `unknown_error` | Unclassified error | Show error banner with `remediation` text |

#### `OAuthCallbackError`

```typescript
interface OAuthCallbackError {
  success: false;
  error_code: string;   // machine-readable error identifier
  message: string;      // human-readable actionable message
  provider: string;     // "google"
}
```

#### Error codes for `OAuthCallbackError`

| `error_code` | Cause |
|--------------|-------|
| `provider_error` | Google returned an error (e.g. user denied consent) |
| `missing_code` | Authorization code absent from callback |
| `missing_state` | CSRF state token absent — possible replay attack |
| `invalid_state` | State token invalid or expired |
| `token_exchange_failed` | Failed to exchange authorization code for tokens |
| `no_refresh_token` | Token exchange succeeded but Google did not return a refresh token |

#### Dashboard Integration Example

```typescript
// Poll status on page load and after OAuth redirect
const checkOAuthStatus = async () => {
  const resp = await fetch('/api/oauth/status');
  const { google } = await resp.json();

  if (google.connected) {
    showConnectedBadge();
  } else if (google.state === 'not_configured') {
    showConnectButton({ onClick: () => window.location.href = '/api/oauth/google/start' });
  } else {
    showRemediationAlert(google.remediation);
  }
};

// Handle callback result (check URL params after redirect back from Google)
const params = new URLSearchParams(window.location.search);
if (params.get('toast') === 'connected') {
  // Re-check status to confirm connected state
  await checkOAuthStatus();
  showSuccessBanner();
} else if (params.has('oauth_error')) {
  const errorCode = params.get('oauth_error');
  showErrorBanner(`OAuth failed: ${errorCode}`);
}
```

## Related Pages

- [Data Access and Refresh](data-access-and-refresh.md) -- Polling intervals and refresh contracts
- [Feature Inventory](feature-inventory.md) -- What the frontend implements per route
- [Information Architecture](information-architecture.md) -- Route map and navigation
- [Connector Metrics and Dashboard Visibility](../connectors/metrics.md) -- Connector API contract details
