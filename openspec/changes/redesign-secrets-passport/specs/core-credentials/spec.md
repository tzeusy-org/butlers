# core-credentials

## ADDED Requirements

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
Credential fingerprints rendered on `/secrets` SHALL be computed on-read using PostgreSQL's `sha256()` function and truncated to the first 8 hex characters. Fingerprints SHALL NOT be persisted to any column, cache, or log.

Rationale: persisting a fingerprint creates a side-channel for offline brute-force attacks against weak secrets; on-read computation eliminates the side-channel without measurably impacting read latency at the page sizes the `/secrets` page renders.

#### Scenario: SELECT computes fingerprint inline
- **WHEN** `GET /api/secrets/inventory` is called
- **THEN** the underlying SELECT query includes a computed column of the form `substr(encode(sha256(value::bytea), 'hex'), 1, 8) AS fingerprint`
- **AND** no DB column anywhere in the schema stores the fingerprint
