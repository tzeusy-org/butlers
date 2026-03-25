## ADDED Requirements

### Requirement: Correction Audit Linkage
The session system SHALL support querying which corrections were performed by a given session and which corrections target a given session. This linkage is stored entirely in the `corrections` table (FK references to `sessions.id`) and does NOT require schema changes to the `sessions` table itself.

#### Scenario: Query corrections performed by a session
- **WHEN** `corrections_by_session(pool, correcting_session_id)` is called
- **THEN** all correction records where `correcting_session_id` matches SHALL be returned, ordered by `created_at`

#### Scenario: Query corrections targeting a session
- **WHEN** `corrections_for_session(pool, target_session_id)` is called
- **THEN** all correction records where `target_session_id` matches SHALL be returned, ordered by `created_at`

#### Scenario: Session detail includes correction count
- **WHEN** `sessions_get(pool, session_id)` is called and the session has associated corrections
- **THEN** the response SHALL include `correction_count` indicating how many corrections target this session

### Requirement: Correction Trigger Source
Sessions that are performing corrections SHALL use the existing trigger source mechanism. The `trigger_source` for a correction session is determined by how it was initiated (e.g., `external`, `route`, `trigger`) — there is no special `correction` trigger source. The correction context is captured in the `corrections` table, not in session metadata.

#### Scenario: Correction session uses standard trigger source
- **WHEN** a user initiates a correction via an external message
- **THEN** the correcting session's `trigger_source` is `external` (or `route` if routed)
- **AND** the correction linkage is recorded in the `corrections` table, not in the session's trigger_source
