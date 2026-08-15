# Credentials

## Purpose
Provides credential storage, resolution, validation, expiry-state derivation, and proactive lifecycle notifications for butler daemons, including a generic DB-backed secret store (`butler_secrets`), Google OAuth credential lifecycle management, environment variable validation, and inline secret detection.

## Requirements

### Requirement: CredentialStore Interface
The `CredentialStore` class SHALL provide async CRUD operations on the `butler_secrets` DB table: `store()`, `load()`, `resolve()`, `has()`, `delete()`, and `list_secrets()`. The store is backed by an asyncpg pool and supports fallback pools for shared credential lookup.

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
`ensure_secrets_schema(pool)` SHALL create the `butler_secrets` table and category index if they do not exist, using `CREATE TABLE IF NOT EXISTS`.

#### Scenario: Table provisioned on first call
- **WHEN** `ensure_secrets_schema(pool)` is called on a fresh database
- **THEN** the `butler_secrets` table and `ix_butler_secrets_category` index are created

### Requirement: Google OAuth Credential Lifecycle
Google credentials SHALL be split across two stores: app credentials (client_id, client_secret, scope) in `butler_secrets` under the `google` category, and the refresh token in `public.entity_info` on the account's companion entity (resolved via `public.google_accounts`). The `GoogleCredentials` Pydantic model validates non-empty fields. Secret values (client_secret, refresh_token) are redacted in `__repr__` and `__str__`.

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
`resolve_google_credentials(store, caller, account=None)` SHALL load credentials from DB only and raise `MissingGoogleCredentialsError` if they are unavailable. The `account` parameter selects which Google account's refresh token to use.

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
The `startup_guard` module SHALL provide `check_google_credentials()` (sync, returns remediation status), `check_google_credentials_with_db(conn)` (async, DB-aware), and `require_google_credentials_or_exit()` (hard-exit guard for connectors).

#### Scenario: DB-aware check passes
- **WHEN** `check_google_credentials_with_db(conn)` is called and credentials exist in DB
- **THEN** it returns `GoogleCredentialCheckResult(ok=True)`

#### Scenario: Hard exit on missing credentials
- **WHEN** `require_google_credentials_or_exit(caller="gmail-connector")` is called and credentials are missing
- **THEN** a formatted error is printed to stderr and `sys.exit(1)` is called

### Requirement: Environment Variable Credential Validation
`validate_credentials()` SHALL check `butler.env.required` and module credential env vars at startup. Missing required vars produce an aggregated `CredentialError`. Optional vars log warnings.

#### Scenario: Missing required env var
- **WHEN** `validate_credentials(env_required=["MY_KEY"])` is called and `MY_KEY` is not set
- **THEN** `CredentialError` is raised listing the missing variable and its source

#### Scenario: Missing optional env var warns
- **WHEN** `validate_credentials(env_optional=["OPT_KEY"])` is called and `OPT_KEY` is not set
- **THEN** a warning is logged but no exception is raised

### Requirement: Async Core Credential Validation
Runtime authentication SHALL use either CLI-level OAuth tokens (device-code flow) or API keys, depending on the provider's `auth_mode` as configured in the CLI auth registry. API-key providers (e.g. Claude with `ANTHROPIC_API_KEY`) store their keys in the credential store via the dashboard Settings → CLI Runtime Authentication card. The `validate_core_credentials_async()` function is a no-op; credential availability is checked lazily at spawn time via `CredentialStore.resolve()`.

#### Scenario: Core credential validation is lazy
- **WHEN** `validate_core_credentials_async()` is called
- **THEN** it SHALL not reject an unavailable runtime credential
- **AND** the runtime resolves that credential lazily when it is spawned

### Requirement: Async Module Credential Validation
`validate_module_credentials_async(module_credentials, credential_store)` SHALL check each module's declared credential keys via `CredentialStore.resolve()`. It returns a dict of per-module missing keys (non-fatal, does not raise).

#### Scenario: Module credential resolvable
- **WHEN** a module's credential key is found in DB or env
- **THEN** it does not appear in the returned failures dict

#### Scenario: Module credential missing
- **WHEN** a module's credential key is not resolvable from DB or env
- **THEN** the module name and missing key appear in the returned dict

