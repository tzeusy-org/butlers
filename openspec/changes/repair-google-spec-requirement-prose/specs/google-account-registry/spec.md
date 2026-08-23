## MODIFIED Requirements

### Requirement: Account Listing

Listing Google accounts SHALL return every registry row in a defined order
with a defined field set.

#### Scenario: List all connected accounts

- **WHEN** `list_google_accounts(pool)` is called
- **THEN** all rows from `public.google_accounts` SHALL be returned ordered by `is_primary DESC, connected_at ASC`
- **AND** each row SHALL include `id`, `email`, `display_name`, `is_primary`, `granted_scopes`, `status`, `connected_at`, `last_token_refresh_at`

### Requirement: Account Soft Limit

The number of connected Google accounts SHALL be bounded by a configurable
soft limit, enforced at the OAuth start endpoint.

#### Scenario: Maximum accounts enforced

- **WHEN** a user attempts to connect a new Google account and the count of active accounts equals or exceeds the soft limit (default 10)
- **THEN** the OAuth start endpoint SHALL return a 409 error with a message indicating the account limit
- **AND** the limit SHALL be configurable via `GOOGLE_MAX_ACCOUNTS` environment variable

### Requirement: Scope Tracking

Granted Google scopes SHALL be recorded on the account row when OAuth
completes, and a module requiring specific scopes SHALL check them at startup
and fail fast with an actionable message when they are missing.

#### Scenario: Scopes recorded on connect

- **WHEN** the OAuth callback completes
- **THEN** the `granted_scopes` array on the `google_accounts` row SHALL be populated from the token response's `scope` field (space-delimited, split into array)

#### Scenario: Scope check at module startup

- **WHEN** a module that requires specific Google scopes (e.g., Calendar requires `calendar`, Gmail requires `gmail.modify`) starts up with `account = "work@gmail.com"`
- **THEN** the module SHALL check `granted_scopes` on the account row
- **AND** if required scopes are missing, the module SHALL fail-fast with an actionable message directing the user to re-authorize the account with additional scopes
