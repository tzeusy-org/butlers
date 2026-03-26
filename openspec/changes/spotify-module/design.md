## Context

The Butlers framework has a mature Spotify _connector_ (polling-based ingestion of listening events) with:
- `SpotifyClient` (`src/butlers/connectors/spotify_client.py`) — async HTTP client with GET-only methods (`get_me`, `get_currently_playing`, `get_recently_played`), automatic token refresh, rate-limit handling.
- OAuth 2.0 PKCE flow (`src/butlers/api/routers/spotify.py`) — dashboard account linking with read-only scopes.
- `CredentialStore` integration — tokens stored in `butler_secrets` table under `category="spotify"`.

What's missing: a **Module** that exposes MCP tools so butlers can _write_ to Spotify (create playlists, control playback, search catalog). The module pattern is well-established (Telegram, Email, WhatsApp modules exist as reference implementations).

## Goals / Non-Goals

**Goals:**
- Register MCP tools for playlist CRUD, playback control, library operations, and catalog search
- Extend `SpotifyClient` with write methods (POST/PUT/DELETE) reusing existing auth, refresh, and rate-limit infrastructure
- Expand OAuth scopes to cover write operations while maintaining backward compatibility (re-auth prompt)
- Follow the existing module pattern: config schema, `register_tools()`, credential resolution via `CredentialStore`
- Handle Spotify Premium requirement gracefully (playback control needs Premium; playlist/search do not)

