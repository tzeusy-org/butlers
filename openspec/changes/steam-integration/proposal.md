## Why

> **Scope note:** Steam is not in the v1 connector/module list. This is a post-v1 extension that follows established patterns (module + connector + account registry) and introduces no new architectural primitives.

Butlers currently has no visibility into gaming activity. Steam is the dominant PC gaming platform with a well-documented Web API that exposes player profiles, game libraries, achievements, friend lists, and activity data. Adding Steam integration lets butlers (especially lifestyle, general, and relationship) react to gaming patterns — tracking play time, noticing new games or achievements, and surfacing gaming-related context alongside other life data. Like Gmail, Steam accounts are per-user and entity-tied, so multi-account support follows the same registry pattern.

## What Changes

- **New `public.steam_accounts` registry table** — stores connected Steam accounts with companion entities, API key credentials, and per-account metadata. Follows the `google_accounts` pattern.
- **New `module-steam` butler module** — provides MCP tools for querying Steam data (player profiles, game libraries, achievements, friends, game news, player counts). Read-only. Defaults to owner's linked account when `steam_id` is omitted.
- **New `connector-steam` background process** — long-running connector that polls Steam accounts for activity changes (recently played games, achievement unlocks, online status changes) and submits `ingest.v1` events to Switchboard. Polling-only (Steam has no push/webhook). Implements the connector base contract.
- **Dashboard API routes** for Steam account management (connect, disconnect, status).

## Capabilities

### New Capabilities

- `steam-account-registry`: Shared registry table for connected Steam accounts with companion entities, API key credential storage, and account lifecycle management
- `module-steam`: Butler module providing read-only MCP tools for Steam Web API queries (player summaries, game libraries, achievements, friends, news, player counts)
- `connector-steam`: Background connector process that polls Steam accounts for activity changes, persists per-game daily playtime aggregates, and submits ingest.v1 events to Switchboard
- `dashboard-steam`: Dashboard API routes and UI for Steam account management (connect/disconnect, status, activity overview) and playtime analytics

### Modified Capabilities

_(none — this is a greenfield integration with no changes to existing specs)_

## Impact

- **RFC 0003 amendment:** Introduces new `source.channel = "gaming"` / `source.provider = "steam"` pair not currently in the Switchboard ingestion contract. Requires RFC 0003 update to register this channel/provider.
- **RFC 0004 extension:** Introduces new `entity_info.type = 'steam_api_key'` for credential storage on companion entities.
- **Database:** New `public.steam_accounts` table + Alembic migration; new `entity_info` types for Steam API key storage
- **Modules:** New `src/butlers/modules/steam.py` implementing `Module` base class
- **Connectors:** New `src/butlers/connectors/steam.py` implementing connector base contract
- **Config:** New `[modules.steam]` section in `butler.toml` for butlers that enable it
- **Dashboard:** New API routes for Steam account CRUD (connect/disconnect/status)
- **Dependencies:** `httpx` (already in deps) for async HTTP to Steam API
- **External:** Requires Steam Web API key per connected account (free registration at steamcommunity.com/dev/apikey)