### Requirement: Inline Secret Detection
`detect_secrets(config_values)` SHALL scan config string values for suspected inline secrets using prefix patterns (sk-, ghp_, xoxb-, etc.), base64-like strings, and key name heuristics. It returns advisory warning messages.

#### Scenario: Known prefix detected
- **WHEN** a config value starts with `sk-` (OpenAI pattern)
- **THEN** a warning message is returned suggesting an environment variable

#### Scenario: No secrets detected
- **WHEN** config values are normal strings (URLs, names, etc.)
- **THEN** an empty list is returned

<!-- Source: connector-spotify -->

### Requirement: Spotify OAuth Token Storage

Spotify access and refresh tokens SHALL remain RFC 0006 Tier 2 credentials because they
are bound to the owner's personal Spotify account. Their sole authoritative
store SHALL be secured rows in `public.entity_info` on the owner entity, read
through `resolve_owner_entity_info()`. The connector-owned Spotify PKCE flow is
the only writer of those rows. A generic OAuth Spotify registry, Passport
projection, User credential inventory row, `CredentialStore`, or other store
SHALL NOT duplicate, persist, or mutate Spotify token material.

The Spotify OAuth app client ID is a system-level app credential rather than
personal-account token material. `SPOTIFY_CLIENT_ID` SHALL remain in Tier 1
`CredentialStore`. Derived expiry and granted-scope state MAY be retained as
non-secret connector metadata, but it SHALL NOT become a second token authority.

`spotify_oauth_access`, `spotify_oauth_refresh`, and
`spotify_oauth_expires_at` are a connector-managed Tier 2 exception to
the generic User credential editor in `PassportAddPanel`. `PassportAddPanel`
SHALL NOT offer those types through `ENTITY_INFO_TYPES`, and generic Secrets
read and mutation endpoints SHALL exclude them server-side. That exclusion
does not transfer token authority to Tier 1 `CredentialStore`.

#### Scenario: Store Spotify OAuth tokens

- **WHEN** the Spotify OAuth flow completes successfully
- **THEN** the access token SHALL be stored as a secured owner
  `public.entity_info` row with `type = 'spotify_oauth_access'`
- **AND** the refresh token SHALL be stored as a secured owner
  `public.entity_info` row with `type = 'spotify_oauth_refresh'`
- **AND** the access-token expiry SHALL be stored as owner `public.entity_info`
  with `type = 'spotify_oauth_expires_at'`
- **AND** `SPOTIFY_CLIENT_ID` SHALL remain in `CredentialStore` under category
  `"spotify"` and SHALL NOT share the Tier 2 token rows

#### Scenario: Spotify authority has no token mirror

- **WHEN** Spotify authorization, refresh, or disconnect changes token state
- **THEN** only the connector-owned PKCE flow SHALL write or delete the
  secured owner `public.entity_info` token rows
- **AND** `connector_registry` MAY contain only derived connection or scope
  metadata
- **AND** a Passport projection MAY invoke connector actions but SHALL NOT
  store or expose a token mirror

#### Scenario: Resolve Spotify credentials for connector

- **WHEN** the Spotify connector needs personal-account token material
- **THEN** it SHALL call
  `resolve_owner_entity_info(pool, "spotify_oauth_access")` and
  `resolve_owner_entity_info(pool, "spotify_oauth_refresh")`
- **AND** the matching secured values SHALL be returned from the owner entity
- **AND** environment variable fallback SHALL NOT be used (these are not infrastructure bootstrap credentials)

#### Scenario: Token refresh updates stored credentials

- **WHEN** the Spotify connector refreshes the access token
- **THEN** the connector-owned flow SHALL update the secured owner
  `spotify_oauth_access` row
- **AND** if the refresh response includes a new refresh token, it SHALL update
  the secured owner `spotify_oauth_refresh` row
- **AND** it SHALL update the owner `spotify_oauth_expires_at` row with the new
  expiry time

#### Scenario: Delete Spotify credentials on disconnect

- **WHEN** the user disconnects Spotify via the dashboard
- **THEN** the connector-owned flow SHALL delete the owner
  `spotify_oauth_access`, `spotify_oauth_refresh`, and
  `spotify_oauth_expires_at` rows and clear its derived granted-scope metadata
- **AND** it SHALL retain `SPOTIFY_CLIENT_ID` so the user can reconnect without re-entering it
- **AND** it SHALL not issue a provider-side authorization-revocation request

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

