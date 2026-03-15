## MODIFIED Requirements

### Requirement: Ingestion Event List (Paginated)
Return a unified stream of all ingestion events (ingested, filtered, errored) ordered by `received_at DESC` with limit/offset pagination, with optional filtering.

#### Scenario: Paginated list
- **WHEN** `ingestion_events_list(pool, limit=20, offset=0)` is called
- **THEN** up to 20 rows are returned, most recent first
- **AND** the result SHALL include events from both `shared.ingestion_events` and `connectors.filtered_events` merged by `received_at DESC`

#### Scenario: Filtered by source channel
- **WHEN** `ingestion_events_list(pool, source_channel="email")` is called
- **THEN** only events with `source_channel = 'email'` are returned from both tables

#### Scenario: Response includes status field
- **WHEN** the unified list is returned
- **THEN** each row SHALL include a `status` field: `ingested` for rows from `shared.ingestion_events`, or the `status` column value for rows from `connectors.filtered_events` (`filtered`, `error`, `replay_pending`, `replay_complete`, `replay_failed`)

#### Scenario: Response includes filter_reason field
- **WHEN** the unified list is returned
- **THEN** each row SHALL include a `filter_reason` field: `null` for ingested events, or the `filter_reason` column value for filtered/errored events

#### Scenario: Filtered by status
- **WHEN** `ingestion_events_list(pool, status="filtered")` is called
- **THEN** only events with the matching status are returned
- **AND** `status="ingested"` queries only `shared.ingestion_events`
- **AND** all other status values query only `connectors.filtered_events`
