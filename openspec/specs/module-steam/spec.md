# Steam Module

## Purpose

The Steam module provides read-only MCP tools for querying the Steam Web API. It exposes player profiles, game libraries, achievements, friend lists, game news, and player counts to butler LLM sessions. When a SteamID is omitted, tools default to the owner's primary linked Steam account.

## Requirements

### Requirement: Steam Module Configuration

The module is configured via `[modules.steam]` in `butler.toml`.

#### Scenario: Config structure

- **WHEN** `[modules.steam]` is configured
- **THEN** it SHALL accept:
  - `default_account` (optional, UUID or SteamID) — override which Steam account to use as default instead of primary
  - `cache_ttl_seconds` (default 300) — TTL for cached API responses to avoid redundant calls within a session
  - `max_batch_size` (default 100) — max SteamIDs per batch in `GetPlayerSummaries`

#### Scenario: Module name and dependencies

- **WHEN** the Steam module is registered
- **THEN** `module.name` SHALL be `"steam"`
- **AND** `module.dependencies` SHALL be `[]` (no module dependencies)

### Requirement: Credential Resolution

The module resolves Steam API keys at startup from the account registry.

#### Scenario: Startup credential resolution

- **WHEN** `on_startup` is called
- **THEN** the module SHALL query `public.steam_accounts` for the primary account (or `default_account` if configured)
- **AND** resolve the API key from the companion entity's `entity_info` where `type = 'steam_api_key'`
- **AND** cache the resolved key and SteamID for tool use

#### Scenario: No Steam account configured

- **WHEN** `on_startup` is called and no active Steam account exists
- **THEN** the module SHALL log a warning and start in degraded mode
- **AND** all tools SHALL return an actionable error: `{"error": "no_steam_account", "message": "No Steam account is connected.", "hint": "Connect a Steam account via the dashboard settings and set it as primary."}`

### Requirement: Player Summary Tool

#### Scenario: Get player summary with default account

- **WHEN** `steam_get_player_summary` is called without `steam_id`
- **THEN** the tool SHALL use the owner's primary Steam account SteamID
- **AND** call `ISteamUser/GetPlayerSummaries/v2` with the resolved API key
- **AND** return: `persona_name`, `profile_url`, `avatar_url`, `persona_state` (online/offline/busy/away/snooze), `last_logoff` (ISO8601), `profile_visibility` (public/private/friends_only), `game_count` (if public)

#### Scenario: Get player summary for specific SteamID

- **WHEN** `steam_get_player_summary` is called with `steam_id = 76561198000000000`
- **THEN** the tool SHALL call the API with the provided SteamID
- **AND** return the same fields as above

#### Scenario: Private profile handling

- **WHEN** the target player's profile is private
- **THEN** the tool SHALL return limited data (persona_name, avatar_url, persona_state only)
- **AND** include `"profile_visibility": "private"` to explain missing fields

### Requirement: Owned Games Tool

#### Scenario: Get owned games

- **WHEN** `steam_get_owned_games` is called with optional `steam_id` and `include_free_games` (default true)
- **THEN** the tool SHALL call `IPlayerService/GetOwnedGames/v1` with `include_appinfo=true` and `include_played_free_games` matching the `include_free_games` parameter
- **AND** return `game_count` and a list of games with: `app_id`, `name`, `playtime_forever_minutes`, `playtime_2weeks_minutes` (if any), `icon_url`, `logo_url`

#### Scenario: Private game library

- **WHEN** the target player's game details are private
- **THEN** the tool SHALL return `{"error": "profile_private", "message": "This player's game library is not publicly visible.", "hint": "The player must set 'Game details' to public in Steam privacy settings."}`

### Requirement: Recently Played Games Tool

#### Scenario: Get recently played games

- **WHEN** `steam_get_recently_played` is called with optional `steam_id` and `count` (default 10)
- **THEN** the tool SHALL call `IPlayerService/GetRecentlyPlayedGames/v1` with the count parameter
- **AND** return `total_count` and a list of games with: `app_id`, `name`, `playtime_2weeks_minutes`, `playtime_forever_minutes`, `icon_url`

### Requirement: Achievements Tool

#### Scenario: Get achievements for a game

- **WHEN** `steam_get_achievements` is called with `app_id` and optional `steam_id`
- **THEN** the tool SHALL call `ISteamUserStats/GetPlayerAchievements/v1` with the app ID and SteamID
- **AND** return `game_name`, `achievements` (list with `api_name`, `name`, `description`, `achieved` boolean, `unlock_time` ISO8601 if achieved)

#### Scenario: Game has no achievements

- **WHEN** the target game does not support achievements
- **THEN** the tool SHALL return `{"error": "no_achievements", "message": "This game does not have Steam achievements.", "hint": "Not all Steam games support achievements."}`

#### Scenario: Achievement stats unavailable

- **WHEN** the player's profile or game stats are private
- **THEN** the tool SHALL return an actionable privacy error

### Requirement: Friend List Tool

#### Scenario: Get friend list

- **WHEN** `steam_get_friend_list` is called with optional `steam_id`
- **THEN** the tool SHALL call `ISteamUser/GetFriendList/v1` with `relationship=friend`
- **AND** return a list of friends with: `steam_id`, `relationship`, `friend_since` (ISO8601)
- **AND** if the caller provides `enrich=true`, batch-fetch player summaries for all friends via `GetPlayerSummaries` (up to 100 per call) and include `persona_name`, `avatar_url`, `persona_state`

#### Scenario: Private friend list

- **WHEN** the target player's friend list is private
- **THEN** the tool SHALL return `{"error": "profile_private", "message": "This player's friend list is not publicly visible.", "hint": "The player must set 'Friends list' to public in Steam privacy settings."}`

### Requirement: Game News Tool

#### Scenario: Get news for a game

- **WHEN** `steam_get_game_news` is called with `app_id` and optional `count` (default 5)
- **THEN** the tool SHALL call `ISteamNews/GetNewsForApp/v2` with the app ID and count
- **AND** return a list of news items with: `gid` (news ID), `title`, `url`, `author`, `contents` (truncated to a configurable `max_length`, default 300 chars), `date` (ISO8601), `feed_label`
- **AND** this tool does NOT require a SteamID parameter (game news is public)

### Requirement: Player Level Tool

#### Scenario: Get player level and badges

- **WHEN** `steam_get_player_level` is called with optional `steam_id`
- **THEN** the tool SHALL call `IPlayerService/GetSteamLevel/v1` for the level
- **AND** return `steam_level`

### Requirement: Current Players Tool

#### Scenario: Get current player count

- **WHEN** `steam_get_current_players` is called with `app_id`
- **THEN** the tool SHALL call `ISteamUserStats/GetNumberOfCurrentPlayers/v1`
- **AND** return `app_id`, `player_count`
- **AND** this tool does NOT require a SteamID or API key (public endpoint)

### Requirement: Vanity URL Resolution Tool

#### Scenario: Resolve vanity URL

- **WHEN** `steam_resolve_vanity_url` is called with `vanity_name`
- **THEN** the tool SHALL call `ISteamUser/ResolveVanityURL/v1` with the vanity name
- **AND** return `steam_id` if found, or `{"error": "not_found", "message": "No Steam account found for vanity URL '<vanity_name>'.", "hint": "Check the spelling or use a numeric SteamID instead."}` if not

### Requirement: Tool Metadata for Argument Sensitivity

#### Scenario: API key is never logged

- **WHEN** `tool_metadata()` is queried
- **THEN** the module SHALL mark internal API key parameters as sensitive
- **AND** API keys SHALL never appear in session logs or tool call traces
