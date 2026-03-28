## Context

Butlers integrates with external services through two complementary patterns: **modules** (MCP tools exposed to butler LLM sessions) and **connectors** (background processes that ingest events into Switchboard). Gmail established the reference implementation for both patterns, plus a shared account registry for multi-account support with entity-tied credentials.

Steam's Web API is simpler than Gmail's: API key auth (no OAuth), read-only access, polling-only (no push/webhook), and JSON responses. This simplicity means we can follow the Gmail architectural pattern while stripping out the OAuth, push notification, and write-path complexity.

The Steam Web API base URL is `https://api.steampowered.com/{interface}/{method}/v{version}/`. Authentication is via `key` query parameter. Key interfaces: `ISteamUser`, `IPlayerService`, `ISteamUserStats`, `ISteamApps`, `ISteamNews`.

## Goals / Non-Goals

**Goals:**

- Multi-account Steam integration following the google_accounts registry pattern
- Read-only MCP tools for on-demand Steam data queries
- Background connector polling for gaming activity events (play sessions, achievements)
- Entity-tied credential storage (API key per account in `entity_info`)
- Per-account error isolation in the connector

**Non-Goals:**

- Steam OAuth/OpenID authentication flow — API keys are manually registered and entered via dashboard
- Publisher/partner API access — user-level keys only
- Steam Trading, Market, or Workshop integration — read-only player data only
- Steam Community features (groups, forums, screenshots) — out of scope for v1
- Real-time presence tracking — polling intervals are minutes, not seconds
- Game-specific APIs (Dota 2, CS2, TF2) — generic interfaces only for v1

## Decisions

### D1: API key auth, no OAuth flow

Steam Web API uses simple API keys registered at `steamcommunity.com/dev/apikey`. Unlike Google, there's no OAuth dance — users register a key manually and enter it through the dashboard.

**Rationale:** Steam's API key system is simpler than OAuth. Building an OAuth flow would be unnecessary complexity for a key-copy-paste operation. The dashboard provides a "Connect Steam Account" form that accepts SteamID + API key, validates the key via a test API call, and stores it.

**Alternative considered:** Steam OpenID for identity verification + separate API key entry. Rejected because OpenID only proves identity — it doesn't provide API access, so you still need the key. Adding OpenID would add web-redirect complexity for minimal benefit.

### D2: Shared `steam_accounts` registry table

Following `public.google_accounts`, create `public.steam_accounts` with companion entities for credential anchoring.

**Rationale:** Proven pattern. Entity-tied credentials let modules resolve API keys through the standard `entity_info` lookup. Companion entities keep Steam accounts visible in the entity graph without polluting the owner's entity.

**Key difference from google_accounts:** `steam_id` (bigint) replaces `email` as the unique external identifier. Steam's 64-bit SteamID is the canonical identity. Display names are mutable and non-unique.

**Schema note:** Cross-butler tables live in the `public` schema (post `core_041` migration). All SQL references in migrations and code MUST use fully qualified `public.steam_accounts`, `public.entities`, `public.entity_info` to avoid shadowing by butler-local tables (e.g., `general.entities`).

### D3: Single `httpx.AsyncClient` with shared Steam API client

Wrap Steam API calls in a thin `SteamAPIClient` class that handles:
- API key injection (query param)
- Rate limit awareness (back off on 429/403)
- Response validation (Steam returns `{"response": {...}}` wrappers)
- Batch support for `GetPlayerSummaries` (up to 100 SteamIDs)

Both the module and connector instantiate their own `SteamAPIClient` per account. The client is stateless except for the `httpx.AsyncClient` connection pool.

**Rationale:** Centralizes Steam API quirks (wrapper unwrapping, error codes, rate limiting) in one place. Both module and connector need the same API calls.

**Location:** `src/butlers/steam/client.py` — shared package, not inside modules or connectors.

### D4: Polling-only connector with configurable intervals per data type

Steam has no push/webhook mechanism. The connector polls at configurable intervals:

