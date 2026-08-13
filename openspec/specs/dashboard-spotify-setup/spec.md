# Dashboard Spotify Setup

## Purpose

The connector-owned Spotify Passport projection at
`/secrets?focus=u:spotify` and its inline drawer, for linking, monitoring, and
managing the Spotify connector. It uses the OAuth 2.0 PKCE flow (no client
secret required) for authorization. The projection is content-blind: it shows
only connector-derived connection state and the fixed
`listening-history` capability category, never account/profile content or
credential material.

## Requirements

### Requirement: Connector-Owned Spotify OAuth 2.0 PKCE Authorization Flow

Spotify connector PKCE is the only production Spotify authorization flow. It SHALL remain so. The
dashboard SHALL invoke the connector-owned Spotify OAuth 2.0 Authorization
Code with PKCE flow for account linking. A generic OAuth Spotify provider
registry or `/api/oauth/spotify/*` route SHALL NOT authorize, exchange,
refresh, or persist Spotify token material.

#### Scenario: Client ID configuration

- **WHEN** the user opens the Spotify provider configuration drawer
- **AND** no Spotify client_id is configured
- **THEN** the dashboard SHALL expose a `configure` control
- **AND** its configuration panel SHALL provide an input field for the user to enter their Spotify app's `client_id`
- **AND** the panel SHALL identify `developer.spotify.com/dashboard` as the source of that client ID
- **AND** the `client_id` SHALL be stored in `CredentialStore` under key `SPOTIFY_CLIENT_ID`

#### Scenario: OAuth PKCE flow initiation

- **WHEN** the user invokes the Spotify authorization action and a `client_id` is configured
- **THEN** the dashboard backend SHALL generate a cryptographically random code verifier (43-128 characters, URL-safe)
- **AND** it SHALL compute the code challenge as `BASE64URL(SHA256(code_verifier))`
- **AND** it SHALL store the code verifier in a short-lived server-side session (TTL 10 minutes)
- **AND** it SHALL return the Spotify authorization URL and opaque CSRF `state` to the dashboard, with URL parameters:
  - `client_id` = stored client_id
  - `response_type` = `code`
  - `redirect_uri` = `https://<tailnet-host>/api/connectors/spotify/oauth/callback` (default `http://localhost:41200/...`, overridable via `SPOTIFY_OAUTH_REDIRECT_URI`)
  - `scope` = the full required scope set (see Requirement: Required Spotify API Scopes, all 10 scopes)
  - `code_challenge_method` = `S256`
  - `code_challenge` = computed challenge
  - `state` = CSRF protection token (stored in session)
- **AND** the dashboard SHALL redirect the user's browser to that authorization URL

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
- **AND** when `OAUTH_DASHBOARD_URL` is configured, the user SHALL be
  redirected back to the Spotify Passport projection on `/secrets`
  (`?focus=u:spotify&toast=connected`) — the surface whose drawer starts this
  connector-owned dance — using connector-owned transient params that the
  Secrets page surfaces as a toast and strips
- **AND** when `OAUTH_DASHBOARD_URL` is not configured, the callback SHALL return a JSON success response after storing the tokens

#### Scenario: OAuth error handling

- **WHEN** Spotify redirects back with an `error` parameter (e.g., `access_denied`)
- **THEN** it SHALL NOT store any tokens
- **AND** when `OAUTH_DASHBOARD_URL` is configured, the user SHALL be
  redirected back to the Spotify Passport projection on `/secrets`
  (`?focus=u:spotify&toast=connection_failed`) with a fixed local failure
  marker, never the provider error code or description
- **AND** when `OAUTH_DASHBOARD_URL` is not configured, the callback SHALL
  return HTTP 400 with the fixed error code `spotify_authorization_failed`

#### Scenario: CSRF protection

- **WHEN** the OAuth callback is received with a `state` parameter that does not match the stored session value
- **THEN** the callback SHALL return HTTP 403 with a fixed local state-error
  code and the Passport SHALL display fixed local copy
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
- **THEN** the status response SHALL include only the closed
  `state: "needs_reauth"` signal for this condition
- **AND** it SHALL NOT expose `missing_scopes` or any dynamic provider-scope
  inventory through the content-blind Passport projection
