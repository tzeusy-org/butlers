## Why

Butlers currently supports exactly one Google account. The refresh token is stored as a singleton on the owner entity via a `UNIQUE(entity_id, type)` constraint in `shared.entity_info`. All Google-consuming modules (Gmail connector, Calendar, Contacts) resolve credentials from this single source. Users with multiple Google accounts (personal, work, shared family) cannot connect more than one — the OAuth flow overwrites the previous token. This blocks real-world usage where life management spans multiple Google identities.

## What Changes

- **Google account registry**: Introduce a `shared.google_accounts` table that tracks connected Google accounts (email, display name, granted scopes, status). Each account row owns its refresh token in `entity_info`.
- **Account-aware credential resolution**: `load_google_credentials()` and friends gain a required `account_id` or `account_email` parameter. The owner-entity singleton pattern is replaced by per-account entity lookup.
- **Multi-account OAuth flow**: The `/api/oauth/google/start` endpoint accepts an optional `account_email` hint. The callback resolves or creates the target account entity and stores credentials against it. Re-authenticating an existing account refreshes its token without affecting others.
- **Account selection in modules**: Calendar and Contacts configs gain an `account` field (email string) to bind to a specific connected account. A default/primary account mechanism preserves backward compatibility.
- **Single multi-account Gmail connector**: The Gmail connector becomes a single process that discovers all connected accounts from the DB and runs independent watch/poll loops per account. No per-account env vars — account binding is fully DB-driven. Dynamic account discovery supports adding/removing accounts without process restart.
- **Account lifecycle management**: Dashboard endpoints to list connected accounts, set primary, disconnect (revoke + delete credentials), and re-authorize (scope upgrade).
- **Migration**: Existing single-account credentials are promoted into the first account entity with `is_primary=true`. **BREAKING**: Code that calls `load_google_credentials()` without an account selector will fail after migration — all call sites must be updated.

## Capabilities

### New Capabilities
- `google-account-registry`: Account lifecycle — registration, listing, primary election, disconnection, scope tracking. Includes the `shared.google_accounts` table schema, account entity creation, and the relationship between accounts and `entity_info` credential rows.
- `google-multi-account-oauth`: OAuth flow changes — account-hint parameter on start, account resolution on callback, re-authorization without overwriting other accounts, scope upgrade flow.
- `dashboard-google-accounts`: Dashboard UI/API for managing connected Google accounts — list, connect new, disconnect, set primary, show scope status per account.

### Modified Capabilities
- `core-credentials`: `load_google_credentials()`, `store_google_credentials()`, `delete_google_credentials()`, and `resolve_google_credentials()` gain account-selector parameter. `resolve_owner_entity_info()` is supplemented by `resolve_account_entity_info()`. The owner-entity singleton assumption is removed for Google credential types.
- `entity-identity`: `shared.entity_info` UNIQUE constraint relaxed or remodeled — multiple `google_oauth_refresh` rows allowed (one per account entity). Account entities created with a new role (e.g., `google_account`).
- `connector-gmail`: Refactored from one-process-per-mailbox to single multi-account process. `GmailConnectorManager` discovers accounts from `shared.google_accounts` and spawns independent `GmailAccountLoop` per account. Per-account config overrides via `google_accounts.metadata.gmail`. Dynamic account discovery via periodic re-scan and MCP tool. `endpoint_identity` is auto-resolved per-account at startup (not set via env var). `GMAIL_ACCOUNT` env var is removed.
- `module-calendar`: `CalendarConfig` gains `account` field. `_GoogleProvider` resolves credentials and calendar ID per-account. Calendar discovery is scoped to the selected account.
- `module-contacts`: `GoogleContactsProvider` config gains `account` field. Sync state (delta tokens, last-sync timestamps) is keyed per-account. Multi-provider config can now include multiple Google provider entries with different accounts.

## Impact

- **Database**: New `shared.google_accounts` table. Migration to create account entities from existing owner credentials. Possible relaxation of `entity_info` UNIQUE constraint (or rekey to `(entity_id, type)` where entity_id is now per-account, not per-owner — constraint stays, model changes).
- **Core credential layer**: `google_credentials.py` and `credential_store.py` — all Google-specific functions change signature.
- **OAuth router**: `api/routers/oauth.py` — start/callback/status/delete endpoints gain account awareness.
- **All Google consumers**: `connectors/gmail.py`, `modules/calendar.py`, `modules/contacts/` — config and credential resolution updated.
- **butler.toml schema**: New optional `account` field in `[modules.calendar]` and `[modules.contacts.providers]` (google type). Gmail connector is configured entirely via DB — no butler.toml changes needed for it.
- **Tests**: `test_google_credentials.py`, `test_gmail_connector.py`, `api/test_oauth.py`, calendar and contacts tests — all need account-parameterized fixtures.
- **Backward compatibility**: Modules that omit `account` fall back to the primary account. Existing single-account deployments continue working after migration without config changes.
