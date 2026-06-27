# Chronicler Butler

## Purpose

The Chronicler butler is a domain butler that reconstructs past time from already-captured evidence across the Butlers ecosystem. It owns retrospective time reconstruction with point events, overlapping episodes, correction overlays, and source projection adapters. Chronicler reads from approved migration-tracked source surfaces, writes only to its own schema, preserves source provenance, precision, and uncertainty on every row, and never invokes an LLM per ingestion event. Per RFC 0014, Chronicler does not plan, schedule, dispatch, ingest externally, or notify.

## Requirements

### Requirement: Butler Identity and Wire Contract

The Chronicler butler SHALL expose a stable identity as a domain butler (not
a staffer). Identity details that may drift (model, cron minutes, module list,
concurrency caps) are owned by `roster/chronicler/butler.toml` per
`about/heart-and-soul/vision.md` Rule 5 and are NOT mirrored here.

#### Scenario: Stable wire identity

- **WHEN** the butler is loaded from `roster/chronicler/butler.toml`
- **THEN** it SHALL have `name = "chronicler"`
- **AND** its database binding SHALL be `[butler.db]` with `name = "butlers"` and `schema = "chronicler"`
- **AND** its runtime role SHALL resolve to `butler_chronicler_rw`

#### Scenario: Domain butler type, not staffer

- **WHEN** the daemon classifies the Chronicler butler
- **THEN** it SHALL be a butler-typed agent eligible for routing from the
  Switchboard as a retrospective-time specialist
- **AND** it SHALL NOT be a staffer (it has no ingress routing authority,
  no connector ownership, and no infrastructure role)

### Requirement: Retrospective-Only Scope

The Chronicler butler SHALL reconstruct past time from already-captured
evidence and SHALL NOT plan, schedule, dispatch, ingest externally, or notify.

#### Scenario: No ingestion ownership

- **WHEN** a passive timestamped event arrives (Spotify playback change,
  Steam game start, OwnTracks point, Home Assistant state change, Google
  Health reading)
- **THEN** Chronicler SHALL NOT receive the event directly
- **AND** the event SHALL route to its owning domain butler per existing
  Switchboard rules
- **AND** Chronicler's projection of that event SHALL run asynchronously
  on schedule, reading only from the owning domain's approved read surface

#### Scenario: No scheduling or planning

- **WHEN** a user request asks "schedule X" or "plan Y"
- **THEN** the Switchboard SHALL NOT route the request to Chronicler
- **AND** Chronicler's tool surface SHALL NOT include scheduling tools

### Requirement: Storage Shape

The Chronicler schema SHALL contain point events, episodes,
**episode-entity links**, episode-event links, overrides, projection
checkpoints, source adapter state, and idempotency keys.

#### Scenario: Point events and episodes separated

- **WHEN** a source adapter projects evidence
- **THEN** instantaneous evidence SHALL be written to `point_events`
- **AND** span-shaped evidence SHALL be written to `episodes`
- **AND** every row SHALL carry `source_name`, `source_ref`, `precision`,
  `privacy`, `retention_days`, and optional `tombstone_at`

#### Scenario: Overlapping episodes permitted

- **WHEN** two episodes from different sources cover overlapping time
- **THEN** both SHALL be stored
- **AND** neither SHALL be merged or discarded
- **AND** overlap queries SHALL return both

#### Scenario: Idempotent replay

- **WHEN** a source adapter re-projects the same source record
- **THEN** the stored row SHALL be updated in place via its
  `(source_name, source_ref)` idempotency key
- **AND** no duplicate row SHALL be created
- **AND** episode-entity links for the replayed episode SHALL be
  replaced atomically (DELETE-then-INSERT within the same transaction)
  so attendee removals and additions on the upstream source propagate
  on the next adapter run

#### Scenario: Episode-entity link cardinality

- **WHEN** a multi-participant event (such as a Google Calendar meeting)
  is projected as an episode
- **THEN** exactly ONE row SHALL be inserted into `episodes` for the
  upstream event, keyed by `(source_name, source_ref)` as today
- **AND** every participant entity that the upstream source has already
  resolved SHALL appear as a row in `episode_entities` referencing the
  episode's `id` and the participant's `entity_id`
- **AND** the participant's role SHALL be recorded as one of `'owner'`,
  `'organizer'`, or `'participant'`
- **AND** unresolved attendees (no entity match in `public.entities`)
  SHALL NOT create rows; they remain visible in the source-side
  payload but not in the chronicler join

#### Scenario: Episode entity link table contract

- **WHEN** the `chronicler.episode_entities` table is created
- **THEN** its primary key SHALL be `(episode_id, entity_id)` to
  enforce per-attendee idempotency
- **AND** `episode_id` SHALL have an ON DELETE CASCADE reference to
  `chronicler.episodes(id)` so episode tombstones cascade to the join
