# Spotify Module

## Purpose

The Spotify module is an MCP module providing butler tools for bidirectional Spotify interaction: playback control, playlist management, library operations, and catalog search. It follows the standard Module pattern (config schema, `register_tools()`, credential resolution via `CredentialStore`) and reuses the `SpotifyClient` extended with write methods.

## ADDED Requirements

### Requirement: Module Identity and Configuration

The Spotify module SHALL implement the `Module` base class with name `"spotify"` and a Pydantic config schema.

#### Scenario: Module registration

- **WHEN** a butler's `butler.toml` includes `[modules.spotify]`
- **THEN** the module SHALL be discovered and registered during butler startup
- **AND** it SHALL have `name = "spotify"` and `dependencies = []`

#### Scenario: Config schema defaults

- **WHEN** `[modules.spotify]` is present with no additional keys
- **THEN** the module SHALL use default configuration
- **AND** `playback_tools` SHALL default to `true` (registered but may fail at runtime on free accounts)

### Requirement: Credential Resolution

The module SHALL resolve Spotify OAuth credentials from the shared `spotify` category in `CredentialStore`.

#### Scenario: Successful credential resolution at startup

- **WHEN** `on_startup` is called with a `CredentialStore`
- **THEN** the module SHALL resolve `SPOTIFY_CLIENT_ID`, `SPOTIFY_ACCESS_TOKEN`, `SPOTIFY_REFRESH_TOKEN`, and `SPOTIFY_TOKEN_EXPIRES_AT`
- **AND** it SHALL construct a `SpotifyClient` instance with those credentials
- **AND** it SHALL call `get_me()` to verify connectivity and cache the user's Spotify profile (including `product` tier)

#### Scenario: Missing credentials at startup

- **WHEN** `on_startup` is called but `SPOTIFY_ACCESS_TOKEN` or `SPOTIFY_REFRESH_TOKEN` is not found
- **THEN** the module SHALL log a warning "Spotify module: no credentials found. Connect Spotify via dashboard settings."
- **AND** all registered tools SHALL return an actionable error when called: "Spotify not connected. Visit dashboard settings to link your Spotify account."

### Requirement: Catalog Search Tool

The module SHALL register a tool for searching the Spotify catalog.

#### Scenario: Search by query

- **WHEN** `spotify_search` is called with `query` (string) and optional `type` (default `"track"`, one of `track`, `artist`, `album`, `playlist`), and optional `limit` (default 10, max 50)
- **THEN** the module SHALL call `GET /v1/search?q={query}&type={type}&limit={limit}`
- **AND** it SHALL return the search results with `items` containing name, id, uri, and relevant metadata

### Requirement: Discovery Tools

The module SHALL register tools for music discovery via Spotify's recommendation and relationship endpoints.

#### Scenario: Get recommendations

- **WHEN** `spotify_get_recommendations` is called with optional `seed_artists` (list of artist IDs, max 5 total seeds), optional `seed_tracks` (list of track IDs), optional `seed_genres` (list of genre strings), and optional `limit` (default 20, max 100)
- **THEN** the module SHALL call `GET /v1/recommendations` with the provided seeds
- **AND** it SHALL return recommended tracks with name, artist, album, uri, and preview_url

#### Scenario: Recommendations endpoint unavailable

- **WHEN** `spotify_get_recommendations` is called and Spotify returns HTTP 403 or 404
- **THEN** the tool SHALL return a graceful error: "Spotify Recommendations API is not available for this app. Use spotify_search and spotify_get_related_artists for discovery instead."

#### Scenario: Get related artists

- **WHEN** `spotify_get_related_artists` is called with `artist_id` (string)
- **THEN** the module SHALL call `GET /v1/artists/{artist_id}/related-artists`
- **AND** it SHALL return up to 20 related artists with name, id, uri, genres, and popularity

### Requirement: Playback State Tools

The module SHALL register tools for reading playback state.

#### Scenario: Get current playback state

- **WHEN** `spotify_get_playback_state` is called
- **THEN** the module SHALL call `GET /v1/me/player`
- **AND** it SHALL return the current device, track, progress, shuffle/repeat state, or `null` if nothing is playing

#### Scenario: Get queue

- **WHEN** `spotify_get_queue` is called
- **THEN** the module SHALL call `GET /v1/me/player/queue`
- **AND** it SHALL return the currently playing track and the upcoming queue

#### Scenario: Get top items

- **WHEN** `spotify_get_top_items` is called with `type` (`artists` or `tracks`) and optional `time_range` (default `"medium_term"`, one of `short_term`, `medium_term`, `long_term`) and optional `limit` (default 10, max 50)
- **THEN** the module SHALL call `GET /v1/me/top/{type}`
- **AND** it SHALL return the user's top items for the specified time range

### Requirement: Playback Control Tools

The module SHALL register tools for controlling playback. These tools require Spotify Premium.

#### Scenario: Play

- **WHEN** `spotify_play` is called with optional `context_uri` (album/playlist URI), optional `uris` (list of track URIs), and optional `device_id`
- **THEN** the module SHALL call `PUT /v1/me/player/play`
- **AND** if neither `context_uri` nor `uris` is provided, it SHALL resume current playback

#### Scenario: Pause

- **WHEN** `spotify_pause` is called with optional `device_id`
- **THEN** the module SHALL call `PUT /v1/me/player/pause`

#### Scenario: Skip to next track

- **WHEN** `spotify_skip_next` is called with optional `device_id`
- **THEN** the module SHALL call `POST /v1/me/player/next`

#### Scenario: Skip to previous track

