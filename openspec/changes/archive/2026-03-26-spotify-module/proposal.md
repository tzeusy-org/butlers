## Why

Butlers can passively ingest Spotify listening data via the existing connector, but they cannot _act_ on Spotify — no creating playlists, no controlling playback, no searching the catalog. The connector is read-only ingestion; a proper module would give butlers stateful, bidirectional MCP tools so they can do things like "make me a focus playlist", "skip this track", or "queue up that album we were talking about". The OAuth flow already exists but only requests read scopes — extending it to include write scopes is low-effort and unlocks high-value butler capabilities.

## What Changes

- **New `spotify` module** (`src/butlers/modules/spotify.py`): Implements the `Module` base class, registers MCP tools for playlist management, playback control, library operations, and catalog search. Reuses the existing `SpotifyClient` (extended with write methods).
- **Extended `SpotifyClient`**: Add PUT/POST/DELETE methods for playback control, playlist CRUD, library modification, and search. The existing GET-only client becomes a full read/write API surface.
- **Expanded OAuth scopes**: The OAuth PKCE flow currently requests only `user-read-playback-state`, `user-read-recently-played`, `user-top-read`. Add write scopes: `playlist-modify-public`, `playlist-modify-private`, `playlist-read-private`, `playlist-read-collaborative`, `user-modify-playback-state`, `user-library-read`, `user-library-modify`. Existing tokens will need re-authorization to pick up new scopes.
- **Module configuration in butler.toml**: The Lifestyle butler (primary home) enables `[modules.spotify]` in its config. Other butlers that want Spotify tools can also add it. The module resolves credentials from `CredentialStore` (same `spotify` category as the connector).
- **Re-authorization UX**: Dashboard shows a "Re-authorize" prompt when connected tokens lack the required scopes.

## Capabilities

### New Capabilities
- `module-spotify`: MCP module providing butler tools for Spotify playback control, playlist management, library operations, and catalog search. Covers tool registration, credential resolution, scope validation, and rate-limit handling.

### Modified Capabilities
- `connector-spotify`: OAuth scopes must be expanded to include write permissions. The connector itself is unchanged, but the shared OAuth flow must request the union of connector + module scopes.
- `dashboard-spotify-setup`: Re-authorization flow when existing tokens lack required scopes. UI indication of current scope coverage.

## Impact

- **`src/butlers/connectors/spotify_client.py`**: Extended with POST/PUT/DELETE methods for write operations. Existing read methods unchanged.
- **`src/butlers/api/routers/spotify.py`**: `_DEFAULT_SCOPES` expanded to include write scopes. Existing connected users will see `needs_reauth` status until they re-authorize.
- **`roster/*/butler.toml`**: Butlers wanting Spotify tools add `[modules.spotify]` section.
- **Frontend `SpotifySetupCard.tsx`**: Handle `needs_reauth` state showing scope upgrade prompt.
- **No new database tables**: Module uses existing `CredentialStore` and `butler_secrets` infrastructure.
- **No new Python dependencies**: `httpx` already available.
- **Spotify Premium requirement**: Playback control endpoints require Spotify Premium. Module tools must handle `403 Premium Required` gracefully with actionable error messages.