- **AND** the table SHALL NOT enforce a foreign key on `entity_id`
  against `public.entities(id)` (matching the existing
  `chronicler.episodes.entity_id` convention so chronicler boots in
  deployments where the relationship butler schema is not yet wired)

#### Scenario: Derived owner column preserved during transition

- **WHEN** an episode has at least one `episode_entities` row with
  `role='owner'`
- **THEN** the canonical `chronicler.episodes.entity_id` column SHALL
  hold that same owner UUID
- **AND** the adapter SHALL write both the column and the join in the
  same transaction so they cannot drift
- **AND** after the cleanup follow-up migration the
  `episodes.entity_id` column SHALL be dropped, leaving the join table
  as the sole entity surface

#### Scenario: Missing upstream join table degrades gracefully

- **WHEN** a calendar adapter runs against a butler schema whose
  upstream `calendar_event_entities` table is absent (calendar module
  not installed on this deployment)
- **THEN** the adapter SHALL emit a debug-level log
- **AND** it SHALL write only the owner row (if resolvable) into
  `episode_entities`
- **AND** it SHALL NOT raise

#### Scenario: Multi-entity does NOT cross the LLM-free boundary

- **WHEN** the calendar adapter resolves participants
- **THEN** it SHALL read the entity set already resolved by the calendar
  module's `_upsert_event_entities` write path
- **AND** it SHALL NOT invoke an LLM for attendee classification or
  entity resolution
- **AND** the no-per-event-LLM invariant from RFC 0014 §D5 SHALL hold

### Requirement: Owner-Only Adapter Entity Attribution

Projection adapters whose source is owner-driven self-tracking data and
carries no distinct participant set (focus, sessions, spotify, steam,
meals, owntracks, reading, google_health) SHALL attribute every projected
row to the owner entity, mirroring the calendar adapter's entity surface
without invoking attendee resolution.

#### Scenario: Owner entity resolved once per run

- **WHEN** an owner-only adapter's `project()` executes
- **THEN** the owner `entity_id` SHALL be resolved exactly once via
  `public.entities WHERE 'owner' = ANY(roles)` (not per row)
- **AND** the resolved `entity_id` SHALL be stamped on every upserted
  episode and point-event row
- **AND** a single `episode_entities` row with `role='owner'` SHALL be
  written per episode within the same transaction as the episode upsert

#### Scenario: Unresolved owner degrades to NULL

- **WHEN** no owner entity exists, the owner entity's `entity_id` is
  NULL, or `public.entities` is absent
- **THEN** the adapter SHALL write `entity_id = NULL` and SHALL NOT write
  an `episode_entities` row
- **AND** it SHALL log at DEBUG level and SHALL NOT raise

#### Scenario: Backfill enumerates all owner-only source names

- **WHEN** historical owner-only rows are backfilled with the owner entity
- **THEN** the backfill SHALL enumerate every owner-only `source_name`
  including the distinct health values `health.steps` and
  `health.heart_rate` separately from `google_health.measurements`
- **AND** it SHALL resolve the owner via `public.entities` rather than the
  calendar-specific `google_accounts` path

### Requirement: Home Assistant Presence Person Attribution

The Home Assistant presence adapter SHALL attribute each `presence_episode`
to the resident whose presence is tracked (the `person.*` entity), not the
schema owner, resolving the entity per tracked person without an LLM call.

#### Scenario: Person entity resolved per tracked person

- **WHEN** the adapter rolls up `home` state runs for a `person.*` entity
  into a `presence_episode`
- **THEN** the HA entity id SHALL be resolved to a graph `entity_id` via
  the `connectors.home_assistant_persons.entity_id` column (a direct FK to
  `public.entities` added in migration core_132, no longer joined through
  `public.contacts`), resolved once per tracked person (not per row)
- **AND** the resolved `entity_id` SHALL be stamped on the episode
- **AND** an `episode_entities` row SHALL be written for the resolved
  person with `role='owner'` (the subject of the episode)

#### Scenario: Unmapped resident degrades to NULL

- **WHEN** the `connectors.home_assistant_persons` mapping is absent, has
  no row for the `person.*` entity, or the mapped contact has a NULL
  `entity_id`
- **THEN** the presence episode SHALL be written with `entity_id = NULL`
  and no `episode_entities` row
- **AND** the adapter SHALL log at DEBUG level and SHALL NOT raise

### Requirement: Correction Overlay Model

User corrections SHALL layer on top of canonical projections via an
`overrides` table without mutating or deleting the canonical rows.

#### Scenario: Correction preserves canonical row

- **WHEN** a user submits a correction for an episode
- **THEN** the canonical episode row SHALL remain unchanged
- **AND** an `overrides` row SHALL reference the canonical row with the
  corrected fields
- **AND** the corrected view SHALL surface the effective values

#### Scenario: Later override wins

