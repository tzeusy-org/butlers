## ADDED Requirements

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