**Non-Goals:**
- Real-time playback event streaming (handled by connector)
- Spotify-to-butler ingest pipeline changes (connector's domain)
- Multi-user Spotify support (user-federated model: one Spotify account per instance)
- Podcast/audiobook playback control (track-type only for v1)
- Offline playback or device transfer logic

## Decisions

### 1. Extend `SpotifyClient` rather than creating a separate client

**Decision:** Add write methods directly to the existing `SpotifyClient` class.

**Rationale:** The client already handles auth, token refresh, and rate limiting correctly. A second client would duplicate this infrastructure. The existing `_get()` helper pattern extends naturally to `_put()`, `_post()`, `_delete()` methods with the same retry and error-handling semantics.

**Alternative considered:** Separate `SpotifyWriteClient` — rejected because it would need to share the same credential state and token refresh logic, leading to coordination complexity.

### 2. Module resolves credentials from shared `spotify` category

**Decision:** The module uses the exact same `CredentialStore` keys and `spotify` category as the connector. No separate credential entries.

**Rationale:** Both the connector and module operate on behalf of the same Spotify user. Token refresh by either component benefits the other (tokens stored back to `CredentialStore`). The `CredentialStore` is the single source of truth.

### 3. OAuth scope expansion with re-authorization flow

**Decision:** Expand `_DEFAULT_SCOPES` in the OAuth router to include both read and write scopes. Detect scope mismatch on existing tokens and surface a `needs_reauth` status.

**Rationale:** Spotify does not support incremental scope grants — the user must re-authorize with the full scope set. The dashboard already handles `needs_auth` state; extending to `needs_reauth` is straightforward.

**Scope set (full):**
- Read (existing): `user-read-playback-state`, `user-read-recently-played`, `user-top-read`
- Read (new): `playlist-read-private`, `playlist-read-collaborative`, `user-library-read`
- Write (new): `playlist-modify-public`, `playlist-modify-private`, `user-modify-playback-state`, `user-library-modify`

### 4. Tool grouping by Spotify Premium requirement

**Decision:** Split tools into two tiers:
- **Free tier** (works with any Spotify account): playlist CRUD, library read/modify, catalog search, get current playback state
- **Premium tier** (requires Spotify Premium): playback control (play, pause, skip, seek, queue, set volume, transfer playback)

Premium tools check the user's `product` field from `get_me()` at module startup and register conditionally, or return an actionable error if the user's plan doesn't support the operation.

**Rationale:** Spotify returns 403 for playback control on free accounts. Failing at tool-call time with a clear message is better than hiding tools entirely (the butler should know _why_ it can't control playback).

### 5. MCP tool design: thin wrappers over SpotifyClient

**Decision:** Each MCP tool is a thin async function that validates input, calls the corresponding `SpotifyClient` method, and returns a structured dict. No business logic in tools.

**Tool inventory:**

| Tool | Spotify Endpoint | Tier |
|------|-----------------|------|
| `spotify_search` | `GET /v1/search` | Free |
| `spotify_get_playback_state` | `GET /v1/me/player` | Free |
| `spotify_get_queue` | `GET /v1/me/player/queue` | Free |
| `spotify_play` | `PUT /v1/me/player/play` | Premium |
| `spotify_pause` | `PUT /v1/me/player/pause` | Premium |
| `spotify_skip_next` | `POST /v1/me/player/next` | Premium |
| `spotify_skip_previous` | `POST /v1/me/player/previous` | Premium |
| `spotify_seek` | `PUT /v1/me/player/seek` | Premium |
| `spotify_set_volume` | `PUT /v1/me/player/volume` | Premium |
| `spotify_add_to_queue` | `POST /v1/me/player/queue` | Premium |
| `spotify_transfer_playback` | `PUT /v1/me/player` | Premium |
| `spotify_get_recommendations` | `GET /v1/recommendations` | Free (may be restricted) |
| `spotify_get_related_artists` | `GET /v1/artists/{id}/related-artists` | Free |
| `spotify_get_playlists` | `GET /v1/me/playlists` | Free |
| `spotify_create_playlist` | `POST /v1/users/{user_id}/playlists` | Free |
| `spotify_add_tracks_to_playlist` | `POST /v1/playlists/{id}/tracks` | Free |
| `spotify_remove_tracks_from_playlist` | `DELETE /v1/playlists/{id}/tracks` | Free |
| `spotify_get_playlist_tracks` | `GET /v1/playlists/{id}/tracks` | Free |
| `spotify_get_saved_tracks` | `GET /v1/me/tracks` | Free |
| `spotify_save_tracks` | `PUT /v1/me/tracks` | Free |
| `spotify_remove_saved_tracks` | `DELETE /v1/me/tracks` | Free |
| `spotify_get_top_items` | `GET /v1/me/top/{type}` | Free |

### 6. No new database tables

**Decision:** The module requires no butler-specific database tables.

**Rationale:** All state is in Spotify's API (playlists, library, playback). Credentials use the shared `butler_secrets` table. No local caching of Spotify data is needed — the API is the source of truth.

## Risks / Trade-offs

- **[Scope re-authorization]** Existing connected users must re-authorize to pick up write scopes. → Mitigation: Dashboard shows clear `needs_reauth` state with one-click re-authorize button (already partially implemented in the `SpotifySetupCard`).
- **[Rate limiting]** Write operations count against the same Spotify rate limit budget as connector polling. → Mitigation: Module tools are user-initiated (not polling), so frequency is naturally low. The existing rate-limit handling in `SpotifyClient` applies uniformly.
- **[Premium gating]** Playback control tools fail on free accounts. → Mitigation: Tools return actionable error messages ("Spotify Premium required for playback control"). The butler can still use playlist, library, and search tools.
- **[Token contention]** Both connector and module may refresh the same token concurrently. → Mitigation: `CredentialStore.store()` is idempotent — last writer wins, and both components read from the store. Worst case: one extra refresh cycle, no data loss.
- **[Recommendations API availability]** Spotify has been restricting the `/v1/recommendations` endpoint for newer developer apps since late 2024. → Mitigation: Tool degrades gracefully on 403/404 with an actionable error pointing to `spotify_search` and `spotify_get_related_artists` as alternatives. Discovery still works without it.
- **[Scope creep]** 22 tools is a large surface for v1. → Mitigation: Tools are thin wrappers with minimal logic. Each maps 1:1 to a Spotify endpoint. No combinatorial complexity.