| Data type | Default interval | API call |
|---|---|---|
| Recently played games | 5 min | `IPlayerService/GetRecentlyPlayedGames` |
| Achievement unlocks | 15 min | `ISteamUserStats/GetPlayerAchievements` (per tracked game) |
| Online status | 5 min | `ISteamUser/GetPlayerSummaries` |
| Friend list changes | 60 min | `ISteamUser/GetFriendList` |
| Game library (purchases) | 24 hours | `IPlayerService/GetOwnedGames` |

**Rationale:** Different data types have different volatility. Play session tracking benefits from shorter intervals; friend list changes are rare. Per-type intervals reduce unnecessary API calls while keeping high-value data fresh.

**Cursor strategy:** Per-account, per-data-type last-poll timestamp stored in `connectors.steam_cursors` table. The connector compares current state against last-known state to detect changes (delta detection).

### D5: Delta detection via state diffing (not history API)

Unlike Gmail (which has a history API for incremental changes), Steam's API only returns current state. The connector must diff current responses against cached previous state to detect changes.

**Implementation:**
- Store last-known state hash + full response per data type in the cursor table
- On each poll, fetch current state, compare hash, emit events only for detected changes
- For achievements: track per-game achievement set, emit events for newly unlocked achievements
- For recently played: track playtime per game, emit events for new play sessions (playtime delta > 0)

**Rationale:** This is the only option — Steam has no change feed or history API. Hash comparison is cheap and avoids storing full previous responses in memory.

### D6: Module tools default to owner's account

When `steam_id` is omitted from module tool calls, the module resolves the owner's primary Steam account from `public.steam_accounts WHERE is_primary = true` and uses that account's SteamID and API key.

**Rationale:** Consistent with how email tools default to the owner's account. Most queries are "show me my games" not "show me someone else's games."

### D7: Privacy-aware error handling

Steam data visibility depends on the target player's privacy settings. Private profiles return empty data or HTTP 401 on friend lists.

**Approach:** Tools return structured error responses indicating privacy restrictions rather than generic failures. E.g., `{"error": "profile_private", "message": "This player's game library is not publicly visible", "hint": "The player must set their game details to public in Steam privacy settings"}`.

**Rationale:** LLM agents need actionable error context to explain results to users. A generic "API error" is unhelpful.

## Risks / Trade-offs

- **[Undocumented rate limits]** → Steam's rate limits are opaque (~100K/day soft limit). Mitigation: conservative default poll intervals, exponential backoff on 429/403, per-account rate tracking in connector metrics. If rate limited, degrade gracefully (skip cycle, don't crash).

- **[Privacy settings break data access]** → Private profiles return empty/error responses with no way to detect this upfront. Mitigation: privacy-aware error types (D7), document limitation in tool descriptions.

- **[No push = polling latency]** → Minimum ~5 min latency for activity detection. Mitigation: acceptable for gaming data (not time-critical like email). Configurable intervals let users tune.

- **[Achievement tracking scales with game count]** → Tracking achievements per-game means O(games) API calls per poll cycle. Mitigation: only track games played in last 2 weeks (from `GetRecentlyPlayedGames`), configurable max tracked games (default 10).

- **[API key management is manual]** → No OAuth flow means users must register and paste API keys. Mitigation: dashboard provides clear instructions with link to Steam dev portal, validates key on entry.

## Resolved Questions

- **Q1 (game purchases):** Yes — the connector tracks new game acquisitions by polling `GetOwnedGames` once daily (86400s). The initial poll establishes a baseline without emitting events. Daily frequency keeps API cost low while still catching purchases within 24 hours.
- **Q2 (vanity URL fallback):** No — `steam_resolve_vanity_url` is a standalone tool. It is NOT integrated as an automatic fallback into other tools. Most flows already have numeric SteamIDs from the account registry or friend list. Vanity URL resolution is a convenience for ad-hoc lookups ("look up my friend gaben").
