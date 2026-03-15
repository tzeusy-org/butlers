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
