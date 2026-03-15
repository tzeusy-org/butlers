# Credentials

## Purpose
Provides credential storage, resolution, and validation for butler daemons, including a generic DB-backed secret store (`butler_secrets`), Google OAuth credential lifecycle management, environment variable validation, and inline secret detection.

## ADDED Requirements

### Requirement: CredentialStore Interface
The `CredentialStore` class provides async CRUD operations on the `butler_secrets` DB table: `store()`, `load()`, `resolve()`, `has()`, `delete()`, and `list_secrets()`. The store is backed by an asyncpg pool and supports fallback pools for shared credential lookup.

#### Scenario: Store a secret
- **WHEN** `store.store(key, value, category, description, is_sensitive)` is called
- **THEN** the secret is persisted via INSERT...ON CONFLICT DO UPDATE (idempotent upsert)
- **AND** the raw value is never logged

#### Scenario: Resolve secret (DB-first, env fallback)
- **WHEN** `store.resolve(key)` is called
- **THEN** the store checks the local DB first, then fallback DBs, then `os.environ[key]`
- **AND** returns the first non-None value found

#### Scenario: Resolve with env fallback disabled
- **WHEN** `store.resolve(key, env_fallback=False)` is called
- **THEN** only DB sources are checked; environment variables are not consulted

#### Scenario: Load from DB only
- **WHEN** `store.load(key)` is called
- **THEN** only the DB is checked (local store first, then fallback pools)
- **AND** returns `None` if the key is not found in any DB

#### Scenario: Delete a secret
- **WHEN** `store.delete(key)` is called
- **THEN** the row is removed from `butler_secrets`
- **AND** returns `True` if a row was deleted, `False` otherwise

#### Scenario: List secrets (metadata only)
- **WHEN** `store.list_secrets(category="google")` is called
- **THEN** `SecretMetadata` records are returned (key, category, is_set, source, timestamps)
- **AND** raw secret values are NEVER included

#### Scenario: Missing table handled gracefully
- **WHEN** the `butler_secrets` table does not exist in a fallback pool
- **THEN** the lookup silently returns `None` for that pool (no crash)

### Requirement: Secret Schema Provisioning
`ensure_secrets_schema(pool)` creates the `butler_secrets` table and category index if they do not exist, using `CREATE TABLE IF NOT EXISTS`.

#### Scenario: Table provisioned on first call
- **WHEN** `ensure_secrets_schema(pool)` is called on a fresh database
- **THEN** the `butler_secrets` table and `ix_butler_secrets_category` index are created

### Requirement: Google OAuth Credential Lifecycle
Google credentials are split across two stores: app credentials (client_id, client_secret, scope) in `butler_secrets` under the `google` category, and the refresh token in `shared.entity_info` on the account's companion entity (resolved via `shared.google_accounts`). The `GoogleCredentials` Pydantic model validates non-empty fields. Secret values (client_secret, refresh_token) are redacted in `__repr__` and `__str__`.

#### Scenario: Store full Google credentials
- **WHEN** `store_google_credentials(store, client_id, client_secret, refresh_token, scope, account=<email_or_id>)` is called
- **THEN** app credentials are upserted in `butler_secrets` with keys `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_SCOPES`
- **AND** the `account` parameter is resolved to a `google_accounts` row and its companion `entity_id`
- **AND** the refresh token is upserted in `shared.entity_info` on the companion entity

#### Scenario: Store credentials with account=None (primary)
- **WHEN** `store_google_credentials(store, ..., account=None)` is called
- **THEN** the primary account is resolved from `shared.google_accounts WHERE is_primary = true`
- **AND** credentials are stored against the primary account's companion entity

#### Scenario: Load Google credentials with account selector
- **WHEN** `load_google_credentials(store, account="work@gmail.com")` is called and all required keys exist
- **THEN** app credentials are loaded from `butler_secrets` (shared across accounts)
- **AND** the refresh token is loaded from `entity_info` on the companion entity for the specified account
- **AND** a `GoogleCredentials` model is returned

#### Scenario: Load Google credentials with account=None (primary)
- **WHEN** `load_google_credentials(store, account=None)` is called
- **THEN** the primary account's refresh token is loaded
- **AND** behavior is identical to the pre-multi-account code for single-account deployments

#### Scenario: Partial credentials are an error
- **WHEN** some but not all required Google credential fields exist in the store
- **THEN** `InvalidGoogleCredentialsError` is raised listing missing fields

#### Scenario: No credentials stored
- **WHEN** none of the required Google credential keys exist
- **THEN** `load_google_credentials()` returns `None`

#### Scenario: Store app credentials (partial)
- **WHEN** `store_app_credentials(store, client_id, client_secret)` is called
- **THEN** only `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` are stored
- **AND** any existing refresh tokens (on any account) are preserved

#### Scenario: Delete Google credentials for specific account
- **WHEN** `delete_google_credentials(store, account="work@gmail.com")` is called
- **THEN** the refresh token entity_info row for the specified account's companion entity is deleted
- **AND** app credentials in `butler_secrets` are NOT deleted (shared across accounts)
- **AND** the `google_accounts` row status is updated to `'revoked'`

