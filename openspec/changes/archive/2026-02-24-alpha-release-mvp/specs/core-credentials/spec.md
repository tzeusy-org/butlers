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
Google credentials (client_id, client_secret, refresh_token, scope) are stored as individual rows in `butler_secrets` under the `google` category. The `GoogleCredentials` Pydantic model validates non-empty fields. Secret values (client_secret, refresh_token) are redacted in `__repr__` and `__str__`.

#### Scenario: Store full Google credentials
- **WHEN** `store_google_credentials(store, client_id, client_secret, refresh_token, scope)` is called
- **THEN** four individual rows are upserted in `butler_secrets` with keys `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`, `GOOGLE_OAUTH_SCOPES`

#### Scenario: Load Google credentials
- **WHEN** `load_google_credentials(store)` is called and all three required keys exist
- **THEN** a `GoogleCredentials` model is returned

#### Scenario: Partial credentials are an error
- **WHEN** some but not all required Google credential fields exist in the store
- **THEN** `InvalidGoogleCredentialsError` is raised listing missing fields

#### Scenario: No credentials stored
- **WHEN** none of the required Google credential keys exist
- **THEN** `load_google_credentials()` returns `None`

#### Scenario: Store app credentials (partial)
- **WHEN** `store_app_credentials(store, client_id, client_secret)` is called
- **THEN** only `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` are stored
- **AND** any existing refresh token is preserved

#### Scenario: Delete Google credentials
- **WHEN** `delete_google_credentials(store)` is called
- **THEN** all four Google credential keys are deleted from `butler_secrets`

### Requirement: Resolve Google Credentials (DB-Only)
`resolve_google_credentials(store, caller)` loads credentials from DB only. Raises `MissingGoogleCredentialsError` if not available.

#### Scenario: Credentials available in DB
- **WHEN** Google credentials are stored in the DB
- **THEN** `resolve_google_credentials()` returns a valid `GoogleCredentials` model

#### Scenario: Credentials not available
- **WHEN** no Google credentials are stored
- **THEN** `MissingGoogleCredentialsError` is raised with an actionable message

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
`validate_core_credentials_async(credential_store, runtime_type)` validates that the runtime's core credentials are resolvable via DB-first resolution. For `claude-code`, checks `ANTHROPIC_API_KEY`; for `gemini`, checks `GOOGLE_API_KEY`.

#### Scenario: Core credential available in DB
- **WHEN** `ANTHROPIC_API_KEY` is stored in `butler_secrets`
- **THEN** `validate_core_credentials_async()` passes without error

#### Scenario: Core credential missing everywhere
- **WHEN** `ANTHROPIC_API_KEY` is not in DB or env
- **THEN** `CredentialError` is raised with a message suggesting dashboard Secrets page or env var

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