- **WHEN** `spotify_skip_previous` is called with optional `device_id`
- **THEN** the module SHALL call `POST /v1/me/player/previous`

#### Scenario: Seek to position

- **WHEN** `spotify_seek` is called with `position_ms` (integer) and optional `device_id`
- **THEN** the module SHALL call `PUT /v1/me/player/seek?position_ms={position_ms}`

#### Scenario: Set volume

- **WHEN** `spotify_set_volume` is called with `volume_percent` (integer 0-100) and optional `device_id`
- **THEN** the module SHALL call `PUT /v1/me/player/volume?volume_percent={volume_percent}`

#### Scenario: Add to queue

- **WHEN** `spotify_add_to_queue` is called with `uri` (track or episode URI) and optional `device_id`
- **THEN** the module SHALL call `POST /v1/me/player/queue?uri={uri}`

#### Scenario: Transfer playback

- **WHEN** `spotify_transfer_playback` is called with `device_id` (string) and optional `play` (boolean, default true)
- **THEN** the module SHALL call `PUT /v1/me/player` with `{"device_ids": [device_id], "play": play}`

#### Scenario: Premium required error

- **WHEN** any playback control tool is called and Spotify returns HTTP 403 with a "Premium required" error
- **THEN** the tool SHALL return an error: "This action requires Spotify Premium. Your account ({product}) does not support playback control. You can still use playlist, library, and search tools."

### Requirement: Playlist Management Tools

The module SHALL register tools for creating and managing playlists.

#### Scenario: Get user's playlists

- **WHEN** `spotify_get_playlists` is called with optional `limit` (default 20, max 50) and optional `offset` (default 0)
- **THEN** the module SHALL call `GET /v1/me/playlists`
- **AND** it SHALL return playlist id, name, description, track count, public/collaborative flags, and owner

#### Scenario: Create playlist

- **WHEN** `spotify_create_playlist` is called with `name` (string), optional `description` (string), and optional `public` (boolean, default false)
- **THEN** the module SHALL call `POST /v1/users/{user_id}/playlists` using the cached user profile `id`
- **AND** it SHALL return the created playlist's id, uri, and external URL

#### Scenario: Add tracks to playlist

- **WHEN** `spotify_add_tracks_to_playlist` is called with `playlist_id` (string) and `uris` (list of track URIs)
- **THEN** the module SHALL call `POST /v1/playlists/{playlist_id}/tracks`
- **AND** it SHALL return the snapshot_id

#### Scenario: Remove tracks from playlist

- **WHEN** `spotify_remove_tracks_from_playlist` is called with `playlist_id` (string) and `uris` (list of track URIs)
- **THEN** the module SHALL call `DELETE /v1/playlists/{playlist_id}/tracks` with body `{"tracks": [{"uri": uri} for uri in uris]}`
- **AND** it SHALL return the snapshot_id

#### Scenario: Get playlist tracks

- **WHEN** `spotify_get_playlist_tracks` is called with `playlist_id` (string), optional `limit` (default 50, max 100), and optional `offset` (default 0)
- **THEN** the module SHALL call `GET /v1/playlists/{playlist_id}/tracks`
- **AND** it SHALL return track name, artist, album, duration, and URI for each item

### Requirement: Library Management Tools

The module SHALL register tools for managing the user's saved tracks.

#### Scenario: Get saved tracks

- **WHEN** `spotify_get_saved_tracks` is called with optional `limit` (default 20, max 50) and optional `offset` (default 0)
- **THEN** the module SHALL call `GET /v1/me/tracks`
- **AND** it SHALL return each saved track with name, artist, album, added_at, and URI

#### Scenario: Save tracks

- **WHEN** `spotify_save_tracks` is called with `ids` (list of track IDs)
- **THEN** the module SHALL call `PUT /v1/me/tracks` with body `{"ids": ids}`

#### Scenario: Remove saved tracks

- **WHEN** `spotify_remove_saved_tracks` is called with `ids` (list of track IDs)
- **THEN** the module SHALL call `DELETE /v1/me/tracks` with body `{"ids": ids}`

### Requirement: Rate Limit and Error Handling

All tools SHALL handle Spotify API errors consistently.

#### Scenario: Rate limit (HTTP 429)

- **WHEN** a Spotify API call returns HTTP 429
- **THEN** the tool SHALL return an error: "Spotify rate limited. Try again in {retry_after} seconds."
- **AND** the `SpotifyClient`'s existing rate-limit handling SHALL apply

#### Scenario: Auth failure (HTTP 401 after refresh)

- **WHEN** a Spotify API call returns HTTP 401 and token refresh also fails
- **THEN** the tool SHALL return an error: "Spotify authorization expired. Re-connect via dashboard settings."

#### Scenario: General API error

- **WHEN** a Spotify API call returns an unexpected error status
- **THEN** the tool SHALL return the status code and a truncated error body for debugging

### Requirement: Tool Sensitivity Metadata

The module SHALL declare sensitivity metadata for tools that modify state.

#### Scenario: Write tool sensitivity

- **WHEN** `tool_metadata()` is called
- **THEN** playback control tools and playlist/library modification tools SHALL be marked as `sensitivity="write"`
- **AND** read-only tools (search, get_playback_state, get_queue, get_playlists, get_playlist_tracks, get_saved_tracks, get_top_items) SHALL be marked as `sensitivity="read"`

### Requirement: Shutdown

The module SHALL clean up resources on shutdown.

#### Scenario: Graceful shutdown

- **WHEN** `on_shutdown` is called
- **THEN** the module SHALL close the `SpotifyClient` HTTP client
