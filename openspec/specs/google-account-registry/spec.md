# Google Account Registry

## Purpose

The `shared.google_accounts` table stores metadata for each connected Google account. It provides account discovery, primary account management, companion entity creation for credential storage, scope tracking, and account lifecycle management (connect, disconnect, hard delete).

## ADDED Requirements

### Requirement: Google Accounts Registry Table

The `shared.google_accounts` table SHALL store metadata for each connected Google account. Each row represents one authenticated Google identity.

#### Schema

```sql
CREATE TABLE shared.google_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id UUID NOT NULL REFERENCES shared.entities(id) ON DELETE CASCADE,
    email VARCHAR UNIQUE,
    display_name VARCHAR,
    is_primary BOOLEAN NOT NULL DEFAULT false,
    granted_scopes TEXT[] NOT NULL DEFAULT '{}',
    status VARCHAR NOT NULL DEFAULT 'active',
    connected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_token_refresh_at TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}'::jsonb,
    CONSTRAINT chk_google_accounts_status CHECK (status IN ('active', 'revoked', 'expired'))
);
```

Indexes:
- `ix_google_accounts_email` on `(email)` — unique, supports lookup by email
- Partial unique index: `CREATE UNIQUE INDEX ix_google_accounts_primary_singleton ON shared.google_accounts ((true)) WHERE is_primary = true` — enforces at most one primary account

#### Scenario: Create a new Google account record

- **WHEN** a Google OAuth callback completes successfully with a new email address
- **THEN** a new `shared.google_accounts` row SHALL be inserted with the authenticated email, display name, and granted scopes
- **AND** a companion entity SHALL be created in `shared.entities` with `entity_type = 'other'` and `roles = ['google_account']`
- **AND** the `entity_id` on the google_accounts row SHALL reference the companion entity

#### Scenario: First account is automatically primary

- **WHEN** the first Google account is connected and no other accounts exist
- **THEN** `is_primary` SHALL be set to `true`

#### Scenario: Subsequent accounts are not primary by default

- **WHEN** a second or subsequent Google account is connected
- **THEN** `is_primary` SHALL be `false`
- **AND** the existing primary account SHALL remain unchanged

#### Scenario: Account lookup by email

- **WHEN** a module resolves credentials with `account = "alice@gmail.com"`
- **THEN** the lookup SHALL query `shared.google_accounts WHERE email = $1`
- **AND** return the account's `entity_id` for credential resolution from `entity_info`

#### Scenario: Account lookup by UUID

- **WHEN** a module resolves credentials with `account = <uuid>`
- **THEN** the lookup SHALL query `shared.google_accounts WHERE id = $1`

#### Scenario: Default to primary account

- **WHEN** a module resolves credentials with `account = None`
- **THEN** the lookup SHALL query `shared.google_accounts WHERE is_primary = true`
- **AND** if no primary account exists, resolution SHALL fail with `MissingGoogleCredentialsError`

### Requirement: Companion Entity for Account Credential Storage

Each Google account row SHALL have a companion entity in `shared.entities` that anchors the account's refresh token in `shared.entity_info`.

#### Scenario: Companion entity creation

- **WHEN** a new Google account is registered
- **THEN** an entity SHALL be created with `tenant_id = 'shared'`, `canonical_name = 'google-account:<email>'`, `entity_type = 'other'`, `roles = ['google_account']`
- **AND** the entity's `id` SHALL be stored as `google_accounts.entity_id`

#### Scenario: Companion entity excluded from identity resolution

- **WHEN** `entity_resolve()` or `entity_neighbors()` runs
- **THEN** entities with `'google_account' = ANY(roles)` SHALL be excluded from results
- **AND** they SHALL NOT appear in dashboard entity lists or "Unidentified Entities" sections

#### Scenario: Refresh token stored on companion entity

- **WHEN** a Google account's refresh token is persisted
- **THEN** it SHALL be stored as `shared.entity_info(entity_id = <companion_entity_id>, type = 'google_oauth_refresh', secured = true)`
- **AND** the `UNIQUE(entity_id, type)` constraint on `entity_info` SHALL naturally allow one token per account

### Requirement: Primary Account Management

Exactly one Google account SHALL be primary at any time (when at least one account exists).

#### Scenario: Set new primary account

- **WHEN** a user sets account B as primary while account A is currently primary
- **THEN** within a single transaction: `is_primary = false` on A, `is_primary = true` on B
- **AND** the partial unique index guarantees at most one primary

#### Scenario: Disconnect primary account with other accounts remaining

- **WHEN** the primary account is disconnected and other accounts exist
- **THEN** the oldest remaining account (by `connected_at`) SHALL be auto-promoted to primary
- **AND** a log entry SHALL record the auto-promotion

#### Scenario: Disconnect the only account

- **WHEN** the sole connected account is disconnected
- **THEN** no primary exists
- **AND** modules that require a Google account SHALL fail-fast at next startup or credential resolution

### Requirement: Account Disconnection

Disconnecting an account SHALL revoke the token with Google and remove local credentials.

#### Scenario: Full disconnect flow

- **WHEN** a user disconnects a Google account
- **THEN** the system SHALL:
  1. Attempt to revoke the refresh token with Google's revocation endpoint (`https://oauth2.googleapis.com/revoke`)
  2. Delete the `entity_info` row for the companion entity (refresh token)
  3. Update `google_accounts.status` to `'revoked'`
  4. If the account was primary, auto-promote the next account
- **AND** revocation failure (network error, already revoked) SHALL NOT block local cleanup

#### Scenario: Hard delete option

- **WHEN** a user requests full removal (not just revocation)
- **THEN** the `google_accounts` row and companion entity SHALL be deleted (CASCADE removes entity_info)
- **AND** modules referencing this account by email SHALL fail-fast at next startup

### Requirement: Account Listing

#### Scenario: List all connected accounts

- **WHEN** `list_google_accounts(pool)` is called
- **THEN** all rows from `shared.google_accounts` SHALL be returned ordered by `is_primary DESC, connected_at ASC`
- **AND** each row SHALL include `id`, `email`, `display_name`, `is_primary`, `granted_scopes`, `status`, `connected_at`, `last_token_refresh_at`

### Requirement: Account Soft Limit

#### Scenario: Maximum accounts enforced

- **WHEN** a user attempts to connect a new Google account and the count of active accounts equals or exceeds the soft limit (default 10)
- **THEN** the OAuth start endpoint SHALL return a 409 error with a message indicating the account limit
- **AND** the limit SHALL be configurable via `GOOGLE_MAX_ACCOUNTS` environment variable

### Requirement: Scope Tracking

#### Scenario: Scopes recorded on connect

- **WHEN** the OAuth callback completes
- **THEN** the `granted_scopes` array on the `google_accounts` row SHALL be populated from the token response's `scope` field (space-delimited, split into array)

#### Scenario: Scope check at module startup

- **WHEN** a module that requires specific Google scopes (e.g., Calendar requires `calendar`, Gmail requires `gmail.modify`) starts up with `account = "work@gmail.com"`
- **THEN** the module SHALL check `granted_scopes` on the account row
- **AND** if required scopes are missing, the module SHALL fail-fast with an actionable message directing the user to re-authorize the account with additional scopes
