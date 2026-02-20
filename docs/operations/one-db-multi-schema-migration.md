# One-DB Multi-Schema Operations Runbook

Status: Active runbook for issue `butlers-1003`
Last updated: 2026-02-20

## 1. Purpose

This runbook defines how to operate Butlers in the target topology:

- one PostgreSQL database (default: `butlers`)
- one schema per butler (`switchboard`, `general`, `relationship`, `health`, `messenger`)
- one shared schema (`shared`)

It covers local development, CI, production cutover, rollback, and troubleshooting.

## 2. Security and Isolation Model (Normative)

All runtime access must follow this contract:

- Each runtime role can access only:
  - its own schema (`<butler_schema>`)
  - `shared`
- Cross-butler schema access is denied by default.
- Runtime roles are non-owners.
- Schema owners are migration/platform roles, not runtime roles.

Required runtime connection behavior:

- All butlers connect to the same database name (for example `butlers`).
- Runtime role search path is constrained to `<butler_schema>,shared,public`.

### Reference role naming

Use one runtime role per butler. Example names:

- `butler_switchboard_app`
- `butler_general_app`
- `butler_relationship_app`
- `butler_health_app`
- `butler_messenger_app`

If your environment uses a different suffix (for example `_rw`), keep policy semantics identical.

## 3. Environment Configuration

### 3.1 Butler config (`butler.toml`)

Set every butler to the same DB name:

```toml
[butler.db]
name = "butlers"
```

Notes:

- `src/butlers/config.py` currently reads `butler.db.name` as the database selector.
- Schema scoping is enforced at the DB-role level (`search_path`) in this release train.

### 3.2 Environment variables

Supported DB connection sources are:

- `DATABASE_URL` (preferred)
- `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`
- Optional SSL control: `POSTGRES_SSLMODE` or `sslmode` in `DATABASE_URL`

Reference implementation: `src/butlers/db.py::db_params_from_env`.

### 3.3 Legacy multi-DB deprecation

Legacy per-butler DB names (`butler_general`, `butler_health`, and peers) are deprecated for runtime traffic in one-DB mode.

Deprecation policy:

- Allowed only as migration sources during cutover windows.
- Must not be used as the active runtime target after one-DB cutover.
- Any docs/scripts still referencing per-butler runtime DBs must be treated as historical and migrated to this runbook.

## 4. Local Development Setup

### 4.1 Bootstrap local DB

1. Start Postgres:
   - `docker compose up -d postgres`
2. Set DB env vars (or `DATABASE_URL`) for local shell.
3. Ensure butler configs use `[butler.db].name = "butlers"`.
4. Provision DB (idempotent):
   - `uv run butlers db provision --dir roster`

### 4.2 Bootstrap schemas and migrations

Run daemons once (or specific butlers) to apply startup migrations:

- `uv run butlers up --only switchboard --only general --only relationship --only health --only messenger`

Startup runs:

- core migration chain (`core_*`)
- enabled module chains
- butler-specific chains

### 4.3 Validate local schema layout

```sql
SELECT schema_name
FROM information_schema.schemata
WHERE schema_name IN ('shared', 'switchboard', 'general', 'relationship', 'health', 'messenger')
ORDER BY schema_name;
```

Expected: six rows.

## 5. CI Topology Contract

CI must run against one ephemeral PostgreSQL database per job and validate:

- schema bootstrap success
- ACL isolation behavior
- regression safety for runtime data access

Minimum CI checks:

1. Apply migrations in a fresh DB and verify required schemas exist.
2. Run integration tests that confirm:
   - own-schema + `shared` access succeeds
   - cross-schema access is denied
3. Run standard quality gate (`make test-qg`) on release-readiness runs.

## 6. Production Cutover Runbook

### 6.1 Preflight checklist (required)

1. Backups and snapshots are verified for all source DBs.
2. Freeze window approved.
3. Rollback config artifact prepared:
   - previous runtime env/config
   - source DB endpoints/credentials
