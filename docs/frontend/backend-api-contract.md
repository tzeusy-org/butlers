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

## Issues Contract

- `GET /api/issues` -> `ApiResponse<Issue[]>`

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

## Butler State Contract

- `GET /api/butlers/{name}/state` -> `ApiResponse<StateEntry[]>`
- `PUT /api/butlers/{name}/state/{key}` -> `ApiResponse<...>`
- `DELETE /api/butlers/{name}/state/{key}` -> `ApiResponse<...>`

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

## Approvals Domain Contract (Target State)

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