### Requirement: Audit Action Vocabulary for Credential Lifecycle (formerly Audit Action Enum Extension)
The `public.audit_log.action` column is unconstrained `TEXT`; this requirement defines the credential-lifecycle action vocabulary that writers SHALL use:

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
| `lifecycle_state_notified` | A direct proactive lifecycle notification was delivered for an attention state |

Because `public.audit_log.action` has no enum or check constraint, this specification is the authoritative action vocabulary; an action addition requires no enum or constraint migration.

#### Scenario: All mutation endpoints write audit rows with new actions
- **WHEN** any `/api/secrets/*` mutation endpoint completes successfully
- **THEN** an `audit_log` row is appended with `actor = "owner"` (single-owner system), `action = <appropriate vocabulary value above>`, `target = <canonical credential key>`, and `note = <stored prose; never LLM-generated>`

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

### Requirement: Expiring Credential State Derivation
The credential inventory SHALL derive lifecycle state deterministically from the secret value, probe result, and known expiration rather than from a separate notification-only state machine. For every current credential category and provider, the imminent-expiry lead window is seven days; no non-default window is currently registered. `expired` takes precedence when `expires_at <= now`, `failing` takes precedence over an upcoming expiry when the latest probe failed, and `expiring` applies when a known future expiration is within the seven-day window. A set credential with no probe result outside that window remains `warn`; a successful probe outside that window is `ok`.

#### Scenario: A known expiry becomes expiring before it expires
- **WHEN** a set credential has an `expires_at` later than `now` but no more than seven days away
- **AND** its latest probe is successful or absent
- **THEN** the inventory state is `expiring`
- **AND** it is not reported as `warn` merely because it has never been probed

#### Scenario: Expiration and failed probes keep their higher-priority states
- **WHEN** a set credential has an `expires_at` at or before `now`
- **THEN** the inventory state is `expired` even if the latest probe also failed
- **WHEN** a set credential has a future `expires_at` within seven days and its latest probe failed
- **THEN** the inventory state is `failing`, not `expiring`

#### Scenario: Google test-mode expiration is derived without inventing other expirations
- **WHEN** a Google account is marked `google_health_test_mode=true` and has `last_token_refresh_at`
- **THEN** its expiration is derived as that timestamp plus seven days before inventory state is derived
- **AND** a non-test-mode provider without a known expiration remains without a fabricated expiration

### Requirement: Proactive Credential Lifecycle Scan
The dashboard API SHALL run a deterministic, zero-LLM lifecycle scan over the same system, CLI, and owner-default User credential projections that power `GET /api/secrets/inventory`. The scan SHALL consider only `expiring`, `failing`, and `expired` as owner-attention states; `warn` and display-only synthetic states are not proactive-notification triggers. Provider-managed System credentials with category `spotify` SHALL be excluded because Spotify's dedicated connector refresh and status path owns their actionable health.

The scan SHALL run from the dashboard API lifespan after `DatabaseManager` initialization, with a default interval of 1,800 seconds and optional positive `SECRETS_LIFECYCLE_SCAN_INTERVAL_S` override. It SHALL sleep before its first scan, keep running after an unexpected scan error, and be cancelled and awaited at application shutdown. A non-positive interval MUST fall back to the default at API startup and be rejected by the loop when supplied directly.

#### Scenario: The scan uses the inventory's credential scope
- **WHEN** the lifecycle scan runs with its shared credential pool available
- **THEN** it collects per-butler and shared System credentials, CLI credentials, and the owner-default User projection using the inventory-family fetches
- **AND** it excludes shared `cli` and `cli-auth` rows from the System pass so they are not double-counted with the CLI family
- **AND** it excludes Spotify-category System rows before the attention and delivery path

#### Scenario: An attention state is found outside the dashboard
- **WHEN** the scan observes a credential in `expiring`, `failing`, or `expired`
- **THEN** it evaluates lifecycle delivery without requiring the owner to visit `/secrets`
- **AND** it does not attempt delivery for an `ok` or `warn` credential

#### Scenario: Per-butler collection is partially degraded
- **WHEN** collection for one butler's System credentials fails
- **THEN** the scan continues collecting healthy butlers and shared credential families
- **AND** its summary includes the failed butler in `sources_degraded` rather than reporting a clean scan

