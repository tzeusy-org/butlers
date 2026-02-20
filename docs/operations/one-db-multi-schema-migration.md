# One-DB Multi-Schema Migration Plan

Status: Planned
Owner issue: `butlers-1003`
Last updated: 2026-02-20

## 1. Purpose

This document is the target-state design and execution plan for migrating Butlers from
multi-database topology to a single PostgreSQL database with per-butler schemas plus a
shared schema.

Target outcome:
- One PostgreSQL database for all butlers.
- One schema per butler plus a `shared` schema.
- Least-privilege ACL where each butler runtime role can access only its own schema and
  `shared`.

## 2. Scope and Non-Goals

In scope:
- Target topology, naming, ownership, and runtime connection model.
- ACL model and permission guarantees.
- Phased migration and cutover/rollback procedure.
- Data parity and isolation verification criteria.
- Local-dev, CI, and production operator guidance.

Non-goals:
- Re-architecting MCP routing semantics.
- Changing butler-facing tool contracts.
- New product functionality unrelated to storage topology.

## 3. Current vs Target Topology

Current (as of 2026-02-20):
- Each butler uses its own Postgres database (`butler_general`, `butler_health`, etc.).
- Shared concerns (for example credential storage) may use separate compatibility stores.

Target:
- Single Postgres database, default name `butlers`.
- Schemas:
  - `shared`
  - `switchboard`
  - `general`
  - `relationship`
  - `health`
  - `messenger`
  - `heartbeat`

## 4. Naming, Ownership, and Role Model

### 4.1 Database and schema ownership

- Database owner role: `butlers_owner`.
- Migration executor role: `butlers_migrator`.
- Runtime roles:
  - `butler_switchboard_rw`
  - `butler_general_rw`
  - `butler_relationship_rw`
  - `butler_health_rw`
  - `butler_messenger_rw`
  - `butler_heartbeat_rw`

Schema ownership:
- `shared` owned by `butlers_owner`.
- Each butler schema owned by `butlers_owner` (not by runtime roles).

Rationale: ownership centralized under platform role prevents runtime principals from
self-escalating ACL.

### 4.2 Runtime connection model

- All daemon/API pools connect to one database DSN.
- Butler runtime pool selects principal via role-specific credentials.
- Connection `search_path` is constrained to:
  - `<butler_schema>,shared,public` for butler runtimes.
  - `switchboard,shared,public` for switchboard runtime.
- SQL remains schema-qualified in migrations and sensitive runtime paths.

## 5. ACL and Isolation Guarantees

The model guarantees that each runtime role can access only its own schema plus `shared`.

### 5.1 Baseline revokes

- `REVOKE ALL ON DATABASE butlers FROM PUBLIC;`
- `REVOKE ALL ON SCHEMA public FROM PUBLIC;`
- For each non-owned schema, no `USAGE` grant is provided to other butler roles.

### 5.2 Per-role grants

For a role `butler_<name>_rw`:
- Own schema `<name>`:
  - `USAGE, CREATE` on schema.
  - `SELECT, INSERT, UPDATE, DELETE, TRIGGER, REFERENCES` on tables.
  - `USAGE, SELECT, UPDATE` on sequences.
  - `EXECUTE` on functions (schema-scoped as needed).
- Shared schema `shared`:
  - `USAGE` on schema.
  - Only operation-minimum grants on shared tables/functions (least privilege),
    defaulting to no access until explicitly granted.
- Other butler schemas:
  - No `USAGE`; all table/sequence/function access denied by default.

### 5.3 Default privileges for future objects

For each schema owner + schema pair:
- `ALTER DEFAULT PRIVILEGES IN SCHEMA <schema> ...` is set so new objects inherit
  intended grants automatically.
- Cross-schema default grants are not configured.

### 5.4 Isolation proof requirements

For every butler role in CI integration tests:
- Positive checks:
  - Can read/write own schema tables.
  - Can perform approved operations in `shared`.
- Negative checks:
  - Fails with permission error when reading/writing at least one table in another
    butler schema.

## 6. Phased Migration Plan

### Phase 0: Preflight and inventory

Entry criteria:
- Current migrations green.
- Full schema/table inventory exported from all source DBs.

Execution:
- Freeze baseline table manifests and row counts.
- Confirm mapping from source DB -> target schema.

Exit criteria:
- Inventory artifact checked into migration records.

Rollback:
- No-op (planning phase).

### Phase 1: Bootstrap one-DB schemas

Entry criteria:
- Approved schema/role naming.

Execution:
- Add additive migrations to create `shared` and per-butler schemas idempotently.
- Add role bootstrap and baseline ACL grants.

Exit criteria:
- Fresh install creates complete schema layout.
- Existing install upgrades without migration chain divergence.

