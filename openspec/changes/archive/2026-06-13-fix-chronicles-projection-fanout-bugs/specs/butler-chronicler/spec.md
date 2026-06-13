## ADDED Requirements

### Requirement: Cross-Schema Fan-Out Collapse

Chronicler projection adapters SHALL collapse a single upstream event mirrored across multiple butler schemas into exactly one chronicler episode by deriving the episode's `source_ref` from the upstream identifier (not the per-schema row id), so the persistent `(source_name, source_ref)` upsert key naturally dedups the fan-out independent of which schema the row was read from first.

#### Scenario: Calendar event present in multiple butler schemas

- **WHEN** the same Google Calendar event appears in three or more
  `{schema}.calendar_event_instances` tables (because three or more
  butlers have the calendar module enabled)
- **THEN** `chronicler.episodes` SHALL contain exactly one row for
  that upstream event
- **AND** the episode's `source_ref` SHALL be derived from
  `origin_instance_ref` (the upstream Google Calendar instance ID),
  NOT from the per-schema `calendar_event_instances.id`

#### Scenario: Same upstream instance synced under multiple event_id values within one schema

- **WHEN** a butler schema's calendar sync inserts the same
  `origin_instance_ref` under two different `calendar_events.id`
  values (the unique key is `(event_id, origin_instance_ref)`, so
  this is permitted)
- **THEN** the chronicler projection SHALL still collapse them into
  a single episode by keying on `origin_instance_ref` alone

#### Scenario: In-run dedup is an optimisation, not the correctness layer

- **WHEN** the projection adapter iterates butler schemas in a
  single run
- **THEN** the `seen_origin` short-circuit SHALL be keyed on the
  same upstream identifier the persistent `source_ref` is derived
  from
- **AND** removing the in-run guard SHALL leave correctness intact
  (the persistent upsert key is the source of truth); the in-run
  guard is purely an efficiency hedge against redundant writes

### Requirement: Episode Title Resolution Order

Projection adapters that compose a human-readable episode title SHALL favour the most-specific available signal, SHALL NOT discard evidence already present in the source row when a fallback is taken, and SHALL document the resolution chain in the adapter docstring so it is deterministic and exhaustive.

#### Scenario: Spotify session prefers track names over endpoint identity

- **WHEN** a Spotify listening session has neither a `context_name`
  nor a `context_uri`, but `track_names` is non-empty
- **THEN** the resulting episode title SHALL surface the track
  names (e.g. `Listened to Beneath the Blazing Sky`)
- **AND** the title SHALL NOT degrade to `Spotify session
  ({endpoint_identity})` while track names are still available

#### Scenario: Routed conversation prefers resolved contact over channel

- **WHEN** a routed `core.sessions` row has a non-NULL
  `ingestion_event_id` AND the `(source_channel,
  source_sender_identity)` pair joins to a known
  `public.contacts.name`
- **THEN** the episode title SHALL be `Conversation with
  {display_name}`

#### Scenario: Routed conversation falls back to channel when contact is unknown

- **WHEN** the same row's contact is unresolved but the channel is
  known
- **THEN** the title SHALL be `Conversation via {channel}` (e.g.
  `Conversation via telegram_user_client`)

#### Scenario: Routed conversation only emits "unknown channel" when no signal is recoverable

- **WHEN** the routed session has no `ingestion_event_id` AND no
  resolvable channel
- **THEN** the title MAY fall through to `Conversation via unknown
  channel`
- **AND** every routed session that DID arrive through a
  switchboard ingest SHALL carry the ingestion event UUID so this
  catch-all is reserved for genuinely identity-less invocations
  (manual SQL, dashboard simulation, replay tooling)

### Requirement: Day-Precision Episodes Anchor to Latest Observation

Projection adapters whose source exposes only daily aggregates SHALL produce episodes with `precision = "day"` AND SHALL anchor the episode's end-of-day bound to the most recent observation timestamp inside the calendar day rather than always parking the bar at midnight UTC; when the daily aggregate exceeds the elapsed time since midnight, the episode start MAY clamp to start-of-day as documented best-effort behaviour for daily-aggregate sources.

#### Scenario: Steam playtime row produces a non-midnight episode

- **WHEN** a `connectors.steam_play_history` row has
  `playtime_minutes = 65`, `date = 2026-05-01`, and
  `recorded_at = 2026-05-01 16:24:00 UTC`
- **THEN** the resulting `chronicler.episodes` row SHALL have
  `start_at` near `15:19 UTC` and `end_at` near `16:24 UTC`
- **AND** the episode's `precision` field SHALL be `"day"`

#### Scenario: Daily playtime exceeds elapsed time

- **WHEN** `playtime_minutes` exceeds the wall-clock duration from
  start-of-day to the latest observation timestamp
- **THEN** the episode's `start_at` MAY clamp to start-of-day UTC
- **AND** the adapter SHALL log this clamp via its standard
  warnings channel so operators can detect connector polling gaps
  and accumulated overnight play
