## MODIFIED Requirements

### Requirement: Provider-Agnostic Architecture

The module defines an abstract `CalendarProvider` interface with concrete implementations per provider. Currently only Google Calendar is implemented via `_GoogleProvider`.

#### Scenario: Provider selection at startup with account

- **WHEN** the Calendar module starts up with `provider = "google"` and `account = "work@gmail.com"` in config
- **THEN** a `_GoogleProvider` instance is created with OAuth credentials resolved from the credential store for the specified Google account
- **AND** the calendar ID is resolved from credential store or auto-discovered via shared "Butlers" calendar on that account

#### Scenario: Provider selection at startup without account (primary)

- **WHEN** the Calendar module starts up with `provider = "google"` and no `account` field in config
- **THEN** credentials are resolved for the primary Google account
- **AND** behavior is identical to pre-multi-account single-account deployments

#### Scenario: Unsupported provider configured

- **WHEN** a provider not in the `_PROVIDER_CLASSES` dict is configured
- **THEN** startup fails with a descriptive error

#### Scenario: Account not connected

- **WHEN** the Calendar module starts with `account = "nonexistent@gmail.com"`
- **AND** no `google_accounts` row exists for that email
- **THEN** startup SHALL fail with a descriptive error directing the user to connect the account via the dashboard OAuth flow

#### Scenario: Account missing required scopes

- **WHEN** the Calendar module starts with an account that does not have `calendar` in its `granted_scopes`
- **THEN** startup SHALL fail with a message directing the user to re-authorize the account with Calendar scope

### Requirement: CalendarConfig Validation

Configuration is declared under `[modules.calendar]` in `butler.toml` with fields: `provider` (required), `account` (optional, email string — Google account to use), `calendar_id` (optional), `timezone` (default `"UTC"`), `conflicts` (policy defaults), `event_defaults` (notification defaults), and `sync` (sync interval settings).

#### Scenario: Valid calendar config with account

- **WHEN** config is provided with `provider = "google"`, `account = "work@gmail.com"`, and valid timezone
- **THEN** the config is validated and normalized (provider lowercased, timezone stripped, account stripped)

#### Scenario: Valid calendar config without account

- **WHEN** config is provided with `provider = "google"` and no `account` field
- **THEN** the config is valid and the module SHALL use the primary Google account at startup

#### Scenario: Conflict policy configuration

- **WHEN** `conflicts` is configured with a `default_policy`
- **THEN** valid policies are `suggest`, `fail`, `allow_overlap`

### Requirement: Google OAuth and Rate Limiting

The Google provider handles OAuth token refresh and rate-limited retries, resolving credentials for the configured account.

#### Scenario: OAuth token refresh for specific account

- **WHEN** the access token expires or is not cached
- **THEN** a refresh-token exchange is performed against `https://oauth2.googleapis.com/token` using the refresh token for the configured Google account
- **AND** the new token is cached with an early-expiry safety margin (60s before actual expiry)
- **AND** on successful refresh, `google_accounts.last_token_refresh_at` SHALL be updated

#### Scenario: Rate-limit retry

- **WHEN** a Google Calendar API request returns 429 or 503
- **THEN** the request is retried up to 3 times with exponential backoff (base 1.0s)

#### Scenario: Credential redaction in errors

- **WHEN** an error message might contain credential values
- **THEN** patterns like `client_secret=...`, `refresh_token=...`, `access_token=...` are redacted before logging or returning to the caller
