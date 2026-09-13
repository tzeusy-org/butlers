# Chronicler Source Compatibility

## Purpose

Defines the contract future timestamped sources must provide so Chronicler can
project lived-time evidence without bespoke LLM interpretation or ad hoc
cross-schema access.

## Requirements

### Requirement: Timestamped Source Compatibility Declaration

Every new timestamped source proposal SHALL declare Chronicler compatibility or
explicitly state that it is not time-bearing.

#### Scenario: Time-bearing source declares compatibility

- **WHEN** a future source such as Fitbit proposes workout, sleep, heart-rate, or step evidence
- **THEN** its proposal/spec SHALL include a `chronicler_compatibility` section
- **AND** that section SHALL define source name, source kind, supported outputs, time fields, boundary semantics, source reference format, taxonomy mapping, confidence semantics, privacy tier, idempotency key, and projection path

#### Scenario: Non-time-bearing source opts out

- **WHEN** a proposed source carries no meaningful observed or effective time evidence
- **THEN** its proposal/spec SHALL state that it is not time-bearing
- **AND** no Chronicler adapter SHALL be required for that source

### Requirement: Compatibility Fields

The Chronicler compatibility declaration SHALL provide the fields Chronicler
needs for deterministic projection.

#### Scenario: Required fields present

- **WHEN** a compatibility declaration is reviewed
- **THEN** it SHALL include:
  - `source_name`
  - `source_kind`
  - `supported_outputs`
  - `time_fields`
  - `boundary_semantics`
  - `source_ref_format`
  - `taxonomy_mapping`
  - `confidence_semantics`
  - `privacy_tier`
  - `idempotency_key`
  - `projection_path`

#### Scenario: Projection path selected

- **WHEN** a compatibility declaration sets `projection_path`
- **THEN** the value SHALL be either `canonical_evidence` or `chronicler_adapter`
- **AND** `chronicler_adapter` SHALL mean Chronicler owns a deterministic adapter reading an approved source surface
- **AND** `canonical_evidence` SHALL NOT be used until a separate RFC/spec defines shared table ownership, write authority, ACLs, provenance, retention, and migration contract

#### Scenario: Concurrent source proposal predates Chronicler acceptance

- **WHEN** a timestamped source proposal predates acceptance of RFC 0014
- **THEN** that source SHALL be listed for compatibility retrofit or explicit deferral before Chronicler claims it as an adapter source

### Requirement: Privacy and Retention Declaration

Compatibility declarations SHALL specify how source privacy and retention apply
to Chronicler projections.

#### Scenario: Sensitive source includes retention policy

- **WHEN** a source declares `privacy_tier = sensitive`
- **THEN** the declaration SHALL specify raw evidence retention, projected evidence retention, allowed precision after source purge, and tombstone behavior

#### Scenario: Source purge behavior defined

- **WHEN** source records may expire or be deleted
- **THEN** the compatibility declaration SHALL define whether Chronicler deletes, tombstones, or lower-precision-retains derived records

### Requirement: Deterministic Projection Compatibility

Compatible sources SHALL expose enough structured evidence for routine
projection without LLM interpretation.

#### Scenario: Source supports episode projection

- **WHEN** a source declares `supported_outputs = episodes` or `both`
- **THEN** it SHALL expose started-at semantics, ended-at semantics or closure policy, boundary confidence, and stable idempotency keys

#### Scenario: Source supports event projection

- **WHEN** a source declares `supported_outputs = events` or `both`
- **THEN** it SHALL expose observed/effective timestamp semantics, event type mapping, source reference, privacy tier, and stable idempotency keys

#### Scenario: Source lacks deterministic evidence

- **WHEN** a source cannot expose enough structured evidence for deterministic projection
- **THEN** it SHALL NOT be marked Chronicler-compatible
- **AND** any future Chronicler support SHALL require a separate source contract change

### Requirement: OwnTracks SSID Presence Compatibility Declaration

The OwnTracks Wi-Fi presence source SHALL use this compatibility declaration:

- `source_name`: `owntracks.ssid_presence`
- `source_kind`: structured OwnTracks location evidence
- `supported_outputs`: episodes
- `time_fields`: `connectors.owntracks_points.ts`, with `recorded_at` as the
  existing clock-skew fallback
- `boundary_semantics`: first and last observations in a two-or-more-point,
  same-endpoint, same-owner-mapped-SSID run; SSID changes, missing or unlabelled
  SSIDs, and gaps over 60 minutes close the run
