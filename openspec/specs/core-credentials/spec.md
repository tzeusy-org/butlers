# Credentials

## Purpose
Provides credential storage, resolution, and validation for butler daemons, including a generic DB-backed secret store (`butler_secrets`), Google OAuth credential lifecycle management, environment variable validation, and inline secret detection.

## Requirements

### Requirement: CredentialStore Interface
The `CredentialStore` class provides async CRUD operations on the `butler_secrets` DB table: `store()`, `load()`, `resolve()`, `has()`, `delete()`, and `list_secrets()`. The store is backed by an asyncpg pool and supports fallback pools for shared credential lookup.

#### Scenario: Store a secret
- **WHEN** `store.store(key, value, category, description, is_sensitive)` is called
- **THEN** the secret is persisted via INSERT...ON CONFLICT DO UPDATE (idempotent upsert)
- **AND** the raw value is never logged

#### Scenario: Resolve secret (DB-first, env fallback)
- **WHEN** `store.resolve(key, env_fallback=True)` is called
- **THEN** the store checks the local DB first, then fallback DBs, then `os.environ[key]`
- **AND** returns the first non-None value found

#### Scenario: Resolve with env fallback disabled
- **WHEN** `store.resolve(key, env_fallback=False)` is called (this is the default; bare `store.resolve(key)` behaves identically)
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
Google credentials are split across two stores: app credentials (client_id, client_secret, scope) in `butler_secrets` under the `google` category, and the refresh token in `public.entity_info` on the account's companion entity (resolved via `public.google_accounts`). The `GoogleCredentials` Pydantic model validates non-empty fields. Secret values (client_secret, refresh_token) are redacted in `__repr__` and `__str__`.

#### Scenario: Store full Google credentials
- **WHEN** `store_google_credentials(store, client_id, client_secret, refresh_token, scope, account=<email_or_id>)` is called
- **THEN** app credentials are upserted in `butler_secrets` with keys `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_SCOPES`
- **AND** the `account` parameter is resolved to a `google_accounts` row and its companion `entity_id`
- **AND** the refresh token is upserted in `public.entity_info` on the companion entity

#### Scenario: Store credentials with account=None (primary)
- **WHEN** `store_google_credentials(store, ..., account=None)` is called
- **THEN** the primary account is resolved from `public.google_accounts WHERE is_primary = true`
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
Runtime authentication uses either CLI-level OAuth tokens (device-code flow) or API keys, depending on the provider's `auth_mode` as configured in the CLI auth registry. API-key providers (e.g. Claude with `ANTHROPIC_API_KEY`) store their keys in the credential store via the dashboard Settings → CLI Runtime Authentication card. The `validate_core_credentials_async()` function is a no-op; credential availability is checked lazily at spawn time via `CredentialStore.resolve()`.

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

<!-- Source: connector-spotify -->

### Requirement: Spotify OAuth Token Storage

The `CredentialStore` SHALL support storing and resolving Spotify OAuth tokens for the Spotify connector.

#### Scenario: Store Spotify OAuth tokens

- **WHEN** the Spotify OAuth flow completes successfully
- **THEN** the following keys SHALL be stored in `CredentialStore` under category `"spotify"`:
  - `SPOTIFY_CLIENT_ID` — the Spotify app client ID (entered by user, not sensitive)
  - `SPOTIFY_ACCESS_TOKEN` — the OAuth access token (sensitive, 1-hour TTL)
  - `SPOTIFY_REFRESH_TOKEN` — the OAuth refresh token (sensitive, long-lived)
  - `SPOTIFY_TOKEN_EXPIRES_AT` — the access token expiry as ISO 8601 timestamp (not sensitive)
- **AND** `SPOTIFY_ACCESS_TOKEN` and `SPOTIFY_REFRESH_TOKEN` SHALL be stored with `is_sensitive=True`

#### Scenario: Resolve Spotify credentials for connector

- **WHEN** the Spotify connector calls `store.resolve("SPOTIFY_ACCESS_TOKEN")`
- **THEN** the access token SHALL be returned from the DB
- **AND** environment variable fallback SHALL NOT be used (these are not infrastructure bootstrap credentials)