#### Scenario: Delete all Google credentials
- **WHEN** `delete_google_credentials(store, account=None, delete_all=True)` is called
- **THEN** all refresh tokens across all account companion entities are deleted
- **AND** app credentials in `butler_secrets` are deleted
- **AND** all `google_accounts` rows are updated to status `'revoked'`

### Requirement: Resolve Google Credentials (DB-Only)
`resolve_google_credentials(store, caller, account=None)` loads credentials from DB only. Raises `MissingGoogleCredentialsError` if not available. The `account` parameter selects which Google account's refresh token to use.

#### Scenario: Credentials available for specific account
- **WHEN** Google credentials are stored for `account = "work@gmail.com"`
- **THEN** `resolve_google_credentials(store, caller="calendar", account="work@gmail.com")` returns a valid `GoogleCredentials` model with that account's refresh token

#### Scenario: Credentials available for primary (default)
- **WHEN** `resolve_google_credentials(store, caller="calendar")` is called without `account`
- **THEN** the primary account's refresh token is used

#### Scenario: Specified account not found
- **WHEN** `resolve_google_credentials(store, caller="gmail", account="nonexistent@gmail.com")` is called
- **AND** no `google_accounts` row exists for that email
- **THEN** `MissingGoogleCredentialsError` is raised with a message indicating the account is not connected

#### Scenario: No primary account exists
- **WHEN** `resolve_google_credentials(store, caller="calendar", account=None)` is called
- **AND** no account has `is_primary = true`
- **THEN** `MissingGoogleCredentialsError` is raised with a message directing the user to connect a Google account

### Requirement: Account Resolution Helpers

New helper functions SHALL provide account-to-entity resolution for credential operations.

#### Scenario: Resolve account entity by email

- **WHEN** `resolve_google_account_entity(pool, email="alice@gmail.com")` is called
- **THEN** the companion entity_id for the specified account is returned
- **AND** if no account exists for that email, `None` is returned

#### Scenario: Resolve primary account entity

- **WHEN** `resolve_google_account_entity(pool, email=None)` is called
- **THEN** the companion entity_id for the primary account is returned
- **AND** if no primary account exists, `None` is returned

#### Scenario: List all account entities

- **WHEN** `list_google_account_entities(pool)` is called
- **THEN** a list of `(account_id, email, entity_id, is_primary)` tuples is returned for all active accounts

### Requirement: Startup Guard for Google-Dependent Components
The `startup_guard` module provides `check_google_credentials()` (sync, returns remediation status), `check_google_credentials_with_db(conn)` (async, DB-aware), and `require_google_credentials_or_exit()` (hard-exit guard for connectors).

#### Scenario: DB-aware check passes
- **WHEN** `check_google_credentials_with_db(conn)` is called and credentials exist in DB
- **THEN** it returns `GoogleCredentialCheckResult(ok=True)`

#### Scenario: Hard exit on missing credentials
- **WHEN** `require_google_credentials_or_exit(caller="gmail-connector")` is called and credentials are missing
- **THEN** a formatted error is printed to stderr and `sys.exit(1)` is called

### Requirement: Environment Variable Credential Validation
`validate_credentials()` checks `butler.env.required` and module credential env vars at startup. Missing required vars produce an aggregated `CredentialError`. Optional vars log warnings.

#### Scenario: Missing required env var
- **WHEN** `validate_credentials(env_required=["MY_KEY"])` is called and `MY_KEY` is not set
- **THEN** `CredentialError` is raised listing the missing variable and its source

#### Scenario: Missing optional env var warns
- **WHEN** `validate_credentials(env_optional=["OPT_KEY"])` is called and `OPT_KEY` is not set
- **THEN** a warning is logged but no exception is raised

### Requirement: Async Core Credential Validation
Runtime authentication uses CLI-level OAuth tokens (device-code flow via the dashboard Settings page), not API keys. The `validate_core_credentials_async()` function is a no-op; no API key validation is performed at startup.

### Requirement: Async Module Credential Validation
`validate_module_credentials_async(module_credentials, credential_store)` checks each module's declared credential keys via `CredentialStore.resolve()`. Returns a dict of per-module missing keys (non-fatal, does not raise).

#### Scenario: Module credential resolvable
- **WHEN** a module's credential key is found in DB or env
- **THEN** it does not appear in the returned failures dict

#### Scenario: Module credential missing
- **WHEN** a module's credential key is not resolvable from DB or env
- **THEN** the module name and missing key appear in the returned dict

### Requirement: Inline Secret Detection
`detect_secrets(config_values)` scans config string values for suspected inline secrets using prefix patterns (sk-, ghp_, xoxb-, etc.), base64-like strings, and key name heuristics. Returns advisory warning messages.

#### Scenario: Known prefix detected
- **WHEN** a config value starts with `sk-` (OpenAI pattern)
- **THEN** a warning message is returned suggesting an environment variable

#### Scenario: No secrets detected
- **WHEN** config values are normal strings (URLs, names, etc.)
- **THEN** an empty list is returned