- `source_ref_format`:
  `connectors.owntracks_points:ssid:{endpoint}:{ssid_sha256_prefix}:{start_epoch}`
- `taxonomy_mapping`: home `presence_episode` to Rest; work
  `occupation_presence_episode` to Work
- `confidence_semantics`: medium from one strong owner-labelled structured
  signal
- `privacy_tier`: normal for the derived episode; the sensitive raw SSID stays
  in source evidence and owner state, not the projected payload or source ref
- `idempotency_key`: `(source_name, source_ref)`
- `projection_path`: `chronicler_adapter`

#### Scenario: Declaration is reflected in the source registry

- **WHEN** the Chronicler source registry is seeded
- **THEN** `owntracks.ssid_presence` SHALL be `supported`
- **AND** its read surface SHALL be
  `connectors.owntracks_points (raw_payload.SSID)`
- **AND** its source registration SHALL be maintained in
  `chronicler.source_adapter_state`
- **AND** its projection checkpoint SHALL be maintained in
  `chronicler.projection_checkpoints`

#### Scenario: Equal-timestamp OwnTracks rows cross a batch boundary

- **WHEN** more OwnTracks SSID evidence rows share one `ts` value than fit in
  a projection batch
- **THEN** the adapter SHALL order them by `(ts, id)` and checkpoint both the
  timestamp and stable source UUID
- **AND** every source row SHALL be incorporated exactly once across
  successful batch runs
- **AND** replay SHALL remain idempotent through the stable
  `(source_name, source_ref)` projection key

#### Scenario: Timestamp-only checkpoint upgrades safely

- **WHEN** an existing `owntracks.ssid_presence` checkpoint has a timestamp
  watermark but no UUID tie-breaker
- **THEN** the adapter SHALL perform one deterministic replay from the retained
  source evidence and rebuild its open-span carryover from scratch
- **AND** that replay SHALL remain batch-limited and persist the composite
  cursor after each successful page; presence of the composite cursor marks
  the timestamp-only upgrade complete
- **AND** it SHALL persist a timestamp-plus-UUID checkpoint for subsequent
  deterministic tuple-ordered runs
- **AND** the replay SHALL update canonical episodes through stable source refs
  rather than create duplicates or double-count legacy carryover
- **AND** a mismatched or malformed composite cursor SHALL fail safe by
  restarting the bounded replay rather than skipping evidence
- **AND** no migration of existing checkpoint rows SHALL be required

### Requirement: Spotify Spoken Session Compatibility Declaration

Spotify spoken-session evidence SHALL declare deterministic future Chronicler
compatibility while this capture-only change defers registration, projection,
and all Chronicler user surfaces.

#### Scenario: Declaration defines a future deterministic source

- **WHEN** the Spotify spoken-session source is reviewed
- **THEN** its declaration SHALL specify:
  - `source_name`: `spotify.spoken_session`
  - `source_kind`: structured Spotify current-playback episode evidence
  - `supported_outputs`: episodes (planned, not implemented by this change)
  - `time_fields`: `started_at` and `ended_at` from connector observations
  - `boundary_semantics`: item switch closes immediately; pause closes after
    configured idle drain; a replay after closure starts a new session
  - `source_ref_format`: `connectors.spotify_spoken_sessions:<idempotency_key>`
  - `taxonomy_mapping`: planned `spoken_episode` activity episode, preserving
    `podcast`, `audiobook`, or `unknown_episode` as source kind
  - `confidence_semantics`: medium from one explicit Spotify playback signal
  - `privacy_tier`: normal bounded metadata with no transcript or raw payload
  - `idempotency_key`: endpoint identity, session start timestamp, and episode ID
  - `projection_path`: planned `chronicler_adapter`

#### Scenario: Capture remains a projection boundary

- **WHEN** this source surface is deployed
- **THEN** no Chronicler adapter, source registry entry, projection checkpoint,
  direct source route, dashboard view, or LLM interpretation SHALL be added
- **AND** a later projection change SHALL independently define adapter reading,
  retention/tombstone behavior, and user-facing semantics

### Requirement: Google Health Fact Projection Compatibility Declaration

Chronicler SHALL declare the supported Health-fact projections separately from
raw Google Health connector ingest, so a source's supported projections and
its unimplemented upstream shapes cannot be conflated.

