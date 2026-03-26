## 1. Extend SpotifyClient with Write Methods

- [ ] 1.1 Add `_put()`, `_post()`, `_delete()` internal request helpers to `SpotifyClient` mirroring the existing `_get()` pattern (auth, retry on 401, rate-limit handling)
- [ ] 1.2 Add `search(query, type, limit)` method — `GET /v1/search`
- [ ] 1.3 Add `get_playback_state()` method — `GET /v1/me/player` (full player state, not just currently-playing)
- [ ] 1.4 Add `get_queue()` method — `GET /v1/me/player/queue`
- [ ] 1.5 Add `get_top_items(type, time_range, limit)` method — `GET /v1/me/top/{type}`
- [ ] 1.6 Add playback control methods: `play()`, `pause()`, `skip_next()`, `skip_previous()`, `seek()`, `set_volume()`, `add_to_queue()`, `transfer_playback()`
- [ ] 1.7 Add playlist methods: `get_playlists()`, `create_playlist()`, `add_tracks_to_playlist()`, `remove_tracks_from_playlist()`, `get_playlist_tracks()`
- [ ] 1.8 Add discovery methods: `get_recommendations(seed_artists, seed_tracks, seed_genres, limit)`, `get_related_artists(artist_id)`
- [ ] 1.9 Add library methods: `get_saved_tracks()`, `save_tracks()`, `remove_saved_tracks()`
- [ ] 1.10 Write unit tests for all new SpotifyClient methods (mock httpx responses)

## 2. Create Spotify Module

- [ ] 2.1 Create `src/butlers/modules/spotify.py` implementing the `Module` base class with `name="spotify"`, config schema, and empty `register_tools()`
- [ ] 2.2 Implement `on_startup()` — resolve credentials from CredentialStore, construct SpotifyClient, call `get_me()` to verify and cache user profile
- [ ] 2.3 Implement `on_shutdown()` — close SpotifyClient HTTP client
- [ ] 2.4 Register read tools: `spotify_search`, `spotify_get_playback_state`, `spotify_get_queue`, `spotify_get_top_items`
- [ ] 2.5 Register discovery tools: `spotify_get_recommendations` (with graceful 403/404 degradation), `spotify_get_related_artists`
- [ ] 2.6 Register playback control tools: `spotify_play`, `spotify_pause`, `spotify_skip_next`, `spotify_skip_previous`, `spotify_seek`, `spotify_set_volume`, `spotify_add_to_queue`, `spotify_transfer_playback`
- [ ] 2.7 Register playlist tools: `spotify_get_playlists`, `spotify_create_playlist`, `spotify_add_tracks_to_playlist`, `spotify_remove_tracks_from_playlist`, `spotify_get_playlist_tracks`
- [ ] 2.8 Register library tools: `spotify_get_saved_tracks`, `spotify_save_tracks`, `spotify_remove_saved_tracks`
- [ ] 2.9 Implement `tool_metadata()` returning sensitivity levels (read vs write) for all tools
- [ ] 2.10 Add Premium-required error handling in playback control tools (catch 403, return actionable message)
- [ ] 2.11 Add missing-credentials error handling (return actionable message when Spotify not connected)
- [ ] 2.12 Write unit tests for module lifecycle (startup, shutdown, tool registration)
- [ ] 2.13 Write unit tests for each tool group (search, discovery, playback, playlist, library) with mocked SpotifyClient

## 3. Expand OAuth Scopes

- [ ] 3.1 Update `_DEFAULT_SCOPES` in `src/butlers/api/routers/spotify.py` to include write scopes: `playlist-read-private`, `playlist-read-collaborative`, `playlist-modify-public`, `playlist-modify-private`, `user-modify-playback-state`, `user-library-read`, `user-library-modify`
- [ ] 3.2 Add scope mismatch detection to the `/status` endpoint — compare stored token scopes against required scopes, return `needs_reauth` state with `missing_scopes` list
- [ ] 3.3 Store granted scopes in CredentialStore during OAuth callback (new key `SPOTIFY_GRANTED_SCOPES`)
- [ ] 3.4 Update existing OAuth flow tests to cover expanded scope set
- [ ] 3.5 Write tests for scope mismatch detection and `needs_reauth` status

## 4. Dashboard Re-authorization UX

- [ ] 4.1 Add `needs_reauth` state handling to `SpotifySetupCard.tsx` — show banner explaining "New features require additional permissions" with Re-authorize button
- [ ] 4.2 Update `use-spotify.ts` hooks to handle `needs_reauth` status from the API
- [ ] 4.3 Verify the existing Re-authorize button (already in connected state) triggers the full OAuth flow with expanded scopes

## 5. Butler Configuration

- [ ] 5.1 Add `[modules.spotify]` section to the Lifestyle butler's `roster/lifestyle/butler.toml` (primary butler that owns music/entertainment domain)
- [ ] 5.2 Document Spotify tools in the Lifestyle butler's CLAUDE.md tool inventory

## 6. Integration Testing

- [ ] 6.1 Write integration test: module startup with valid credentials → all tools registered
- [ ] 6.2 Write integration test: module startup with missing credentials → tools return actionable errors
- [ ] 6.3 Write integration test: playlist create → add tracks → get tracks → remove tracks flow
- [ ] 6.4 Write integration test: search → play from results flow (mocked at HTTP level)
