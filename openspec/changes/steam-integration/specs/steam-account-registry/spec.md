# Steam Account Registry

## Purpose

The `public.steam_accounts` table stores metadata for each connected Steam account. It provides account discovery, primary account management, companion entity creation for API key credential storage, and account lifecycle management (connect, disconnect, hard delete). Follows the same pattern as `public.google_accounts`.

## ADDED Requirements

### Requirement: Steam Accounts Registry Table

The `public.steam_accounts` table SHALL store metadata for each connected Steam account. Each row represents one authenticated Steam identity.

#### Schema

```sql
CREATE TABLE public.steam_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id UUID NOT NULL REFERENCES public.entities(id) ON DELETE CASCADE,
    steam_id BIGINT UNIQUE NOT NULL,
    display_name VARCHAR,
    profile_url VARCHAR,
    avatar_url VARCHAR,
    is_primary BOOLEAN NOT NULL DEFAULT false,
    status VARCHAR NOT NULL DEFAULT 'active',
    connected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_poll_at TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}'::jsonb,
    CONSTRAINT chk_steam_accounts_status CHECK (status IN ('active', 'suspended', 'revoked'))
);
```

Indexes:
- `ix_steam_accounts_steam_id` on `(steam_id)` — unique, supports lookup by Steam's 64-bit ID
- Partial unique index: `CREATE UNIQUE INDEX ix_steam_accounts_primary_singleton ON public.steam_accounts ((true)) WHERE is_primary = true` — enforces at most one primary account

#### Scenario: Create a new Steam account record

- **WHEN** a user connects a Steam account via the dashboard with a valid SteamID and API key
- **THEN** a new `public.steam_accounts` row SHALL be inserted with the SteamID, display name (fetched via `GetPlayerSummaries`), profile URL, and avatar URL
- **AND** a companion entity SHALL be created in `public.entities` with `entity_type = 'other'` and `roles = ['steam_account']`
- **AND** the `entity_id` on the steam_accounts row SHALL reference the companion entity
- **AND** the API key SHALL be stored in `public.entity_info` with `type = 'steam_api_key'` and `secured = true` on the companion entity

#### Scenario: First account is automatically primary

- **WHEN** the first Steam account is connected and no other accounts exist
- **THEN** `is_primary` SHALL be set to `true`

#### Scenario: Subsequent accounts are not primary by default

- **WHEN** a second or subsequent Steam account is connected
- **THEN** `is_primary` SHALL be `false`
- **AND** the existing primary account SHALL remain unchanged

#### Scenario: Account lookup by SteamID

- **WHEN** a module resolves credentials with `steam_id = 76561198000000000`
- **THEN** the lookup SHALL query `public.steam_accounts WHERE steam_id = $1`
- **AND** return the account's `entity_id` for API key resolution from `entity_info`

#### Scenario: Account lookup by UUID

- **WHEN** a module resolves credentials with `account = <uuid>`
- **THEN** the lookup SHALL query `public.steam_accounts WHERE id = $1`

#### Scenario: Default to primary account

- **WHEN** a module resolves credentials with `steam_id = None`
- **THEN** the lookup SHALL query `public.steam_accounts WHERE is_primary = true`
- **AND** if no primary account exists, resolution SHALL fail with `MissingSteamCredentialsError`

#### Scenario: API key validation on connect

- **WHEN** a user submits a SteamID and API key to connect
- **THEN** the system SHALL validate the key by calling `ISteamUser/GetPlayerSummaries/v2/?key=<key>&steamids=<steam_id>`
- **AND** if the response is successful and returns player data, the key is valid
- **AND** if the response returns HTTP 403 or empty results, the connection SHALL be rejected with an actionable error message

### Requirement: Companion Entity for Account Credential Storage

Each Steam account row SHALL have a companion entity in `public.entities` that anchors the account's API key in `public.entity_info`.

#### Scenario: Companion entity creation

- **WHEN** a new Steam account is registered
- **THEN** an entity SHALL be created with `tenant_id = 'shared'`, `canonical_name = 'steam-account:<steam_id>'`, `entity_type = 'other'`, `roles = ['steam_account']`
- **AND** the entity's `id` SHALL be stored as `steam_accounts.entity_id`

#### Scenario: Companion entity excluded from identity resolution

- **WHEN** Switchboard performs identity resolution on an incoming message
- **THEN** companion entities with role `steam_account` SHALL NOT match as sender identities
- **AND** they exist solely as credential anchors

#### Scenario: API key stored as secured entity_info

- **WHEN** a Steam account's API key is stored
- **THEN** it SHALL be written to `public.entity_info` with `entity_id = <companion_entity_id>`, `type = 'steam_api_key'`, `value = <api_key>`, `secured = true`
- **AND** secured entity_info rows are excluded from general queries (only explicit credential resolution reads them)

### Requirement: Account Lifecycle Management

#### Scenario: Disconnect (soft delete)

- **WHEN** a user disconnects a Steam account via the dashboard
- **THEN** the account's `status` SHALL be set to `'revoked'`
- **AND** the connector SHALL stop polling this account on the next discovery cycle
- **AND** the companion entity and entity_info rows SHALL be retained (credentials are not deleted)
- **AND** if the disconnected account was primary, no automatic promotion occurs — the user must manually set a new primary

#### Scenario: Hard delete

- **WHEN** a user requests permanent deletion of a Steam account
- **THEN** the `public.steam_accounts` row SHALL be deleted (CASCADE deletes the companion entity and its entity_info)

#### Scenario: Reconnect a revoked account

- **WHEN** a user reconnects a previously revoked Steam account (same SteamID)
- **THEN** the existing row's `status` SHALL be updated to `'active'`
- **AND** the API key in entity_info SHALL be updated if a new key is provided
- **AND** the connector SHALL resume polling on the next discovery cycle

### Requirement: Metadata Schema

The `metadata` JSONB column stores per-account configuration overrides.

#### Scenario: Default metadata structure

- **WHEN** a Steam account is created with no metadata overrides
- **THEN** `metadata` SHALL default to `{}`
- **AND** the connector SHALL use global defaults for all poll intervals and settings

#### Scenario: Per-account poll interval overrides

- **WHEN** `metadata` contains `{"poll_intervals": {"recently_played": 300, "achievements": 900}}`
- **THEN** the connector SHALL use those intervals for this account instead of global defaults
- **AND** data types not listed SHALL use global defaults

#### Scenario: Tracked games override

- **WHEN** `metadata` contains `{"tracked_games": [730, 570, 440]}`
- **THEN** the connector SHALL track achievements only for those app IDs instead of auto-detecting from recently played
