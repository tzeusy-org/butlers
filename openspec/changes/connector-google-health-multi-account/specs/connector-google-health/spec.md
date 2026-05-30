# Connector Google Health — Multi-Account Delta

## MODIFIED Requirements

### Requirement: Owner Account Discovery and Scope Verification

The connector SHALL operate against **every** `public.google_accounts` row whose `status = 'active'` and whose `granted_scopes` contains all three Google Health scopes. It SHALL maintain independent per-account polling state and emit a heartbeat per account.

#### Scenario: Startup with one or more accounts granting Health scopes

- **WHEN** the connector starts and `public.google_accounts` contains one or more `status = 'active'` rows with all three Google Health scopes in `granted_scopes`
- **THEN** the connector SHALL spawn per-resource polling loops for each such account
- **AND** SHALL emit one heartbeat per account labelled `endpoint_identity = google_health:user:<email>`
- **AND** SHALL report aggregate health status `healthy`

#### Scenario: Startup with no health-scoped accounts

- **WHEN** no active `public.google_accounts` row carries all three Google Health scopes
- **THEN** the connector SHALL report aggregate health status `degraded`
- **AND** SHALL emit no envelopes
- **AND** SHALL re-check every 300 seconds

#### Scenario: Account added while running

- **WHEN** the connector is running and a new `google_accounts` row appears (status=active, all three Health scopes granted)
- **THEN** within `scope_recheck_s` (default 300s) the connector SHALL spawn per-resource polling loops for the new account
- **AND** SHALL emit a new heartbeat row for it

#### Scenario: Account loses health scopes mid-run

- **WHEN** the connector is running and one of the polled accounts loses any Google Health scope
- **THEN** within `scope_recheck_s` the connector SHALL stop polling that account and close its heartbeat
- **AND** SHALL continue polling any other still-eligible accounts uninterrupted

### Requirement: Owner Contact Info Registration

The pairing flow SHALL pre-register each owner Google Health identity as its own `public.contact_info` row. One row per Google account.

#### Scenario: Contact-info upsert per account

- **WHEN** the OAuth callback for `scope_set=health` completes successfully for a given Google account
- **THEN** a row SHALL be upserted into `public.contact_info` with `type = "google_health"`, `value = <google_user_id_for_that_account>`, `entity_id = <owner_entity_id>`, `secured = false`
- **AND** re-running pairing for the same account SHALL be idempotent
- **AND** multiple health-scoped accounts SHALL produce multiple `(type='google_health', value=<email_n>)` rows that share the same `entity_id`

### Requirement: Ingest Envelope Construction

The connector SHALL produce `ingest.v1` envelopes whose identity fields disambiguate by account.

#### Scenario: External event identity includes account scope

- **WHEN** the connector emits any envelope
- **THEN** `event.external_event_id` SHALL be `google_health:<email>:<resource>:<record_id>` (sleep) or `google_health:<email>:<resource>:<YYYY-MM-DD>` (daily summaries)
- **AND** `source.endpoint_identity` SHALL be `google_health:user:<email>`
- **AND** `control.idempotency_key` SHALL be `google_health:<email>:<resource>:<record_id>`

### Requirement: Checkpoint Persistence

The connector SHALL persist per-resource cursors that disambiguate by account, so per-account polling state survives restarts without cross-account collisions.

#### Scenario: Cursor key includes account identifier

- **WHEN** a polling loop successfully submits an envelope
- **THEN** the connector SHALL persist via `cursor_store.save_cursor(pool, connector_type="google_health", endpoint_identity="google_health:user:<email>:<account_uuid>:<resource>", cursor_value=...)`
- **AND** the per-account dimension SHALL be encoded into the `endpoint_identity` between the email and the resource

### Requirement: Health Status Reporting

The connector SHALL report health status via the shared heartbeat mechanism. Allowed states are `healthy | degraded | error` (no `broken` state). With multiple accounts, the connector SHALL report one heartbeat row per account AND a connector-level aggregate.

#### Scenario: Per-account heartbeat

- **WHEN** the connector runs polling loops for a given account
- **THEN** it SHALL emit a heartbeat row to `switchboard.connector_registry` with `connector_type = "google_health"` and `endpoint_identity = "google_health:user:<email>"`
- **AND** the per-account `state` and `error_message` fields SHALL reflect only that account's polling outcome

#### Scenario: Aggregate state computation

- **WHEN** computing the connector-level aggregate `state` exposed on the connector's `/health` endpoint
- **THEN** it SHALL be the worst-of state across all per-account heartbeats (`error` > `degraded` > `healthy`)
- **AND** the per-account heartbeat rows SHALL remain individually queryable

## REMOVED Requirements

### Requirement: Non-primary accounts are ignored

**Reason:** This invariant was a single-owner v1 default. With multi-account Google OAuth in production (`openspec/specs/google-multi-account-oauth/spec.md`) and observed silent data loss when upstream Fitbit→Google sync targets a non-primary account, polling only the `is_primary` row is now incorrect. Replaced by the modified `Owner Account Discovery and Scope Verification` requirement above. Single-account installs remain a strict subset of the new behaviour.

## ADDED Requirements

### Requirement: Per-Account Token Cache

The connector SHALL maintain one access-token cache per account, keyed by `google_accounts.id`. Tokens MUST be minted via the scope-restricted refresh-token grant (per the existing `Scope-Restricted Access Token Minting` requirement) using that account's own refresh token.

#### Scenario: Refresh token resolution per account

- **WHEN** the connector needs a fresh access token for a given account
- **THEN** it SHALL read the refresh token from `public.entity_info` keyed by that account's `companion_entity_id` (the existing companion-entity pattern in `google_credentials._resolve_entity_refresh_token`)
- **AND** SHALL NOT use the shared `resolve_owner_entity_info` "primary account" helper for non-primary accounts

#### Scenario: Mint isolation

- **WHEN** a mint or refresh call fails for one account (transient network error, invalidated refresh token, etc.)
- **THEN** that failure SHALL be confined to the failing account's polling loop
- **AND** SHALL NOT block mints or polls for other accounts
- **AND** the per-account heartbeat SHALL transition to `error` or `degraded` for the failing account only
