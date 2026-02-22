# Backend API Contract (Target State)

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

## Core System Endpoints

- `GET /api/health` -> `{ "status": "ok" | string }`
- `GET /api/butlers` -> `ApiResponse<ButlerSummary[]>`
- `GET /api/butlers/{name}` -> `ApiResponse<ButlerDetail>`
- `GET /api/butlers/{name}/config` -> `ApiResponse<ButlerConfigResponse>`
- `GET /api/butlers/{name}/skills` -> `ApiResponse<SkillInfo[]>`
- `POST /api/butlers/{name}/trigger` -> `TriggerResponse`
- `GET /api/butlers/{name}/mcp/tools` -> `ApiResponse<MCPToolInfo[]>`
- `POST /api/butlers/{name}/mcp/call` -> `ApiResponse<MCPToolCallResponse>`

## Sessions Contract

- `GET /api/sessions` -> `PaginatedResponse<SessionSummary>`
- `GET /api/sessions/{id}` -> `ApiResponse<SessionDetail>`
- `GET /api/butlers/{name}/sessions` -> `PaginatedResponse<SessionSummary>`
- `GET /api/butlers/{name}/sessions/{id}` -> `ApiResponse<SessionDetail>`

Required query filters for list endpoints:

- `offset`
- `limit`
- `butler` (cross-butler endpoint)
- `trigger_source`
- `status` (`success` | `failed`)
- `since` (ISO timestamp)
- `until` (ISO timestamp)

## Traces Contract

- `GET /api/traces` -> `PaginatedResponse<TraceSummary>`
- `GET /api/traces/{traceId}` -> `ApiResponse<TraceDetail>`

## Timeline Contract

- `GET /api/timeline` -> `TimelineResponse`

Required query support:

- `limit`
- repeated `butler`
- repeated `event_type`
- `before` (cursor token)

## Notifications Contract

- `GET /api/notifications` -> `PaginatedResponse<NotificationSummary>`
- `GET /api/notifications/stats` -> `ApiResponse<NotificationStats>`
- `GET /api/butlers/{name}/notifications` -> `PaginatedResponse<NotificationSummary>`

Required query support:

- `offset`
- `limit`
- `butler` (cross-butler endpoint)
- `channel`
- `status`
- `since`
- `until`

`NotificationSummary.metadata` normalization:

- API responses always emit `metadata` as either an object or `null`.
- Legacy non-object metadata payloads (arrays, strings, scalars) are normalized to `null`.

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

## Costs Contract

- `GET /api/costs/summary?period={today|7d|30d|90d}` -> `ApiResponse<CostSummary>`
- `GET /api/costs/daily` -> `ApiResponse<DailyCost[]>`
- `GET /api/costs/top-sessions?limit=...` -> `ApiResponse<TopSession[]>`

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
- `POST /api/calendar/workspace/sync` -> `ApiResponse<CalendarWorkspaceSyncResponse>`
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
- Returns per-target trigger outcomes in `data.targets`.

Mutation endpoint requirements:

- `POST /api/calendar/workspace/user-events` accepts `{butler_name, action, request_id?, payload}`.
- User action values: `create|update|delete`.
- User event update/delete payloads that touch recurring provider events must pass `recurrence_scope="series"` in v1; non-series scopes are not supported by runtime tools yet.
- `POST /api/calendar/workspace/butler-events` accepts `{butler_name, action, request_id?, payload}`.
- Butler action values: `create|update|delete|toggle`.
- Butler payloads must include `event_id` for `update|delete|toggle`; `toggle` also requires `enabled`.
- Both mutation endpoints return `action`, `tool_name`, `request_id`, `result`, and projection freshness metadata (`projection_version`, `staleness_ms`, `projection_freshness`).

Operational sync and telemetry guidance:

- Frontend clients should treat `projection_freshness` and `source_freshness.sync_state`/`staleness_ms` as the canonical sync health indicators for UX state.
- `request_id` is the correlation key for idempotent replay and audit/action-log tracing across API, MCP tool calls, and projection reconciliation.
- `POST /api/calendar/workspace/sync` target statuses (`status`, `detail`, `error`) are the contract surface for operator-visible sync telemetry.

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
- `GET /api/health/medications` -> `PaginatedResponse<Medication>`
- `GET /api/health/medications/{medicationId}/doses` -> `Dose[]`
- `GET /api/health/conditions` -> `PaginatedResponse<HealthCondition>`
- `GET /api/health/symptoms` -> `PaginatedResponse<Symptom>`
- `GET /api/health/meals` -> `PaginatedResponse<Meal>`
- `GET /api/health/research` -> `PaginatedResponse<HealthResearch>`

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
- `GET /api/memory/episodes` -> `PaginatedResponse<Episode>`
- `GET /api/memory/facts` -> `PaginatedResponse<Fact>`
- `GET /api/memory/facts/{factId}` -> `ApiResponse<Fact>`
- `GET /api/memory/rules` -> `PaginatedResponse<MemoryRule>`
- `GET /api/memory/rules/{ruleId}` -> `ApiResponse<MemoryRule>`
- `GET /api/memory/activity` -> `ApiResponse<MemoryActivity[]>`

## Approvals Domain Contract

- `GET /api/approvals/actions` -> `PaginatedResponse<ApprovalAction>`
- `GET /api/approvals/actions/{actionId}` -> `ApiResponse<ApprovalAction>`
- `POST /api/approvals/actions/{actionId}/approve` -> `ApiResponse<ApprovalAction>`
- `POST /api/approvals/actions/{actionId}/reject` -> `ApiResponse<ApprovalAction>`
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
  - `status` (`pending|approved|rejected|expired|executed`)
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
  - Success (no `OAUTH_DASHBOARD_URL`): `200 OAuthCallbackSuccess`
  - Success (with `OAUTH_DASHBOARD_URL`): `302` → `{OAUTH_DASHBOARD_URL}?oauth_success=true`
  - Error (no dashboard URL): `400 OAuthCallbackError`
  - Error (with `OAUTH_DASHBOARD_URL`): `302` → `{OAUTH_DASHBOARD_URL}?oauth_error={error_code}`

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

#### `OAuthCallbackSuccess`

```typescript
interface OAuthCallbackSuccess {
  success: true;
  message: string;
  provider: string;       // "google"
  scope: string | null;   // space-separated scopes granted
}
```

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
if (params.has('oauth_success')) {
  // Re-check status to confirm connected state
  await checkOAuthStatus();
  showSuccessBanner();
} else if (params.has('oauth_error')) {
  const errorCode = params.get('oauth_error');
  showErrorBanner(`OAuth failed: ${errorCode}`);
}
```