#### Scenario: The shared credential pool is unavailable
- **WHEN** the dashboard API has no shared credential pool
- **THEN** the lifecycle scan returns a zero-count no-op summary
- **AND** it does not attempt an owner delivery

### Requirement: Transition-Debounced Lifecycle Delivery
The lifecycle scan SHALL use the canonical credential key as its delivery identity and compare the current attention state with the most recent `public.audit_log` row whose `action` is `lifecycle_state_notified` for that key. If the most recently delivered state equals the current state, the scan SHALL skip the delivery path. A different current attention state SHALL be eligible for another notification; a return to a previously notified state is still skipped when that state remains the latest delivered marker.

Only a confirmed direct delivery MAY advance the marker: it appends `action="lifecycle_state_notified"`, `target=<canonical credential key>`, and `note=<delivered state>`. Deferred, suppressed, failed, and marker/audit-write-error paths SHALL leave the marker unadvanced so a later scan can retry instead of silently losing the condition. If the marker lookup fails, the scan SHALL treat the credential as not yet notified rather than suppressing a potentially actionable alert.

This read-then-write debounce SHALL be operated with one dashboard-API replica. It does not provide a cross-replica claim or uniqueness guarantee; horizontal dashboard-API scaling requires a new concurrency contract before this lifecycle scan can be replicated.

#### Scenario: One delivered attention state does not repeat every scan
- **WHEN** the latest lifecycle marker for `u:google` has `note="expiring"` and the current state is `expiring`
- **THEN** the scan sends no notification and records no new attention-ledger outcome for that credential

#### Scenario: A later attention state is delivered
- **WHEN** the latest lifecycle marker for a canonical credential key is `expiring` and the current state is `expired`
- **THEN** the scan attempts an `expired` notification
- **AND** after confirmed direct delivery with a successful audit write it appends a new marker with `note="expired"`

### Requirement: Lifecycle Notification Remediation and Delivery Honesty
The lifecycle notification SHALL identify the credential state and include a dashboard URL ending in `/secrets?focus=<URL-encoded canonical credential key>`. For a User credential whose catalogued provider is OAuth, it SHALL additionally include a remediation line directing the owner to re-authorize from that credential card; non-OAuth credentials SHALL not receive that line. The notification SHALL NOT embed a dashboard-API URL: `DASHBOARD_URL` is the frontend base and the API is reachable only under a separate, deployment-specific mount that is not derivable from it, so a composed `/api/oauth/<provider>/start` link is dead on every path-mounted deployment. The `/secrets` deep link already lands on the card whose re-authorize control starts the same dance.

Lifecycle delivery SHALL use medium-priority owner Telegram delivery and the established notify-boundary order: (1) Switchboard `delivery_preferences` quiet-hours evaluation, (2) owner quiet-hours and context-bus suppression, then (3) recipient resolution and delivery. The first gate defers by placing a `notify.v1` envelope on Switchboard's deferred-notifications queue; the next gates suppress and rely on a later lifecycle scan. Every delivery decision SHALL be recorded to the attention ledger as `delivered`, `deferred`, `suppressed`, or `failed` with a machine-readable reason. A ledger-write failure MUST NOT change the delivery decision or lifecycle-marker behavior it describes; `failed` and `deferred` are never interchangeable.

For a failed transport delivery, or an unexpected error after a complete message and recipient are known, the scan SHALL best-effort enqueue one retry envelope on Switchboard's deferred-notifications queue with a 30-minute backoff (or the already-resolved quiet-hours delivery time). Before enqueueing it SHALL cancel pending envelopes for the same credential's state-independent `/secrets?focus=` fragment, so the latest state supersedes old retries. A later confirmed direct delivery SHALL cancel any remaining pending retry envelope for that fragment. Missing-recipient and pre-resolution failures SHALL be recorded as `failed` without a malformed retry envelope.

#### Scenario: The owner gets an actionable OAuth remediation message
- **WHEN** `u:google` transitions to `expiring` and direct delivery succeeds
- **THEN** the message includes `/secrets?focus=u%3Agoogle`
- **AND** it includes a re-authorize remediation line and no `/api/` URL
- **AND** the attention ledger records `outcome="delivered"` with a `state_transition:expiring` reason

