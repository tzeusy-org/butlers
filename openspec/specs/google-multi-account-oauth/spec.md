# Google Multi-Account OAuth

## Purpose

Extends the Google OAuth flow to support multiple Google accounts. The OAuth start endpoint accepts an account hint for pre-selection, the callback resolves the authenticated identity and stores credentials per-account using companion entities, and force-consent mode enables scope upgrades.

## ADDED Requirements

### Requirement: Account-Hint OAuth Start

The OAuth start endpoint SHALL accept an optional account hint to pre-select the Google account during authorization.

#### Scenario: Start OAuth with account hint

- **WHEN** `GET /api/oauth/google/start?account_hint=work@gmail.com` is called
- **THEN** the generated Google authorization URL SHALL include `login_hint=work@gmail.com`
- **AND** the CSRF state token SHALL store the hint for callback resolution

#### Scenario: Start OAuth without account hint

- **WHEN** `GET /api/oauth/google/start` is called without `account_hint`
- **THEN** no `login_hint` is passed to Google
- **AND** the user selects their account on Google's consent screen

#### Scenario: Account limit check on start

- **WHEN** `GET /api/oauth/google/start` is called and the active account count equals the soft limit
- **AND** the `account_hint` does not match an existing account email (i.e., this would be a new account)
- **THEN** the endpoint SHALL return 409 with `{"error": "account_limit_reached", "max_accounts": <limit>}`

### Requirement: Account-Resolving OAuth Callback

The OAuth callback SHALL resolve the authenticated Google account and store credentials per-account.

#### Scenario: Callback for new account

- **WHEN** the OAuth callback receives a valid code
- **THEN** the system SHALL:
  1. Exchange the code for tokens
  2. Call Google's userinfo endpoint (`https://www.googleapis.com/oauth2/v2/userinfo`) to obtain the authenticated email and display name
  3. Check if a `google_accounts` row exists for that email
  4. If not: create a companion entity, create a `google_accounts` row, set `is_primary` if first account
  5. Store the refresh token in `entity_info` on the companion entity
  6. Update `granted_scopes` from the token response
- **AND** the response SHALL include the account email and whether it's new or re-authorized

#### Scenario: Callback for existing account (re-authorization)

- **WHEN** the OAuth callback's userinfo email matches an existing `google_accounts` row
- **THEN** the existing account's refresh token SHALL be updated (not a new account created)
- **AND** `granted_scopes` SHALL be refreshed from the token response
- **AND** `status` SHALL be set to `'active'` (in case it was previously `'revoked'` or `'expired'`)
- **AND** `last_token_refresh_at` SHALL be updated
- **AND** other accounts SHALL NOT be affected

#### Scenario: Callback without refresh token

- **WHEN** the token exchange response does not include a `refresh_token` (Google only returns refresh_token on first consent or when `access_type=offline&prompt=consent` is used)
- **THEN** if the account already exists and has a stored refresh token, the existing token SHALL be preserved
- **AND** if the account is new and no refresh token is provided, the callback SHALL return an error directing the user to re-authorize with `prompt=consent`

#### Scenario: Userinfo call failure

- **WHEN** the userinfo API call fails after a successful token exchange
- **THEN** the callback SHALL return a 502 error with `{"error": "userinfo_failed"}`
- **AND** no credentials SHALL be stored (atomic: either everything succeeds or nothing is persisted)

### Requirement: Force Consent for Scope Upgrade

#### Scenario: Re-authorize with additional scopes

- **WHEN** `GET /api/oauth/google/start?account_hint=work@gmail.com&force_consent=true` is called
- **THEN** the authorization URL SHALL include `prompt=consent` to force Google to return a new refresh token
- **AND** the `scope` parameter SHALL include all requested scopes (existing + new)

### Requirement: State Token Carries Account Context

#### Scenario: Account context in CSRF state

- **WHEN** the OAuth start endpoint generates a CSRF state token
- **THEN** the state store entry SHALL include `account_hint` (if provided) and `force_consent` flag
- **AND** the callback SHALL read these from the state store during resolution

### Requirement: Scope Set Registry

The OAuth start endpoint SHALL accept a `scope_set` query parameter enumerating one or more named scope sets to include in the authorization URL.

