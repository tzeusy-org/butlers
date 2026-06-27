# Steam Connector

## Purpose

The Steam connector is a long-running background process that polls connected Steam accounts for activity changes and submits normalized `ingest.v1` events to the Switchboard. It implements the connector base contract (polling mode only — Steam has no push/webhook mechanism). The connector discovers accounts from `public.steam_accounts`, runs independent per-account polling loops with per-data-type intervals, detects changes via state diffing, and maintains crash-safe cursors.

## Requirements

### Requirement: Steam Connector Identity and Authentication

The Steam connector runs as a single process that discovers and manages all connected Steam accounts. It authenticates each account via API key resolved from the account's companion entity.

#### Scenario: Multi-account discovery at startup

- **WHEN** the Steam connector starts
- **THEN** it SHALL query `public.steam_accounts` for all rows with `status = 'active'`
- **AND** for each qualifying account, it SHALL resolve the API key from the account's companion entity in `entity_info` (type `steam_api_key`)
- **AND** it SHALL spawn independent polling loops per account
- **AND** startup SHALL succeed even if some accounts fail credential resolution (degraded mode — failed accounts are logged and skipped)

#### Scenario: Per-account connector identity

- **WHEN** a polling loop runs for account with `steam_id = 76561198000000000`
- **THEN** `source.channel = "gaming"`, `source.provider = "steam"`, and `source.endpoint_identity = "steam:user:76561198000000000"`
- **AND** the endpoint identity is derived from the account's SteamID

> **RFC 0003 amended:** The `gaming/steam` channel/provider pair is registered in the Switchboard ingestion contract (`roster/switchboard/tools/routing/contracts.py`: `SourceChannel` includes `gaming`, `SourceProvider` includes `steam`, and `_ALLOWED_PROVIDERS_BY_CHANNEL["gaming"] = {"steam"}`).

#### Scenario: No qualifying accounts

- **WHEN** the connector starts and no active Steam accounts exist
- **THEN** the connector SHALL start in idle mode (health = `degraded`, no active loops)
- **AND** it SHALL periodically re-scan for new accounts (see dynamic account discovery)

#### Scenario: Dynamic account discovery

- **WHEN** the connector is running
- **THEN** it SHALL re-scan `public.steam_accounts` every 300 seconds (configurable via dashboard settings under Steam connector configuration)
- **AND** new active accounts SHALL have polling loops spawned
- **AND** accounts that became `revoked` or `suspended` SHALL have their loops gracefully shut down

### Requirement: Polling Modes and Intervals

The connector polls multiple data types at independent intervals per account.

#### Scenario: Default poll intervals

- **WHEN** a Steam account has no per-account metadata overrides
- **THEN** the connector SHALL run exactly two background pollers:
  - Recently played games: 300 seconds (5 minutes)
  - Online status: 300 seconds (5 minutes)
