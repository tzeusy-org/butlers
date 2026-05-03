## ADDED Requirements

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
