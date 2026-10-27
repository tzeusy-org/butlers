## ADDED Requirements

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
