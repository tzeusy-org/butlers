# Dashboard Spotify Setup — Delta for spotify-module change

## MODIFIED Requirements

### Requirement: Required Spotify API Scopes

The OAuth authorization request SHALL include exactly the scopes needed for connector and module operation.

#### Scenario: Scope specification

- **WHEN** the OAuth authorization URL is constructed
- **THEN** the `scope` parameter SHALL include:
  - `user-read-playback-state` — allows reading current playback state
  - `user-read-recently-played` — allows reading recently played tracks
  - `user-top-read` — allows reading top artists and tracks for preference profiling
  - `playlist-read-private` — allows reading user's private playlists
  - `playlist-read-collaborative` — allows reading collaborative playlists
  - `playlist-modify-public` — allows creating and modifying public playlists
  - `playlist-modify-private` — allows creating and modifying private playlists
  - `user-modify-playback-state` — allows controlling playback (requires Premium)
  - `user-library-read` — allows reading saved tracks and albums
  - `user-library-modify` — allows saving and removing library items

## ADDED Requirements

### Requirement: Scope Mismatch Detection

The dashboard SHALL detect when existing tokens lack required scopes and prompt re-authorization.

#### Scenario: Needs re-authorization state

- **WHEN** the Spotify status endpoint is called
- **AND** valid tokens exist but were authorized with a subset of the required scopes
- **THEN** the status response SHALL include `state: "needs_reauth"` and `missing_scopes` listing the scopes not yet granted
- **AND** the dashboard SHALL display a "Re-authorize" button with explanation: "New Spotify features require additional permissions."

#### Scenario: Re-authorization preserves connection

- **WHEN** the user clicks "Re-authorize"
- **THEN** the OAuth PKCE flow SHALL be initiated with the full scope set
- **AND** on successful completion, the new tokens (with expanded scopes) SHALL replace the old tokens
- **AND** the status SHALL transition to `connected`
