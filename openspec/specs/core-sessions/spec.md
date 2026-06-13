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

### Requirement: Routed Sessions Carry Ingestion Event Identifier

A session created with `trigger_source = "route"` whose originating message produced a row in `public.ingestion_events` SHALL persist that ingestion event UUID in its `ingestion_event_id` column, since it is the join key chronicler and other downstream readers use to attribute the session back to a sender, channel, contact, or upstream message.

#### Scenario: Routed session links back to ingestion event

- **WHEN** a switchboard `route.execute` accepts a message and the
  background task spawns a sub-butler session
- **THEN** the resulting `{schema}.sessions` row SHALL have
  `ingestion_event_id` equal to the `public.ingestion_events.id`
  that the switchboard ingest inserted in the same transaction as
  `public.message_inbox`
- **AND** `request_id` and `ingestion_event_id` SHALL be the same
  UUID7 value (this is an invariant established by
  `roster/switchboard/tools/ingestion/ingest.py`)

#### Scenario: Non-routed sessions need not carry ingestion event id

- **WHEN** a session is created with any non-route trigger source
  (`tick`, `schedule:*`, `trigger`, `external`, `dashboard`,
  `healing`, `qa`, `deadline:*`)
- **THEN** the session row's `ingestion_event_id` MAY be NULL
- **AND** chronicler title resolution SHALL NOT depend on this
  column for non-route sessions

#### Scenario: Backfill safety — request_id is the canonical recovery key

- **WHEN** a deployment carries historical route sessions with
  `ingestion_event_id IS NULL` from a prior daemon version
- **THEN** the backfill path SHALL use the session's `request_id`
  cast to UUID as the recovery value, gated by an `EXISTS` check
  against `public.ingestion_events.id`, so that no synthetic UUIDs
  are introduced
