# Core Credentials — Spotify Delta

## ADDED Requirements

### Requirement: Spotify OAuth Token Storage

The `CredentialStore` SHALL support storing and resolving Spotify OAuth tokens for the Spotify connector.

#### Scenario: Store Spotify OAuth tokens

- **WHEN** the Spotify OAuth flow completes successfully
- **THEN** the following keys SHALL be stored in `CredentialStore` under category `"spotify"`:
  - `SPOTIFY_CLIENT_ID` — the Spotify app client ID (entered by user, not sensitive)
  - `SPOTIFY_ACCESS_TOKEN` — the OAuth access token (sensitive, 1-hour TTL)
  - `SPOTIFY_REFRESH_TOKEN` — the OAuth refresh token (sensitive, long-lived)
  - `SPOTIFY_TOKEN_EXPIRES_AT` — the access token expiry as ISO 8601 timestamp (not sensitive)
- **AND** `SPOTIFY_ACCESS_TOKEN` and `SPOTIFY_REFRESH_TOKEN` SHALL be stored with `is_sensitive=True`

#### Scenario: Resolve Spotify credentials for connector

- **WHEN** the Spotify connector calls `store.resolve("SPOTIFY_ACCESS_TOKEN")`
- **THEN** the access token SHALL be returned from the DB
- **AND** environment variable fallback SHALL NOT be used (these are not infrastructure bootstrap credentials)

#### Scenario: Token refresh updates stored credentials

- **WHEN** the Spotify connector refreshes the access token
- **THEN** it SHALL call `store.store("SPOTIFY_ACCESS_TOKEN", new_token, category="spotify", is_sensitive=True)` to update the stored value
- **AND** if the refresh response includes a new refresh token, it SHALL also update `SPOTIFY_REFRESH_TOKEN`
- **AND** it SHALL update `SPOTIFY_TOKEN_EXPIRES_AT` with the new expiry time

#### Scenario: Delete Spotify credentials on disconnect

- **WHEN** the user disconnects Spotify via the dashboard
- **THEN** all four Spotify credential keys SHALL be deleted from `CredentialStore`
- **AND** `store.delete()` SHALL be called for each key
