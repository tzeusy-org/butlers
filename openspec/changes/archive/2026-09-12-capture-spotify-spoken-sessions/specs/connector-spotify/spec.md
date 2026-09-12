## ADDED Requirements

### Requirement: Capture-Only Spoken Playback Evidence

The Spotify connector SHALL request both track and episode items from current
playback. When an actively playing item is an episode, it SHALL normalize a
separate spoken session without changing track listening-session behavior. The
connector SHALL classify an episode with `item.show` as `podcast`, with
`item.audiobook` as `audiobook`, and all other episode parents as
`unknown_episode`.

#### Scenario: Podcast episode opens a passive spoken session

- **WHEN** current playback first reports an active episode with a `show`
  parent
- **THEN** the connector SHALL open a `podcast` spoken session, persist its
  bounded connector evidence, and submit one `spotify.spoken_session`
  metadata-tier `ingest.v1` envelope through the existing policy/replay path
- **AND** a narrow global `substring` policy for the stable
  `spotify:spoken:` event-id prefix SHALL pre-resolve `metadata_only` triage,
  so the envelope is persisted without LLM classification, butler routing, or
  proactive notification
- **AND** the envelope SHALL have `payload.raw = null` and SHALL NOT route
  directly to Education or Chronicler

#### Scenario: Audiobook chapter opens a spoken session

- **WHEN** current playback first reports an active episode with an `audiobook`
  parent
- **THEN** the connector SHALL open an `audiobook` spoken session using the
  chapter and audiobook identifiers available in that response
- **AND** it SHALL NOT add the chapter name to music `ListeningSession.track_names`

#### Scenario: Parentless episode remains explicit

- **WHEN** current playback reports an active episode with neither `show` nor
  `audiobook`
- **THEN** the connector SHALL capture it as `unknown_episode`
- **AND** it SHALL NOT infer a podcast or audiobook type from title or URI text

#### Scenario: Repeat, switch, pause, and replay boundaries are deterministic

- **WHEN** the same spoken episode is observed again while active
- **THEN** the connector SHALL update the existing evidence row without a
  duplicate passive envelope
- **WHEN** a different spoken episode becomes active
- **THEN** it SHALL close the prior spoken session and open a new one
- **WHEN** playback pauses past the configured idle drain
- **THEN** it SHALL close the spoken session
- **WHEN** the same episode resumes after that close
- **THEN** it SHALL open a distinct replay session with a new start-boundary key

#### Scenario: Spoken evidence is bounded and idempotent

- **WHEN** the connector writes a spoken session
- **THEN** it SHALL upsert `connectors.spotify_spoken_sessions` by a stable key
  composed of endpoint identity, initial observed timestamp, and episode ID
- **AND** the row SHALL contain only typed session/episode/parent fields and a
  bounded metadata object, with no transcript, description, HTML, or raw
  Spotify API payload
- **AND** connector evidence-write failure SHALL NOT prevent the normal passive
  envelope submission

#### Scenario: Connector-owned ACLs remain least-privilege

- **WHEN** core migrations create the spoken evidence surface
- **THEN** they SHALL tolerate absent runtime roles while granting
  `connector_writer` connector DML and `butler_chronicler_rw` SELECT only when
  those roles exist
- **AND** no butler receives write permission through this migration

## MODIFIED Requirements

### Requirement: Polling-Based Ingestion Loop

The connector SHALL poll the Spotify Web API at configurable intervals to detect playback state changes.

#### Scenario: Active playback polling

- **WHEN** the connector detects active playback (Spotify returns `is_playing: true`)
- **THEN** it SHALL poll `GET /me/player/currently-playing` every `SPOTIFY_POLL_ACTIVE_S` seconds (default 60)
- **AND** the request SHALL include both `track` and `episode` additional types
- **AND** each poll response SHALL be compared against the appropriate music or spoken state to detect item changes and context changes

#### Scenario: Idle polling with backoff

- **WHEN** the connector detects no active playback (no device, `is_playing: false`, or private session)
- **THEN** it SHALL increase the poll interval using exponential backoff up to `SPOTIFY_POLL_IDLE_S` seconds (default 300)
- **AND** any state change (playback resumes) SHALL reset the poll interval to `SPOTIFY_POLL_ACTIVE_S`

#### Scenario: Recently-played polling

- **WHEN** the connector completes a poll cycle
- **THEN** it SHALL also poll `GET /me/player/recently-played` with the `after` cursor parameter set to the last-seen play timestamp
- **AND** gap-fill polling SHALL be throttled to `SPOTIFY_GAP_FILL_IDLE_INTERVAL_S` (default 10800)
- **AND** tracks not already observed via the `currently-playing` endpoint SHALL be emitted as a single batched gap-fill digest (`external_event_id = "spotify:gapfill:<first_ms>:<last_ms>"`), not as per-track events

#### Scenario: Private session handling

- **WHEN** the Spotify API indicates a private session (no playback data returned despite an active device)
- **THEN** the connector SHALL treat this as idle state and back off polling
- **AND** it SHALL NOT emit any events for the private session period
