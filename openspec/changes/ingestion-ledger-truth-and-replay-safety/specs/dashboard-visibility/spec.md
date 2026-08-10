## MODIFIED Requirements

### Requirement: Ingestion Timeline Action Column
The ingestion timeline table SHALL display an Action column with a Replay
button only for events that are both status-replayable and server-confirmed
replay-safe.

#### Scenario: Action column rendering
- **WHEN** the timeline table renders
- **THEN** an "Action" column SHALL appear as the last column

#### Scenario: Replay button for safe filtered events
- **WHEN** a row has status `filtered` or `error` and server-derived
  replay-policy evidence is safe
- **THEN** the Action column SHALL display a "Replay" button
- **AND** clicking the button SHALL call `POST /api/ingestion/events/{id}/replay`

#### Scenario: Replay button for safe replay_failed events
- **WHEN** a row has status `replay_failed` and server-derived replay-policy
  evidence is safe
- **THEN** the Action column SHALL display a "Retry" button
- **AND** clicking the button SHALL call `POST /api/ingestion/events/{id}/replay`

#### Scenario: Unsafe event action is non-actionable
- **WHEN** a row has a status that could otherwise be replayed but its
  server-derived replay policy is unsafe or unresolved
- **THEN** the Action column SHALL not expose a clickable replay control
- **AND** the UI SHALL provide a concise non-sensitive explanation

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