Rollback:
- Revert deployment to pre-bootstrap revision.
- Drop newly-created schemas only in non-production dry-runs.

### Phase 2: ACL hardening and runtime wiring

Entry criteria:
- Schema bootstrap complete.

Execution:
- Apply full per-role grant/revoke model and default privileges.
- Refactor daemon/API DB config to schema-aware one-DB model.

Exit criteria:
- Runtime components use one DB with correct schema scoping.
- Integration tests validate positive + negative access paths.

Rollback:
- Re-enable legacy multi-DB config path.
- Retain one-DB schemas for reattempt (no destructive cleanup in prod).

### Phase 3: Data migration and parity verification

Entry criteria:
- ACL and runtime wiring validated in staging.

Execution:
- Backfill data from each source DB into corresponding target schema.
- Run deterministic parity checks (counts + checksums + sample record diff).

Exit criteria:
- All parity checks pass.
- No unresolved critical mismatches.

Rollback:
- Abort cutover.
- Keep source DBs authoritative.
- Discard/rebuild target schema data in staging before next attempt.

### Phase 4: Cutover and observation window

Entry criteria:
- Staging dry-run successful end-to-end.
- Production parity checks green.

Execution:
- Flip runtime configuration to one-DB endpoints.
- Monitor health, error rates, and data consistency during observation window.

Exit criteria:
- Stable operation for one full observation window (recommended: 24h).
- No Sev1/Sev2 migration regressions.

Rollback:
- Revert runtime config to legacy DB topology.
- Preserve one-DB state for forensic comparison.

### Phase 5: Legacy decommission

Entry criteria:
- Cutover stable.

Execution:
- Mark legacy DBs read-only.
- Snapshot backup and retention archive.
- Remove legacy write paths and compatibility shims.

Exit criteria:
- Legacy DBs no longer used by runtime traffic.

Rollback:
- Restore from retained snapshots only if post-cutover latent loss is detected.

## 7. Data Validation and Cutover Gates

### 7.1 Correctness gates

Required checks (must pass):
- Row-count parity for all core/module tables per butler schema.
- Deterministic checksum parity for key business columns.
- Spot-validation samples for high-risk tables.
- Behavioral smoke tests for each butler's critical MCP tools.

Failure policy:
- Any parity mismatch on required tables blocks cutover.

### 7.2 Isolation gates

Required checks (must pass):
- Each runtime role can use own schema + `shared` only.
- Cross-butler schema reads/writes denied with permission errors.
- Shared-schema operations limited to explicitly granted actions.

Failure policy:
- Any unexpected cross-schema access blocks cutover.

## 8. Rollback Strategy Summary

Principles:
- Keep source data authoritative until cutover signoff.
- Do not perform destructive cleanup before parity verification.
- Ensure config rollback path is fast and scripted.

Minimum rollback artifacts:
- Last-known-good runtime config.
- Source DB snapshots.
- Migration manifest + parity reports.

## 9. Work Decomposition (Child Issues)

`butlers-1003` is decomposed into executable child tasks:

- `butlers-1003.1` design and migration sequencing.
- `butlers-1003.2` schema bootstrap migrations (`shared` + per-butler schemas).
- `butlers-1003.3` ACL grants/revokes and default privileges.
- `butlers-1003.4` data backfill + parity tooling.
- `butlers-1003.5` runtime config refactor to one-DB schema semantics.
- `butlers-1003.6` integration tests for ACL isolation and runtime behavior.
- `butlers-1003.7` docs/runbooks for operations and deployment.

## 10. Environment-Specific Guidance

### 10.1 Local development

- Run a single local Postgres instance/database for all butlers.
- Provision role credentials per butler role for realistic ACL testing.
- Use schema-qualified queries in manual debugging to avoid hidden `search_path`
  assumptions.

### 10.2 CI

- Use one ephemeral Postgres database per CI run.
- Run migration bootstrap + ACL tests in standard quality gates.
- Block merges on parity/isolation test failures.

### 10.3 Production

- Preflight: verify backups, snapshots, and rollback config are ready.
- Execute staged cutover with explicit go/no-go checkpoints.
- Keep a post-cutover observation window before legacy decommission.

## 11. Open Questions

- Whether any shared tables need read-only vs read-write split by role.
- Whether cross-schema analytics should run via dedicated reporting role instead of
  runtime principals.
- Whether temporary dual-write is required for all modules or only selected tables.

## 12. Definition of Done for Epic `butlers-1003`

Epic is complete only when:
- One-DB multi-schema topology is deployed.
- ACL isolation guarantees are enforced and tested.
- Data parity checks pass with no unresolved loss.
- Runtime behavior is equivalent post-cutover.
- Local/CI/production docs reflect the new operating model.
