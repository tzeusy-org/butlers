## ADDED Requirements

### Requirement: Ingestion Timeline Status Column
The ingestion timeline table at `/butlers/ingestion?tab=timeline` SHALL display a Status column indicating the outcome of each event.

#### Scenario: Status column rendering
- **WHEN** the timeline table renders
- **THEN** a "Status" column SHALL appear after the "Sender" column
- **AND** each row SHALL display a color-coded status badge

#### Scenario: Status badge colors
- **WHEN** a status badge is rendered
- **THEN** `ingested` SHALL render as a green badge, `filtered` as a gray badge, `error` as a red badge, `replay_pending` as a blue badge, `replay_complete` as a green-outline badge, and `replay_failed` as a red-outline badge

#### Scenario: Filter reason tooltip
- **WHEN** the status is `filtered` or `error`
- **THEN** hovering over the status badge SHALL display a tooltip with the `filter_reason` value
- **AND** for `error` status, the tooltip SHALL also include the `error_detail` if available

### Requirement: Ingestion Timeline Action Column
The ingestion timeline table SHALL display an Action column with a Replay button for replayable events.

#### Scenario: Action column rendering
- **WHEN** the timeline table renders
- **THEN** an "Action" column SHALL appear as the last column

#### Scenario: Replay button for filtered events
- **WHEN** a row has status `filtered` or `error`
- **THEN** the Action column SHALL display a "Replay" button
- **AND** clicking the button SHALL call `POST /api/ingestion/events/{id}/replay`

#### Scenario: Replay button for replay_failed events
- **WHEN** a row has status `replay_failed`
- **THEN** the Action column SHALL display a "Retry" button
- **AND** clicking the button SHALL call `POST /api/ingestion/events/{id}/replay`

#### Scenario: Replay button disabled during pending
- **WHEN** a row has status `replay_pending`
- **THEN** the Action column SHALL display a spinner or "Pending..." label
- **AND** no button SHALL be clickable

#### Scenario: No action for ingested events
- **WHEN** a row has status `ingested` or `replay_complete`
- **THEN** the Action column SHALL be empty (no button rendered)

#### Scenario: Optimistic UI update on replay
- **WHEN** the operator clicks the Replay button and the API returns 200
- **THEN** the row's status badge SHALL immediately update to `replay_pending` (optimistic update)
- **AND** the Replay button SHALL be replaced with a spinner

#### Scenario: Error handling on replay
- **WHEN** the replay API returns 409 or another error
- **THEN** a toast notification SHALL display the error message
- **AND** the row's status SHALL remain unchanged

### Requirement: Ingestion Timeline Status Filter
The ingestion timeline filter bar SHALL include a Status filter dropdown.

#### Scenario: Status filter options
- **WHEN** the operator interacts with the Status filter
- **THEN** the dropdown SHALL contain options: All (default), Ingested, Filtered, Error, Replay Pending, Replay Complete, Replay Failed
- **AND** selecting a status SHALL pass `status=<value>` to the API query and reset pagination

### Requirement: Ingestion Timeline Unified Data Source
The timeline table SHALL display events from both `shared.ingestion_events` and `connectors.filtered_events` in a single merged view.

#### Scenario: Unified ordering
- **WHEN** the timeline loads
- **THEN** events from both sources SHALL be interleaved by `received_at DESC`
- **AND** the operator SHALL not be able to distinguish the source table visually (unified UX)

#### Scenario: Column mapping for filtered events
- **WHEN** a filtered event row is displayed
- **THEN** the Request ID column SHALL show the `connectors.filtered_events.id`
- **AND** the Channel column SHALL show `source_channel`
- **AND** the Sender column SHALL show `sender_identity`
- **AND** the Tier column SHALL be empty or show "—" (filtered events have no ingestion tier)
- **AND** the Tokens and Cost columns SHALL be empty or show "—" (no sessions spawned)
- **AND** the row SHALL NOT be expandable (no session flamegraph for filtered events)
