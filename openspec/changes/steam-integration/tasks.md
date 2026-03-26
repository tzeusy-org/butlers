## 0. Pre-requisites (RFC Amendments)

- [ ] 0.1 Amend RFC 0003 to register `gaming/steam` as a valid source channel/provider pair — add `gaming` to SourceChannel enum, `steam` to SourceProvider enum, and `gaming/steam` to the channel-provider validation matrix
- [ ] 0.2 Amend RFC 0004 to register `steam_api_key` as a valid `entity_info.type` for credential storage

## 1. Shared Steam API Client

- [ ] 1.1 Create `src/butlers/steam/__init__.py` and `src/butlers/steam/client.py` with `SteamAPIClient` class — wraps `httpx.AsyncClient`, handles key injection, response unwrapping, rate limit backoff, batch `GetPlayerSummaries`
- [ ] 1.2 Add unit tests for `SteamAPIClient` — response parsing, error handling, batch splitting, rate limit backoff logic (mocked HTTP)

## 2. Steam Account Registry

- [ ] 2.1 Create Alembic migration for `public.steam_accounts` table with indexes and constraints per spec (use fully qualified `public.` schema references — do NOT rely on search_path)
- [ ] 2.2 Create Alembic migration for `connectors.steam_cursors` table (endpoint_identity, data_type, last_poll_at, state_hash, state_snapshot JSONB)
- [ ] 2.3 Implement `SteamAccountResolver` helper — lookup by SteamID, UUID, or primary; resolve API key from companion entity_info
- [ ] 2.4 Add tests for account resolver — primary lookup, SteamID lookup, missing account error, revoked account skipping

## 3. Steam Module

- [ ] 3.1 Create `src/butlers/modules/steam.py` implementing `Module` base class — config schema (SteamConfig), credential resolution in `on_startup`, degraded mode when no account
- [ ] 3.2 Implement `steam_get_player_summary` tool — default to owner SteamID, privacy-aware response
- [ ] 3.3 Implement `steam_get_owned_games` tool — include_free flag, privacy error handling
- [ ] 3.4 Implement `steam_get_recently_played` tool — count parameter, privacy handling
- [ ] 3.5 Implement `steam_get_achievements` tool — per-game lookup, no-achievements error, privacy handling
- [ ] 3.6 Implement `steam_get_friend_list` tool — optional enrich via batch GetPlayerSummaries, privacy handling
- [ ] 3.7 Implement `steam_get_game_news` tool — public endpoint, no SteamID required
- [ ] 3.8 Implement `steam_get_player_level` and `steam_get_current_players` tools
- [ ] 3.9 Implement `steam_resolve_vanity_url` tool — not-found error handling
- [ ] 3.10 Implement `tool_metadata()` marking API key as sensitive
- [ ] 3.11 Add module to module registry discovery
- [ ] 3.12 Add unit tests for all tools — mocked SteamAPIClient, privacy error paths, default account resolution

## 4. Steam Connector

- [ ] 4.1 Create `src/butlers/connectors/steam.py` with `SteamConnector` class — multi-account discovery, per-account loop spawning, dynamic rescan
- [ ] 4.2 Implement recently-played poller — state diffing, play session event emission, cursor persistence
- [ ] 4.3 Implement achievement poller — tracked game auto-detection from recently played, per-game achievement diffing, unlock event emission
- [ ] 4.4 Implement online status poller — persona_state + gameextrainfo diffing, status change event emission
- [ ] 4.5 Implement friend list poller — set diffing, add/remove event emission
- [ ] 4.5b Implement game library poller — daily poll of GetOwnedGames, set diffing for new purchases, baseline-on-first-run (no initial flood)
- [ ] 4.6 Implement cursor persistence in `connectors.steam_cursors` — save after poll, load on restart, cleanup for revoked accounts
- [ ] 4.7 Implement ingest.v1 envelope construction for all four event types per spec
- [ ] 4.8 Implement rate limiting — per-account exponential backoff on 429/403, transient error retry, privacy skip
- [ ] 4.9 Implement health status aggregation — per-account, per-data-type status, SteamID redaction
- [ ] 4.10 Implement filtered event batch flush — record errors and source-filter-blocked events to `connectors.filtered_events` after each poll cycle
- [ ] 4.11 Implement replay queue drain loop — query `filtered_events WHERE status='replay_pending'`, process up to 10 per cycle, update status
- [ ] 4.12 Implement source filter gate — `IngestionPolicyEvaluator` per account, pre-submit evaluation, filter key types (app_id, event_type, sender_identity)
- [ ] 4.13 Implement heartbeat protocol — `connector.heartbeat.v1` envelopes at configurable interval with identity, health, counters, checkpoint
- [ ] 4.14 Implement Prometheus metrics — polls_total, events_submitted_total, events_filtered_total, api_errors_total, api_latency_seconds, rate_limit_backoffs_total
- [ ] 4.15 Set `control.idempotency_key` and `control.policy_tier`/`control.ingestion_tier` on all ingest.v1 envelopes
- [ ] 4.16 Add unit tests for connector — discovery, polling loops, delta detection, cursor round-trip, event mapping, replay drain, filter gate, heartbeat (mocked API)

## 5. Play History Storage

- [ ] 5.1 Create Alembic migration for `connectors.steam_play_history` table with indexes and UNIQUE constraint
- [ ] 5.2 Implement playtime delta upsert logic in the recently-played poller — write daily aggregates, skip baseline backfill
- [ ] 5.3 Add tests for play history persistence — upsert idempotency, daily rollover, baseline skip

## 6. Dashboard Integration

- [ ] 6.1 Create dashboard API routes for Steam account management — `POST /api/steam/accounts` (connect with key validation), `DELETE` (disconnect), `GET` (list), `PUT .../primary` (set primary)
- [ ] 6.2 Create dashboard API routes for playtime analytics — `GET /api/steam/playtime` (summary), `GET /api/steam/playtime/<app_id>` (per-game), scoped to primary account by default
- [ ] 6.3 Create dashboard API route for connector health proxy — `GET /api/steam/connector/health`
- [ ] 6.4 Add Pydantic request/response models for all Steam dashboard endpoints
- [ ] 6.5 Add tests for dashboard API routes — account CRUD, key validation error paths, playtime queries

## 7. Configuration and Integration

- [ ] 7.1 Add `[modules.steam]` example config to relevant butler `butler.toml` files (general, lifestyle if applicable)
- [ ] 7.2 Add Steam connector entry point / startup configuration
- [ ] 7.3 End-to-end integration test — connect account → module tools return data → connector emits events → play history persisted (mocked Steam API)