4. Schema bootstrap verified in target DB:
   - `shared`, `switchboard`, `general`, `relationship`, `health`, `messenger`
5. Runtime roles exist and have own-schema + `shared` permissions only.
6. Staging dry-run parity and ACL checks passed.

### 6.2 Cutover steps

1. Quiesce writers on legacy topology.
2. Run final incremental sync from legacy DBs to target schemas.
3. Confirm parity gates (counts/checksums/sample reads).
4. Switch runtime configs so all butlers target `[butler.db].name = "butlers"`.
5. Restart daemons and dashboard API.
6. Execute smoke checks:
   - butlers start cleanly
   - dashboard endpoints read/write expected data
   - connector ingress succeeds
7. Start observation window (recommended: 24h).

### 6.3 Validation gates (must pass)

Correctness:

- row-count parity for required tables
- deterministic checksum parity for selected high-risk tables
- per-butler tool smoke checks

Isolation:

- each runtime role can read/write own schema
- each runtime role can use only approved `shared` objects
- each runtime role is denied on at least one non-owned schema table

## 7. Rollback Runbook

Trigger rollback if any cutover gate fails or Sev1/Sev2 regression appears.

### 7.1 Rollback steps

1. Stop or quiesce one-DB runtime writers.
2. Restore last-known-good runtime config (legacy topology).
3. Restart services against legacy DB endpoints.
4. Validate core health and critical tools on legacy path.
5. Preserve one-DB state for forensic comparison (do not destroy immediately).
6. Record incident timeline, failed gate, and delta findings.

### 7.2 Rollback exit criteria

- Production traffic stable on legacy topology.
- Data integrity confirmed on legacy source of truth.
- Root-cause issue captured for next migration attempt.

## 8. Troubleshooting

### 8.1 `permission denied for schema <schema>`

Likely cause:

- missing `USAGE` or table grants for runtime role

Checks:

```sql
SELECT n.nspname AS schema, r.rolname AS role, has_schema_privilege(r.rolname, n.nspname, 'USAGE') AS has_usage
FROM pg_namespace n
CROSS JOIN pg_roles r
WHERE n.nspname IN ('shared','switchboard','general','relationship','health','messenger')
  AND r.rolname LIKE 'butler\\_%\\_app';
```

### 8.2 Unexpected cross-schema access success

Likely cause:

- over-broad grant or inherited role

Checks:

```sql
SELECT grantee, table_schema, table_name, privilege_type
FROM information_schema.role_table_grants
WHERE grantee LIKE 'butler\\_%\\_app'
  AND table_schema IN ('switchboard','general','relationship','health','messenger','shared')
ORDER BY grantee, table_schema, table_name, privilege_type;
```

Expected:

- each role has table privileges only in its own schema and explicitly approved `shared` tables.

### 8.3 Butler starts but queries wrong schema

Likely cause:

- role `search_path` not constrained

Checks:

```sql
SELECT rolname, rolconfig
FROM pg_roles
WHERE rolname LIKE 'butler\\_%\\_app';
```

Expected role config contains:

- `search_path=<own_schema>,shared,public`

### 8.4 OAuth/credentials store mismatch after cutover

Symptoms:

- OAuth appears complete but modules still cannot resolve credentials

Checks:

- verify `shared.butler_secrets` exists
- verify runtime can read required secret keys
- confirm legacy fallback DB vars are not unintentionally overriding one-DB behavior

Relevant code paths:

- `src/butlers/credential_store.py`
- `src/butlers/daemon.py` (`_build_credential_store`)
- `src/butlers/api/deps.py` (dashboard shared credential pool wiring)

## 9. Operator Signoff Checklist

A cutover attempt is complete only when all are true:

1. Production is running on one DB (`butlers`) with per-butler schemas + `shared`.
2. ACL isolation checks pass in production-like validation.
3. Observability shows stable error rates during observation window.
4. Rollback artifacts are archived and verified.
5. Legacy per-butler DB runtime paths are marked deprecated or removed.
