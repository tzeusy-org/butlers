# Spotify Connector — Delta for spotify-module change

## MODIFIED Requirements

### Requirement: Required Spotify API Scopes

The OAuth authorization request SHALL include the scopes needed for both connector operation and module tools.

#### Scenario: Scope specification

- **WHEN** the OAuth authorization URL is constructed
- **THEN** the `scope` parameter SHALL include read scopes:
  - `user-read-playback-state` — read current playback state
  - `user-read-recently-played` — read recently played tracks
  - `user-top-read` — read top artists and tracks
  - `playlist-read-private` — read private playlists
  - `playlist-read-collaborative` — read collaborative playlists
  - `user-library-read` — read saved tracks/albums
- **AND** write scopes:
  - `playlist-modify-public` — create/edit public playlists
  - `playlist-modify-private` — create/edit private playlists
  - `user-modify-playback-state` — control playback (Premium)
  - `user-library-modify` — save/remove library tracks