ID: REQ-chronicler-source-compatibility-006
Source: RFC 0014 Amendment 1; [Observed] `src/butlers/chronicler/adapters/google_health.py`
Scope: v1-mandatory

#### Scenario: Supported Health fact shapes declare deterministic projection

- **WHEN** the Google Health and Health fact source boundary is reviewed
- **THEN** its declaration SHALL specify:
  - `source_name`: `google_health.measurements` for `sleep_session` and a
    conditionally present `workout_session`; `health.steps` for
    `measurement_steps` or `daily_steps`; and `health.heart_rate` for
    `measurement_resting_hr`, `heart_rate_summary`, or
    `measurement_heart_rate`
  - `source_kind`: durable Health-owned wellness facts after their owner has
    accepted ingestion and Health `mem_011` has granted the Chronicler role
    read access; adapters read facts rather than raw API records or connector
    envelopes
  - `supported_outputs`: one `sleep_episode` per `sleep_session`, one
    `workout_episode` only for an independently present `workout_session`,
    one `daily_steps` point event per step fact, and one
    `heart_rate_summary` point event per heart-rate fact
  - `time_fields`: `valid_at` as the session start or daily/window anchor,
    `metadata.end_time` or `metadata.duration_ms` as the episode closure
    fallback, and `created_at` as the projection watermark
  - `boundary_semantics`: sleep and a separately present workout have minute
    precision; daily steps and daily heart-rate summaries have day precision;
    manual point heart-rate measurements have minute precision
  - `source_ref_format`: `health.facts:{predicate}:{idempotency_key}`; when a
    fact has no idempotency key the fallback is
    `health.facts:{predicate}:{fact_id}`. A cross-batch continuation of an
    open sleep session MAY retain its predecessor `source_ref` so one session
    is stitched in place rather than fragmented
  - `taxonomy_mapping`: sleep and workout are activity episodes; steps and
    heart-rate summaries are evidence point events
  - `confidence_semantics`: sleep has medium confidence from its structured
    session evidence; a workout has medium confidence from its strong
    `workout_session` fact and high confidence when
    `average_heart_rate` or `max_heart_rate` supplies a second evidence kind;
    point-event projections do not assert episode confidence
  - `privacy_tier`: sleep and heart-rate projections are sensitive; step
    projections are normal; a workout is normal unless its fact carries
    `average_heart_rate` or `max_heart_rate`, when it is sensitive
  - `idempotency_key`: the persistent `(source_name, source_ref)` projection
    key
  - `projection_path`: `chronicler_adapter`

#### Scenario: Read-only source availability and retention stay explicit

- **WHEN** a Health fact projection runs
- **THEN** it SHALL read only active facts from the approved optional
  `health.facts` surface, whose table-specific `SELECT` privilege is
  established by Health `mem_011`, and SHALL degrade to an inactive source
  state rather than raise when that surface is unavailable
- **AND** the Health writer's existing `operational` retention class SHALL
  remain the raw-fact policy for this declaration
- **AND** the projection SHALL not copy the raw connector payload into
  Chronicler; its projected record uses the existing Chronicler retention
  default and no source-absence-only tombstone is implied
- **AND** when an upstream fact is later inactive, absent, or purged, an
  already-projected record SHALL retain its written precision and SHALL NOT be
  automatically deleted, tombstoned, or lowered in precision solely because
  the source disappeared; normal Chronicler retention and explicit corrections
  remain separate lifecycle controls

#### Scenario: Workout adapter does not imply Google Health workout ingest

- **WHEN** the scheduled Chronicler workout adapter finds no
  `workout_session` fact
- **THEN** it SHALL project no workout episode
- **AND** the current Google Health connector SHALL NOT be inferred to have
  emitted a workout resource or written that fact
- **AND** a future connector workout source SHALL define its own resource,
  fact predicate and metadata contract, source reference, and verification
  before this declaration treats it as a Google Health-produced output

## Source References

- Non-Negotiable Rule 1 (single-owner data sovereignty)
- Non-Negotiable Rule 3 (MCP-only inter-butler communication)
- Non-Negotiable Rule 7 (transport is connector responsibility)
- RFC 0003 (Switchboard routing and ingestion)
- RFC 0006 (Database schema isolation)
- RFC 0010 (Cross-Butler Briefing Exception)
- RFC 0014 (Chronicler Time Butler, Draft)