- **AND** the drawer SHALL expose a `connect via spotify` action that starts a new OAuth PKCE flow

#### Scenario: Re-authorization preserves connection

- **WHEN** the user clicks `connect via spotify` while the status is `needs_reauth`
- **THEN** the OAuth PKCE flow SHALL be initiated with the full scope set
- **AND** on successful completion, the new tokens (with expanded scopes) SHALL replace the old tokens
- **AND** the status SHALL transition to `connected`

### Requirement: Content-Blind Connector Status Drawer

The dashboard SHALL display Spotify connection status within its
connector-owned Passport projection drawer. The drawer is not a profile or
credential inspector: it SHALL display only a closed connection state and the
fixed `listening-history` capability category.

#### Scenario: Connected state

- **WHEN** the user has valid Spotify credentials stored
- **THEN** the drawer SHALL display:
  - A green connection status dot
  - The fixed capability evidence `listening-history`
  - A `disconnect` action
- **AND** it SHALL NOT display a Spotify user ID, display name, account type,
  client ID, token, raw provider response, or raw error text

#### Scenario: Client ID not configured

- **WHEN** no Spotify `client_id` is stored
- **THEN** the drawer SHALL display a dim status dot and a `configure` action

#### Scenario: Authorization needed

- **WHEN** a Spotify `client_id` is stored but no access token is stored
- **THEN** the drawer SHALL display an amber status dot and a `connect via spotify` action that initiates OAuth

#### Scenario: Error state

- **WHEN** Spotify credentials are stored but Spotify token verification has failed
- **THEN** the drawer SHALL display:
  - A red status dot
  - An error panel headed `Error: re-authorization needed`
  - A `re-connect` action that initiates a fresh OAuth flow
- **AND** the panel SHALL use fixed local copy and SHALL NOT display a raw
  provider error description

### Requirement: Dashboard API Endpoints

The dashboard SHALL expose REST API endpoints for Spotify account management.

#### Scenario: Status endpoint

- **WHEN** `GET /api/connectors/spotify/status` is called
- **THEN** it SHALL return JSON with only `connected` (bool), `state` (one of
  `unconfigured | authorization_needed | connected | needs_reauth | error`),
  and `capability_categories` (exactly `["listening-history"]`)
- **AND** it SHALL NOT return a client ID, token, Spotify user ID, display
  name, account type, raw provider error, raw probe result, audit payload, or
  free-form provider-derived text

#### Scenario: OAuth start endpoint

- **WHEN** `POST /api/connectors/spotify/oauth/start` is called
- **THEN** it SHALL return JSON with: `authorization_url` (string) — the full Spotify authorization URL with PKCE parameters — and `state` (the opaque CSRF token)
- **AND** the code verifier and CSRF state SHALL be stored server-side

#### Scenario: OAuth callback endpoint

- **WHEN** `GET /api/connectors/spotify/oauth/callback` is called with valid `code` and `state` parameters
- **THEN** it SHALL perform the token exchange
- **AND** when `OAUTH_DASHBOARD_URL` is configured, it SHALL redirect to the
  Spotify Passport projection on `/secrets`
- **AND** when `OAUTH_DASHBOARD_URL` is not configured, it SHALL return a JSON success response

#### Scenario: Disconnect endpoint

- **WHEN** `POST /api/connectors/spotify/disconnect` is called
- **THEN** it SHALL delete the locally stored `SPOTIFY_ACCESS_TOKEN`, `SPOTIFY_REFRESH_TOKEN`, `SPOTIFY_TOKEN_EXPIRES_AT`, and `SPOTIFY_GRANTED_SCOPES` keys from `CredentialStore`
- **AND** it SHALL retain `SPOTIFY_CLIENT_ID` so the user can reconnect without re-entering it
- **AND** it SHALL not issue a provider-side authorization-revocation request
- **AND** it SHALL return `{"disconnected": true}`

#### Scenario: Client ID configuration endpoint

- **WHEN** `POST /api/connectors/spotify/config` is called with `{"client_id": "<value>"}`
- **THEN** it SHALL store the client_id in `CredentialStore` under key `SPOTIFY_CLIENT_ID`
- **AND** it SHALL validate that the client_id is a 32-character hexadecimal string
- **AND** it SHALL return `{"configured": true}`