- **AND** achievement, friend list, and game library data SHALL NOT be polled in the background (they produced noise without matching the owner's game start/stop goals)
- **AND** that data remains reachable only via the on-demand MCP tools `steam_get_achievements`, `steam_list_friends`, and `steam_list_owned_games` (the achievement/friend/game-library poller code exists but is never spawned at runtime)

#### Scenario: Per-account interval overrides

- **WHEN** `steam_accounts.metadata` contains `{"poll_intervals": {"recently_played": 120}}`
- **THEN** the connector SHALL use 120 seconds for recently played and global defaults for other data types

#### Scenario: Per-data-type polling loops

- **WHEN** polling loops are active for an account
- **THEN** each data type SHALL run as an independent asyncio task with its own interval
- **AND** failure in one data type's poll SHALL NOT affect other data types for the same account
- **AND** each data type SHALL have independent backoff state

### Requirement: Delta Detection via State Diffing

Steam's API returns current state only (no history/change feed). The connector detects changes by comparing current responses against cached previous state.

#### Scenario: Recently played game detection

- **WHEN** the recently played poller runs
- **THEN** it SHALL call `IPlayerService/GetRecentlyPlayedGames/v1`
- **AND** compare against the last-known state
- **AND** emit an `ingest.v1` event for each game where:
  - `playtime_2weeks` increased (ongoing play session)
  - A new game appears in the recently played list (new play session)

#### Scenario: Achievement unlock detection

- **WHEN** the achievement poller runs for a tracked game
- **THEN** it SHALL call `ISteamUserStats/GetPlayerAchievements/v1` for the game
- **AND** compare the set of `achieved=true` achievements against the last-known set
- **AND** emit an `ingest.v1` event for each newly unlocked achievement

#### Scenario: Tracked games for achievement polling

- **WHEN** the achievement poller determines which games to track
- **THEN** it SHALL use `metadata.tracked_games` if present
- **AND** otherwise auto-detect from `GetRecentlyPlayedGames` (games played in last 2 weeks)
- **AND** limit to a maximum of 10 tracked games (configurable via dashboard settings under Steam connector configuration)

#### Scenario: Game purchase detection

- **WHEN** the game library poller runs (daily)
- **THEN** it SHALL call `IPlayerService/GetOwnedGames/v1` with `include_appinfo=true`
- **AND** compare the set of owned app IDs against the last-known set
- **AND** emit an `ingest.v1` event for each newly appearing game (not previously in the set)
- **AND** the initial poll SHALL establish the baseline set without emitting events (no flood of "purchased" events for existing library)

#### Scenario: Online status change detection

- **WHEN** the online status poller runs
- **THEN** it SHALL call `ISteamUser/GetPlayerSummaries/v2`
- **AND** compare `persona_state` and `gameextrainfo` (currently playing game) against last-known state
- **AND** emit an `ingest.v1` event when:
  - `persona_state` changes (online ↔ offline ↔ away, etc.)
  - `gameextrainfo` changes (started/stopped playing a game)

#### Scenario: Friend list change detection

- **WHEN** the friend list poller runs
- **THEN** it SHALL call `ISteamUser/GetFriendList/v1`
- **AND** compare the set of friend SteamIDs against the last-known set
- **AND** emit `ingest.v1` events for added and removed friends

### Requirement: Cursor Persistence

Per-account, per-data-type cursors enable crash-safe resume.

#### Scenario: Cursor storage

- **WHEN** a poll cycle completes successfully
- **THEN** the connector SHALL persist a cursor with:
  - `endpoint_identity` (e.g., `"steam:user:76561198000000000"`)
  - `data_type` (e.g., `"recently_played"`, `"achievements:730"`, `"online_status"`, `"friends"`)
  - `last_poll_at` (timestamp)
  - `state_hash` (SHA256 of the serialized response for diffing)
  - `state_snapshot` (JSONB of the last-known state)
- **AND** the cursor SHALL be stored in the `connectors` schema (table `connectors.steam_cursors`)

#### Scenario: Resume after crash

- **WHEN** the connector restarts after a crash
- **THEN** it SHALL load cursors from `connectors.steam_cursors` for each active account
- **AND** resume polling from the last-known state (no duplicate event emission)

#### Scenario: Cursor cleanup on account disconnect

- **WHEN** an account's status changes to `revoked`
- **THEN** cursors for that account's `endpoint_identity` SHALL be retained for 30 days (in case of reconnect)
- **AND** cursors older than 30 days for revoked accounts SHALL be purged by a periodic cleanup task

### Requirement: ingest.v1 Field Mapping

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
  - `payload.raw` = persona_state, gameextrainfo, previous state

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

### Requirement: Rate Limiting and Error Handling

#### Scenario: Rate limit detection and backoff

- **WHEN** a Steam API call returns HTTP 429 or 403
- **THEN** the connector SHALL apply exponential backoff starting at 60 seconds, doubling up to 3600 seconds (1 hour)
- **AND** the backoff SHALL be per-account (one account's rate limit does not affect others)
- **AND** the account's health status SHALL transition to `degraded`

#### Scenario: Transient error handling

- **WHEN** a Steam API call fails with a network error or 5xx status
- **THEN** the connector SHALL retry with exponential backoff (starting at 5 seconds, max 300 seconds)
- **AND** after 5 consecutive failures, the account SHALL transition to `error` health status

#### Scenario: Privacy error handling

- **WHEN** a Steam API call returns empty data due to privacy settings
- **THEN** the connector SHALL NOT treat this as an error
- **AND** it SHALL log a debug message and skip event emission for that data type
- **AND** the cursor SHALL still be advanced (privacy is not a transient error)

### Requirement: Health Status Reporting

#### Scenario: Aggregated health endpoint

- **WHEN** the health endpoint is queried
- **THEN** the connector SHALL return:
  - `status`: worst-case across all accounts (`healthy`, `degraded`, `error`)
  - `uptime_seconds`
  - `active_accounts`: count of accounts with active polling loops
  - `account_health`: per-account details with `steam_id` (redacted), `endpoint_identity`, `status`, `data_types` (per-type last poll time and status, under `data_types[*].last_poll_at`), `error` (if any)

#### Scenario: SteamID redaction in health output

- **WHEN** health status is returned
- **THEN** SteamIDs SHALL be partially redacted (e.g., `"7656***0000"`) in the health response

### Requirement: Source Channel and Provider Registration

Steam introduces a channel/provider pair that is registered in the Switchboard's ingestion contract (RFC 0003 amended; see `roster/switchboard/tools/routing/contracts.py`).

#### Scenario: SourceChannel enum extension

- **WHEN** the Steam connector submits ingest.v1 envelopes
- **THEN** `source.channel` SHALL be `"gaming"` (new enum value)
- **AND** `source.provider` SHALL be `"steam"` (new enum value)
- **AND** the valid pairing `gaming/steam` SHALL be added to the channel-provider validation matrix

#### Scenario: Ingestion tier assignment

- **WHEN** the connector constructs an ingest.v1 envelope
- **THEN** `control.policy_tier` SHALL be `"default"` (user's own activity, no priority escalation)
- **AND** `control.ingestion_tier` SHALL be `"full"` (Tier 1 — include complete `payload.raw`)

#### Scenario: Idempotency key format

- **WHEN** the connector constructs an ingest.v1 envelope
- **THEN** `control.idempotency_key` SHALL equal `event.external_event_id` (each event ID is already globally unique by construction)
- **AND** the Switchboard's dedup layer SHALL use this key to prevent duplicate ingestion after crash recovery

### Requirement: Filtered Event Batch Flush

The connector SHALL implement the connector base contract's filtered event batch flush obligation per `connectors.filtered_events` table schema.

#### Scenario: No-change polls are not recorded

- **WHEN** a poll cycle detects no changes (state hash unchanged)
- **THEN** no events are emitted and no filtered events are recorded (unchanged state is normal, not a filter action)

#### Scenario: Error events recorded in filtered_events

- **WHEN** a poll cycle encounters an API error for a specific data type
- **THEN** the error SHALL be recorded in `connectors.filtered_events` with:
  - `connector_type = "steam"`
  - `endpoint_identity = "steam:user:<steam_id>"`
  - `source_channel = "gaming"`
  - `status = 'error'`
  - `error_detail` containing the error message and HTTP status code
  - `full_payload` containing whatever partial data was available
- **AND** the buffer SHALL be flushed in a single batch INSERT after each poll cycle

#### Scenario: Source-filter-blocked events recorded

- **WHEN** the ingestion policy evaluator blocks a Steam event (see source filter gate requirement)
- **THEN** the event SHALL be recorded in `connectors.filtered_events` with `status = 'filtered'` and `filter_reason` describing which rule matched

### Requirement: Replay Queue Drain Loop

The connector SHALL check for pending replay requests after each poll cycle per the connector base contract.

#### Scenario: Drain loop executes after poll cycle

- **WHEN** a poll cycle completes (including filtered event flush)
- **THEN** it SHALL query `connectors.filtered_events` for rows with `status = 'replay_pending'` matching `connector_type = 'steam'` and the account's `endpoint_identity`
- **AND** it SHALL process up to 10 replay items per cycle using `FOR UPDATE SKIP LOCKED`

#### Scenario: Replay uses standard ingestion path

- **WHEN** the connector processes a replay item
- **THEN** it SHALL deserialize `full_payload` from the row
- **AND** construct a complete `ingest.v1` envelope from the stored payload
- **AND** submit to the Switchboard's `ingest_v1` MCP tool using the same code path as normal ingestion
- **AND** update the row's status to `'replay_complete'` on success or `'replay_failed'` on error

### Requirement: Source Filter Gate

The connector SHALL evaluate active ingestion policy rules before submitting events to the Switchboard.

#### Scenario: Policy evaluator initialization

- **WHEN** the connector starts
- **THEN** it SHALL initialize an `IngestionPolicyEvaluator` with scope `'connector:steam:<endpoint_identity>'` per account
- **AND** call `ensure_loaded()` to load active rules from the database

#### Scenario: Pre-submit filter evaluation

- **WHEN** the connector is about to submit an ingest.v1 event
- **THEN** it SHALL evaluate the event against active rules
- **AND** if any `block` rule matches, the event SHALL NOT be submitted
- **AND** blocked events SHALL be recorded in filtered_events with the matching rule as `filter_reason`

#### Scenario: Filter key types for Steam events

- **WHEN** evaluating rules against Steam events
- **THEN** the following filter key types SHALL be supported:
  - `app_id` — Steam application ID (integer as string)
  - `event_type` — event type (`play_session`, `achievement_unlock`, `status_change`, `game_purchase`, `friend_change`)
  - `sender_identity` — `steam:<steam_id>`

#### Scenario: No active filters passes all events

- **WHEN** no source filter rules exist for the connector scope
- **THEN** the filter gate SHALL be a no-op and all events SHALL pass through

### Requirement: Heartbeat Protocol

The connector SHALL send periodic heartbeat envelopes to the Switchboard for liveness tracking.

#### Scenario: Heartbeat envelope

- **WHEN** the heartbeat interval elapses (default 60 seconds, configurable via dashboard settings under Steam connector configuration)
- **THEN** the connector SHALL submit a `connector.heartbeat.v1` envelope containing:
  - `identity.connector_type = "steam"`
  - `identity.endpoint_identity` = comma-separated list of active endpoint identities
  - `health.status` = aggregated health (`healthy`, `degraded`, `error`)
  - `health.active_accounts` = count of active polling loops
  - `counters.events_submitted` = total events submitted since startup
  - `counters.events_filtered` = total events filtered since startup
  - `counters.errors` = total errors since startup
  - `checkpoint.last_poll_at` = most recent poll timestamp across all accounts

#### Scenario: Heartbeat failure does not crash

- **WHEN** a heartbeat submission fails (Switchboard unavailable)
- **THEN** the connector SHALL log a warning and continue polling
- **AND** the next heartbeat SHALL be attempted on schedule

### Requirement: Prometheus Metrics

The connector SHALL export Prometheus metrics for observability.

#### Scenario: Steam-specific counters

- **WHEN** the metrics endpoint is queried
- **THEN** the following counters SHALL be available:
  - `connector_steam_polls_total{data_type, endpoint_identity, status}` — total polls by type and outcome (success/error/privacy)
  - `connector_steam_events_submitted_total{event_type, endpoint_identity}` — events submitted to Switchboard
  - `connector_steam_events_filtered_total{filter_reason, endpoint_identity}` — events blocked by source filters
  - `connector_steam_api_errors_total{endpoint_identity, http_status}` — API errors by status code
  - `connector_steam_api_latency_seconds{data_type, endpoint_identity}` — API call latency histogram
  - `connector_steam_rate_limit_backoffs_total{endpoint_identity}` — rate limit backoff events