#### Scenario: Registered scope sets

- **WHEN** the scope catalog is consulted
- **THEN** it SHALL enumerate named scope sets including at least:
  - `base` — `openid email profile`
  - `calendar` — `https://www.googleapis.com/auth/calendar` and related read variants
  - `drive` — `https://www.googleapis.com/auth/drive.readonly` and related variants
  - `gmail` — existing Gmail scopes already used by `connector-gmail`
  - `health` — `https://www.googleapis.com/auth/googlehealth.sleep`, `https://www.googleapis.com/auth/googlehealth.activity_and_fitness`, `https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements`
- **AND** the `base` set SHALL always be included implicitly

#### Scenario: Single-set request

- **WHEN** `GET /api/oauth/google/start?scope_set=health` is called
- **THEN** the authorization URL SHALL include the `health` set's scopes unioned with any scopes already stored in `granted_scopes` for the hinted account
- **AND** SHALL implicitly include the `base` set

#### Scenario: Multi-set request

- **WHEN** `GET /api/oauth/google/start?scope_set=calendar,drive,health&force_consent=true&account_hint=owner@example.com` is called
- **THEN** the authorization URL SHALL include the union of scopes for all three requested sets (plus `base`)
- **AND** the callback SHALL update `granted_scopes` with the full union after successful consent

#### Scenario: Unknown scope set

- **WHEN** `GET /api/oauth/google/start?scope_set=bogus` is called
- **THEN** the endpoint SHALL return HTTP 400 with `{"error": "unknown_scope_set", "scope_set": "bogus", "known": [...]}`

#### Scenario: Backward compatibility for callers that omit scope_set

- **WHEN** `GET /api/oauth/google/start` is called with no `scope_set` parameter
- **THEN** the endpoint SHALL behave as it does today (existing default scope composition)
- **AND** Google Health scopes SHALL only be included when explicitly requested via `scope_set=health`

### Requirement: Google Health Scopes are Restricted

#### Scenario: Restricted-scope documentation in the OAuth catalog

- **WHEN** a developer or operator reads the Google OAuth scope catalog source
- **THEN** each Google Health scope entry SHALL carry an inline comment noting that the scope is Restricted, production-mode use requires a one-time privacy and security review, and test mode is sufficient for single-developer / single-user self-hosting (subject to 7-day refresh token expiry)

#### Scenario: Test-mode awareness in the OAuth callback

- **WHEN** the OAuth callback completes for a Google Health scope grant and the OAuth client is in test mode
- **THEN** the callback SHALL set `metadata.google_health_test_mode = true` on the `google_accounts` row

### Requirement: Additive Schema Support for Test-Mode Tracking

#### Scenario: Metadata JSONB column

- **WHEN** the `public.google_accounts` schema is migrated
- **THEN** it SHALL include a `metadata JSONB NOT NULL DEFAULT '{}'::jsonb` column (if not already present)
- **AND** `metadata.google_health_test_mode` SHALL be written only by the OAuth callback; absence of the key means not test mode

#### Scenario: Last-refresh timestamp column

- **WHEN** the OAuth callback issues or refreshes a token for a `google_accounts` row
- **THEN** `public.google_accounts.last_token_refresh_at TIMESTAMPTZ` SHALL be updated to `now()`
- **AND** the dashboard's 7-day test-mode expiry heuristic SHALL read this column

### Requirement: Scope-Selective Revocation

#### Scenario: Revoke Google Health scopes only

- **WHEN** `DELETE /api/connectors/google-health/disconnect` is invoked
- **THEN** the pipeline SHALL call Google's token-revocation endpoint scoped to the three Google Health scopes
- **AND** SHALL update `public.google_accounts.granted_scopes` to remove the three entries while preserving `calendar`, `drive`, and other granted scopes
- **AND** SHALL NOT delete the `google_accounts` row or the companion entity

#### Scenario: Full account disconnect preserves semantics

- **WHEN** an owner fully disconnects a Google account via `DELETE /api/oauth/google/accounts/<id>`
- **THEN** all Google Health scopes SHALL be revoked alongside any other granted scopes (union revocation; no change to existing behaviour)
