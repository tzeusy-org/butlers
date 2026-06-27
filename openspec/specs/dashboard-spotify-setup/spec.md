# Dashboard Spotify Setup

## Purpose

A dedicated section on the Butlers dashboard settings page at `/butlers/settings` for linking, monitoring, and managing the user's Spotify account. Uses the OAuth 2.0 PKCE flow (no client secret required) for authorization. Modeled after the Google OAuth account management pattern: status card with health badge, account linking flow, and disconnect capability.

## ADDED Requirements

### Requirement: Spotify OAuth 2.0 PKCE Authorization Flow

The dashboard SHALL implement the Spotify OAuth 2.0 Authorization Code with PKCE flow for account linking.

#### Scenario: Client ID configuration

- **WHEN** the user navigates to the Spotify settings section
- **AND** no Spotify client_id is configured
- **THEN** the dashboard SHALL display a setup prompt explaining that the user must create a Spotify Developer app at https://developer.spotify.com/dashboard
- **AND** it SHALL provide an input field for the user to enter their Spotify app's `client_id`
- **AND** the `client_id` SHALL be stored in `CredentialStore` under key `SPOTIFY_CLIENT_ID`

#### Scenario: OAuth PKCE flow initiation

- **WHEN** the user clicks "Connect Spotify" and a `client_id` is configured
- **THEN** the dashboard backend SHALL generate a cryptographically random code verifier (43-128 characters, URL-safe)
- **AND** it SHALL compute the code challenge as `BASE64URL(SHA256(code_verifier))`
- **AND** it SHALL store the code verifier in a short-lived server-side session (TTL 10 minutes)
- **AND** it SHALL redirect the user's browser to `https://accounts.spotify.com/authorize` with parameters:
  - `client_id` = stored client_id
  - `response_type` = `code`
  - `redirect_uri` = `https://<tailnet-host>/api/connectors/spotify/oauth/callback` (default `http://localhost:41200/...`, overridable via `SPOTIFY_OAUTH_REDIRECT_URI`)
  - `scope` = the full required scope set (see Requirement: Required Spotify API Scopes, all 10 scopes)
  - `code_challenge_method` = `S256`
  - `code_challenge` = computed challenge
  - `state` = CSRF protection token (stored in session)

#### Scenario: OAuth callback and token exchange

- **WHEN** Spotify redirects back to `/api/connectors/spotify/oauth/callback` with an authorization code
- **THEN** the dashboard backend SHALL verify the `state` parameter matches the stored CSRF token
- **AND** it SHALL exchange the authorization code for tokens via `POST https://accounts.spotify.com/api/token` with:
  - `grant_type` = `authorization_code`
  - `code` = the authorization code
  - `redirect_uri` = the same redirect URI used in the authorization request
  - `client_id` = stored client_id
  - `code_verifier` = the stored code verifier
- **AND** the response SHALL contain `access_token`, `refresh_token`, `expires_in`, `token_type`, and `scope`
- **AND** tokens SHALL be stored in `CredentialStore` under keys `SPOTIFY_ACCESS_TOKEN` and `SPOTIFY_REFRESH_TOKEN`
- **AND** the token expiry time SHALL be stored under key `SPOTIFY_TOKEN_EXPIRES_AT`
- **AND** the user SHALL be redirected back to the settings page with a success indicator

#### Scenario: OAuth error handling

- **WHEN** Spotify redirects back with an `error` parameter (e.g., `access_denied`)
- **THEN** the dashboard SHALL display a user-friendly error message
- **AND** it SHALL NOT store any tokens
- **AND** the user SHALL be redirected back to the settings page with the error displayed

#### Scenario: CSRF protection

- **WHEN** the OAuth callback is received with a `state` parameter that does not match the stored session value
- **THEN** the callback SHALL return HTTP 403 and display an error message
- **AND** no tokens SHALL be stored

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

### Requirement: Scope Mismatch Detection

The dashboard SHALL detect when existing tokens lack required scopes and prompt re-authorization.

#### Scenario: Needs re-authorization state

- **WHEN** the Spotify status endpoint is called
- **AND** valid tokens exist but were authorized with a subset of the required scopes
- **THEN** the status response SHALL include `state: "needs_reauth"` and `missing_scopes` listing the scopes not yet granted
- **AND** the dashboard SHALL display a "Re-authorize" button with explanation: "Your Spotify authorization is missing required permissions. Please re-authorize to grant access."

#### Scenario: Re-authorization preserves connection

- **WHEN** the user clicks "Re-authorize"
- **THEN** the OAuth PKCE flow SHALL be initiated with the full scope set
- **AND** on successful completion, the new tokens (with expanded scopes) SHALL replace the old tokens
- **AND** the status SHALL transition to `connected`

### Requirement: Connection Status Card

The dashboard SHALL display a Spotify connection status card on the settings page.

#### Scenario: Connected state

- **WHEN** the user has valid Spotify credentials stored
- **THEN** the settings page SHALL display a `SpotifyStatusCard` showing:
  - Connection status badge: "Connected" (green)
  - Spotify display name and account type (free/premium)
  - Last sync time (from connector heartbeat)
  - "Disconnect" button

#### Scenario: Disconnected state

- **WHEN** no Spotify credentials are stored
- **THEN** the settings page SHALL display the card with:
  - Connection status badge: "Not connected" (grey)
  - "Connect Spotify" button (initiates OAuth flow)
  - Brief explanation of what Spotify integration provides

#### Scenario: Error state

- **WHEN** Spotify credentials are stored but the token refresh has failed
- **THEN** the settings page SHALL display the card with:
  - Connection status badge: "Error — re-authorization needed" (red)
  - "Re-connect" button (initiates fresh OAuth flow)
  - Error description

### Requirement: Dashboard API Endpoints

The dashboard SHALL expose REST API endpoints for Spotify account management.

#### Scenario: Status endpoint

- **WHEN** `GET /api/connectors/spotify/status` is called
- **THEN** it SHALL return JSON with: `connected` (bool), `spotify_user_id` (string or null), `display_name` (string or null), `account_type` (string or null), `last_sync_at` (ISO timestamp or null), `error` (string or null)

#### Scenario: OAuth start endpoint

- **WHEN** `POST /api/connectors/spotify/oauth/start` is called
- **THEN** it SHALL return JSON with: `authorization_url` (string) — the full Spotify authorization URL with PKCE parameters
- **AND** the code verifier and CSRF state SHALL be stored server-side

#### Scenario: OAuth callback endpoint

- **WHEN** `GET /api/connectors/spotify/oauth/callback` is called with valid `code` and `state` parameters
- **THEN** it SHALL perform the token exchange and redirect to the settings page

#### Scenario: Disconnect endpoint

- **WHEN** `POST /api/connectors/spotify/disconnect` is called
- **THEN** it SHALL delete `SPOTIFY_ACCESS_TOKEN`, `SPOTIFY_REFRESH_TOKEN`, `SPOTIFY_TOKEN_EXPIRES_AT`, and `SPOTIFY_CLIENT_ID` from `CredentialStore`
- **AND** it SHALL return `{"disconnected": true}`

#### Scenario: Client ID configuration endpoint

- **WHEN** `POST /api/connectors/spotify/config` is called with `{"client_id": "<value>"}`
- **THEN** it SHALL store the client_id in `CredentialStore` under key `SPOTIFY_CLIENT_ID`
- **AND** it SHALL validate that the client_id is a 32-character hexadecimal string
- **AND** it SHALL return `{"configured": true}`