#### Scenario: Quiet hours defer without pretending the transition is delivered
- **WHEN** Switchboard delivery preferences defer the medium-priority lifecycle notification
- **THEN** a Switchboard `notify.v1` envelope is queued for its computed batch delivery time
- **AND** the attention ledger records `outcome="deferred"` with reason `delivery_preferences_quiet_hours`
- **AND** no lifecycle-state marker is appended before a confirmed direct delivery

#### Scenario: Quiet-hours queue failure is failed and retryable
- **WHEN** Switchboard delivery preferences require a medium-priority lifecycle notification to defer, the owner recipient resolves, and the deferred-notifications queue cannot persist its `notify.v1` envelope
- **THEN** the attention ledger records `outcome="failed"`, reason `delivery_preferences_queue_failure_retryable`, and a null `notification_ref`
- **AND** the scan does not deliver directly inside quiet hours, increment its deferred count, or append a lifecycle-state marker
- **AND** a later lifecycle scan remains eligible to retry the transition

#### Scenario: A suppression is visible and retriable
- **WHEN** owner quiet hours or an active `dnd` or `sleeping` context signal suppresses the lifecycle notification
- **THEN** the attention ledger records `outcome="suppressed"` with the applicable machine-readable reason
- **AND** no lifecycle-state marker is appended, so the next scan remains eligible to retry

#### Scenario: A transport outage records failure and keeps only the latest retry
- **WHEN** direct lifecycle delivery returns a transport failure for a credential that already has a pending retry envelope
- **THEN** the prior pending envelope matching that credential's `/secrets?focus=` fragment is cancelled before the new envelope is queued
- **AND** the attention ledger records `outcome="failed"`, a `delivery_error:<detail>` reason, and the retry envelope reference when enqueueing succeeded
- **AND** no lifecycle-state marker is appended

### Requirement: Live Codex Device-Auth Reconciliation
The runtime SHALL treat the shared/public Tier 1 `cli-auth/codex` value as the
authoritative Codex device-auth state when its adapter is supplied a credential
store with a shared fallback; only flat topology SHALL use its local store as
authority. Before a new Codex subprocess is launched, it SHALL reconcile that
DB-backed value to the canonical local `~/.codex/auth.json` path when the
contents differ. The reconciliation SHALL never log credential content, SHALL
write a replacement atomically with mode `0600`, and SHALL refresh the local
rotation baseline after a DB-originated write.

#### Scenario: Dashboard refresh takes effect on the next invocation
- **WHEN** the dashboard has stored a newer `cli-auth/codex` value while a
  daemon remains running with a different local `auth.json`
- **THEN** the next Codex invocation SHALL use the stored value without
  requiring a daemon restart
- **AND** no completed or already-running session SHALL be changed or replayed

#### Scenario: Stale schema-local state cannot shadow a dashboard refresh
- **WHEN** a schema-isolated daemon has an older local `cli-auth/codex` row
  and the public/shared row contains a newer dashboard credential
- **THEN** Codex reconciliation and runtime-originated persistence SHALL use
  the shared row
- **AND** the local row SHALL not prevent the newer dashboard credential from
  reaching the next invocation

#### Scenario: Matching local token is left untouched
- **WHEN** the stored `cli-auth/codex` value exactly matches the canonical
  local `auth.json`
- **THEN** reconciliation SHALL not replace the file
- **AND** it SHALL record the existing file as the rotation baseline

#### Scenario: Reconciliation remains credential-safe under degradation
- **WHEN** the credential store has no `cli-auth/codex` value, cannot be read,
  exceeds the bounded best-effort synchronization wait, or the local
  replacement cannot be written
- **THEN** reconciliation SHALL log only safe context and SHALL not expose a
  raw credential value
- **AND** it SHALL not itself prevent the existing runtime invocation path

#### Scenario: Concurrent reconciliation cannot expose a partial file
- **WHEN** multiple local runtime invocations reconcile the same Codex
  auth-file path concurrently
- **THEN** every visible file state SHALL be a complete credential document
- **AND** the final file mode SHALL remain `0600`

#### Scenario: A stale runtime rotation cannot overwrite a dashboard refresh
- **WHEN** a Codex subprocess was launched with an older authority snapshot
  and the dashboard writes a newer `cli-auth/codex` value before that process
  finishes
- **THEN** post-invocation rotation persistence SHALL perform a conditional
  update using the launch snapshot
