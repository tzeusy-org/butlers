## MODIFIED Requirements

### Requirement: ingest.v1 Field Mapping

The connector SHALL map each detected Steam delta to an `ingest.v1` envelope with Steam-specific source identity, event identity, payload summary, and tier controls. No Steam envelope SHALL select a target butler. The `gaming` channel is a telemetry feed: an envelope is recorded into domain state and wakes reasoning only through a declared subscription admitted with origin `obligation` (`REQ-butler-switchboard-007`).

Source: RFC 0038 D1, D5; RFC 0003 disposition
Scope: v1-mandatory

#### Scenario: Play session event mapping

- **WHEN** a new or continued play session is detected
- **THEN** the `ingest.v1` envelope SHALL be:
  - `source.channel = "gaming"`
  - `source.provider = "steam"`
  - `source.endpoint_identity = "steam:user:<steam_id>"`
  - `event.type = "play_session"`
  - `event.external_event_id = "steam:play:<steam_id>:<app_id>:<poll_timestamp>"`
  - `event.observed_at` = poll timestamp (RFC3339)
  - `sender.identity = "steam:<steam_id>"`
  - `payload.normalized_text` = human-readable summary (e.g., "Played Counter-Strike 2 for 45 minutes")
  - `payload.raw` = full API response data (app_id, name, playtime deltas)

#### Scenario: Achievement unlock event mapping

- **WHEN** a new achievement unlock is detected
- **THEN** the `ingest.v1` envelope SHALL be:
  - `event.type = "achievement_unlock"`
  - `event.external_event_id = "steam:achievement:<steam_id>:<app_id>:<achievement_api_name>"`
  - `payload.normalized_text` = human-readable summary (e.g., "Unlocked 'Expert Marksman' in Counter-Strike 2")
  - `payload.raw` = achievement details (api_name, display name, description, unlock_time, game name)

#### Scenario: Online status change event mapping

- **WHEN** an online status change is detected
- **THEN** the `ingest.v1` envelope SHALL be:
  - `event.type = "status_change"`
  - `event.external_event_id = "steam:status:<steam_id>:<poll_timestamp>"`
  - `payload.normalized_text` = human-readable summary (e.g., "Now playing Dota 2" or "Went offline")
  - `payload.raw` = `null`
  - `control.ingestion_tier` = `"metadata"` so the Switchboard persists the presence delta as a metadata-only reference (`payload.raw = null`) — this controls *persistence shape only* and does not, on its own, withhold goal admission
  - The actual goal-admission withholding is a `scope='global'`, `rule_type='substring'` `ingestion_rules` row matching the `"steam:status:"` prefix of `external_event_id` (seeded by migration `025_switchboard_steam_status_skip.py`; see `ingestion-policy` spec's "Gaming (Steam) connector populates envelope" scenario), evaluated by `IngestionPolicyEvaluator(scope='global')` before goal admission

#### Scenario: Game purchase event mapping

- **WHEN** a new game is detected in the owned games library
- **THEN** the `ingest.v1` envelope SHALL be:
  - `event.type = "game_purchase"`
  - `event.external_event_id = "steam:purchase:<steam_id>:<app_id>"`
  - `payload.normalized_text` = human-readable summary (e.g., "Added 'Elden Ring' to library")
  - `payload.raw` = game details (app_id, name, playtime_forever — typically 0 for new purchases)

#### Scenario: Friend list change event mapping

- **WHEN** a friend list change is detected
- **THEN** the `ingest.v1` envelope SHALL be:
  - `event.type = "friend_change"`
  - `event.external_event_id = "steam:friend:<steam_id>:<friend_steam_id>:<added|removed>"`
  - `payload.normalized_text` = human-readable summary (e.g., "Added friend 'PlayerName'")
  - `payload.raw` = friend SteamID, relationship, direction (added/removed)

### Requirement: Source Channel and Provider Registration

The connector SHALL submit all Steam envelopes with the registered `gaming`/`steam` source channel/provider pair and valid tier/idempotency controls (RFC 0003 amended; see `roster/switchboard/tools/routing/contracts.py`). The ingestion tier SHALL control persistence shape only; goal admission for status changes SHALL be withheld by the seeded global ingestion rule.

Source: RFC 0038 D1; RFC 0003 disposition
Scope: v1-mandatory

#### Scenario: SourceChannel enum extension

- **WHEN** the Steam connector submits ingest.v1 envelopes
- **THEN** `source.channel` SHALL be `"gaming"` (new enum value)
- **AND** `source.provider` SHALL be `"steam"` (new enum value)
- **AND** the valid pairing `gaming/steam` SHALL be added to the channel-provider validation matrix

#### Scenario: Ingestion tier assignment

- **WHEN** the connector constructs an ingest.v1 envelope
- **THEN** `control.policy_tier` SHALL be `"default"` (user's own activity, no priority escalation)
- **AND** play session, achievement unlock, game purchase, and friend-change envelopes SHALL set `control.ingestion_tier = "full"` (Tier 1 — include complete `payload.raw`)
- **AND** online status-change envelopes SHALL set `control.ingestion_tier = "metadata"` (Tier 2 — persist the presence summary; goal admission is independently withheld by the seeded global `ingestion_rules` row described in the "Online status change event mapping" scenario above, not by this tier alone)

#### Scenario: Idempotency key format

- **WHEN** the connector constructs an ingest.v1 envelope
- **THEN** `control.idempotency_key` SHALL equal `event.external_event_id` (each event ID is already globally unique by construction)
- **AND** the Switchboard's dedup layer SHALL use this key to prevent duplicate ingestion after crash recovery