#### Scenario: Token refresh updates stored credentials

- **WHEN** the Spotify connector refreshes the access token
- **THEN** it SHALL call `store.store("SPOTIFY_ACCESS_TOKEN", new_token, category="spotify", is_sensitive=True)` to update the stored value
- **AND** if the refresh response includes a new refresh token, it SHALL also update `SPOTIFY_REFRESH_TOKEN`
- **AND** it SHALL update `SPOTIFY_TOKEN_EXPIRES_AT` with the new expiry time

#### Scenario: Delete Spotify credentials on disconnect

- **WHEN** the user disconnects Spotify via the dashboard
- **THEN** all four Spotify credential keys SHALL be deleted from `CredentialStore`
- **AND** `store.delete()` SHALL be called for each key

<!-- Source: redesign-secrets-passport -->

### Requirement: Test-State Columns on Credential Tables
`butler_secrets` (per-butler schema) and `public.entity_info` SHALL each gain four columns to cache the most recent probe outcome for the credential:

| Column | Type | Nullable | Default | Purpose |
|---|---|---|---|---|
| `last_verified` | `TIMESTAMPTZ` | YES | `NULL` | Timestamp of most recent successful probe |
| `last_test_ok` | `BOOLEAN` | YES | `NULL` | Outcome of most recent probe (NULL = never probed) |
| `last_test_code` | `INTEGER` | YES | `NULL` | HTTP / provider response code from most recent probe |
| `last_test_message` | `TEXT` | YES | `NULL` | Verbatim error tail from most recent probe (truncated to 512 chars) |

These columns are **caches** of the most recent row in `public.secret_probe_log` for the (scope, key) pair. They SHALL be written by the probe mutation endpoints (`/api/secrets/{user,system,cli}/<key>/probe`) inside the same transaction that writes the probe-log row, so the cache is never stale relative to the log.

#### Scenario: Backfill on migration
- **WHEN** the Alembic migration adding the four columns runs against an existing database
- **THEN** every existing row in `butler_secrets` and `public.entity_info` has the four columns set to `NULL`
- **AND** the migration MUST NOT attempt to backfill by triggering live probes (which would call external providers during DB upgrade)

#### Scenario: Cache write on probe
- **WHEN** a probe mutation endpoint records a probe result
- **THEN** within the same SQL transaction, the corresponding `butler_secrets` or `entity_info` row's `last_verified`, `last_test_ok`, `last_test_code`, `last_test_message` columns are updated
- **AND** if the probe succeeded, `last_verified` is set to `now()`; if it failed, `last_verified` is left at its previous value (a failed probe does not constitute verification)

