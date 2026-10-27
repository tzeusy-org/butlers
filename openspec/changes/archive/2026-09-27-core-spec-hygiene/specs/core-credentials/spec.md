## RENAMED Requirements

- FROM: `### Requirement: Audit Action Vocabulary for Credential Lifecycle (formerly Audit Action Enum Extension)`
- TO: `### Requirement: Audit Action Vocabulary for Credential Lifecycle`

## MODIFIED Requirements

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

Retention: rows are kept for at least 90 days. An archive path is permitted (e.g. periodic move to a cold-storage table) but is not specified here.

#### Scenario: Probe writes one row
- **WHEN** any probe mutation endpoint runs
- **THEN** exactly one row is inserted into `public.secret_probe_log`
- **AND** the row's `credential_scope` and `credential_key` match the URL path of the endpoint

#### Scenario: Recent-probe query performance
- **WHEN** any per-credential read endpoint queries the most recent probe row for a (scope, key) pair
- **THEN** the query uses the `ix_secret_probe_log_lookup` index and returns in < 5 ms even with > 1 million log rows