- **WHEN** a user submits a second correction for the same episode
- **THEN** the later override SHALL apply on top of the earlier override
- **AND** correction history SHALL expose both via
  `GET /api/chronicler/episodes/{id}/corrections`

### Requirement: Source Compatibility Declarations

Every source adapter SHALL declare its chronicler compatibility status
before projection runs against it.

#### Scenario: Initial supported sources

- **WHEN** Chronicler starts for the first time
- **THEN** `core.sessions` SHALL be declared `supported`
- **AND** `google_calendar.completed` SHALL be declared `supported`
- **AND** `spotify.session_summary` SHALL be declared `deferred` unless a
  durable evidence surface exists
- **AND** `google_health.*` SHALL be declared `deferred`

#### Scenario: Optional schema guard

- **WHEN** a source adapter's optional schema is missing on the current
  deployment
- **THEN** the adapter SHALL mark its state as inactive with the missing-
  schema reason
- **AND** SHALL NOT raise an unhandled exception
- **AND** the missing-schema state SHALL be visible via
  `source_adapter_state`

### Requirement: No Per-Event LLM Invocation

Chronicler projection adapters SHALL NOT invoke an LLM on a per-event basis.

#### Scenario: Projection adapter path forbids LLM

- **WHEN** a projection adapter runs under its scheduled job
- **THEN** it SHALL NOT make any LLM call
- **AND** guardrail tests SHALL assert the no-LLM invariant

#### Scenario: Bounded Tier 2 interpretation

- **WHEN** a Tier 2 interpretation path runs (day-close, drilldown,
  ambiguity resolution, correction assistance)
- **THEN** its input bundle SHALL be bounded to a fixed maximum size
- **AND** the call SHALL preserve provenance in output
- **AND** the call SHALL be a single LLM invocation, not a fan-out over
  events

#### Scenario: Day-close prose stays human-readable

- **WHEN** the scheduled day-close interpretation sends a human-facing summary
- **THEN** timestamps SHALL be described in the owner's configured timezone
- **AND** raw source references, connector row IDs, truncation flags, and other
  machine-only provenance fields SHALL NOT be printed by default
- **AND** machine provenance SHALL remain available to the cache/staleness path
  from tool-call results rather than depending on prose citations

### Requirement: API Surface Distinct from /api/timeline

Chronicler SHALL expose its read and correction API under `/api/chronicler/*`.
The existing `/api/timeline` route SHALL remain the operational cross-butler
session/notification stream.

#### Scenario: Distinct namespace

- **WHEN** a request arrives at `/api/timeline`
- **THEN** it SHALL continue to return aggregated session + notification
  events across all butlers
- **AND** Chronicler SHALL NOT claim or modify this route

#### Scenario: Chronicler endpoints

- **WHEN** a request arrives at `/api/chronicler/events`, `/api/chronicler/episodes`,
  `/api/chronicler/episodes/{id}`, `/api/chronicler/episodes/{id}/events`,
  or `/api/chronicler/episodes/{id}/corrections`
- **THEN** the Chronicler-owned router SHALL handle it
- **AND** responses SHALL carry provenance fields (source_name, source_ref,
  precision, privacy)

### Requirement: Switchboard Routing Boundary

Switchboard routing SHALL route only explicit retrospective time-review
requests to Chronicler.

#### Scenario: Explicit retrospective routes to Chronicler

- **WHEN** a user asks "what did I do yesterday?", "how much time did I
  spend listening to music?", "when did I last go running?"
- **THEN** the Switchboard SHALL route the message to Chronicler

#### Scenario: Domain-next-action stays with owning butler

- **WHEN** a user asks "recommend me music"
- **THEN** the message SHALL route to Lifestyle, NOT Chronicler

#### Scenario: Scheduling intent stays with calendar-capable butler

- **WHEN** a user asks "schedule a meeting"
- **THEN** the message SHALL route to a calendar-capable butler, NOT
  Chronicler

#### Scenario: Passive event routes to owning butler

- **WHEN** a Spotify/Steam/OwnTracks/Google-Health event is ingested
- **THEN** it SHALL route to its owning domain butler, NOT Chronicler

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

## Source References

- Non-Negotiable Rule 1 (single-owner data sovereignty)
- Non-Negotiable Rule 3 (MCP-only inter-butler communication)
- Non-Negotiable Rule 4 (LLM reasoning is ephemeral)
- Non-Negotiable Rule 5 (butler.toml is identity; specs describe behavior)
- RFC 0006 (database schema isolation)
- RFC 0009 (situational context bus — Chronicler reads but does not write)
- RFC 0010 (cross-butler read surface precedent)
- RFC 0014 (Chronicler retrospective time butler — full normative contract)
  §D1 Storage Shape, §D3 Adapter Contract, §D5 Sparse Interpretation
  Guardrails