### Requirement: `public.secret_probe_log` Cross-Butler Probe History Table
The Switchboard's migration chain SHALL create `public.secret_probe_log` to store the canonical history of every probe call across all butlers:

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGSERIAL PRIMARY KEY` | |
| `credential_scope` | `TEXT NOT NULL` | One of `user`, `system`, `cli` |
| `credential_key` | `TEXT NOT NULL` | Canonical key: provider slug (user), env var name (system), runtime id (cli) |
| `ok` | `BOOLEAN NOT NULL` | Probe outcome |
| `code` | `INTEGER NULL` | HTTP/provider code (NULL when not applicable) |
| `latency_ms` | `INTEGER NULL` | Round-trip latency |
| `at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | When the probe ran (server clock) |
| `message` | `TEXT NULL` | Verbatim provider error tail (truncated to 512 chars) |
| `recorded_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | When the row was inserted (may differ from `at` for buffered/retried writes) |

The table SHALL be in the `public` schema (cross-butler reads required by the `/api/secrets/*` endpoints; consistent with `about/legends-and-lore/rfcs/0006-database-schema-and-isolation.md:21-25`).

The table SHALL have one index: `ix_secret_probe_log_lookup` on `(credential_scope, credential_key, recorded_at DESC)` to support fast "last N probes for this key" queries.

Retention: rows are kept for at least 90 days. An archive path is permitted (e.g. periodic move to a cold-storage table) but is not specified by this change.

#### Scenario: Probe writes one row
- **WHEN** any probe mutation endpoint runs
- **THEN** exactly one row is inserted into `public.secret_probe_log`
- **AND** the row's `credential_scope` and `credential_key` match the URL path of the endpoint

#### Scenario: Recent-probe query performance
- **WHEN** any per-credential read endpoint queries the most recent probe row for a (scope, key) pair
- **THEN** the query uses the `ix_secret_probe_log_lookup` index and returns in < 5 ms even with > 1 million log rows

### Requirement: `public.provider_feature_catalogue` WhatBreaks Source-of-Truth Table
The Switchboard's migration chain SHALL create `public.provider_feature_catalogue` to back the WhatBreaks affordance with a server-side catalogue (resolving brief §5 Q8 as "Option B"):

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGSERIAL PRIMARY KEY` | |
| `provider` | `TEXT NOT NULL` | Provider slug (e.g. `google`, `telegram`, `spotify`, `home_assistant`) |
| `butler` | `TEXT NOT NULL` | Butler name (e.g. `health`, `lifestyle`, `home`) or `'*'` for ecosystem-wide |
| `feature` | `TEXT NOT NULL` | User-facing feature label (e.g. `"Google Fit ingestion"`, `"Spotify listening history"`) |
| `severity` | `TEXT NOT NULL CHECK (severity IN ('high', 'medium', 'low'))` | Feature criticality if the credential is sick |
| `required_scopes` | `JSONB NOT NULL DEFAULT '[]'` | Array of scope strings required to keep the feature alive |
| `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | Last-updated timestamp |

Unique constraint: `(provider, butler, feature)` so the same butler cannot register the same feature twice for the same provider.

Index: `(provider, butler)` for fast `?provider=` filtering.

The catalogue SHALL be bootstrapped by an Alembic seed during the initial migration covering the providers known at change-implementation time. Each butler MAY UPSERT its own `(provider, butler, feature, severity, required_scopes)` rows on startup so the catalogue tracks the actual roster as it grows. UPSERT on startup MUST be idempotent.

#### Scenario: Catalogue read for WhatBreaks render
- **WHEN** the `/secrets` page renders the WhatBreaks list for a User OAuth credential
- **THEN** the frontend fetches `GET /api/secrets/breaks-catalogue?provider=<p>`
- **AND** the endpoint reads `public.provider_feature_catalogue` filtered by `provider`
- **AND** the rendered list contains one row per `(butler, feature)` returned, sorted by `severity DESC, butler ASC, feature ASC`

#### Scenario: Butler UPSERTs on startup
- **WHEN** the health butler boots and its module discovers it consumes the `google` provider
- **THEN** the module UPSERTs rows for each `(provider=google, butler=health, feature=<label>, severity=<lvl>, required_scopes=<set>)` it depends on
- **AND** UPSERTs are idempotent: running the boot sequence twice produces zero net row changes after the first run

### Requirement: Audit Action Enum Extension for Credential Lifecycle
The audit action enum used by `public.audit_log` (originally specified by `redesign-settings-dispatch-console`) SHALL be extended with the following values for credential-lifecycle events:

| Action | Used by |
|---|---|
| `verified` | Probe success |
| `failed` | Probe failure |
| `rotated` | Value replaced (User rotate, System set on existing key, CLI rotate) |
| `connected` | OAuth dance completed successfully |
| `disconnected` | Credential explicitly disconnected (User disconnect, CLI revoke) |
| `warned` | Scope mismatch or expiring-soon detected during probe |
| `overrode` | System override created (per-butler `butler_secrets` row added) |
| `revoked` | System override removed (per-butler `butler_secrets` row removed via `DELETE /api/secrets/system/<key>?target=<butler>`) |
| `attempted` | OAuth dance initiated (begin endpoint called) but not yet completed |
| `set` | New System secret created (first-time `POST /api/secrets/system/<key>`) |

These values SHALL be added to whichever enum or check constraint enforces the action vocabulary in `public.audit_log`. If the column is `TEXT` with a check constraint, the constraint is extended; if it is an enum type, the enum is altered.

#### Scenario: All mutation endpoints write audit rows with new actions
- **WHEN** any `/api/secrets/*` mutation endpoint completes successfully
- **THEN** an `audit_log` row is appended with `actor = "owner"` (single-owner system), `action = <appropriate enum value above>`, `target = <canonical credential key>`, and `note = <stored prose; never LLM-generated>`

### Requirement: `public.audit_log` Index for Credential-Key Filtering
The Switchboard's migration chain SHALL add an index `ix_audit_log_target_ts` on `public.audit_log (target, ts DESC)` to support `GET /api/audit-log?key=<key>` filtering in O(log N) time even at high audit-log row counts. (The `public.audit_log` timestamp column is `ts`, declared by `redesign-settings-dispatch-console`'s `dashboard-audit-log` spec; this index reuses that column unchanged.)

#### Scenario: Audit filter performance
- **WHEN** `GET /api/audit-log?key=u:google&limit=50` is called against an audit log with > 1 million rows
- **THEN** the query uses `ix_audit_log_target_ts` and returns in < 50 ms

### Requirement: Credential-Key Normalisation Function
The `core-credentials` capability SHALL expose a Python utility `normalize_credential_key(scope: str, key: str) -> str` returning the canonical form `<prefix>:<key>` used by `audit_log.target`, the `/secrets` focus-key URL parameter, and `secret_probe_log.credential_key`. The function SHALL be used by every audit-write callsite and by the `/api/audit-log?key=` filter to ensure a consistent key vocabulary.

**Implementation:** `src/butlers/core/credential_keys.py` — module `butlers.core.credential_keys`.

The module exposes two public helpers:
- `normalize_credential_key(scope, key)` — primary factory; maps long-form scope (`"user"`, `"system"`, `"cli"`) or single-letter alias (`"u"`, `"s"`, `"c"`) to the canonical `<prefix>:<key>` string. Raises `ValueError` for unknown scopes.
- `normalize_key_param(raw_key)` — entry-point for `GET /api/audit-log?key=`; accepts either short-prefix or long-scope form and delegates to `normalize_credential_key`.

**Audit-write contract:** Every code path that appends a credential-lifecycle row to `public.audit_log` MUST pass `normalize_credential_key(scope, key)` as the `target` argument. Writing a raw, un-normalised string is a defect because it breaks the `?key=` filter's index lookup (`ix_audit_log_target_ts` on `(target, ts DESC)`).

#### Scenario: Normalisation roundtrip
- **WHEN** `normalize_credential_key("user", "google")` is called
- **THEN** the return value is `"u:google"`
- **AND** `normalize_credential_key("system", "BUTLER_TELEGRAM_TOKEN")` returns `"s:BUTLER_TELEGRAM_TOKEN"`
- **AND** `normalize_credential_key("cli", "claude")` returns `"c:claude"`

#### Scenario: Audit-write → key-filter round-trip
- **WHEN** a credential-lifecycle endpoint (e.g. `POST /api/secrets/user/<provider>/rotate`) appends an audit row using `normalize_credential_key("user", provider)` as `target`
- **THEN** `GET /api/audit-log?key=u:<provider>` returns that row
- **AND** `GET /api/audit-log?key=user:<provider>` returns the same row (long-scope form is also accepted by the filter)

### Requirement: On-Read Fingerprint Computation (No Persistence)
Credential fingerprints rendered on `/secrets` SHALL be computed on-read by hashing the secret value with SHA-256 and truncating to the first 8 hex characters. The computation runs in the application layer (`_fingerprint()` in `secrets_v2.py`) over the value fetched by the read query. Fingerprints SHALL NOT be persisted to any column, cache, or log.

Rationale: persisting a fingerprint creates a side-channel for offline brute-force attacks against weak secrets; on-read computation eliminates the side-channel without measurably impacting read latency at the page sizes the `/secrets` page renders.

#### Scenario: Fingerprint computed on-read
- **WHEN** `GET /api/secrets/inventory` is called
- **THEN** the secret value returned by the read query is hashed on-read with SHA-256 and truncated to the first 8 hex characters (`hashlib.sha256(value.encode()).hexdigest()[:8]`)
- **AND** no DB column anywhere in the schema stores the fingerprint
