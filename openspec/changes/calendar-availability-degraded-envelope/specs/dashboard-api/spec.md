## MODIFIED Requirements

### Requirement: Calendar Workspace
`src/butlers/api/routers/calendar_workspace.py` provides a normalized calendar read surface, a full-text search endpoint, a find-time availability endpoint, a metadata endpoint, a sync trigger, and mutation endpoints for both user-view and butler-view events. The read endpoint SHALL support server-side `status`, `source_type`, and `editable` facets and keyset (cursor) pagination; the search endpoint SHALL match a free-text query against event title, description, and location; the find-time endpoint SHALL return ranked open slots and SHALL fail open with an explicit degraded envelope when free/busy is unreachable.

#### Scenario: Workspace read
- **WHEN** `GET /api/calendar/workspace?view=user&start=...&end=...` is called
- **THEN** calendar entries are fan-out queried across butler DBs (joining `calendar_event_instances`, `calendar_events`, `calendar_sources`, and `calendar_sync_cursors`)
- **AND** entries are normalized into `UnifiedCalendarEntry` objects with computed `source_type`, `status`, and `sync_state`
- **AND** optional `timezone` parameter converts all timestamps to the requested display timezone

#### Scenario: Workspace read applies status, source_type, and editable facets server-side
- **WHEN** `GET /api/calendar/workspace` is called with any of the optional `status`, `source_type`, or `editable` query params
- **THEN** the corresponding predicate is applied in the fan-out query (`status` over the instance/event status, `source_type` over the computed entry kind, `editable` over the source's `writable` flag) rather than returning all entries for the window and filtering client-side
- **AND** multiple supplied facets are combined with AND
- **AND** omitting a facet leaves that dimension unfiltered (the prior behavior)
- **AND** an unknown `status` or `source_type` value yields a 400 validation error

#### Scenario: Workspace read paginates with a keyset cursor
- **WHEN** `GET /api/calendar/workspace` is called with `limit` (bounded, with a default) and no `cursor`
- **THEN** at most `limit` entries are returned ordered by the workspace keyset `(starts_at, id)`
- **AND** the response includes `has_more: true` and an opaque `next_cursor` encoding the last `(starts_at, id)` seen when more rows remain for the window
- **AND** the response does NOT include a `total` field, per the repo's keyset pagination convention

#### Scenario: Workspace read follows the cursor to the next page
- **WHEN** `GET /api/calendar/workspace` is called with `cursor=<next_cursor>` from the prior page
- **THEN** the next page of entries strictly after the encoded keyset position is returned with no overlap with the prior page
- **AND** when no further rows remain the response has `has_more: false` and a null `next_cursor`
- **AND** a malformed or unparseable `cursor` yields a 400 validation error

#### Scenario: Workspace search by free text
- **WHEN** `GET /api/calendar/workspace/search?q=dentist&view=user` is called with a non-empty `q`
- **THEN** matches are fan-out queried across butler DBs over `calendar_events` `title`, `description`, and `location`
- **AND** matches are returned as `UnifiedCalendarEntry`-shaped rows carrying each match's date(s), ranked by trigram relevance, so the UI can group by day and jump-to the event
- **AND** results respect the same lane (`view`) and optional `butlers`/`sources` scoping as the read endpoint

#### Scenario: Workspace search with an empty query
- **WHEN** `GET /api/calendar/workspace/search` is called with a missing or blank `q`
- **THEN** an empty match list is returned (the whole calendar is NOT returned) and no error is raised

#### Scenario: Workspace search degrades when all targeted schemas fail
- **WHEN** `GET /api/calendar/workspace/search` is called and every targeted butler schema fails to respond (pool unreachable, or both trigram and ILIKE queries raise for all schemas)
- **THEN** the endpoint returns HTTP 200 with `entries: []` and `available: false` in the response payload
- **AND** `available: false` MUST NOT be returned when only some schemas fail — if at least one schema returns results (even an empty set), `available` is `true`
- **BECAUSE** a misleading "no results" is worse than an honest "search unavailable"; the UI SHALL render a "search unavailable" indicator rather than an empty-results state when `available` is `false`

#### Scenario: Find-time returns ranked open slots
- **WHEN** `POST /api/calendar/workspace/find-time` is called with `duration_minutes`, `search_start`, `search_end`, and optional `calendar_ids` and `constraints`
- **THEN** the endpoint dispatches `calendar_find_free_slots` via the MCP bridge and returns HTTP 200 with `available: true` and a ranked `slots` list
- **AND** no calendar event is created as a side effect

#### Scenario: Find-time fails open with available=false when free/busy is unreachable
- **WHEN** `POST /api/calendar/workspace/find-time` is called and the MCP call to `calendar_find_free_slots` raises any error (butler unreachable, free/busy provider error, connection failure)
- **THEN** the endpoint returns HTTP 200 with `available: false`, a human-readable `reason` string, and an empty `slots` list
- **AND** the endpoint MUST NOT return HTTP 500 or propagate the exception to the caller
- **BECAUSE** the "Find time" panel MUST remain renderable even when the butler or its calendar provider is temporarily unreachable; the UI SHALL show "free/busy unavailable" rather than a broken panel

#### Scenario: Workspace mutations
- **WHEN** user-event or butler-event mutation endpoints are called
- **THEN** the request is proxied to the owning butler via MCP tool calls (`calendar_create_event`, `calendar_update_event`, etc.)
- **AND** projection freshness metadata is fetched after mutation and included in the response
