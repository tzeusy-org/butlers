# Butler Switchboard — Spotify Delta

## ADDED Requirements

### Requirement: Spotify Source Channel and Provider Registration

The Switchboard SHALL accept Spotify as a valid ingestion source.

#### Scenario: SourceChannel registration

- **WHEN** the `SourceChannel` literal type is defined
- **THEN** it SHALL include `"spotify"` as a valid channel value

#### Scenario: SourceProvider registration

- **WHEN** the `SourceProvider` literal type is defined
- **THEN** it SHALL include `"spotify"` as a valid provider value

#### Scenario: Channel-provider pair validation

- **WHEN** an ingest envelope arrives with `source.channel = "spotify"`
- **THEN** the Switchboard SHALL validate that `source.provider` is `"spotify"` (the only allowed provider for this channel)
- **AND** the pair SHALL be registered in `_ALLOWED_PROVIDERS_BY_CHANNEL` as `{"spotify": frozenset({"spotify"})}`

### Requirement: Spotify Ingest Event Shape

Spotify ingest envelopes follow the canonical `ingest.v1` schema with Spotify-specific field semantics.

#### Scenario: Spotify envelope acceptance

- **WHEN** a Spotify ingest envelope arrives at the Switchboard
- **THEN** it SHALL be accepted if it conforms to `ingest.v1` with:
  - `source.channel = "spotify"`
  - `source.provider = "spotify"`
  - `source.endpoint_identity` matching pattern `"spotify:<spotify_user_id>"`
  - `sender.identity` containing a Spotify user ID
  - `control.idempotency_key` matching pattern `"spotify:<endpoint_identity>:<event_id>"`

### Requirement: Spotify Non-Interactive Channel

Spotify is a data-source channel, not an interactive messaging channel. Events are routed for contextual awareness only.

#### Scenario: Non-interactive classification

- **WHEN** a message arrives from `source.channel = "spotify"`
- **THEN** the Switchboard SHALL NOT recognize it as an interactive channel
- **AND** routing guidance SHALL NOT instruct the target butler to reply via the source channel
- **AND** `"spotify"` SHALL NOT be added to `_INTERACTIVE_ROUTE_CHANNELS`

#### Scenario: Spotify conversation history strategy

- **WHEN** the Switchboard loads conversation history for routing context on a Spotify event
- **THEN** it SHALL use the `"realtime"` strategy (same as messaging connectors) to provide recent listening context
- **AND** `HISTORY_STRATEGY` SHALL include `"spotify": "realtime"`