- **AND** its update SHALL be skipped when the shared value has changed

#### Scenario: A stale runtime health result cannot affect a dashboard replacement
- **WHEN** a Codex subprocess launched on an older authority reports an auth
  failure after the dashboard has stored a replacement credential
- **THEN** its credential health update SHALL be conditional on the exact
  credential bytes used by that subprocess
- **AND** it SHALL not mark the replacement credential failing

#### Scenario: Value replacement atomically clears prior health state
- **WHEN** a runtime health update for credential A obtains the row lock before
  a dashboard refresh or a winning runtime rotation replaces A with B
- **THEN** the value-changing write SHALL clear the prior test status, code,
  message, and verification timestamp in the same database statement
- **AND** B SHALL not inherit A's healthy or failing state

#### Scenario: Dashboard Codex probe binds to the canonical authority it tests
- **WHEN** a dashboard Codex test begins with a shared credential B while the
  canonical local auth file still contains A
- **THEN** the test endpoint SHALL reconcile the canonical file to B before
  running the provider status command
- **AND** it SHALL persist health, probe history, and audit evidence only when
  that file still matches B and the shared credential value remains B
- **AND** those durable records SHALL share the value-fenced credential-row
  transaction so a later replacement cannot interleave between them
- **AND** a concurrent replacement or local-file change SHALL leave the HTTP
  probe response intact while withholding its durable health result
- **AND** when the status command itself rotates B to B-prime, the endpoint
  SHALL finalize B-prime through the same B-bound conditional write while
  still withholding that probe's health, history, and audit result
- **AND** a concurrent shared replacement C SHALL win that conditional write
  and be reconciled locally rather than overwritten by B-prime

#### Scenario: Absent or unavailable authority is never implicitly bootstrapped
- **WHEN** the shared Codex credential is absent, revoked, unavailable, or
  malformed while a canonical local auth file exists
- **THEN** a runtime preflight or post-operation finalizer SHALL NOT create or
  recreate the shared credential from that local file
- **AND** it SHALL NOT attach a runtime health result to the absent authority
- **AND** explicit dashboard device authentication remains the supported
  bootstrap path for a new shared credential

#### Scenario: Direct dispatcher authority is explicit
- **WHEN** a direct `DiscretionDispatcher` has only a schema-local model pool
- **THEN** it SHALL not construct a Codex credential authority from that pool
- **AND** callers with a known shared/public credential pool SHALL pass it
  explicitly to the runtime adapter

#### Scenario: Unknown post-crash local state is recovered conservatively
- **WHEN** a fresh process has no launch-bound local rotation baseline and the
  canonical local auth file differs from shared credential authority
- **THEN** reconciliation SHALL apply the shared authority rather than infer
  that the local file is a valid successor
- **AND** durable cross-process rotation provenance remains follow-up
  `bu-gg4fo`

#### Scenario: Invalid stored auth preserves a valid local file
- **WHEN** the authority contains an empty or malformed non-object Codex auth
  document while the local canonical file is valid
- **THEN** reconciliation and startup restoration SHALL not replace that local
  file
- **AND** safe logs SHALL not disclose either credential value

## Source References

- Non-Negotiable Rule 1 (`about/heart-and-soul/vision.md`): the single owner controls the instance and receives actionable credential remediation without exposing secret values.
- Non-Negotiable Rule 4 (`about/heart-and-soul/vision.md`): inventory-state derivation and the background scan are deterministic infrastructure, not LLM judgment.
- Non-Negotiable Rule 7 (`about/heart-and-soul/vision.md`): the job uses the established notification boundary instead of taking ownership of transport-specific connector behavior.
- RFC 0011 (Proactive Insight Delivery Protocol), especially Amendment 1: proactive egress is quiet-hours/context-aware and records durable, honest delivery outcomes in the attention ledger.
- `openspec/specs/core-notify/spec.md`: owner-notification outcome vocabulary and failure semantics.
- `openspec/specs/time-aware-delivery/spec.md`: deferred-notification storage, retry-envelope supersession, and flush behavior.
- `src/butlers/api/routers/secrets_v2.py`: shared inventory-state derivation and known-expiry handling.
- `src/butlers/jobs/secrets_lifecycle.py` and `src/butlers/api/app.py`: dashboard-lifespan scan, owner delivery, debounce, and shutdown behavior.
