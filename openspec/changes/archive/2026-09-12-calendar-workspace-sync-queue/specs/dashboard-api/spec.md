## ADDED Requirements

### Requirement: Canonical Calendar Workspace Operational Ownership

Aggregate Calendar Workspace source/read and global-sync surfaces SHALL select one deterministic operational owner for duplicate provider-source ledger rows while preserving the underlying cross-schema fan-out ledger unchanged.

#### Scenario: Fresh core-capable owner wins a stale non-core duplicate

- **WHEN** the same provider `source_key` is returned from multiple schemas and one copy is disabled or lacks the calendar `core` tool group while another enabled copy is core-capable
- **THEN** `GET /api/calendar/workspace` source freshness and `GET /api/calendar/workspace/meta` connected/writable source data use the enabled core-capable copy
- **AND** the selection prefers the latest successful/sync timestamp among otherwise eligible copies with deterministic schema/id tie-breaking
- **AND** the router does not delete, update, or omit the raw duplicate rows from the versioned read-model boundary

#### Scenario: Global sync batches by canonical owner

- **WHEN** `POST /api/calendar/workspace/sync` is called with `all=true`
- **THEN** the API selects canonical enabled provider rows and groups them by their selected `db_butler`
- **AND** it sends at most one owner-wide queued force-sync request without `calendar_id` to each selected owner
- **AND** it does not invoke non-core duplicate owners or issue one provider request per cross-schema duplicate

### Requirement: Queued Calendar Workspace Sync Acknowledgement

Calendar Workspace manual sync SHALL acknowledge durable queued execution separately from provider completion.

#### Scenario: Global or source sync is accepted without waiting for provider I/O

- **WHEN** `POST /api/calendar/workspace/sync` accepts one or more queued `calendar_force_sync` MCP acknowledgements
- **THEN** it returns HTTP `202 Accepted` with per-owner/per-source targets whose `status` is `queued`, request correlation, and coalescing information
- **AND** `triggered_count` counts accepted queued targets
- **AND** `full=true` is forwarded as queued recovery intent but the response does not claim that recovery has already completed

#### Scenario: Queue acknowledgement surfaces immediate dispatch failure honestly

- **WHEN** a selected owner cannot be reached or rejects queued force-sync acceptance
- **THEN** its target is returned with `status="failed"` and an actionable error while independently accepted targets remain visible
- **AND** the API does not report a provider sync as completed merely because another owner accepted a command

#### Scenario: Completion remains observable through existing calendar telemetry

- **WHEN** a client receives a queued workspace-sync acknowledgement
- **THEN** it uses source freshness and calendar action-log/audit status to observe the eventual `applied` or `failed` outcome
- **AND** the frontend describes the acknowledgement as queued rather than completed or recovered
